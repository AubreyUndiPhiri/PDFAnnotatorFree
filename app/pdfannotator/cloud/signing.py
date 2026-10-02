"""Signing links: ask someone to sign a PDF from a link.

How it works
- Set up once (setup_service): two small Google Apps Script web apps are
  created in the user's own Google account through the Apps Script API.
  The *service* runs as the user and can only use the "Aupedean Signing"
  folder in their Drive. The *page* is what signers open: it runs as the
  signer, who must sign in with Google, so their email is verified by Google.
  The two share a secret and prove every message with an HMAC.
- A request (create_request) is a folder in "Aupedean Signing" holding the
  pages as pictures and request.json (who should sign, where, a secret token
  that is part of the link, expiry). The link is <page url>?r=<id>&t=<token>.
- The signer reads the pages, types their name, draws their signature and
  agrees; the service saves signature.png and result.json in the folder.
- SigningManager checks Drive for results in the background; the signature
  is then stamped into the PDF (stamp_signature) with a line recording who
  signed, their Google-verified email and when.
"""
import calendar
import json
import secrets
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf as fitz
from PySide6.QtCore import QObject, QTimer, Signal

from . import worker
from .drive import DriveClient
from .google_auth import GOOGLE_DIR, ApiError, AuthError, Offline, http_request, load_secret, save_secret

SCRIPT_API = "https://script.googleapis.com/v1"
CONFIG_FILE = GOOGLE_DIR / "signing.bin"
REQUESTS_FILE = GOOGLE_DIR / "sign_requests.json"
SCRIPTS_DIR = Path(__file__).resolve().parent / "apps_script"
ROOT_FOLDER = "Aupedean Signing"
APPS_SCRIPT_SETTINGS = "https://script.google.com/home/usersettings"
PAGE_DPI = 110
MAX_PAGES = 40

WAITING, SIGNED, APPLIED, CANCELLED, EXPIRED = "waiting", "signed", "applied", "cancelled", "expired"
STATUS_TEXT = {WAITING: "Waiting for signature", SIGNED: "Signed (not yet added to the PDF)",
               APPLIED: "Signed", CANCELLED: "Cancelled", EXPIRED: "Expired"}

_SERVICE_MANIFEST = {
    "timeZone": "Etc/UTC", "runtimeVersion": "V8", "exceptionLogging": "STACKDRIVER",
    "oauthScopes": ["https://www.googleapis.com/auth/drive"],
    "webapp": {"executeAs": "USER_DEPLOYING", "access": "ANYONE_ANONYMOUS"},
}
_PAGE_MANIFEST = {
    "timeZone": "Etc/UTC", "runtimeVersion": "V8", "exceptionLogging": "STACKDRIVER",
    "oauthScopes": ["https://www.googleapis.com/auth/userinfo.email",
                    "https://www.googleapis.com/auth/script.external_request"],
    "webapp": {"executeAs": "USER_ACCESSING", "access": "ANYONE"},  # signers must sign in with Google
}


class SetupError(Exception):
    def __init__(self, message, url=None):
        super().__init__(message)
        self.url = url


# --------------------------------------------------------------------------
# Setting up the two web apps
# --------------------------------------------------------------------------

def load_config():
    return load_secret(CONFIG_FILE)


def save_config(config):
    save_secret(CONFIG_FILE, config)


def script_sources(secret, root_id, service_url):
    """The Apps Script files for (service, page), with the placeholders filled in."""
    def read(name):
        return (SCRIPTS_DIR / name).read_text(encoding="utf-8")

    service = read("service.gs").replace("{{SECRET}}", secret).replace("{{ROOT_ID}}", root_id)
    page = read("page.gs").replace("{{SECRET}}", secret).replace("{{SERVICE_URL}}", service_url or "")
    return service, page, read("page.html")


def _script_call(session, method, path, body=None):
    try:
        return session.json(method, SCRIPT_API + path, body=body)
    except ApiError as e:
        text = e.message.lower()
        if e.status == 403 and "apps script api" in text and "user" in text:
            raise SetupError("Turn on \"Google Apps Script API\" in your Apps Script settings (one switch), "
                             "then try again.", APPS_SCRIPT_SETTINGS) from e
        if e.status == 403 and ("has not been used" in text or "disabled" in text or e.reason == "SERVICE_DISABLED"
                                or "accessNotConfigured" in e.reason):
            raise SetupError("Enable the \"Apps Script API\" in your Google Cloud project (APIs & Services > "
                             "Library), then try again.",
                             "https://console.cloud.google.com/apis/library/script.googleapis.com") from e
        if e.status == 403 and "insufficient" in text:
            raise SetupError("Your Google sign-in doesn't allow creating the signing page. Disconnect and connect "
                             "the Google account again (the new permissions are asked for then).") from e
        raise


def _deploy(session, title, files):
    project = _script_call(session, "POST", "/projects", {"title": title})
    script_id = project["scriptId"]
    _script_call(session, "PUT", f"/projects/{script_id}/content", {"files": files})
    version = _script_call(session, "POST", f"/projects/{script_id}/versions", {"description": "AUPedean Annotator"})
    deployment = _script_call(session, "POST", f"/projects/{script_id}/deployments", {
        "versionNumber": version["versionNumber"], "manifestFileName": "appsscript",
        "description": "AUPedean Annotator signing links"})
    url = next((ep["webApp"]["url"] for ep in deployment.get("entryPoints", [])
                if ep.get("entryPointType") == "WEB_APP" and ep.get("webApp", {}).get("url")), None)
    if not url:
        raise SetupError("Google created the script but didn't return its web address.")
    return script_id, deployment.get("deploymentId", ""), url


def setup_service(session, progress=lambda text: None):
    """Create the signing service and page in the user's Google account and
    save their addresses. Returns the config."""
    drive = DriveClient(session)
    progress("Creating the \"Aupedean Signing\" folder in your Drive...")
    root = drive.ensure_folder(ROOT_FOLDER, "root", description="Signature requests made with AUPedean Annotator. "
                                                                 "Deleting a folder here cancels that request.")
    secret = secrets.token_hex(32)
    service_src, _page_src, _html = script_sources(secret, root["id"], "")
    progress("Creating the signing service (runs as you, only in that folder)...")
    service_id, service_dep, service_url = _deploy(session, "AUPedean Sign - service", [
        {"name": "appsscript", "type": "JSON", "source": json.dumps(_SERVICE_MANIFEST)},
        {"name": "Code", "type": "SERVER_JS", "source": service_src},
    ])
    _s, page_src, page_html = script_sources(secret, root["id"], service_url)
    progress("Creating the signing page (signers sign in with Google)...")
    page_id, page_dep, page_url = _deploy(session, "AUPedean Sign - page", [
        {"name": "appsscript", "type": "JSON", "source": json.dumps(_PAGE_MANIFEST)},
        {"name": "Code", "type": "SERVER_JS", "source": page_src},
        {"name": "page", "type": "HTML", "source": page_html},
    ])
    config = {"root_folder_id": root["id"], "secret": secret, "service_script_id": service_id,
              "service_deployment_id": service_dep, "service_url": service_url, "page_script_id": page_id,
              "page_deployment_id": page_dep, "page_url": page_url, "owner": session.email,
              "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "approved": False}
    save_config(config)
    progress("Done.")
    return config


def service_approved(config):
    """True once the owner has opened the service page and allowed it."""
    try:
        _s, _h, body = http_request("GET", config["service_url"], timeout=30)
        return json.loads(body).get("ok") is True
    except (ApiError, Offline, ValueError, KeyError):
        return False


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------

@dataclass
class SignRequest:
    id: str
    token: str
    title: str
    signer_email: str
    signer_name: str
    message: str
    page: int
    rect: list                  # where the signature goes, in PDF coordinates (unrotated)
    field: dict                 # the same box as fractions of the page as shown (for the web page)
    folder_id: str
    doc_path: str
    drive_file_id: str = ""
    created: str = ""
    expires: str = ""
    status: str = WAITING
    signed_name: str = ""
    signed_email: str = ""
    signed_at: str = ""
    applied_at: str = ""
    error: str = ""
    kind: str = "google"        # "google" (a signing link) or "file" (a signing file, see signing_file.py)
    file_path: str = ""         # the signing file, for kind "file"

    @property
    def status_text(self):
        return STATUS_TEXT.get(self.status, self.status)


class RequestStore:
    def __init__(self, path=None):
        self.path = Path(path or REQUESTS_FILE)
        self.items = []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = [SignRequest(**{k: v for k, v in d.items() if k in SignRequest.__dataclass_fields__})
                          for d in data]
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(r) for r in self.items], indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, req):
        self.items.insert(0, req)
        self.save()

    def get(self, request_id):
        return next((r for r in self.items if r.id == request_id), None)

    def remove(self, request_id):
        self.items = [r for r in self.items if r.id != request_id]
        self.save()


def link(config, req):
    return config["page_url"] + "?" + urllib.parse.urlencode({"r": req.id, "t": req.token})


SIGNATURE_WORDS = ("signature", "sign here", "signed")


def find_signature_spot(doc):
    """(page index, box in unrotated PDF coordinates) where a signature
    probably goes: beside the last "Signature"/"Sign here" label in the
    document, else the bottom right of the last page."""
    for index in range(doc.page_count - 1, -1, -1):
        page = doc[index]
        labels = [r for word in SIGNATURE_WORDS for r in page.search_for(word)]
        if not labels:
            continue
        label = max(labels, key=lambda r: (r.y1, r.x0))    # the lowest one: usually the one to fill in
        shown = page.rect
        if label.x1 + 190 <= shown.x1:                     # room on its right
            box = fitz.Rect(label.x1 + 6, label.y1 - 40, label.x1 + 186, label.y1 + 4)
        else:                                              # otherwise just above it
            box = fitz.Rect(label.x0, label.y0 - 50, label.x0 + 180, label.y0 - 2)
        box &= shown
        if box.width > 40 and box.height > 20:
            box = box * page.derotation_matrix
            box.normalize()
            return index, box
    page = doc[doc.page_count - 1]
    w, h = page.rect.width, page.rect.height
    box = fitz.Rect(w - 250, h - 130, w - 70, h - 80) * page.derotation_matrix
    box.normalize()
    return page.number, box


def field_fractions(page, rect):
    """The box `rect` (unrotated PDF coordinates) as fractions of the page as displayed."""
    shown = fitz.Rect(rect) * page.rotation_matrix
    shown.normalize()
    w, h = page.rect.width, page.rect.height
    return {"page": page.number, "x": shown.x0 / w, "y": shown.y0 / h, "w": shown.width / w, "h": shown.height / h}


def create_request(session, config, pdf_bytes, title, doc_path, page_index, rect, signer_email, signer_name="",
                   message="", expires_days=14, drive_file_id="", progress=lambda text: None):
    """Upload the pages and request details; returns the new SignRequest."""
    drive = DriveClient(session)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_index]
    request_id = secrets.token_urlsafe(12).replace("-", "a").replace("_", "b")
    token = secrets.token_urlsafe(24)
    now = time.time()
    expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + expires_days * 86400)) if expires_days else ""
    progress("Creating the request on Google Drive...")
    folder = drive.create_folder(request_id, config["root_folder_id"],
                                 description=f"Signature request: {title} -> {signer_email}")
    pages = []
    count = min(doc.page_count, MAX_PAGES)
    for i in range(count):
        progress(f"Uploading page {i + 1} of {count}...")
        pix = doc[i].get_pixmap(dpi=PAGE_DPI, alpha=False)
        name = f"page-{i + 1}.jpg"
        drive.create(name, folder["id"], content=pix.tobytes("jpeg", jpg_quality=82), mime="image/jpeg")
        pages.append({"file": name, "w": pix.width, "h": pix.height})
    req = SignRequest(id=request_id, token=token, title=title, signer_email=signer_email.strip().lower(),
                      signer_name=signer_name.strip(), message=message.strip(), page=page_index,
                      rect=list(fitz.Rect(rect)), field=field_fractions(page, rect), folder_id=folder["id"],
                      doc_path=doc_path, drive_file_id=drive_file_id,
                      created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), expires=expires)
    body = {"token": token, "title": title, "message": req.message, "requester": session.email,
            "signer_email": req.signer_email, "signer_name": req.signer_name, "field": req.field,
            "pages": pages, "created": req.created, "expires": expires or None}
    drive.create("request.json", folder["id"], content=json.dumps(body).encode(), mime="application/json")
    progress("Done.")
    return req


def check_request(session, req):
    """(status, png bytes or None, result dict or None) from Drive."""
    drive = DriveClient(session)
    try:
        folder = drive.get(req.folder_id)
    except ApiError as e:
        if e.status == 404:
            return CANCELLED, None, None
        raise
    if folder.get("trashed"):
        return CANCELLED, None, None
    result_file = drive.find_child(req.folder_id, "result.json")
    if result_file is None:
        if req.expires and time.time() > calendar.timegm(time.strptime(req.expires, "%Y-%m-%dT%H:%M:%SZ")):
            return EXPIRED, None, None
        return WAITING, None, None
    result = json.loads(drive.download_bytes(result_file["id"]))
    signature = drive.find_child(req.folder_id, "signature.png")
    png = drive.download_bytes(signature["id"]) if signature else None
    return SIGNED, png, result


def cancel_request(session, req):
    DriveClient(session).trash(req.folder_id)


def tidy_request(session, req):
    """After the signature is in the PDF: remove the page pictures (the
    request, signature and record stay in Drive as evidence)."""
    drive = DriveClient(session)
    for item in drive.children(req.folder_id):
        if item["name"].startswith("page-") and item["name"].endswith(".jpg"):
            try:
                drive.delete(item["id"])
            except ApiError:
                pass


def stamp_signature(doc, req, png, result):
    """Draw the signature into the page (as page content, so it can't be
    moved off by accident) with a small line saying who signed and when."""
    page = doc[req.page]
    rect = fitz.Rect(req.rect)
    page.insert_image(rect, stream=png, keep_proportion=True, overlay=True)
    when = result.get("signed_at", "")
    try:
        when = time.strftime("%d %b %Y %H:%M UTC", time.strptime(when[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        pass
    email = f" ({result['email']})" if result.get("email") else ""
    how = {"Google sign-in": "verified by Google sign-in",
           "email code": "email verified by AUPedean Sign",
           "email link": "from the link emailed by AUPedean Sign"}.get(result.get("verified_by", "Google sign-in"),
                                                                "with an AUPedean signing file")
    line = f"Signed by {result.get('name', '')}{email}, {when}, {how}"
    box = fitz.Rect(rect.x0, rect.y1 + 1, max(rect.x1, rect.x0 + 260), rect.y1 + 12) & page.rect
    if not box.is_empty:
        page.insert_textbox(box, line, fontsize=5.5, fontname="helv", color=(0.35, 0.38, 0.45))


# --------------------------------------------------------------------------
# Background checking
# --------------------------------------------------------------------------

class SigningManager(QObject):
    """Checks waiting requests every couple of minutes; emits `signed`
    with (request, png, result) when a signature comes back."""
    signed = Signal(object, object, object)
    changed = Signal()
    POLL_MS = 120_000

    def __init__(self, session_factory, parent=None, store=None):
        super().__init__(parent)
        self._session_factory = session_factory
        self.store = store or RequestStore()
        self._checking = False
        self.timer = QTimer(self)
        self.timer.setInterval(self.POLL_MS)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    def waiting(self):
        """Google signing-link requests still to check (signing files come back by hand)."""
        return [r for r in self.store.items if r.status in (WAITING, SIGNED) and r.kind == "google"]

    def check(self, on_done=None):
        requests = self.waiting()
        session = self._session_factory()
        if self._checking or not requests or session is None:
            if on_done:
                on_done()
            return
        self._checking = True

        def job():
            found = []
            for req in requests:
                try:
                    found.append((req, *check_request(session, req)))
                except (Offline, AuthError):
                    raise
                except ApiError as e:
                    req.error = e.message
            return found

        def done(found):
            self._checking = False
            for req, status, png, result in found:
                req.error = ""
                if status == SIGNED and png:
                    req.status = SIGNED
                    req.signed_name, req.signed_email = result.get("name", ""), result.get("email", "")
                    req.signed_at = result.get("signed_at", "")
                    self.signed.emit(req, png, result)
                elif status in (CANCELLED, EXPIRED):
                    req.status = status
            self.store.save()
            self.changed.emit()
            if on_done:
                on_done()

        def failed(_exc):
            self._checking = False
            if on_done:
                on_done()

        worker.run(job, done, failed)

    def mark_applied(self, req):
        req.status = APPLIED
        req.applied_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.store.save()
        self.changed.emit()
        session = self._session_factory() if req.kind == "google" else None
        if session is not None:
            worker.run(lambda: tidy_request(session, req))
