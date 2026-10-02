"""Signature requests by email, end to end: AUPedean encrypts and sends,
the signing page (in headless Chrome) verifies the email code, decrypts and
signs, and AUPedean collects the signature by itself and stamps the original.
The Cloudflare service is played by fake_sign_service.py (the real worker is
tested in test_sign_worker.py).

    python -m pytest tests/test_sign_service.py
"""
import os
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from fake_sign_service import CODE, FakeSignService
from pdfannotator.cloud import sign_service, signing, worker
from test_signing_file import BROWSER


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(sign_service, "SERVICE_FILE", tmp_path / "service.json")
    monkeypatch.setattr(sign_service, "SESSION_FILE", tmp_path / "session.bin")
    monkeypatch.setattr(sign_service, "BUILT_IN", tmp_path / "none.json")
    monkeypatch.setattr(sign_service, "SIGN_DIR", tmp_path)
    fake = FakeSignService()
    sign_service.save_service_url(fake.base)
    yield fake
    worker.wait_all()
    fake.close()


def test_encryption_round_trip():
    key = sign_service.new_key()
    text = sign_service.key_text(key)
    assert len(text) == 43 and sign_service.key_from_text(text) == key
    sealed = sign_service.seal(key, b"secret document")
    assert b"secret" not in sealed.encode() and sign_service.unseal(key, sealed) == b"secret document"
    with pytest.raises(Exception):
        sign_service.unseal(sign_service.new_key(), sealed)   # the wrong key can't open it


def test_sign_in_with_email_code(service):
    client = sign_service.SignService(service.base)
    with pytest.raises(sign_service.ServiceError, match="isn't allowed"):
        client.login_code("stranger@evil.org")
    client.login_code("me@example.com")
    with pytest.raises(sign_service.ServiceError, match="isn't right"):
        client.login_verify("me@example.com", "000000")
    email, token = client.login_verify("me@example.com", CODE)
    sign_service.save_session(email, token)
    assert sign_service.current_client().me() == "me@example.com"


@pytest.mark.skipif(BROWSER is None, reason="needs Chrome or Edge")
def test_request_sign_in_browser_and_collect_automatically(app, service, tmp_path, monkeypatch):
    from pdfannotator.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    client = sign_service.SignService(service.base)
    client.login_code("me@example.com")
    sign_service.save_session(*client.login_verify("me@example.com", CODE))

    original = tmp_path / "Lease.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 90), "Lease agreement", fontsize=20)
    doc.save(original)
    win = MainWindow()
    store = signing.RequestStore(tmp_path / "requests.json")
    win.cloud.signing.store = store
    win.cloud.service.store = store
    win.cloud.returns.store = store

    req = sign_service.send_request(sign_service.current_client(), original.read_bytes(), "Lease", str(original), 0,
                                    fitz.Rect(330, 700, 510, 750), "sam@gmail.com", "Sam", "Please sign",
                                    "Aubrey", "me@example.com")
    store.add(req)
    link = service.link_for("sam@gmail.com")
    assert "#k=" in link and service.requests[req.id]["doc"]   # emailed; stored encrypted
    stored = "".join(service.requests[req.id]["doc"].values())
    assert "Lease agreement" not in stored and "JVBER" not in stored   # not readable on the service

    # the signer: open the emailed link, enter the emailed code, sign (the page's test mode does the clicks)
    out = subprocess.run([BROWSER, "--headless=new", "--disable-gpu", "--no-first-run",
                          f"--user-data-dir={tmp_path / 'profile'}", "--virtual-time-budget=30000", "--dump-dom",
                          link + "&test=" + CODE], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert 'id="selftest-done">signed<' in out.stdout, out.stdout[-2000:]
    assert service.requests[req.id]["status"] == "signed"

    # AUPedean notices by itself, stamps the original, and tells the service to delete its copy
    win.cloud.service.check()
    deadline = time.time() + 15
    while store.get(req.id).status != signing.APPLIED and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    assert store.get(req.id).status == signing.APPLIED
    text = fitz.open(original)[0].get_text()
    assert "Signed by Test Signer (sam@gmail.com)" in text and "email verified by AUPedean Sign" in text
    deadline = time.time() + 10
    while service.requests[req.id]["status"] != "completed" and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    assert service.requests[req.id]["status"] == "completed" and "result" not in service.requests[req.id]
    win.close()


def test_quick_request_opens_from_the_link_alone(app, service, tmp_path):
    if BROWSER is None:
        pytest.skip("needs Chrome or Edge")
    client = sign_service.SignService(service.base)
    client.login_code("me@example.com")
    sign_service.save_session(*client.login_verify("me@example.com", CODE))
    doc = fitz.open()
    doc.new_page().insert_text((72, 700), "Client signature:", fontsize=12)
    page_index, rect = signing.find_signature_spot(doc)
    assert page_index == 0 and rect.x0 > 150 and 650 < rect.y1 < 720     # beside the label
    req = sign_service.send_request(sign_service.current_client(), doc.tobytes(), "Quote", str(tmp_path / "q.pdf"),
                                    page_index, rect, "client@gmail.com", quick=True)
    out = subprocess.run([BROWSER, "--headless=new", "--disable-gpu", "--no-first-run",
                          f"--user-data-dir={tmp_path / 'profile'}", "--virtual-time-budget=30000", "--dump-dom",
                          service.link_for("client@gmail.com") + "&test=auto"],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert 'id="selftest-done">signed<' in out.stdout, out.stdout[-2000:]
    assert not [m for m in service.mails if m["subject"] == "code" and m["to"] == "client@gmail.com"]   # no code
    info = next(r for r in client.requests() if r["id"] == req.id)
    assert info["status"] == "signed"
    _png, result = sign_service.collect(sign_service.current_client(), req, info)
    assert result["verified_by"] == "email link"


def test_signing_page_refuses_a_wrong_code(app, service, tmp_path):
    if BROWSER is None:
        pytest.skip("needs Chrome or Edge")
    client = sign_service.SignService(service.base)
    client.login_code("me@example.com")
    sign_service.save_session(*client.login_verify("me@example.com", CODE))
    doc = fitz.open()
    doc.new_page()
    req = sign_service.send_request(sign_service.current_client(), doc.tobytes(), "Doc", str(tmp_path / "d.pdf"), 0,
                                    fitz.Rect(300, 700, 480, 750), "sam@gmail.com")
    out = subprocess.run([BROWSER, "--headless=new", "--disable-gpu", "--no-first-run",
                          f"--user-data-dir={tmp_path / 'profile'}", "--virtual-time-budget=20000", "--dump-dom",
                          service.link_for("sam@gmail.com") + "&test=999999"],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert 'id="selftest-done"' not in out.stdout
    assert 'id="verifyError">That code is not right' in out.stdout   # shown to the signer; nothing opened
    assert service.requests[req.id]["status"] == "waiting"
