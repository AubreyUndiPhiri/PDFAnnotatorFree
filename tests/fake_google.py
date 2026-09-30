"""A small stand-in for Google's sign-in, Drive and Apps Script APIs, so the
cloud code can be tested without a Google account or the internet.

Only what the app uses is implemented: the token endpoint, userinfo, Drive
v3 files (list with the query forms the app sends, get, media download,
export, JSON create / patch / delete, resumable uploads) and the Apps Script
projects / content / versions / deployments calls.
"""
import hashlib
import http.server
import json
import re
import threading
import time
import urllib.parse

FOLDER = "application/vnd.google-apps.folder"


class FakeGoogle:
    def __init__(self):
        self.files = {}          # id -> {"meta": {...}, "content": bytes}
        self.sessions = {}
        self.tokens = set()
        self.expire_next = False  # the next authorised request gets a 401
        self.refreshes = 0
        self.scripts = {}
        self.calls = []
        self._n = 0
        self.lock = threading.Lock()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def new_id(self, prefix="f"):
        with self.lock:
            self._n += 1
            return f"{prefix}{self._n:04d}"

    # ---- helpers tests use -------------------------------------------------------
    def add_file(self, name, content=b"", mime="application/pdf", parent="root", **extra):
        file_id = self.new_id()
        meta = {"id": file_id, "name": name, "mimeType": mime, "parents": [parent], "trashed": False,
                "version": "1", "modifiedTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                "owners": [{"displayName": "Owner", "emailAddress": "owner@example.com"}], **extra}
        if mime != FOLDER:
            meta["md5Checksum"] = hashlib.md5(content).hexdigest()
            meta["size"] = str(len(content))
        self.files[file_id] = {"meta": meta, "content": content}
        return meta

    def set_content(self, file_id, content):
        entry = self.files[file_id]
        entry["content"] = content
        entry["meta"]["version"] = str(int(entry["meta"]["version"]) + 1)
        entry["meta"]["md5Checksum"] = hashlib.md5(content).hexdigest()

    def child(self, parent, name):
        return next((f for f in self.files.values() if parent in f["meta"]["parents"]
                     and f["meta"]["name"] == name and not f["meta"]["trashed"]), None)

    # ---- the server -------------------------------------------------------------------
    def _handler(self):
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body=None, headers=None):
                data = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if not isinstance(body, bytes):
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _authorised(self):
                auth = self.headers.get("Authorization", "")
                token = auth[7:] if auth.startswith("Bearer ") else ""
                if fake.expire_next and token in fake.tokens:
                    fake.expire_next = False
                    fake.tokens.discard(token)
                if token not in fake.tokens:
                    self._send(401, {"error": {"code": 401, "message": "Invalid Credentials"}})
                    return False
                return True

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_PUT(self):
                self._route("PUT")

            def do_PATCH(self):
                self._route("PATCH")

            def do_DELETE(self):
                self._route("DELETE")

            def _route(self, method):
                url = urllib.parse.urlparse(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                path = url.path
                body = self._body()
                fake.calls.append((method, path))
                if path == "/token":
                    return self._token(urllib.parse.parse_qs(body.decode()))
                if path.startswith("/macros/"):
                    return self._send(200, {"ok": True, "service": "aupedean-sign-service"})
                if path.startswith("/upload-session/"):
                    return self._finish_upload(path.rsplit("/", 1)[1], body)
                if not self._authorised():
                    return
                if path == "/userinfo":
                    return self._send(200, {"email": "owner@example.com"})
                if path.startswith("/script/v1/projects"):
                    return self._script(method, path, body)
                m = re.fullmatch(r"/upload/drive/v3/files(?:/([^/]+))?", path)
                if m:
                    return self._start_upload(method, m.group(1), body)
                m = re.fullmatch(r"/drive/v3/files(?:/([^/]+))?(/export)?", path)
                if m:
                    return self._drive(method, m.group(1), bool(m.group(2)), q, body)
                self._send(404, {"error": {"code": 404, "message": f"no route {method} {path}"}})

            def _token(self, form):
                grant = form.get("grant_type", [""])[0]
                if grant == "refresh_token":
                    fake.refreshes += 1
                token = f"tok{fake.new_id('')}"
                fake.tokens.add(token)
                self._send(200, {"access_token": token, "expires_in": 3600, "refresh_token": "refresh-1",
                                 "scope": "openid email https://www.googleapis.com/auth/drive "
                                          "https://www.googleapis.com/auth/script.projects "
                                          "https://www.googleapis.com/auth/script.deployments"})

            # -- Drive
            def _match(self, meta, query):
                for clause in query.split(" and "):
                    clause = clause.strip()
                    m = re.fullmatch(r"'(.+)' in parents", clause)
                    if m and m.group(1) not in meta["parents"]:
                        return False
                    m = re.fullmatch(r"name = '(.+)'", clause)
                    if m and meta["name"] != m.group(1).replace("\\'", "'"):
                        return False
                    m = re.fullmatch(r"name contains '(.+)'", clause)
                    if m and m.group(1).lower() not in meta["name"].lower():
                        return False
                    m = re.fullmatch(r"mimeType (!?=) '(.+)'", clause)
                    if m and ((meta["mimeType"] == m.group(2)) != (m.group(1) == "=")):
                        return False
                    if clause == "trashed = false" and meta["trashed"]:
                        return False
                    if clause == "sharedWithMe = true" and not meta.get("shared"):
                        return False
                    if clause == "starred = true" and not meta.get("starred"):
                        return False
                return True

            def _drive(self, method, file_id, export, q, body):
                if file_id is None:
                    if method == "GET":
                        found = [f["meta"] for f in fake.files.values() if self._match(f["meta"], q.get("q", ""))]
                        return self._send(200, {"files": found})
                    if method == "POST":
                        data = json.loads(body)
                        meta = fake.add_file(data["name"], mime=data.get("mimeType", "application/octet-stream"),
                                             parent=(data.get("parents") or ["root"])[0])
                        return self._send(200, meta)
                entry = fake.files.get(file_id)
                if entry is None:
                    return self._send(404, {"error": {"code": 404, "message": "File not found"}})
                if method == "DELETE":
                    del fake.files[file_id]
                    return self._send(204, b"")
                if method == "PATCH":
                    entry["meta"].update(json.loads(body))
                    return self._send(200, entry["meta"])
                if export or q.get("alt") == "media":
                    return self._send(200, entry["content"], {"Content-Type": "application/octet-stream"})
                return self._send(200, entry["meta"])

            def _start_upload(self, method, file_id, body):
                sid = fake.new_id("u")
                fake.sessions[sid] = {"file_id": file_id, "meta": json.loads(body or b"{}")}
                self._send(200, b"", {"Location": f"{fake.base}/upload-session/{sid}"})

            def _finish_upload(self, sid, content):
                session = fake.sessions.pop(sid)
                if session["file_id"]:
                    fake.set_content(session["file_id"], content)
                    return self._send(200, fake.files[session["file_id"]]["meta"])
                meta = session["meta"]
                created = fake.add_file(meta["name"], content, mime=self.headers.get("Content-Type") or
                                        "application/octet-stream", parent=(meta.get("parents") or ["root"])[0])
                self._send(200, created)

            # -- Apps Script
            def _script(self, method, path, body):
                parts = path.split("/")[4:]   # after /script/v1/projects
                if method == "POST" and not parts:
                    sid = fake.new_id("s")
                    fake.scripts[sid] = {"title": json.loads(body)["title"], "files": []}
                    return self._send(200, {"scriptId": sid})
                sid = parts[0]
                if parts[1:] == ["content"]:
                    fake.scripts[sid]["files"] = json.loads(body)["files"]
                    return self._send(200, {})
                if parts[1:] == ["versions"]:
                    return self._send(200, {"versionNumber": 1})
                if parts[1:] == ["deployments"]:
                    dep = fake.new_id("d")
                    return self._send(200, {"deploymentId": dep, "entryPoints": [
                        {"entryPointType": "WEB_APP", "webApp": {"url": f"{fake.base}/macros/s/{dep}/exec"}}]})
                self._send(404, {"error": {"code": 404, "message": "no such script call"}})

        return Handler
