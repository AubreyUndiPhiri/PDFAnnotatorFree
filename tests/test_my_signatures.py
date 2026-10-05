"""My Signatures (up to 5 kept to use again) and signing on a phone: a link
emailed to yourself, opened on the phone (headless Chrome here), and the
signature drawn there comes back to the Draw Signature dialog.

    python -m pytest tests/test_my_signatures.py
"""
import os
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QColor
from PySide6.QtWidgets import QApplication, QMessageBox

from fake_sign_service import CODE, FakeSignService
from pdfannotator import signature_library
from pdfannotator.cloud import sign_service, worker
from pdfannotator.dialogs import SignaturePadDialog
from test_signing_file import BROWSER


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(signature_library, "LIBRARY_DIR", tmp_path / "signatures")
    return tmp_path / "signatures"


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(sign_service, "SERVICE_FILE", tmp_path / "service.json")
    monkeypatch.setattr(sign_service, "SESSION_FILE", tmp_path / "session.bin")
    monkeypatch.setattr(sign_service, "BUILT_IN", tmp_path / "none.json")
    monkeypatch.setattr(sign_service, "SIGN_DIR", tmp_path)
    fake = FakeSignService()
    sign_service.save_service_url(fake.base)
    client = sign_service.SignService(fake.base)
    client.login_code("me@example.com")
    sign_service.save_session(*client.login_verify("me@example.com", CODE))
    yield fake
    worker.wait_all()
    fake.close()


def _png(color="black", w=60, h=20):
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(color))
    path = os.path.join(os.environ["AUPEDEAN_SIGNATURES_DIR"], f"tmp-{color}.png")
    img.save(path)
    with open(path, "rb") as f:
        return f.read()


def _draw(canvas):
    def event(kind, x, y):
        return QMouseEvent(kind, QPointF(x, y), QPointF(x, y), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)

    canvas.mousePressEvent(event(QEvent.MouseButtonPress, 60, 100))
    canvas.mouseMoveEvent(event(QEvent.MouseMove, 200, 110))
    canvas.mouseMoveEvent(event(QEvent.MouseMove, 300, 95))
    canvas.mouseReleaseEvent(None)


def test_library_keeps_at_most_five(library):
    assert signature_library.MAX_SIGNATURES == 5
    saved = [signature_library.add(_png()) for _ in range(5)]
    assert signature_library.signatures() == sorted(saved, key=lambda p: (p.stat().st_mtime, p.name))
    assert signature_library.is_full()
    with pytest.raises(signature_library.LibraryFull):
        signature_library.add(_png())
    signature_library.remove(saved[0])
    assert len(signature_library.signatures()) == 4 and not signature_library.is_full()
    signature_library.remove(__file__)                      # only files in the library are ever removed
    assert os.path.exists(__file__)


def test_draw_save_and_pick_again(app, library):
    dlg = SignaturePadDialog()
    assert dlg.phone_btn is None                            # no phone button without the signature service
    assert dlg.is_empty() and "none yet" in dlg.library_label.text()
    _draw(dlg.canvas)
    assert not dlg.is_empty()
    dlg.save_box.setChecked(True)
    dlg.accept()
    (saved,) = signature_library.signatures()
    img = QImage(str(saved))
    assert img.width() < 300 and img.height() < 60          # cut to the ink, not the whole pad

    dlg = SignaturePadDialog()
    assert len(dlg.tiles) == 1 and "1 of 5" in dlg.library_label.text()
    dlg.tiles[0].click()
    assert dlg.picked == saved and not dlg.is_empty()
    assert not dlg.save_box.isEnabled()                     # already saved: not saved twice
    dlg.accept()
    assert len(signature_library.signatures()) == 1
    assert QImage(dlg.save_to_temp_png()).size() == img.size()

    _draw(dlg.canvas)                                       # drawing over a picked one starts a new one
    assert dlg.picked is None and dlg.canvas.picture is None and dlg.save_box.isEnabled()


def test_full_library_wont_save_a_sixth(app, library, monkeypatch):
    for _ in range(5):
        signature_library.add(_png())
    dlg = SignaturePadDialog()
    assert len(dlg.tiles) == 5 and not dlg.save_box.isEnabled() and "full" in dlg.save_box.text()
    _draw(dlg.canvas)
    dlg.accept()
    assert len(signature_library.signatures()) == 5
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    dlg = SignaturePadDialog()
    dlg._remove(signature_library.signatures()[0])          # right-click > remove makes room
    assert len(dlg.tiles) == 4 and dlg.save_box.isEnabled()


def test_phone_signature_is_kept(app, library):
    dlg = SignaturePadDialog(sign_on_phone=lambda parent: _png("navy", 300, 90))
    assert dlg.phone_btn is not None
    dlg.phone_btn.click()
    assert dlg.canvas.picture.size().width() == 300 and dlg.save_box.isChecked()
    dlg.accept()
    (saved,) = signature_library.signatures()
    assert QImage(str(saved)).width() == 300                # kept at full size


@pytest.mark.skipif(BROWSER is None, reason="needs Chrome or Edge")
def test_sign_on_phone_end_to_end(app, library, service, tmp_path):
    from pdfannotator.cloud.ui import PhoneSignatureDialog

    dlg = PhoneSignatureDialog(sign_service.current_client(), "me@example.com")
    deadline = time.time() + 10
    while dlg.capture is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    assert dlg.capture is not None and "me@example.com" in dlg.status.text()
    link = service.phone_link_for("me@example.com")         # emailed to yourself
    assert "/m/" in link and "#k=" in link
    (stored,) = service.captures.values()

    # the phone: open the link, draw, send (the page's test mode does it)
    out = subprocess.run([BROWSER, "--headless=new", "--disable-gpu", "--no-first-run",
                          f"--user-data-dir={tmp_path / 'profile'}", "--virtual-time-budget=20000", "--dump-dom",
                          link + "&test=1"], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert 'id="selftest-done">sent<' in out.stdout, out.stdout[-2000:]
    assert stored["status"] == "signed" and "iVBOR" not in stored["data"]   # encrypted on the service

    deadline = time.time() + 15
    while dlg.png is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    assert dlg.png and dlg.png.startswith(b"\x89PNG") and not QImage.fromData(dlg.png).isNull()
    worker.wait_all()
    app.processEvents()
    assert not service.captures                              # collected, then deleted on the service
