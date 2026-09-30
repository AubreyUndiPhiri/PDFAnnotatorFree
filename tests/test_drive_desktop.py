"""Google Drive for desktop: finding its folders, and the Google Drive menu
opening and saving there (Google's app does the syncing).

    python -m pytest tests/test_drive_desktop.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from pdfannotator.cloud import drive_desktop


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake_drive(tmp_path, monkeypatch):
    """A stand-in for the "Google Drive" drive G: (streaming mode)."""
    g = tmp_path / "G"
    (g / "My Drive" / "Work").mkdir(parents=True)
    (g / "Shared drives").mkdir()
    monkeypatch.setattr(drive_desktop, "_drive_letters", lambda: [str(g)])
    monkeypatch.setattr(drive_desktop, "_volume_label", lambda root: "Google Drive" if root == str(g) else "")
    monkeypatch.setattr(drive_desktop, "_registry_mount_points", lambda: [])
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home") if p == "~" else p)
    drive_desktop.find_roots(refresh=True)
    yield g
    drive_desktop._cache = (0.0, None)


def test_finds_the_google_drive_drive(fake_drive):
    roots = dict(drive_desktop.find_roots(refresh=True))
    assert roots == {"My Drive": str(fake_drive / "My Drive"), "Shared drives": str(fake_drive / "Shared drives")}
    inside = fake_drive / "My Drive" / "Work" / "a.pdf"
    assert drive_desktop.containing_root(str(inside)) == ("My Drive", str(fake_drive / "My Drive"))
    assert drive_desktop.containing_root(str(fake_drive.parent / "elsewhere.pdf")) is None


def test_finds_mirror_mode_and_registry_mount(tmp_path, monkeypatch):
    mirror = tmp_path / "home" / "My Drive"
    mirror.mkdir(parents=True)
    mount = tmp_path / "H"
    (mount / "My Drive").mkdir(parents=True)
    monkeypatch.setattr(drive_desktop, "_drive_letters", lambda: [])
    monkeypatch.setattr(drive_desktop, "_registry_mount_points", lambda: [str(mount)])
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home") if p == "~" else p)
    labels = drive_desktop.find_roots(refresh=True)
    assert ("My Drive", str(mount / "My Drive")) in labels and ("My Drive", str(mirror)) in labels
    drive_desktop._cache = (0.0, None)


def test_nothing_found(tmp_path, monkeypatch):
    monkeypatch.setattr(drive_desktop, "_drive_letters", lambda: [])
    monkeypatch.setattr(drive_desktop, "_registry_mount_points", lambda: [])
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "nobody") if p == "~" else p)
    assert drive_desktop.find_roots(refresh=True) == []
    drive_desktop._cache = (0.0, None)


def test_menu_opens_and_saves_in_my_drive(app, fake_drive, tmp_path, monkeypatch):
    from pdfannotator.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    doc_in_drive = fake_drive / "My Drive" / "Work" / "Lease.pdf"
    d = fitz.open()
    d.new_page()
    d.save(doc_in_drive)
    asked = {}

    def open_names(parent, caption, directory, *a, **k):
        asked["open_dir"] = directory
        return [str(doc_in_drive)], ""

    def save_name(parent, caption, directory, *a, **k):
        asked["save_dir"] = directory
        return str(fake_drive / "My Drive" / "Copy.pdf"), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(open_names))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(save_name))
    win = MainWindow()
    win.show()
    file_menu_texts = [a.text() for a in win._file_menu.actions()]
    assert "Open from Google Drive..." in file_menu_texts   # also in the File menu, next to Open

    win.cloud.act_open.trigger()
    assert asked["open_dir"] == str(fake_drive / "My Drive")
    assert win.current_tab().document.path == str(doc_in_drive)
    win.update_sync_status()
    assert "In Google Drive (My Drive)" in win.status_sync_label.text()

    win.new_tab()   # a document that isn't in Drive yet: Save to Google Drive = Save As, in My Drive
    win.cloud.act_save.trigger()
    assert asked["save_dir"].startswith(str(fake_drive / "My Drive"))
    assert os.path.isfile(fake_drive / "My Drive" / "Copy.pdf")
    win.close()


def test_menu_explains_when_google_drive_is_missing(app, tmp_path, monkeypatch):
    from pdfannotator.cloud import ui
    from pdfannotator.main_window import MainWindow

    monkeypatch.setattr(drive_desktop, "_drive_letters", lambda: [])
    monkeypatch.setattr(drive_desktop, "_registry_mount_points", lambda: [])
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "nobody") if p == "~" else p)
    drive_desktop.find_roots(refresh=True)
    shown = []
    monkeypatch.setattr(ui.DriveDesktopDialog, "exec", lambda self: shown.append(self) or QDialog.Rejected)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames",
                        staticmethod(lambda *a, **k: pytest.fail("no file dialog without Google Drive")))
    win = MainWindow()
    win.cloud.act_open.trigger()
    assert len(shown) == 1
    drive_desktop._cache = (0.0, None)
    win.close()
