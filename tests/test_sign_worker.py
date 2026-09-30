"""The Aupedean Sign service (Cloudflare Worker), run for real in a headless
browser: SQLite (sql.js) plays Cloudflare's D1 database and a stub keeps the
emails Brevo would send. worker_harness.html holds the scenario.

    python -m pytest tests/test_sign_worker.py
"""
import functools
import html
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import pytest

from pdfannotator.cloud import sign_service

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
SQLJS = "https://cdn.jsdelivr.net/npm/sql.js@1.10.3/dist/"
BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
BROWSER = next((b for b in BROWSERS if os.path.isfile(b)), None) or shutil.which("chromium") or shutil.which("chrome")


def _sqljs():
    CACHE.mkdir(exist_ok=True)
    for name in ("sql-wasm.js", "sql-wasm.wasm"):
        target = CACHE / name
        if not target.exists():
            try:
                urllib.request.urlretrieve(SQLJS + name, target)
            except OSError:
                pytest.skip("sql.js couldn't be downloaded (offline)")
    return CACHE


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.mark.skipif(BROWSER is None, reason="needs Chrome or Edge")
def test_worker_end_to_end(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    cache = _sqljs()
    for name in ("sql-wasm.js", "sql-wasm.wasm"):
        shutil.copy(cache / name, site / name)
    shutil.copy(HERE / "worker_harness.html", site / "harness.html")
    (site / "worker.js").write_text(sign_service.worker_source(), encoding="utf-8")
    (site / "schema.sql").write_text(sign_service.SCHEMA, encoding="utf-8")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/harness.html"
        out = subprocess.run([BROWSER, "--headless=new", "--disable-gpu", "--no-first-run",
                              f"--user-data-dir={tmp_path / 'profile'}", "--virtual-time-budget=30000",
                              "--dump-dom", url], capture_output=True, text=True, encoding="utf-8", timeout=120)
    finally:
        server.shutdown()
    m = re.search(r"RESULT (\[.*?\])</pre>", out.stdout, re.S)
    assert m, "the harness didn't finish:\n" + out.stdout[-1500:] + out.stderr[-1500:]
    checks = json.loads(html.unescape(m.group(1)))
    failed = [c for c in checks if not c["ok"]]
    assert not failed, json.dumps(failed, indent=1)
    assert len(checks) >= 30
