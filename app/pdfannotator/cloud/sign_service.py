"""Aupedean Sign: signature requests by email, through a small service the
owner installs once in their own free Cloudflare account (emails go through
their free Brevo account). See sign_service/worker.js for the service.

- People using Aupedean sign in with their email and a 6-digit code.
- A request: the document (pages as pictures + the PDF) is encrypted here
  with a fresh AES-256-GCM key and uploaded; the service emails the signer a
  link holding the key after the "#", which browsers never send to servers.
- The signer proves it's their inbox with a code, reads, signs; the
  signature comes back encrypted with the same key, and SignServiceManager
  collects it automatically and hands it to be stamped into the original.

The client, the encryption and the installer have no Qt; the manager does.
"""
import base64
import json
import os
import re
import secrets
import time
import urllib.parse
import uuid
from pathlib import Path

import pymupdf as fitz
from PySide6.QtCore import QObject, QTimer, Signal

from . import signing, signing_file, worker
from .google_auth import ApiError, Offline, http_request, load_secret, save_secret
from ..fonts import APP_DATA

SIGN_DIR = Path(os.environ.get("AUPEDEAN_SIGN_DIR") or APP_DATA / "sign")   # tests use a temp folder
SERVICE_FILE = SIGN_DIR / "service.json"
SESSION_FILE = SIGN_DIR / "session.bin"
OWNER_FILE = SIGN_DIR / "owner.bin"                 # the setup keys, kept (encrypted) to update the service
BUILT_IN = Path(__file__).resolve().parents[2] / "assets" / "sign_service.json"   # shipped with the app, if any
SOURCE_DIR = Path(__file__).resolve().parent / "sign_service"
PDFLIB = Path(__file__).resolve().parents[2] / "assets" / "js" / "pdf-lib.min.js"

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
BREVO_API = "https://api.brevo.com/v3"
WORKER_NAME = "aupedean-sign"
DATABASE_NAME = "aupedean-sign"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, created INTEGER, expires INTEGER);
CREATE TABLE IF NOT EXISTS codes (k TEXT PRIMARY KEY, code_hash TEXT, expires INTEGER, attempts INTEGER,
  sent_at INTEGER, window_start INTEGER, sends INTEGER);
CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, owner TEXT NOT NULL, owner_name TEXT, signer_email TEXT NOT NULL,
  signer_name TEXT, title TEXT, message TEXT, status TEXT, parts INTEGER, created INTEGER, expires INTEGER,
  signed_at INTEGER, signed_ip TEXT, signed_ua TEXT, done_at INTEGER);
CREATE INDEX IF NOT EXISTS requests_owner ON requests (owner, created);
CREATE TABLE IF NOT EXISTS blobs (request_id TEXT NOT NULL, kind TEXT NOT NULL, part INTEGER NOT NULL, data TEXT,
  PRIMARY KEY (request_id, kind, part));
CREATE TABLE IF NOT EXISTS signer_tokens (token_hash TEXT PRIMARY KEY, request_id TEXT, expires INTEGER);
CREATE TABLE IF NOT EXISTS mail_count (day TEXT PRIMARY KEY, n INTEGER);
CREATE TABLE IF NOT EXISTS quick_requests (id TEXT PRIMARY KEY);
"""

FRIENDLY = {
    "not-allowed": "This email address isn't allowed to use this signature service. Ask its owner to add it.",
    "bad-email": "That doesn't look like an email address.",
    "wait": "Wait a few seconds before asking for another code.",
    "too-many-codes": "Too many codes were asked for. Try again in an hour.",
    "code-expired": "That code has expired. Ask for a new one.",
    "wrong-code": "That code isn't right. Check the email and try again.",
    "too-many-attempts": "Too many wrong codes. Ask for a new one.",
    "signed-out": "You're signed out of the signature service. Sign in again.",
    "daily-limit": "You've sent the most requests allowed in one day. Try again tomorrow.",
    "too-big": "This document is too large to send for signature.",
    "busy": "The signature service has sent all the emails it can today. Try again tomorrow.",
    "email-failed": "The signature service couldn't send the email (check the Brevo account).",
}


class ServiceError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or FRIENDLY.get(code, code))
        self.code = code


class SetupError(Exception):
    def __init__(self, message, url=None):
        super().__init__(message)
        self.url = url


# --------------------------------------------------------------------------
# Where the service is, and who is signed in
# --------------------------------------------------------------------------

def built_in_url():
    """The service shipped with the app (assets/sign_service.json), if any: then nobody sets anything up."""
    try:
        return json.loads(BUILT_IN.read_text(encoding="utf-8")).get("url", "").rstrip("/")
    except (OSError, ValueError):
        return ""


def service_url():
    built_in = built_in_url()
    if built_in:
        return built_in
    try:
        return json.loads(SERVICE_FILE.read_text(encoding="utf-8")).get("url", "").rstrip("/")
    except (OSError, ValueError):
        return ""


def save_service_url(url):
    SIGN_DIR.mkdir(parents=True, exist_ok=True)
    SERVICE_FILE.write_text(json.dumps({"url": url.rstrip("/")}), encoding="utf-8")


def load_session():
    data = load_secret(SESSION_FILE)
    if data and data.get("url") == service_url():
        return data
    return None


def save_session(email, token):
    save_secret(SESSION_FILE, {"url": service_url(), "email": email, "token": token})


def sign_out():
    try:
        SESSION_FILE.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------
# Encryption (AES-256-GCM; the 12-byte nonce goes in front, like the web page expects)
# --------------------------------------------------------------------------

def new_key():
    return os.urandom(32)


def key_text(key):
    return base64.urlsafe_b64encode(key).rstrip(b"=").decode()


def key_from_text(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def seal(key, data: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, data, None)).decode()


def unseal(key, text: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = base64.b64decode(text)
    return AESGCM(key).decrypt(raw[:12], raw[12:], None)


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------

class SignService:
    def __init__(self, url, token=None):
        self.url = url.rstrip("/")
        self.token = token

    def _call(self, method, path, body=None, text=None, raw=False, timeout=60):
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif text is not None:
            data = text.encode()
            headers["Content-Type"] = "text/plain; charset=utf-8"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            _s, _h, resp = http_request(method, self.url + path, data=data, headers=headers, timeout=timeout)
        except ApiError as e:
            code = e.reason or e.message
            try:
                code = json.loads(e.message).get("error", code)
            except (ValueError, AttributeError):
                pass
            raise ServiceError(code, FRIENDLY.get(code) or e.message) from None
        if raw:
            return resp.decode("utf-8")
        return json.loads(resp) if resp else {}

    def health(self):
        return self._call("GET", "/api/health").get("service") == "aupedean-sign"

    def login_code(self, email):
        self._call("POST", "/api/login/code", {"email": email})

    def login_verify(self, email, code):
        reply = self._call("POST", "/api/login/verify", {"email": email, "code": code})
        self.token = reply["token"]
        return reply["email"], reply["token"]

    def me(self):
        return self._call("GET", "/api/me")["email"]

    PART_CHARS = 1800000   # the service keeps documents in parts of this size (a database row holds 2 MB)

    def create(self, meta, sealed_document):
        """Create the request and upload the (sealed) document. Returns its id."""
        size = self.PART_CHARS
        chunks = [sealed_document[i:i + size] for i in range(0, len(sealed_document), size)] or [""]
        request_id = self._call("POST", "/api/requests", {**meta, "parts": len(chunks)})["id"]
        for n, chunk in enumerate(chunks):
            self._call("PUT", f"/api/requests/{request_id}/doc?part={n}", text=chunk, timeout=300)
        return request_id

    def send(self, request_id, key):
        self._call("POST", f"/api/requests/{request_id}/send", {"key": key_text(key)})

    def requests(self, status=None):
        path = "/api/requests" + (f"?status={urllib.parse.quote(status)}" if status else "")
        return self._call("GET", path)["requests"]

    def result(self, request_id):
        return self._call("GET", f"/api/requests/{request_id}/result", raw=True)

    def done(self, request_id):
        self._call("POST", f"/api/requests/{request_id}/done", {})

    def cancel(self, request_id):
        self._call("POST", f"/api/requests/{request_id}/cancel", {})


def current_client():
    """A signed-in SignService, or None."""
    url, session = service_url(), load_session()
    if not url or not session:
        return None
    return SignService(url, session["token"])


def send_request(client, pdf_bytes, title, doc_path, page_index, rect, signer_email, signer_name="", message="",
                 requester_name="", requester_email="", expires_days=14, progress=lambda text: None, quick=False):
    """Encrypt and upload the document, and have the service email the
    signer. Returns the local SignRequest (holding the key). A quick request
    opens from the emailed link alone (no code for the signer)."""
    progress("Preparing the document...")
    key = new_key()
    doc = signing_file.document_data(pdf_bytes, page_index, rect)
    package = {"format": "aupedean-sign-document/1", "title": title, "message": message, "signer_name": signer_name,
               "requester_name": requester_name, "requester_email": requester_email, **doc}
    sealed = seal(key, json.dumps(package).encode())
    progress("Uploading (encrypted)...")
    request_id = client.create({"signer_email": signer_email, "signer_name": signer_name, "title": title,
                                "message": message, "owner_name": requester_name, "expires_days": expires_days,
                                "quick": quick},
                               sealed)
    progress("Emailing the signer...")
    client.send(request_id, key)
    return signing.SignRequest(
        id=request_id, token=key_text(key), title=title, signer_email=signer_email.lower(), signer_name=signer_name,
        message=message, page=page_index, rect=list(fitz.Rect(rect)), field=doc["field"], folder_id=client.url,
        doc_path=doc_path, created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), kind="service")


def collect(client, req, info):
    """(png bytes, result) for a signed request: its signature, decrypted,
    and what the service recorded (the verified email, the time)."""
    result = json.loads(unseal(key_from_text(req.token), client.result(req.id)))
    png = base64.b64decode(result["signature_png"])
    signed_at = info.get("signed_at")
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(signed_at / 1000)) if signed_at else ""
    return png, {"name": result.get("name", ""), "email": req.signer_email, "signed_at": when,
                 "verified_by": "email link" if info.get("quick") else "email code", "ip": info.get("signed_ip", ""), "user_agent": info.get("signed_ua", "")}


# --------------------------------------------------------------------------
# Installing the service (Cloudflare + Brevo APIs; no programming tools)
# --------------------------------------------------------------------------

def _cf(token, method, path, body=None, data=None, content_type=None, ok_codes=()):
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type
    try:
        _s, _h, raw = http_request(method, CLOUDFLARE_API + path, data=data, headers=headers, timeout=120)
    except ApiError as e:
        detail = e.message
        if e.status in (401, 403):
            raise SetupError("Cloudflare didn't accept the API token, or it's missing a permission. It needs: "
                             "Account > Workers Scripts > Edit, Account > D1 > Edit and Account > Account "
                             "Settings > Read.", "https://dash.cloudflare.com/profile/api-tokens") from e
        if e.status in ok_codes:
            return None
        raise SetupError(f"Cloudflare said: {detail}") from e
    reply = json.loads(raw) if raw else {}
    if reply.get("success") is False:
        errors = "; ".join(err.get("message", "") for err in reply.get("errors", []))
        raise SetupError(f"Cloudflare said: {errors}")
    return reply.get("result")


def _multipart(fields):
    boundary = "----aupedean" + uuid.uuid4().hex
    out = []
    for name, filename, ctype, content in fields:
        disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        out.append(f"--{boundary}\r\nContent-Disposition: {disp}\r\nContent-Type: {ctype}\r\n\r\n".encode())
        out.append(content if isinstance(content, bytes) else content.encode())
        out.append(b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def worker_source():
    source = (SOURCE_DIR / "worker.js").read_text(encoding="utf-8")
    page = (SOURCE_DIR / "page.html").read_text(encoding="utf-8")
    return (source.replace("__PAGE_HTML__", json.dumps(page))
                  .replace("__PDFLIB__", json.dumps(PDFLIB.read_text(encoding="utf-8"))))


def normalize_brevo_key(api_key):
    """The plain xkeysib- key. Brevo's "copy for MCP" button gives base64 of {"api_key": "..."}."""
    api_key = "".join(api_key.split())
    if not api_key.startswith(("xkeysib-", "xsmtpsib-")):
        try:
            wrapped = json.loads(base64.b64decode(api_key + "=" * (-len(api_key) % 4), validate=True))
            if isinstance(wrapped, dict) and isinstance(wrapped.get("api_key"), str):
                return wrapped["api_key"].strip()
        except ValueError:
            pass
    return api_key


def _reply_details(e):
    """What else the refusal said: its body (as text) and tell-tale headers."""
    text = e.body.decode("utf-8", "replace") if isinstance(e.body, bytes) else str(e.body or "")
    text = " ".join(re.sub(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", " ", text, flags=re.S | re.I).split())
    heads = {k.lower(): v for k, v in (e.headers or {}).items()}
    extra = [f"reply: {text[:300]}" if text else "reply: (empty)"]
    extra += [f"{k}: {heads[k]}" for k in ("server", "cf-ray", "x-sib-server") if k in heads]
    return "\n" + "\n".join(extra)


def _firewall_page(e):
    """True for a 403 from the firewall in front of Brevo (an HTML page), not from Brevo itself."""
    heads = {k.lower(): v for k, v in (e.headers or {}).items()}
    body = e.body.decode("utf-8", "replace").lower() if isinstance(e.body, bytes) else ""
    return e.status == 403 and ("html" in heads.get("content-type", "") or "<html" in body
                                or "attention required" in body or "access denied" in body)


def check_brevo(api_key):
    """(account email, [verified sender emails]) for a Brevo API key."""
    for_mcp = not "".join(api_key.split()).startswith(("xkeysib-", "xsmtpsib-"))   # the eyJ... kind
    api_key = normalize_brevo_key(api_key)
    headers = {"api-key": api_key, "accept": "application/json"}
    step = "/account"
    try:
        _s, _h, raw = http_request("GET", BREVO_API + step, headers=headers, timeout=30)
        account = json.loads(raw)
        step = "/senders"
        try:
            _s, _h, raw = http_request("GET", BREVO_API + step, headers=headers, timeout=30)
            senders = [s["email"].lower() for s in json.loads(raw).get("senders", []) if s.get("active", True)]
        except ApiError as e:
            if e.status != 403:
                raise
            senders = []   # the key may not list senders; sending can still work, so don't stop here
    except ApiError as e:
        detail = f"\n\nBrevo said ({e.status}) to GET {step}: {e.message}" + _reply_details(e)
        if _firewall_page(e):
            raise SetupError("Brevo's firewall blocked this computer before it looked at the key. This happens "
                             "behind a VPN, proxy, some antivirus web shields, or on a network Brevo distrusts. "
                             "Turn the VPN or proxy off (or try another network, such as your phone's hotspot) "
                             "and install again." + detail) from e
        if "ip address" in e.message.lower():
            raise SetupError("Brevo blocked the request because it came from an IP address it doesn't know. "
                             "Under Security > Authorised IPs, add this IP or turn the check off." + detail,
                             "https://app.brevo.com/security/authorised_ips") from e
        if api_key.strip().startswith("xsmtpsib-"):
            raise SetupError("That's an SMTP key. Make an API key (it starts with xkeysib-) under "
                             "SMTP & API > API Keys." + detail, "https://app.brevo.com/settings/keys/api") from e
        if e.status == 403 and for_mcp:
            raise SetupError("Brevo knows that key but won't let it use its API: it was made for MCP (it starts "
                             "with eyJ). Under SMTP & API > API Keys, click Generate a new API key, leave the MCP "
                             "option off, and paste the key that starts with xkeysib-." + detail,
                             "https://app.brevo.com/settings/keys/api") from e
        if e.status == 403:
            raise SetupError("Brevo knows that key but won't let it use its API. Usually the Brevo account isn't "
                             "activated yet: complete your profile (name, company, address, phone) in Brevo, and "
                             "check your inbox for a message from Brevo asking for more details. If the key was "
                             "made with the MCP option on, make a new one with it off." + detail,
                             "https://app.brevo.com/account/profile") from e
        raise SetupError("Brevo didn't accept that API key. Make a new one under SMTP & API > API Keys." + detail,
                         "https://app.brevo.com/settings/keys/api") from e
    return account.get("email", "").lower(), senders


def install(cf_token, brevo_key, owner_email, sender_email, allow="", sender_name="Aupedean Sign",
            progress=lambda text: None, wait_online=120, requests_per_day=0, emails_per_day=0):
    """Create (or update) the service in the Cloudflare account and return its address."""
    owner_email, sender_email = owner_email.strip().lower(), sender_email.strip().lower()
    progress("Checking the Brevo account...")
    _account, senders = check_brevo(brevo_key)        # as pasted: it tells an MCP key apart
    brevo_key = normalize_brevo_key(brevo_key)
    if senders and sender_email not in senders:
        raise SetupError(f"{sender_email} isn't a verified sender in Brevo. Add it under Senders, Domains & "
                         "Dedicated IPs > Senders (Brevo emails you a confirmation), or use one listed there: "
                         + ", ".join(senders), "https://app.brevo.com/senders/list")
    progress("Finding your Cloudflare account...")
    accounts = _cf(cf_token, "GET", "/accounts") or []
    if not accounts:
        raise SetupError("The Cloudflare token can't see any account. Give it the Account Settings > Read permission.")
    account = accounts[0]["id"]
    progress("Creating the database...")
    found = _cf(cf_token, "GET", f"/accounts/{account}/d1/database?name={DATABASE_NAME}") or []
    database = next((d for d in found if d.get("name") == DATABASE_NAME), None)
    if database is None:
        database = _cf(cf_token, "POST", f"/accounts/{account}/d1/database", {"name": DATABASE_NAME})
    database_id = database.get("uuid") or database.get("id")
    _cf(cf_token, "POST", f"/accounts/{account}/d1/database/{database_id}/query", {"sql": SCHEMA})
    progress("Installing the signing service...")
    metadata = {
        "main_module": "worker.js", "compatibility_date": "2025-06-01",
        "bindings": [
            {"type": "d1", "name": "DB", "id": database_id},
            {"type": "secret_text", "name": "BREVO_KEY", "text": brevo_key},
            {"type": "plain_text", "name": "SENDER", "text": sender_email},
            {"type": "plain_text", "name": "SENDER_NAME", "text": sender_name},
            {"type": "plain_text", "name": "OWNER", "text": owner_email},
            {"type": "plain_text", "name": "ALLOW", "text": allow},
            {"type": "plain_text", "name": "REQUESTS_PER_DAY", "text": str(requests_per_day or "")},
            {"type": "plain_text", "name": "EMAILS_PER_DAY", "text": str(emails_per_day or "")},
        ],
    }
    body, ctype = _multipart([("metadata", None, "application/json", json.dumps(metadata)),
                              ("worker.js", "worker.js", "application/javascript+module", worker_source())])
    _cf(cf_token, "PUT", f"/accounts/{account}/workers/scripts/{WORKER_NAME}", data=body, content_type=ctype)
    progress("Giving it a web address...")
    sub = _cf(cf_token, "GET", f"/accounts/{account}/workers/subdomain", ok_codes=(404,))
    name = (sub or {}).get("subdomain")
    if not name:
        name = "aupedean-" + secrets.token_hex(3)
        _cf(cf_token, "PUT", f"/accounts/{account}/workers/subdomain", {"subdomain": name})
    _cf(cf_token, "POST", f"/accounts/{account}/workers/scripts/{WORKER_NAME}/subdomain",
        {"enabled": True, "previews_enabled": False})
    url = f"https://{WORKER_NAME}.{name}.workers.dev"
    save_service_url(url)
    save_secret(OWNER_FILE, {"cf_token": cf_token, "brevo_key": brevo_key, "owner": owner_email,
                             "sender": sender_email, "allow": allow})
    if allow == "*":
        progress(f'To ship it with the app, put {{"url": "{url}"}} in app/assets/sign_service.json.')
    progress("Waiting for the address to come online (a new one can take a minute or two)...")
    deadline = time.time() + wait_online
    while time.time() < deadline:
        try:
            if SignService(url).health():
                break
        except (ServiceError, Offline, ValueError):
            pass
        time.sleep(5)
    progress("Done.")
    return url


# --------------------------------------------------------------------------
# Background collection
# --------------------------------------------------------------------------

class SignServiceManager(QObject):
    """Every minute while requests are waiting: asks the service which were
    signed and emits `signed` (request, png, result) for each."""
    signed = Signal(object, object, object)
    changed = Signal()
    POLL_MS = 60_000

    def __init__(self, store, parent=None, client_factory=current_client):
        super().__init__(parent)
        self.store = store
        self.client_factory = client_factory
        self._checking = False
        self.timer = QTimer(self)
        self.timer.setInterval(self.POLL_MS)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    def waiting(self):
        return [r for r in self.store.items if r.kind == "service" and r.status == signing.WAITING]

    def check(self, on_done=None):
        waiting = self.waiting()
        client = self.client_factory()
        if self._checking or not waiting or client is None:
            if on_done:
                on_done()
            return
        self._checking = True
        ids = {r.id: r for r in waiting}

        def job():
            found, statuses = [], {}
            for info in client.requests():
                if info["id"] not in ids:
                    continue
                statuses[info["id"]] = info["status"]
                if info["status"] == "signed":
                    found.append((ids[info["id"]], *collect(client, ids[info["id"]], info)))
            return found, statuses

        def done(result):
            self._checking = False
            found, statuses = result
            for req_id, status in statuses.items():
                if status in ("cancelled", "expired"):
                    ids[req_id].status = status
            self.store.save()
            for req, png, info in found:
                self.signed.emit(req, png, info)
            self.changed.emit()
            if on_done:
                on_done()

        def failed(_exc):
            self._checking = False
            if on_done:
                on_done()

        worker.run(job, done, failed)

    def acknowledge(self, req):
        """The signature is in the PDF: the service deletes its copy."""
        client = self.client_factory()
        if client is not None:
            worker.run(lambda: client.done(req.id))
