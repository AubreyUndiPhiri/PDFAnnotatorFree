"""A stand-in for the AUPedean Sign service (worker.js), speaking the same
API, so AUPedean and the signing page can be tested together without
Cloudflare. Codes are always 424242; sent emails are kept in .mails."""
import http.server
import json
import re
import secrets
import threading
import time
import urllib.parse
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
PAGE = APP / "pdfannotator" / "cloud" / "sign_service" / "page.html"
PDFLIB = APP / "assets" / "js" / "pdf-lib.min.js"
CODE = "424242"


class FakeSignService:
    def __init__(self, allow=("@example.com",)):
        self.allow = allow
        self.mails, self.sessions, self.signer_tokens, self.requests = [], {}, {}, {}
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def link_for(self, signer):
        mail = next(m for m in reversed(self.mails) if m["to"] == signer and "/s/" in m["html"])
        return re.search(r'href="([^"]+)"', mail["html"]).group(1)

    def _handler(self):
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body, ctype="application/json"):
                data = (json.dumps(body) if ctype == "application/json" else body).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _err(self, status, code):
                self._send(status, {"error": code})

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n).decode() if n else ""

            def _bearer(self):
                h = self.headers.get("Authorization", "")
                return h[7:] if h.startswith("Bearer ") else ""

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_PUT(self):
                self._route("PUT")

            def _route(self, method):
                url = urllib.parse.urlparse(self.path)
                path, q = url.path, {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                body = self._body()
                if path == "/api/health":
                    return self._send(200, {"ok": True, "service": "aupedean-sign", "version": 1})
                if re.fullmatch(r"/s/[A-Za-z0-9]{16,40}", path):
                    return self._send(200, PAGE.read_text(encoding="utf-8"), "text/html")
                if path == "/pdf-lib.js":
                    return self._send(200, PDFLIB.read_text(encoding="utf-8"), "text/javascript")
                if path == "/api/login/code":
                    email = json.loads(body)["email"].lower()
                    if not any(email.endswith(a) or email == a for a in fake.allow):
                        return self._err(403, "not-allowed")
                    fake.mails.append({"to": email, "subject": "code", "html": f"code {CODE}"})
                    return self._send(200, {"ok": True})
                if path == "/api/login/verify":
                    data = json.loads(body)
                    if data["code"] != CODE:
                        return self._err(400, "wrong-code")
                    token = secrets.token_urlsafe(24)
                    fake.sessions[token] = data["email"].lower()
                    return self._send(200, {"ok": True, "token": token, "email": data["email"].lower()})
                if path == "/api/me" or path.startswith("/api/requests"):
                    user = fake.sessions.get(self._bearer())
                    if not user:
                        return self._err(401, "signed-out")
                    return self._owner(method, path, q, body, user)
                m = re.fullmatch(r"/api/s/([A-Za-z0-9]{16,40})/(info|code|verify|open|doc|sign)", path)
                if m:
                    return self._signer(method, m.group(1), m.group(2), q, body)
                self._err(404, "not-found")

            def _owner(self, method, path, q, body, user):
                if path == "/api/me":
                    return self._send(200, {"ok": True, "email": user})
                if path == "/api/requests" and method == "POST":
                    data = json.loads(body)
                    rid = secrets.token_hex(12)
                    fake.requests[rid] = {**data, "id": rid, "owner": user, "status": "draft", "doc": {},
                                          "signer_email": data["signer_email"].lower(), "created": time.time() * 1000}
                    return self._send(200, {"ok": True, "id": rid, "part_chars": 1800000})
                if path == "/api/requests" and method == "GET":
                    rows = [dict((k, v) for k, v in r.items() if k not in ("doc", "result"))
                            for r in fake.requests.values() if r["owner"] == user
                            and (not q.get("status") or r["status"] == q["status"])]
                    return self._send(200, {"ok": True, "requests": rows})
                m = re.fullmatch(r"/api/requests/([0-9a-f]{24})/(doc|send|result|done|cancel)", path)
                r = fake.requests.get(m.group(1)) if m else None
                if r is None or r["owner"] != user:
                    return self._err(404, "not-found")
                action = m.group(2)
                if action == "doc":
                    r["doc"][int(q["part"])] = body
                elif action == "send":
                    key = json.loads(body)["key"]
                    link = f"{fake.base}/s/{r['id']}#k={key}"
                    fake.mails.append({"to": r["signer_email"], "subject": "Please sign", "html": f'<a href="{link}">'})
                    r["status"] = "waiting"
                elif action == "result":
                    return self._send(200, r["result"], "text/plain")
                elif action in ("done", "cancel"):
                    r["status"] = "completed" if action == "done" else "cancelled"
                    r.pop("result", None)
                    r["doc"] = {}
                return self._send(200, {"ok": True})

            def _signer(self, method, rid, action, q, body):
                r = fake.requests.get(rid)
                if r is None or r["status"] == "draft":
                    return self._err(404, "not-found")
                if action == "info":
                    return self._send(200, {"ok": True, "request": {
                        "id": rid, "title": r["title"], "message": r.get("message", ""), "status": r["status"],
                        "requester": r["owner"], "requester_name": r.get("owner_name", ""),
                        "signer_email": r["signer_email"][:2] + "***@" + r["signer_email"].split("@")[1],
                        "quick": bool(r.get("quick"))}})
                if r["status"] != "waiting":
                    return self._err(409, "already-signed")
                if action == "code":
                    fake.mails.append({"to": r["signer_email"], "subject": "code", "html": f"code {CODE}"})
                    return self._send(200, {"ok": True, "to": r["signer_email"]})
                if action in ("verify", "open"):
                    if action == "open" and not r.get("quick"):
                        return self._err(403, "verify-again")
                    if action == "verify" and json.loads(body).get("code") != CODE:
                        return self._err(400, "wrong-code")
                    token = secrets.token_urlsafe(24)
                    fake.signer_tokens[token] = rid
                    return self._send(200, {"ok": True, "token": token, "parts": len(r["doc"])})
                if fake.signer_tokens.get(self._bearer()) != rid:
                    return self._err(401, "verify-again")
                if action == "doc":
                    return self._send(200, r["doc"][int(q.get("part", 0))], "text/plain")
                if action == "sign":
                    r.update(status="signed", result=body, signed_at=int(time.time() * 1000),
                             signed_ip="203.0.113.9", signed_ua=self.headers.get("User-Agent", ""))
                    return self._send(200, {"ok": True, "signed_at": r["signed_at"]})
                self._err(404, "not-found")

        return Handler
