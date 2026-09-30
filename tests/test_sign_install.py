"""The one-click installer of the signature service, against stand-ins for
Cloudflare's and Brevo's APIs: the right calls, in order, with the right
settings.

    python -m pytest tests/test_sign_install.py
"""
import http.server
import json
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import pytest

from pdfannotator.cloud import sign_service


class FakeApis:
    def __init__(self):
        self.calls, self.uploaded, self.sql = [], None, None
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _route(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                path = self.path
                fake.calls.append((method, path.split("?")[0]))
                if path.startswith("/brevo/"):
                    if self.headers.get("api-key") != "xkeysib-good":
                        return self._reply(401, {"message": "Key not found"})
                    if path == "/brevo/account":
                        return self._reply(200, {"email": "owner@example.com"})
                    return self._reply(200, {"senders": [{"email": "owner@example.com", "active": True}]})
                if self.headers.get("Authorization") != "Bearer cf-good":
                    return self._reply(403, {"success": False, "errors": [{"message": "Authentication error"}]})
                ok = lambda result: self._reply(200, {"success": True, "result": result})
                if path == "/cf/accounts":
                    return ok([{"id": "acc1", "name": "Mine"}])
                if path.startswith("/cf/accounts/acc1/d1/database?"):
                    return ok([])
                if path == "/cf/accounts/acc1/d1/database" and method == "POST":
                    return ok({"uuid": "db-uuid", "name": "aupedean-sign"})
                if path == "/cf/accounts/acc1/d1/database/db-uuid/query":
                    fake.sql = json.loads(body)["sql"]
                    return ok([{"success": True}])
                if path == "/cf/accounts/acc1/workers/scripts/aupedean-sign" and method == "PUT":
                    fake.uploaded = (self.headers.get("Content-Type"), body)
                    return ok({"id": "aupedean-sign"})
                if path == "/cf/accounts/acc1/workers/subdomain" and method == "GET":
                    return self._reply(404, {"success": False, "errors": [{"code": 10007, "message": "no subdomain"}]})
                if path == "/cf/accounts/acc1/workers/subdomain" and method == "PUT":
                    fake.subdomain = json.loads(body)["subdomain"]
                    return ok({"subdomain": fake.subdomain})
                if path == "/cf/accounts/acc1/workers/scripts/aupedean-sign/subdomain":
                    return ok({"enabled": True})
                self._reply(404, {"success": False, "errors": [{"message": f"no route {method} {path}"}]})

            do_GET = lambda self: self._route("GET")
            do_POST = lambda self: self._route("POST")
            do_PUT = lambda self: self._route("PUT")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def apis(tmp_path, monkeypatch):
    fake = FakeApis()
    monkeypatch.setattr(sign_service, "CLOUDFLARE_API", fake.base + "/cf")
    monkeypatch.setattr(sign_service, "BREVO_API", fake.base + "/brevo")
    monkeypatch.setattr(sign_service, "SIGN_DIR", tmp_path)
    monkeypatch.setattr(sign_service, "SERVICE_FILE", tmp_path / "service.json")
    monkeypatch.setattr(sign_service, "OWNER_FILE", tmp_path / "owner.bin")
    yield fake
    fake.server.shutdown()


def test_install(apis):
    steps = []
    url = sign_service.install("cf-good", "xkeysib-good", "Owner@Example.com", "owner@example.com", "@example.com",
                               progress=steps.append, wait_online=0)
    assert url == f"https://aupedean-sign.{apis.subdomain}.workers.dev" and sign_service.service_url() == url
    assert "CREATE TABLE IF NOT EXISTS requests" in apis.sql
    ctype, body = apis.uploaded
    assert ctype.startswith("multipart/form-data")
    meta = json.loads(body.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
    bindings = {b["name"]: b for b in meta["bindings"]}
    assert meta["main_module"] == "worker.js"
    assert bindings["DB"] == {"type": "d1", "name": "DB", "id": "db-uuid"}
    assert bindings["BREVO_KEY"]["type"] == "secret_text"          # the key is a secret, not plain text
    assert bindings["OWNER"]["text"] == "owner@example.com" and bindings["ALLOW"]["text"] == "@example.com"
    assert b"Only the person this was sent to" in body and b"__PAGE_HTML__" not in body   # page and pdf-lib inside
    assert ("POST", "/cf/accounts/acc1/workers/scripts/aupedean-sign/subdomain") in apis.calls
    assert steps[-1] == "Done."


def test_install_explains_bad_keys(apis):
    with pytest.raises(sign_service.SetupError, match="Brevo didn't accept"):
        sign_service.install("cf-good", "xkeysib-bad", "o@example.com", "o@example.com", wait_online=0)
    with pytest.raises(sign_service.SetupError, match="Workers Scripts > Edit"):
        sign_service.install("cf-bad", "xkeysib-good", "o@example.com", "owner@example.com", wait_online=0)
    with pytest.raises(sign_service.SetupError, match="isn't a verified sender"):
        sign_service.install("cf-good", "xkeysib-good", "o@example.com", "someone@else.com", wait_online=0)
