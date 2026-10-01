"""Gestures, pen tablets, right-click menus and Edit Photo, driven the way
Qt delivers them (synthetic wheel / tablet events, menus captured)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import numpy as np
import pymupdf as fitz
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QInputDevice, QPointingDevice, QTabletEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QDialog

from pdfannotator import image_editor, pdf_ops
from pdfannotator.tools import Tool


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from pdfannotator.main_window import MainWindow

    arr = np.full((80, 120, 4), 255, np.uint8)
    arr[20:60, 30:90] = (200, 30, 30, 255)
    png = image_editor.rgba_to_png(arr)
    doc = fitz.open()
    page = doc.new_page()
    pdf_ops.add_image_stamp(page, fitz.Rect(100, 100, 340, 260), png)
    path = tmp_path / "pic.pdf"
    doc.save(path)
    w = MainWindow()
    w.resize(1100, 800)
    w.show()
    w.open_files_as_tabs([str(path)])
    tab = w.current_tab()
    tab.set_zoom(1.0)
    tab._apply_pinch()
    for _ in range(5):
        app.processEvents()
    yield w, tab
    monkeypatch.setattr(w, "_confirm_close_tab", lambda _tab: True)   # no "save changes?" prompt
    w.close()


def _menu_texts(monkeypatch, *owners):
    seen = []
    for owner in owners:
        monkeypatch.setattr(owner, "exec_menu", lambda menu, _pos: seen.append([x.text() for x in menu.actions()]))
    return seen


def _image_point(tab):
    widget = tab.page_widgets[0]
    widget.render()
    annot = next(widget.page().annots())
    rect = tab.annot_rect_px(widget, annot)
    return widget, annot, rect.center()


def test_ctrl_wheel_and_trackpad_pinch_zoom_smoothly(window, app):
    _w, tab = window
    viewport = tab.scroll_area.viewport()
    start = tab.zoom
    pos = QPointF(viewport.width() / 2, viewport.height() / 2)
    event = QWheelEvent(pos, viewport.mapToGlobal(pos), QPoint(), QPoint(0, 120), Qt.NoButton,
                        Qt.ControlModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(viewport, event)
    tab._apply_pinch()
    assert tab.zoom == pytest.approx(start * 1.0015 ** 120, rel=0.01)
    small = QWheelEvent(pos, viewport.mapToGlobal(pos), QPoint(0, -8), QPoint(0, -8), Qt.NoButton,
                        Qt.ControlModifier, Qt.ScrollUpdate, False)   # a trackpad pinch: tiny steps
    before = tab.zoom
    QApplication.sendEvent(viewport, small)
    tab._apply_pinch()
    assert tab.zoom < before and tab.zoom > before * 0.97


def test_right_click_on_a_picture_offers_edit_photo(window, monkeypatch):
    w, tab = window
    seen = _menu_texts(monkeypatch, tab)
    widget, annot, center = _image_point(tab)
    tab.show_page_menu(widget, center, QPoint())
    assert "Edit Photo..." in seen[-1] and "Delete" in seen[-1] and "Copy" in seen[-1]
    assert tab.selected and tab.selected[0][1].xref == annot.xref       # right-click selects it
    tab.show_page_menu(widget, QPoint(20, widget.height() - 20), QPoint())   # empty spot
    assert "Edit Photo..." not in seen[-1] and "Add Note Here..." in seen[-1]


def test_edit_photo_removes_the_background_in_place(window, monkeypatch):
    w, tab = window
    widget, annot, _c = _image_point(tab)
    old_rect = fitz.Rect(annot.rect)

    def run(dlg):
        dlg._remove_background()
        return QDialog.Accepted

    monkeypatch.setattr(image_editor.ImageEditorDialog, "exec", run)
    tab.edit_photo(widget, annot=annot)
    new = next(widget.page().annots())
    result = image_editor.image_to_rgba(pdf_ops.image_stamp_bytes(new))
    assert result[0, 0, 3] == 0 and result[40, 60, 3] == 255       # white gone, subject kept
    assert abs(new.rect.x0 - old_rect.x0) < 0.5 and abs(new.rect.width - old_rect.width) < 0.5


def test_pen_eraser_end_erases_then_restores_the_tool(window, app):
    w, tab = window
    widget = tab.page_widgets[0]
    w.set_tool(Tool.INK)
    eraser = QPointingDevice("test eraser", 99, QInputDevice.DeviceType.Stylus, QPointingDevice.PointerType.Eraser,
                             QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 3)

    def send(kind, x, y, button, buttons):
        pos = QPointF(x, y)
        ev = QTabletEvent(kind, eraser, pos, widget.mapToGlobal(pos), 0.6, 0, 0, 0, 0, 0,
                          Qt.NoModifier, button, buttons)
        QApplication.sendEvent(widget, ev)

    send(QEvent.TabletPress, 30, 30, Qt.LeftButton, Qt.LeftButton)
    assert w.current_tool == Tool.ERASER
    send(QEvent.TabletMove, 60, 60, Qt.NoButton, Qt.LeftButton)
    send(QEvent.TabletRelease, 60, 60, Qt.LeftButton, Qt.NoButton)
    assert w.current_tool == Tool.INK


def test_pen_barrel_right_click_opens_the_page_menu(window, monkeypatch):
    w, tab = window
    seen = _menu_texts(monkeypatch, tab)
    widget = tab.page_widgets[0]
    pen = QPointingDevice("test pen", 98, QInputDevice.DeviceType.Stylus, QPointingDevice.PointerType.Pen,
                          QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 3)
    pos = QPointF(40, 40)
    QApplication.sendEvent(widget, QTabletEvent(QEvent.TabletPress, pen, pos, widget.mapToGlobal(pos), 0.5, 0, 0,
                                                0, 0, 0, Qt.NoModifier, Qt.RightButton, Qt.RightButton))
    assert seen and "Add Note Here..." in seen[-1]


def test_right_click_on_a_document_tab(window, monkeypatch):
    w, _tab = window
    seen = _menu_texts(monkeypatch, w)
    bar = w.tabs.tabBar()
    w._tab_menu(bar.tabRect(0).center())
    assert {"Close", "Close Others", "Close All", "Show in Folder", "Copy File Path"} <= set(seen[-1])


def test_copy_paste_keeps_the_picture(window):
    w, tab = window
    widget, annot, center = _image_point(tab)
    tab.selected = [(0, annot)]
    tab.copy_selected()
    tab.paste()
    pictures = [a for a in widget.page().annots() if pdf_ops.is_image_stamp(a)]
    assert len(pictures) == 2


# ---- rich text in text boxes
def _type_box(w, tab, steps):
    from pdfannotator.tools import Tool as _Tool

    w.set_tool(_Tool.TEXTBOX)
    tab.begin_text_edit(tab.page_widgets[0], origin_pdf=fitz.Point(60, 400))
    editor = tab.text_edit["editor"]
    for step in steps:
        if step.startswith("!"):
            w.text_format_actions[step[1:]].trigger()
        else:
            editor.insertPlainText(step)
    return tab.finish_text_editing()


def test_text_formatting_buttons_while_typing(window):
    w, tab = window
    annot = _type_box(w, tab, ["H", "!sub", "2", "!sub", "O is ", "!b", "bold", "!b", " and x", "!sup", "2", "!sup"])
    runs = pdf_ops.rich_runs(annot)
    assert {"t": "2", "v": -1} in runs and {"t": "bold", "b": 1} in runs and {"t": "2", "v": 1} in runs
    assert annot.info["content"] == "H2O is bold and x2"          # other apps still get the words


def test_formatting_a_selected_box_and_keyboard_shortcuts(window, app):
    from PySide6.QtGui import QKeyEvent

    w, tab = window
    annot = _type_box(w, tab, ["plain words"])
    assert pdf_ops.rich_runs(annot) is None
    tab.selected = [(0, annot)]
    assert tab.format_text("i") and tab.format_text("u")
    box = next(a for a in tab.page_widgets[0].page().annots() if a.xref == annot.xref)
    assert pdf_ops.rich_runs(box) == [{"t": "plain words", "i": 1, "u": 1}]
    tab.format_text("align1")
    assert pdf_ops.freetext_style(box)["align"] == 1
    # Ctrl+B while typing
    tab.begin_text_edit(tab.page_widgets[0], annot=box)
    editor = tab.text_edit["editor"]
    editor.selectAll()
    editor.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_B, Qt.ControlModifier))
    box = tab.finish_text_editing()
    assert all(r.get("b") for r in pdf_ops.rich_runs(box))


def test_formatting_survives_save_copy_and_odd_characters(window, tmp_path):
    w, tab = window
    annot = _type_box(w, tab, ["Price — ", "!b", "€5", "!b"])
    tab.selected = [(0, annot)]
    tab.copy_selected()
    tab.paste()
    page = tab.page_widgets[0].page()
    formatted = [a for a in page.annots() if pdf_ops.is_text_box(a) and pdf_ops.rich_runs(a)]
    assert len(formatted) == 2
    out = tmp_path / "rich.pdf"
    tab.document.doc.save(out)
    reopened = fitz.open(out)
    text = reopened[0].get_text()
    assert "—" in text and "€5" in text                           # drawn with real glyphs
    assert any(pdf_ops.rich_runs(a) for a in reopened[0].annots() if pdf_ops.is_text_box(a))


def test_bullets_numbering_justify_and_line_spacing(window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    w, tab = window
    w.set_tool(Tool.TEXTBOX)
    tab.begin_text_edit(tab.page_widgets[0], origin_pdf=fitz.Point(60, 500), width_pt=220)
    editor = tab.text_edit["editor"]
    editor.insertPlainText("Steps")
    editor.insertPlainText("\n")
    w.text_format_actions["number"].trigger()
    editor.insertPlainText("Open the file\nSign it on the last page")
    w.text_format_actions["align3"].trigger()
    monkeypatch.setattr(QInputDialog, "getDouble", staticmethod(lambda *a, **k: (1.8, True)))
    w._custom_text_spacing()
    annot = tab.finish_text_editing()
    style = pdf_ops.freetext_style(annot)
    assert style["paras"] == ["", "number", "number"]
    assert style["align"] == pdf_ops.JUSTIFY and style["spacing"] == pytest.approx(1.8)
    assert "Custom (1.8)" in w.spacing_custom.text()
    # the selected box: bullets on every paragraph, then off again
    tab.selected = [(0, annot)]
    tab.format_text("bullet")
    box = next(a for a in tab.page_widgets[0].page().annots() if a.xref == annot.xref)
    assert pdf_ops.freetext_style(box)["paras"] == ["bullet"] * 3
    tab.format_text("bullet")
    assert pdf_ops.freetext_style(box)["paras"] == [""] * 3
    tab.format_text("spacing:1")
    assert pdf_ops.freetext_style(box)["spacing"] == 1.0


def test_ribbon_has_no_duplicate_underline_or_strike(window):
    w, _tab = window
    ribbon_actions = set()
    for act in w.tool_toolbar.actions():
        widget = w.tool_toolbar.widgetForAction(act)
        if widget is not None and hasattr(widget, "defaultAction") and widget.defaultAction():
            ribbon_actions.add(widget.defaultAction())
    assert w.text_format_actions["u"] not in ribbon_actions and w.text_format_actions["s"] not in ribbon_actions
    assert w.text_format_actions["b"] in ribbon_actions                # the rest are there


def _drag_eraser(app, widget, points):
    from PySide6.QtGui import QMouseEvent

    def send(kind, p, button, buttons):
        ev = QMouseEvent(kind, QPointF(*p), widget.mapToGlobal(QPointF(*p)), button, buttons, Qt.NoModifier)
        QApplication.sendEvent(widget, ev)

    send(QEvent.MouseButtonPress, points[0], Qt.LeftButton, Qt.LeftButton)
    for p in points[1:]:
        send(QEvent.MouseMove, p, Qt.NoButton, Qt.LeftButton)
    send(QEvent.MouseButtonRelease, points[-1], Qt.LeftButton, Qt.NoButton)
    app.processEvents()


def test_eraser_rubs_out_part_of_a_stroke_and_stroke_eraser_removes_it(window, app):
    from PySide6.QtGui import QColor

    w, tab = window
    widget = tab.page_widgets[0]
    page = widget.page()
    for y in (420, 480):
        pdf_ops.add_ink(page, [fitz.Point(60 + i, y) for i in range(0, 240, 4)], QColor("black"), 2)
    tab.document.snapshot()
    widget.render()

    def inks():
        return [a for a in page.annots() if a.type[0] == fitz.PDF_ANNOT_INK]

    w.set_eraser_mode("point")
    assert w.current_tool == Tool.ERASER and w.tool_actions[Tool.ERASER].text() == "Eraser"
    w.set_eraser_size(10)
    x = tab.pdf_to_px(widget, fitz.Point(180, 420)).x()
    y0, y1 = tab.pdf_to_px(widget, fitz.Point(180, 405)).y(), tab.pdf_to_px(widget, fitz.Point(180, 435)).y()
    _drag_eraser(app, widget, [(x, y0), (x, (y0 + y1) / 2), (x, y1)])
    assert len(inks()) == 3                       # the top stroke is cut in two; the other is untouched
    tab.undo()
    widget = tab.page_widgets[0]
    widget.render()
    page = widget.page()
    assert len(inks()) == 2                       # one drag, one undo step

    w.set_eraser_mode("stroke")
    assert w.tool_actions[Tool.ERASER].text() == "Stroke Eraser"
    _drag_eraser(app, widget, [(x, y0), (x, (y0 + y1) / 2), (x, y1)])
    assert len(inks()) == 1                       # the whole top stroke went
    w.toggle_eraser_mode()
    assert w.eraser_mode == "point"


def test_text_box_list_styles(window):
    w, tab = window
    w.set_tool(Tool.TEXTBOX)
    tab.begin_text_edit(tab.page_widgets[0], origin_pdf=fitz.Point(60, 560), width_pt=220)
    editor = tab.text_edit["editor"]
    w.list_actions["lower-alpha-paren"].trigger()
    editor.insertPlainText("First\nSecond")
    annot = tab.finish_text_editing()
    style = pdf_ops.freetext_style(annot)
    assert style["paras"] == ["lower-alpha-paren"] * 2
    lines = pdf_ops.rich_layout(style["runs"] or [{"t": style["text"]}], 12, "Helvetica", 200, style["paras"])
    assert [l["marker"] for l in lines] == ["a)", "b)"]
    tab.selected = [(0, annot)]
    tab.format_text("upper-roman")
    box = next(a for a in tab.page_widgets[0].page().annots() if a.xref == annot.xref)
    assert pdf_ops.freetext_style(box)["paras"] == ["upper-roman"] * 2
    assert pdf_ops.list_marker("upper-roman", 4) == "IV." and pdf_ops.list_marker("number-parens", 3) == "(3)"
    tab.format_text("none")
    assert pdf_ops.freetext_style(box)["paras"] == [""] * 2


def test_rename_a_document_from_its_tab(window, tmp_path):
    w, tab = window
    old = tab.document.path
    assert w.rename_tab(tab, "Renamed copy")
    assert tab.document.path.endswith("Renamed copy.pdf") and os.path.exists(tab.document.path)
    assert not os.path.exists(old)
    assert "Renamed copy.pdf" in w.tabs.tabText(w.tabs.indexOf(tab))
    fresh = w.new_tab()
    assert w.rename_tab(fresh, "Minutes")              # never saved: just the name
    assert fresh.display_name() == "Minutes" and "Minutes" in w.tabs.tabText(w.tabs.indexOf(fresh))
    # double-clicking the tab opens the name for editing in place
    w.start_tab_rename(w.tabs.indexOf(fresh))
    edit = w._rename_edit
    assert edit is not None and edit.text() == "Minutes"
    edit.setText("Agenda")
    edit.returnPressed.emit()
    assert fresh.display_name() == "Agenda"
