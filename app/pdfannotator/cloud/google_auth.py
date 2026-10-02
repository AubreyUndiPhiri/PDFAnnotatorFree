"""Sign in with Google and make authorised API requests.

OAuth 2.0 for installed apps: the browser opens Google's sign-in page, Google
redirects back to a one-off web server on 127.0.0.1, and the code is swapped
for tokens (with PKCE). The user supplies their own OAuth client (a "Desktop
app" client from Google Cloud Console, see ui.ConnectDialog).

The client secret and tokens are stored in %APPDATA%/AupedeanAnnotator/google,
encrypted with Windows DPAPI (only this Windows user can read them).

No Qt here.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..fonts import APP_DATA

GOOGLE_DIR = Path(os.environ.get("AUPEDEAN_GOOGLE_DIR") or APP_DATA / "google")  # tests use a temp folder
CLIENT_FILE = GOOGLE_DIR / "client.bin"
TOKEN_FILE = GOOGLE_DIR / "token.bin"

SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/drive",               # open and sync any Drive file
    "https://www.googleapis.com/auth/script.projects",     # create the signing web page
    "https://www.googleapis.com/auth/script.deployments",
]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
TIMEOUT = 60
USER_AGENT = "AupedeanAnnotator/2.0 (Windows)"


class AuthError(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status, message, reason="", body=b"", headers=None):
        super().__init__(message)
        self.status, self.message, self.reason = status, message, reason
        self.body, self.headers = body, headers or {}   # the raw reply, for explaining odd refusals


class Offline(Exception):
    """No connection to Google (the request can be retried later)."""


# --------------------------------------------------------------------------
# Storage, encrypted for this Windows user
# --------------------------------------------------------------------------

def _dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        return data
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out))  # UI_FORBIDDEN
    if not ok:
        raise AuthError("Windows could not " + ("protect" if protect else "read") + " the stored Google sign-in.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def save_secret(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(_dpapi(json.dumps(value).encode("utf-8"), protect=True))
    os.replace(tmp, path)


def load_secret(path: Path):
    try:
        return json.loads(_dpapi(path.read_bytes(), protect=False).decode("utf-8"))
    except (OSError, ValueError, AuthError):
        return None


# --------------------------------------------------------------------------
# The OAuth client (from the JSON file Google Cloud Console downloads)
# --------------------------------------------------------------------------

def parse_client_json(text: str) -> dict:
    """{"client_id", "client_secret"} from a client_secret_*.json file."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise AuthError("That file isn't a Google OAuth client file (it isn't JSON).") from e
    if "web" in data and "installed" not in data:
        raise AuthError("This is a \"Web application\" client. Create an OAuth client of type \"Desktop app\" "
                        "instead and download its JSON.")
    info = data.get("installed") or data
    client_id, secret = info.get("client_id"), info.get("client_secret")
    if not client_id or not client_id.endswith(".apps.googleusercontent.com"):
        raise AuthError("No OAuth client ID was found in that file.")
    return {"client_id": client_id, "client_secret": secret or ""}


def load_client():
    return load_secret(CLIENT_FILE)


def save_client(client: dict):
    save_secret(CLIENT_FILE, client)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def http_request(method, url, params=None, data=None, headers=None, timeout=TIMEOUT):
    """(status, headers, body bytes). Raises ApiError (Google said no) or
    Offline (no connection)."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    # Cloudflare (in front of the signature service and Brevo) refuses Python's
    # default User-Agent with "error code: 1010", so say who we are
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        message, reason = e.reason, ""
        try:
            err = json.loads(body).get("error")
            if isinstance(err, dict):
                message = err.get("message", message)
                reason = (err.get("errors") or [{}])[0].get("reason", "") or err.get("status", "")
            elif isinstance(err, str):
                message = json.loads(body).get("error_description", err)
                reason = err
            elif err is None:  # {"code": ..., "message": ...} (e.g. Brevo)
                parsed = json.loads(body)
                message = parsed.get("message", message)
                reason = parsed.get("code", "")
        except (ValueError, AttributeError):
            pass
        raise ApiError(e.code, str(message), reason, body, dict(e.headers or {})) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        host = urllib.parse.urlparse(url).hostname or ""
        if isinstance(getattr(e, "reason", None), socket.gaierror) and host not in _DNS_OVERRIDES:
            ip = _resolve_publicly(host)   # this network's DNS doesn't know the name (yet): ask a public one
            if ip:
                _DNS_OVERRIDES[host] = ip
                return http_request(method, url, data=data, headers=headers, timeout=timeout)
        raise Offline(f"Can't reach {host or 'the internet'} ({getattr(e, 'reason', e)}).") from None


# A name this network's DNS can't find (a brand-new workers.dev address is
# often remembered as "doesn't exist" for a while) is looked up with public
# DNS over HTTPS instead, and connections to it use that address.
_DNS_OVERRIDES = {}
_system_getaddrinfo = socket.getaddrinfo


def _getaddrinfo(host, *args, **kwargs):
    return _system_getaddrinfo(_DNS_OVERRIDES.get(host, host), *args, **kwargs)


socket.getaddrinfo = _getaddrinfo


def _resolve_publicly(host):
    """An IPv4 address for `host` from Cloudflare's or Google's DNS over HTTPS
    (reached by IP address, so no DNS is needed to ask), or None."""
    for resolver in ("https://1.1.1.1/dns-query", "https://8.8.8.8/resolve"):
        req = urllib.request.Request(f"{resolver}?name={urllib.parse.quote(host)}&type=A",
                                     headers={"accept": "application/dns-json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                answers = json.loads(resp.read()).get("Answer") or []
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError):
            continue
        ips = [a["data"] for a in answers if a.get("type") == 1 and a.get("data")]
        if ips:
            return ips[0]
    return None


def _form_post(url, fields):
    status, _h, body = http_request("POST", url, data=urllib.parse.urlencode(fields).encode(),
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    return json.loads(body)


# --------------------------------------------------------------------------
# Sign-in
# --------------------------------------------------------------------------

class _Catcher(http.server.BaseHTTPRequestHandler):
    result = None

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" not in query and "error" not in query:
            self.send_response(404)
            self.end_headers()
            return
        type(self).result = {k: v[0] for k, v in query.items()}
        ok = "code" in query
        page = ("<h2>Signed in to AUPedean Annotator</h2><p>You can close this tab and go back to the app.</p>" if ok
                else "<h2>Sign-in was cancelled</h2><p>You can close this tab.</p>")
        body = (f"<!doctype html><meta charset=utf-8><title>AUPedean Annotator</title>"
                f"<body style=\"font-family:Segoe UI,sans-serif;margin:3em;color:#1b2236\">{page}</body>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def sign_in(client, open_browser, cancel: threading.Event = None, timeout=300):
    """Run the browser sign-in and return the stored credentials dict.
    open_browser(url) shows Google's page; cancel stops waiting."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)

    class Handler(_Catcher):
        result = None

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 0.5
    redirect = f"http://127.0.0.1:{server.server_address[1]}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "code_challenge": challenge, "code_challenge_method": "S256",
        "state": state, "access_type": "offline", "prompt": "consent",
    })
    try:
        open_browser(url)
        deadline = time.time() + timeout
        while Handler.result is None:
            if cancel is not None and cancel.is_set():
                raise AuthError("Sign-in was cancelled.")
            if time.time() > deadline:
                raise AuthError("Sign-in timed out. Try again.")
            server.handle_request()
    finally:
        server.server_close()
    result = Handler.result
    if result.get("state") != state:
        raise AuthError("The sign-in response didn't match this request. Try again.")
    if "error" in result:
        raise AuthError("Google sign-in was cancelled or refused (" + result["error"] + ").")
    try:
        tokens = _form_post(TOKEN_URL, {
            "code": result["code"], "client_id": client["client_id"], "client_secret": client["client_secret"],
            "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier,
        })
    except ApiError as e:
        raise AuthError(f"Google didn't accept the sign-in: {e.message}") from e
    creds = {
        "access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": time.time() + int(tokens.get("expires_in", 3600)) - 60,
        "scope": tokens.get("scope", ""),
    }
    creds["email"] = _user_email(creds["access_token"])
    save_secret(TOKEN_FILE, creds)
    return creds


def _user_email(access_token):
    try:
        _s, _h, body = http_request("GET", USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        return json.loads(body).get("email", "")
    except (ApiError, Offline, ValueError):
        return ""


def load_credentials():
    return load_secret(TOKEN_FILE)


def disconnect():
    creds = load_credentials()
    if creds:
        try:
            http_request("POST", REVOKE_URL, params={"token": creds.get("refresh_token") or creds["access_token"]},
                         data=b"", timeout=15)
        except (ApiError, Offline):
            pass
    for path in (TOKEN_FILE,):
        try:
            path.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------
# An authorised session
# --------------------------------------------------------------------------

class Session:
    """Adds the access token to requests and refreshes it when it expires.
    Safe to use from several threads."""

    def __init__(self, client=None, creds=None):
        self.client = client or load_client()
        self.creds = creds or load_credentials()
        self._lock = threading.Lock()
        if not self.client or not self.creds:
            raise AuthError("Not connected to a Google account.")

    @property
    def email(self):
        return self.creds.get("email", "")

    def has_scope(self, scope):
        return scope in self.creds.get("scope", "").split()

    def _token(self, force=False):
        with self._lock:
            if force or time.time() >= self.creds.get("expires_at", 0):
                if not self.creds.get("refresh_token"):
                    raise AuthError("The Google sign-in has expired. Connect the account again.")
                try:
                    tokens = _form_post(TOKEN_URL, {
                        "client_id": self.client["client_id"], "client_secret": self.client["client_secret"],
                        "refresh_token": self.creds["refresh_token"], "grant_type": "refresh_token",
                    })
                except ApiError as e:
                    if e.status in (400, 401):
                        raise AuthError("Google no longer accepts this sign-in (it may have been removed "
                                        "in your Google account). Connect the account again.") from e
                    raise
                self.creds["access_token"] = tokens["access_token"]
                self.creds["expires_at"] = time.time() + int(tokens.get("expires_in", 3600)) - 60
                save_secret(TOKEN_FILE, self.creds)
            return self.creds["access_token"]

    def request(self, method, url, params=None, data=None, headers=None, timeout=TIMEOUT):
        headers = dict(headers or {})
        for attempt in (0, 1):
            headers["Authorization"] = f"Bearer {self._token(force=attempt == 1)}"
            try:
                return http_request(method, url, params, data, headers, timeout)
            except ApiError as e:
                if e.status == 401 and attempt == 0:
                    continue  # the token was revoked or expired early: refresh once
                raise

    def json(self, method, url, params=None, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        _s, _h, raw = self.request(method, url, params, data, headers)
        return json.loads(raw) if raw else {}
