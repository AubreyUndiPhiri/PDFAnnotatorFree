"""Google Drive sync and signing links, against a fake Google (fake_google.py).

    python -m pytest tests/test_cloud.py
"""
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QApplication

from fake_google import FakeGoogle
from pdfannotator.cloud import drive as drive_mod, google_auth, signing, sync, worker


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def google(tmp_path, monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(google_auth, "TOKEN_URL", fake.base + "/token")
    monkeypatch.setattr(google_auth, "USERINFO_URL", fake.base + "/userinfo")
    monkeypatch.setattr(google_auth, "REVOKE_URL", fake.base + "/revoke")
    monkeypatch.setattr(google_auth, "CLIENT_FILE", tmp_path / "client.bin")
    monkeypatch.setattr(google_auth, "TOKEN_FILE", tmp_path / "token.bin")
    monkeypatch.setattr(drive_mod, "API", fake.base + "/drive/v3")
    monkeypatch.setattr(drive_mod, "UPLOAD_API", fake.base + "/upload/drive/v3")
    monkeypatch.setattr(signing, "SCRIPT_API", fake.base + "/script/v1")
    monkeypatch.setattr(signing, "CONFIG_FILE", tmp_path / "signing.bin")
    monkeypatch.setattr(sync, "CACHE_DIR", tmp_path / "cache")
    yield fake
    worker.wait_all()
    fake.close()


def _browser_that_consents(url):
    """Plays the user's browser: Google redirects back with a code."""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    back = q["redirect_uri"][0] + "?" + urllib.parse.urlencode({"code": "the-code", "state": q["state"][0]})
    threading.Thread(target=lambda: urllib.request.urlopen(back, timeout=10).read(), daemon=True).start()


def _signed_in():
    client = {"client_id": "123.apps.googleusercontent.com", "client_secret": "s"}
    google_auth.save_client(client)
    google_auth.sign_in(client, _browser_that_consents, timeout=20)
    return google_auth.Session()


def _wait(app, condition, seconds=15):
    deadline = time.time() + seconds
    while not condition() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return condition()


def _pdf(text="Hello"):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def test_client_file_and_encrypted_storage(tmp_path):
    good = json.dumps({"installed": {"client_id": "abc.apps.googleusercontent.com", "client_secret": "xyz"}})
    assert google_auth.parse_client_json(good) == {"client_id": "abc.apps.googleusercontent.com",
                                                   "client_secret": "xyz"}
    with pytest.raises(google_auth.AuthError, match="Desktop app"):
        google_auth.parse_client_json(json.dumps({"web": {"client_id": "abc.apps.googleusercontent.com"}}))
    path = tmp_path / "secret.bin"
    google_auth.save_secret(path, {"refresh_token": "very-secret"})
    assert b"very-secret" not in path.read_bytes() or os.name != "nt"   # encrypted on Windows
    assert google_auth.load_secret(path) == {"refresh_token": "very-secret"}


def test_sign_in_and_token_refresh(google):
    session = _signed_in()
    assert session.email == "owner@example.com"
    assert session.has_scope("https://www.googleapis.com/auth/drive")
    google.expire_next = True     # Google rejects the next token: the session refreshes and retries
    files = drive_mod.DriveClient(session).children("root")
    assert files == [] and google.refreshes == 1


def test_drive_browse_upload_download(google, tmp_path):
    session = _signed_in()
    d = drive_mod.DriveClient(session)
    folder = d.ensure_folder("Work")
    assert d.ensure_folder("Work")["id"] == folder["id"]   # found, not duplicated
    google.add_file("Report.pdf", _pdf(), parent=folder["id"])
    google.add_file("Notes", b"PK docx bytes", mime=drive_mod.GOOGLE_DOC, parent=folder["id"])
    names = sorted(f["name"] for f in d.children(folder["id"]))
    assert names == ["Notes", "Report.pdf"]
    notes = next(f for f in d.children(folder["id"]) if f["name"] == "Notes")
    assert drive_mod.local_name(notes) == "Notes.docx" and drive_mod.is_openable(notes)
    local = tmp_path / "up.pdf"
    local.write_bytes(_pdf("uploaded"))
    created = d.create("Up.pdf", folder["id"], path=str(local))
    assert google.files[created["id"]]["content"] == local.read_bytes()
    d.download(created, str(tmp_path / "down.pdf"))
    assert (tmp_path / "down.pdf").read_bytes() == local.read_bytes()
    assert [f["name"] for f in d.search("up")] == ["Up.pdf"]


def test_background_sync_uploads_saves_and_pulls_remote_changes(app, google, tmp_path):
    session = _signed_in()
    remote = google.add_file("Contract.pdf", _pdf("v1"))
    registry = sync.Registry(tmp_path / "sync.json")
    manager = sync.SyncManager(lambda: session, parent=None, registry=registry)
    manager.DEBOUNCE_MS = 50
    opened = []
    manager.open_remote(remote, opened.append, pytest.fail)
    assert _wait(app, lambda: opened)
    path = opened[0]
    assert open(path, "rb").read() == google.files[remote["id"]]["content"]

    # a save here is uploaded in the background as a new revision
    with open(path, "wb") as f:
        f.write(_pdf("edited here"))
    assert _wait(app, lambda: google.files[remote["id"]]["meta"]["version"] == "2")
    assert google.files[remote["id"]]["content"] == open(path, "rb").read()
    assert _wait(app, lambda: manager.entry(path).state == sync.SYNCED)

    # someone edits it on Drive: the next check downloads it and asks the tab to reload
    reloaded = []
    manager.remote_updated.connect(reloaded.append)
    google.set_content(remote["id"], _pdf("edited on Drive"))
    manager.poll([path])
    assert _wait(app, lambda: reloaded == [path])
    assert open(path, "rb").read() == google.files[remote["id"]]["content"]

    # both changed: a conflict, resolved by keeping both
    conflicts = []
    manager.conflict.connect(conflicts.append)
    google.set_content(remote["id"], _pdf("Drive again"))
    with open(path, "wb") as f:
        f.write(_pdf("mine again"))
    assert _wait(app, lambda: conflicts == [path])
    manager.resolve_conflict(path, "both")
    assert _wait(app, lambda: manager.entry(path).state == sync.SYNCED)
    assert any("my copy" in f["meta"]["name"] for f in google.files.values())
    assert open(path, "rb").read() == google.files[remote["id"]]["content"]


def test_offline_uploads_wait_and_retry(app, google, tmp_path):
    session = _signed_in()
    remote = google.add_file("Draft.pdf", _pdf("v1"))
    manager = sync.SyncManager(lambda: session, registry=sync.Registry(tmp_path / "sync.json"))
    manager.DEBOUNCE_MS = 50
    opened = []
    manager.open_remote(remote, opened.append, pytest.fail)
    assert _wait(app, lambda: opened)
    path = opened[0]
    real_upload = drive_mod.DriveClient.update_content

    def offline(*a, **k):
        raise google_auth.Offline("no network")

    drive_mod.DriveClient.update_content = offline
    try:
        with open(path, "wb") as f:
            f.write(_pdf("written offline"))
        assert _wait(app, lambda: manager.entry(path).state == sync.OFFLINE)
        assert manager.entry(path).pending
    finally:
        drive_mod.DriveClient.update_content = real_upload
    manager.poll([path])          # back online: the waiting upload goes
    assert _wait(app, lambda: manager.entry(path).state == sync.SYNCED and not manager.entry(path).pending)
    assert google.files[remote["id"]]["content"] == open(path, "rb").read()


def test_signing_setup_request_and_signature_round_trip(app, google, tmp_path):
    session = _signed_in()
    config = signing.setup_service(session)
    service, page = sorted(google.scripts.values(), key=lambda s: s["title"], reverse=True)
    assert service["title"] == "Aupedean Sign - service" and page["title"] == "Aupedean Sign - page"
    for script in (service, page):
        for f in script["files"]:
            assert "{{" not in f["source"], f"placeholder left in {f['name']}"
    service_manifest = json.loads(next(f["source"] for f in service["files"] if f["name"] == "appsscript"))
    page_manifest = json.loads(next(f["source"] for f in page["files"] if f["name"] == "appsscript"))
    assert service_manifest["webapp"] == {"executeAs": "USER_DEPLOYING", "access": "ANYONE_ANONYMOUS"}
    assert page_manifest["webapp"] == {"executeAs": "USER_ACCESSING", "access": "ANYONE"}  # Google sign-in required
    assert config["secret"] in next(f["source"] for f in page["files"] if f["name"] == "Code")
    assert signing.service_approved(config)

    doc_path = tmp_path / "contract.pdf"
    doc_path.write_bytes(_pdf("Please sign below"))
    rect = fitz.Rect(300, 700, 480, 750)
    req = signing.create_request(session, config, doc_path.read_bytes(), "Contract", str(doc_path), 0, rect,
                                 "Signer@Example.com", "Sam Signer", "Thanks!")
    folder = google.files[req.folder_id]
    assert folder["meta"]["parents"] == [config["root_folder_id"]]
    stored = json.loads(google.child(req.folder_id, "request.json")["content"])
    assert stored["token"] == req.token and stored["signer_email"] == "signer@example.com"
    assert google.child(req.folder_id, "page-1.jpg") is not None
    link = urllib.parse.urlparse(signing.link(config, req))
    assert urllib.parse.parse_qs(link.query) == {"r": [req.id], "t": [req.token]}

    # the signer signs on the web page: the service writes these two files
    sig = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 60), True)
    sig.set_rect(sig.irect, (20, 20, 120, 255))
    google.add_file("signature.png", sig.tobytes("png"), mime="image/png", parent=req.folder_id)
    google.add_file("result.json", json.dumps({"name": "Sam Signer", "email": "signer@example.com",
                                               "signed_at": "2026-09-30T10:15:00.000Z"}).encode(),
                    mime="application/json", parent=req.folder_id)

    store = signing.RequestStore(tmp_path / "requests.json")
    store.add(req)
    manager = signing.SigningManager(lambda: session, store=store)
    got = []
    manager.signed.connect(lambda r, png, result: got.append((r, png, result)))
    manager.check()
    assert _wait(app, lambda: got)
    r, png, result = got[0]
    doc = fitz.open(doc_path)
    signing.stamp_signature(doc, r, png, result)
    page0 = doc[0]
    assert page0.get_images(), "the signature picture is on the page"
    assert "Signed by Sam Signer (signer@example.com)" in page0.get_text()
    manager.mark_applied(r)
    assert store.get(req.id).status == signing.APPLIED
    assert _wait(app, lambda: google.child(req.folder_id, "page-1.jpg") is None)  # tidied, evidence kept
    assert google.child(req.folder_id, "result.json") is not None


def test_cancelled_request(app, google, tmp_path):
    session = _signed_in()
    config = signing.setup_service(session)
    req = signing.create_request(session, config, _pdf(), "Doc", str(tmp_path / "d.pdf"), 0,
                                 fitz.Rect(10, 10, 100, 40), "x@example.com")
    signing.cancel_request(session, req)
    assert signing.check_request(session, req)[0] == signing.CANCELLED


def test_app_opens_drive_pdf_syncs_saves_and_applies_signature(app, google, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    session = _signed_in()
    from pdfannotator.main_window import MainWindow

    win = MainWindow()
    win.show()
    ctrl = win.cloud
    ctrl.sync.registry = sync.Registry(tmp_path / "sync.json")
    ctrl.sync.DEBOUNCE_MS = 50
    assert ctrl.session() is not None and ctrl.session().email == "owner@example.com"
    remote = google.add_file("Lease.pdf", _pdf("Lease v1"))
    ctrl.sync.open_remote(remote, lambda p: win.open_files_as_tabs([p]), pytest.fail)
    assert _wait(app, lambda: win.current_tab() is not None and win.current_tab().document.path
                 and win.current_tab().document.path.endswith("Lease.pdf"))
    tab = win.current_tab()
    win.update_sync_status()
    assert "Synced with Google Drive" in win.status_sync_label.text()

    # edit and save in the app: uploaded in the background
    tab.document.page(0).insert_text((72, 120), "edited in Aupedean")
    tab.document.snapshot()
    tab.save()
    assert _wait(app, lambda: google.files[remote["id"]]["meta"]["version"] == "2")
    assert b"edited in Aupedean" in fitz.open(stream=google.files[remote["id"]]["content"]).tobytes() or \
        "edited in Aupedean" in fitz.open(stream=google.files[remote["id"]]["content"], filetype="pdf")[0].get_text()

    # changed on Drive: the open tab reloads it
    # the upload of that save (and the watcher's follow-up) must be done first: checks skip busy files
    assert _wait(app, lambda: not ctrl.sync._busy and ctrl.sync.entry(tab.document.path).state == sync.SYNCED)
    google.set_content(remote["id"], _pdf("Lease v3 from Drive"))
    ctrl.sync.poll([tab.document.path])
    assert _wait(app, lambda: "Lease v3 from Drive" in tab.document.page(0).get_text())

    # a signature comes back for this open PDF: stamped in and saved (and so synced)
    req = signing.SignRequest(id="abcdefgh12", token="t", title="Lease", signer_email="s@example.com",
                              signer_name="", message="", page=0, rect=[300, 700, 480, 750],
                              field={"page": 0, "x": 0.5, "y": 0.8, "w": 0.3, "h": 0.06}, folder_id="",
                              doc_path=tab.document.path)
    ctrl.signing.store = signing.RequestStore(tmp_path / "req.json")
    ctrl.signing.store.add(req)
    sig = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 120, 40), True)
    ctrl._apply_signature(req, sig.tobytes("png"), {"name": "Sam", "email": "s@example.com",
                                                    "signed_at": "2026-09-30T10:00:00Z"})
    assert "Signed by Sam (s@example.com)" in tab.document.page(0).get_text()
    assert not tab.document.dirty and ctrl.signing.store.get(req.id).status == signing.APPLIED
    assert _wait(app, lambda: google.files[remote["id"]]["meta"]["version"] == "4")  # the signed PDF uploaded
    tab.document.doc and win.close()
