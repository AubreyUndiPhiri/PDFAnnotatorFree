"""Signing files: make one, sign it in a real (headless) browser, bring the
signed PDF back and see the signature land in the original.

    python -m pytest tests/test_signing_file.py
"""
import base64
import html
import os
import re
import shutil
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from pdfannotator.cloud import signing, signing_file

BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
BROWSER = next((b for b in BROWSERS if os.path.isfile(b)), None) or shutil.which("chromium") or shutil.which("chrome")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _contract(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 90), "Tenancy agreement", fontsize=20)
    page.insert_textbox(fitz.Rect(72, 120, 520, 600), "The tenant agrees to the terms. " * 40, fontsize=10)
    doc.save(path)
    return path


def sign_in_browser(html_path, out_dir):
    """Open the signing file with #selftest: the page signs as "Test Signer"
    and prints the signed PDF, which is returned as bytes."""
    url = "file:///" + str(html_path).replace("\\", "/").replace(" ", "%20") + "#selftest"
    result = subprocess.run([BROWSER, "--headless=new", "--disable-gpu", "--no-first-run",
                             f"--user-data-dir={out_dir / 'browser-profile'}", "--virtual-time-budget=20000",
                             "--dump-dom", url], capture_output=True, text=True, encoding="utf-8", timeout=120)
    m = re.search(r'<pre id="selftest">(.*?)</pre>', result.stdout, re.S)
    assert m, "the signing page didn't produce a signed PDF:\n" + result.stderr[-2000:]
    return base64.b64decode(html.unescape(m.group(1)))


def test_page_is_safe_for_awkward_titles(tmp_path):
    original = _contract(str(tmp_path / "c.pdf"))
    page_html, req = signing_file.make_signing_file(open(original, "rb").read(), "</script><b>x", original, 0,
                                                    fitz.Rect(300, 700, 480, 750))
    assert "</script><b>x" not in page_html   # can't end a script block or inject markup
    assert req.kind == "file" and req.token and len(req.id) >= 12
    assert page_html.count("</script>") == 3  # data, pdf-lib, the page's own script


@pytest.mark.skipif(BROWSER is None, reason="needs Chrome or Edge")
def test_sign_in_browser_and_bring_back(app, tmp_path, monkeypatch):
    from pdfannotator.main_window import MainWindow

    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    original = _contract(str(tmp_path / "Tenancy.pdf"))
    win = MainWindow()
    win.show()
    store = signing.RequestStore(tmp_path / "requests.json")
    win.cloud.signing.store = store
    win.cloud.returns.store = store
    win.cloud.returns.folders = [str(tmp_path / "Downloads")]
    os.makedirs(tmp_path / "Downloads")

    page_html, req = signing_file.make_signing_file(open(original, "rb").read(), "Tenancy agreement", original, 0,
                                                    fitz.Rect(330, 700, 510, 750), "Sam", "", "Please sign",
                                                    "Aubrey", "aubrey@example.com")
    req.file_path = signing_file.save_signing_file(page_html, "Tenancy agreement", tmp_path / "files")
    store.add(req)
    win.cloud.returns.update()
    signed = sign_in_browser(req.file_path, tmp_path)
    receipt = signing_file.read_receipt(signed)
    assert receipt["name"] == "Test Signer" and receipt["request_id"] == req.id

    # the signed copy arrives in Downloads (from WhatsApp, email...): picked up automatically
    returned = tmp_path / "Downloads" / "Tenancy agreement - signed by Test Signer.pdf"
    returned.write_bytes(signed)
    win.cloud.returns.scan()
    deadline = time.time() + 10
    while store.get(req.id).status != signing.APPLIED and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    assert store.get(req.id).status == signing.APPLIED
    stamped = fitz.open(original)[0].get_text()   # the ORIGINAL got the signature, and was saved
    assert "Signed by Test Signer (test@example.com)" in stamped and "AUPedean signing file" in stamped
    assert win.tab_for_path(original) is not None and any("signed" in s for s in shown)

    # opening the signed copy again doesn't sign twice; it shows the original
    count = sum(1 for line in fitz.open(original)[0].get_text().splitlines() if line.startswith("Signed by"))
    win.open_files_as_tabs([str(returned)])
    assert sum(1 for line in fitz.open(original)[0].get_text().splitlines() if line.startswith("Signed by")) == count
    win.close()


def test_forged_or_unknown_copies_just_open(app, tmp_path, monkeypatch):
    from pdfannotator.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    original = _contract(str(tmp_path / "Doc.pdf"))
    win = MainWindow()
    store = signing.RequestStore(tmp_path / "requests.json")
    win.cloud.signing.store = store
    win.cloud.returns.store = store
    _html, req = signing_file.make_signing_file(open(original, "rb").read(), "Doc", original, 0,
                                                fitz.Rect(330, 700, 510, 750))
    store.add(req)
    forged = fitz.open(original)
    forged.embfile_add(signing_file.RECEIPT_NAME, (
        '{"format": "aupedean-signature/1", "request_id": "%s", "token": "guessed", "name": "Mallory", '
        '"signature_png": "iVBORw0KGgo="}' % req.id).encode())
    forged_path = str(tmp_path / "forged.pdf")
    forged.save(forged_path)
    win.open_files_as_tabs([forged_path])
    assert store.get(req.id).status == signing.WAITING          # not accepted
    assert win.current_tab().document.path == forged_path       # just opened like any PDF
    assert "Mallory" not in fitz.open(original)[0].get_text()
    win.close()
