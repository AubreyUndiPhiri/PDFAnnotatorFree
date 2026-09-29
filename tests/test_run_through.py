"""Run-through: trigger every menu command and use every tool on a real
document, with file/print/message dialogs answered automatically, and fail
on any exception (including ones raised inside Qt slots).

    python -m pytest tests/test_run_through.py -v
"""
import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import fitz
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QDialog, QFileDialog, QInputDialog, QMessageBox,
)

from pdfannotator.tools import Tool

# Commands that would end the session or wait on real hardware / other apps
SKIP = {"Exit", "Close All", "Print..."}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def harness(app, tmp_path, monkeypatch):
    errors, messages = [], []

    def hook(etype, value, tb):
        errors.append("".join(traceback.format_exception(etype, value, tb)))

    monkeypatch.setattr(sys, "excepthook", hook)

    # --- files the dialogs "choose"
    src = tmp_path / "doc.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 90), f"Page {i + 1} heading text to search", fontsize=18)
        page.insert_textbox(fitz.Rect(72, 120, 520, 700), "Body text " * 120, fontsize=11)
    doc.save(src)
    extra = tmp_path / "extra.pdf"
    d2 = fitz.open()
    d2.new_page()
    d2.save(extra)
    image = tmp_path / "logo.png"
    fitz.open().new_page(width=80, height=40).get_pixmap().save(image)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    counter = iter(range(10_000))

    def save_name(*a, **k):
        return str(out_dir / f"saved_{next(counter)}.pdf"), "PDF Files (*.pdf)"

    def open_name(parent=None, caption="", *a, **k):
        if "image" in caption.lower() or "scan" in caption.lower():
            return str(image), ""
        return str(extra), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(save_name))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(open_name))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([str(extra)], "")))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(out_dir)))
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (2, True)))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Answer", True)))
    monkeypatch.setattr(QInputDialog, "getMultiLineText", staticmethod(lambda *a, **k: ("A comment", True)))
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor(30, 90, 200)))
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(lambda *a, k=kind, **kw: messages.append((k, a[2] if len(a) > 2 else ""))))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: True)
    from PySide6.QtGui import QDesktopServices
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda *a, **k: True))

    from pdfannotator.main_window import MainWindow

    win = MainWindow()
    win.resize(1300, 900)
    win.show()
    win.current_tab().load(str(src))
    app.processEvents()
    yield win, errors, messages
    win.close()


def _menu_actions(menu, path=""):
    for act in menu.actions():
        if act.isSeparator():
            continue
        label = f"{path} > {act.text().replace('&', '')}" if path else act.text().replace("&", "")
        if act.menu():
            yield from _menu_actions(act.menu(), label)
        else:
            yield label, act


def test_every_menu_command(app, harness):
    win, errors, messages = harness
    failures = []
    triggered = 0
    import shiboken6

    for label, act in list(_menu_actions(win.menuBar())):
        if not shiboken6.isValid(act):
            continue  # a dynamic menu (Window, Favorites) was rebuilt meanwhile
        if act.text().replace("&", "") in SKIP or not act.isEnabled():
            continue
        before = len(errors)
        try:
            tab = win.current_tab()
            if tab is not None and tab.document.page_count and not tab.selected:
                tab.select_all_on_page(0)  # give Cut/Copy/Delete something to act on
            act.trigger()
            triggered += 1
            for _ in range(3):
                app.processEvents()
        except Exception:
            errors.append(traceback.format_exc())
        if len(errors) > before:
            failures.append(f"{label}:\n{errors[-1]}")
        if win.isFullScreen():
            win.showNormal()
    print(f"{triggered} commands run; messages shown:")
    for kind, text in messages:
        print(f"  [{kind}] {text}")
    criticals = [m for m in messages if m[0] == "critical"]
    assert not failures, "\n\n".join(failures)
    assert not criticals, criticals


def test_every_tool_on_a_page(app, harness):
    win, errors, messages = harness
    tab = win.current_tab()
    pw = tab.page_widgets[0]
    pw.render()
    failures = []
    for tool in Tool:
        if tool == Tool.IMAGE_STAMP:
            continue
        before = len(errors)
        try:
            win.set_tool(tool)
            if tool == Tool.POLYGON:
                for pt in (QPoint(120, 150), QPoint(220, 160), QPoint(180, 260)):
                    QTest.mouseClick(pw, Qt.LeftButton, pos=pt)
                QTest.keyClick(pw, Qt.Key_Return)
            else:
                QTest.mousePress(pw, Qt.LeftButton, pos=QPoint(110, 140))
                QTest.mouseMove(pw, QPoint(260, 240))
                QTest.mouseRelease(pw, Qt.LeftButton, pos=QPoint(260, 240))
            if tab.text_edit is not None:
                QTest.keyClicks(tab.text_edit["editor"], "Run-through text")
                QTest.keyClick(tab.text_edit["editor"], Qt.Key_Escape)
            app.processEvents()
        except Exception:
            errors.append(traceback.format_exc())
        if len(errors) > before:
            failures.append(f"{tool.name}:\n{errors[-1]}")
        pw = tab.page_widgets[0] if tab.page_widgets else pw
    assert not failures, "\n\n".join(failures)
    # undo everything, then redo everything: must stay consistent
    while tab.document.can_undo():
        win.undo()
    while tab.document.can_redo():
        win.redo()
    assert not errors, errors
