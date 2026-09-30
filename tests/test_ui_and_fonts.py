"""pytest checks for the icon set, theme and custom-font support.

    python -m pytest tests
"""
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pymupdf as fitz
import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from pdfannotator import fonts, icons, pdf_ops
from pdfannotator.tools import TOOL_ICONS, TOOL_GROUPS, Tool

APP_DIR = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_every_tool_has_an_icon_file():
    for tool, name in TOOL_ICONS.items():
        assert (icons.ICONS_DIR / f"{name}.svg").is_file(), f"{tool} -> {name}.svg missing"


def test_every_toolbar_tool_is_grouped_once():
    grouped = [t for group in TOOL_GROUPS for t in group]
    assert len(grouped) == len(set(grouped))
    assert set(grouped) == set(Tool) - {Tool.IMAGE_STAMP}


def test_icons_referenced_in_code_exist():
    pattern = re.compile(r'icons\.icon\("([a-z0-9-]+)"|a\("[^"]+", [^,]+, "([a-z0-9-]+)"|_icon_button\("([a-z0-9-]+)"'
                         r'|icon="([a-z0-9-]+)"')
    names = set()
    for path in (APP_DIR / "pdfannotator").glob("*.py"):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            names.update(n for n in match.groups() if n)
    assert names, "pattern found no icon references"
    missing = sorted(n for n in names if not (icons.ICONS_DIR / f"{n}.svg").is_file())
    assert not missing, f"missing icons: {missing}"


def test_icons_render(app):
    for svg in icons.ICONS_DIR.glob("*.svg"):
        pm = icons.pixmap(svg.stem, 24, "#000000")
        assert not pm.toImage().isNull()
        assert any(pm.toImage().pixelColor(x, y).alpha() for x in range(24) for y in range(24)), svg.stem


def test_main_window_builds_with_theme(app):
    from pdfannotator.main_window import MainWindow

    win = MainWindow()
    assert app.styleSheet(), "theme style sheet was not applied"
    assert all(not act.icon().isNull() for act in win.tool_actions.values())
    win.set_tool(Tool.TEXTBOX)
    assert win._property_actions["font"][-1].isVisible()
    assert not win._property_actions["stamp"][-1].isVisible()
    win.set_tool(Tool.STAMP)
    assert win._property_actions["stamp"][-1].isVisible()
    assert not win._property_actions["font"][-1].isVisible()
    win.close()


def _make_test_font(path: Path):
    """A tiny valid TTF with square glyphs for A-Z, so the embed path can be
    tested without the real AUPedean scan."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    def box():
        pen = TTGlyphPen(None)
        pen.moveTo((50, 0))
        pen.lineTo((50, 700))
        pen.lineTo((450, 700))
        pen.lineTo((450, 0))
        pen.closePath()
        return pen.glyph()

    letters = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
    order = [".notdef", "space"] + letters
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(order)
    cmap = {32: "space", **{ord(c): c for c in letters}, **{ord(c.lower()): c for c in letters}}
    fb.setupCharacterMap(cmap)
    fb.setupGlyf({".notdef": box(), "space": TTGlyphPen(None).glyph(), **{c: box() for c in letters}})
    fb.setupHorizontalMetrics({n: (500, 0 if n == "space" else 50) for n in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "TestHand", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    fb.setupPost()
    fb.save(str(path))


@pytest.fixture
def test_font(tmp_path, monkeypatch):
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    _make_test_font(font_dir / "TestHand.ttf")
    monkeypatch.setattr(fonts, "FONTS_DIR", font_dir)
    monkeypatch.setattr(fonts, "_custom_fonts", {})
    fonts.register_custom_fonts()
    return "TestHand"


def _appearance_fonts(doc, annot):
    kind, ap = doc.xref_get_key(annot.xref, "AP/N")
    assert kind == "xref"
    return doc.xref_get_key(int(ap.split()[0]), "Resources/Font")[1]


def test_custom_font_text_is_a_movable_annotation(app, tmp_path, test_font):
    assert test_font in fonts.available_fonts()
    doc = fitz.open()
    page = doc.new_page()
    annot = pdf_ops.add_text_box(page, fitz.Point(50, 50), "HELLO WORLD\nSECOND LINE", QColor("black"), 14, test_font)
    assert annot.type[1] == "FreeText"
    assert "F-TestHand" in _appearance_fonts(doc, annot)

    # Moving and resizing rebuild the appearance with the custom font, not Helvetica
    pdf_ops.move_annot(annot, 30, 40)
    assert "F-TestHand" in _appearance_fonts(doc, annot)
    pdf_ops.resize_annot(annot, fitz.Rect(annot.rect.x0, annot.rect.y0, annot.rect.x0 + 60, annot.rect.y0 + 10))
    assert "F-TestHand" in _appearance_fonts(doc, annot)
    assert pdf_ops.freetext_style(annot)["fixed_width"]
    assert annot.rect.height >= 4 * pdf_ops.LINE_HEIGHT * 14 - 0.5  # re-wrapped, not clipped

    out = tmp_path / "out.pdf"
    doc.save(out)
    reopened = fitz.open(out)
    rpage = reopened[0]
    saved = next(rpage.annots())
    # The (subset) font lives in the text box's appearance, not the page
    assert "F-TestHand" in _appearance_fonts(reopened, saved)
    base_fonts = {reopened.xref_get_key(x, "BaseFont")[1] for x in range(1, reopened.xref_length())}
    assert any("TestHand" in name for name in base_fonts), base_fonts
    assert saved.info["content"] == "HELLO WORLD\nSECOND LINE"
    assert pdf_ops.freetext_style(saved)["fontname"] == test_font


def test_base14_text_box_style_round_trip():
    doc = fitz.open()
    page = doc.new_page()
    annot = pdf_ops.add_text_box(page, fitz.Point(50, 50), "Times text", QColor(200, 0, 0), 16, "Times")
    style = pdf_ops.freetext_style(annot)
    assert style["fontname"] == "Times" and style["fontsize"] == 16
    assert abs(style["color"][0] - 200 / 255) < 0.01
    w, h = pdf_ops.text_box_size("Times text", 16, "Times")
    assert abs(annot.rect.width - w) < 0.5 and abs(annot.rect.height - h) < 0.5


def _window_with_page(app, tmp_path):
    from pdfannotator.main_window import MainWindow

    src = tmp_path / "blank.pdf"
    d = fitz.open()
    d.new_page(width=400, height=600)
    d.save(src)
    win = MainWindow()
    win.resize(1200, 900)
    win.show()
    tab = win.current_tab()
    tab.load(str(src))
    pw = tab.page_widgets[0]
    pw.render()
    app.processEvents()
    return win, tab, pw


def test_click_type_escape_then_move_and_resize(app, tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    win, tab, pw = _window_with_page(app, tmp_path)
    win.set_tool(Tool.TEXTBOX)
    assert pw.cursor().shape() == Qt.IBeamCursor

    # Click on the page: the editor opens right there, on the page
    QTest.mouseClick(pw, Qt.LeftButton, pos=QPoint(100, 120))
    assert tab.text_edit is not None
    editor = tab.text_edit["editor"]
    assert editor.parent() is pw and editor.isVisible()
    start_width = editor.width()
    QTest.keyClicks(editor, "Hello world, typed on the page")
    assert editor.width() > start_width  # grows with the text
    QTest.keyClick(editor, Qt.Key_Escape)

    # Finished: a FreeText box exists, is selected, and the Select tool is active
    assert tab.text_edit is None
    assert win.current_tool == Tool.SELECT
    page = tab.document.page(0)
    boxes = [a for a in page.annots() if a.type[1] == "FreeText"]
    assert len(boxes) == 1 and boxes[0].info["content"] == "Hello world, typed on the page"
    assert len(tab.selected) == 1
    click_pdf = pw.to_pdf_point(QPoint(100, 120))
    assert abs(boxes[0].rect.x0 - click_pdf.x) < 1 and abs(boxes[0].rect.y0 - click_pdf.y) < 1

    # Resize with the right-edge handle: box gets narrower, text wraps, box grows taller
    frame, has_handles = tab.selection_frame(pw)
    assert has_handles
    right = QPoint(round(frame.right()), round(frame.center().y()))
    old = fitz.Rect(boxes[0].rect)
    QTest.mousePress(pw, Qt.LeftButton, pos=right)
    QTest.mouseMove(pw, right - QPoint(round(frame.width() / 2), 0))
    tab.update_select_drag(pw, right - QPoint(round(frame.width() / 2), 0))
    QTest.mouseRelease(pw, Qt.LeftButton, pos=right - QPoint(round(frame.width() / 2), 0))
    box = next(a for a in tab.document.page(0).annots() if a.type[1] == "FreeText")
    assert box.rect.width < old.width - 10
    assert box.rect.height > old.height

    # Move it by dragging the body
    inside = QPoint(round(frame.left() + 5), round(frame.top() + 5))
    before = fitz.Rect(box.rect)
    QTest.mousePress(pw, Qt.LeftButton, pos=inside)
    tab.update_select_drag(pw, inside + QPoint(40, 30))
    QTest.mouseRelease(pw, Qt.LeftButton, pos=inside + QPoint(40, 30))
    box = next(a for a in tab.document.page(0).annots() if a.type[1] == "FreeText")
    assert box.rect.x0 > before.x0 + 5 and box.rect.y0 > before.y0 + 5

    # Arrow keys nudge the selection
    tab.selected = [(0, box)]
    x0 = box.rect.x0
    QTest.keyClick(pw, Qt.Key_Right, Qt.ShiftModifier)
    box = next(a for a in tab.document.page(0).annots() if a.type[1] == "FreeText")
    assert abs(box.rect.x0 - (x0 + 10)) < 0.5
    win.close()


def test_double_click_edits_in_place(app, tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    win, tab, pw = _window_with_page(app, tmp_path)
    page = tab.document.page(0)
    annot = pdf_ops.add_text_box(page, fitz.Point(60, 80), "First draft", QColor("black"), 14, "Helvetica")
    tab.document.snapshot()
    pw.render()
    win.set_tool(Tool.SELECT)
    inside = tab.annot_rect_px(pw, annot).center().toPoint()
    QTest.mouseDClick(pw, Qt.LeftButton, pos=inside)
    assert tab.text_edit is not None and tab.text_edit["annot"] is not None
    editor = tab.text_edit["editor"]
    assert editor.toPlainText() == "First draft"
    editor.selectAll()
    QTest.keyClicks(editor, "Final text")
    # Clicking elsewhere on the page finishes editing
    QTest.mouseClick(pw, Qt.LeftButton, pos=QPoint(350, 550))
    assert tab.text_edit is None
    boxes = [a for a in tab.document.page(0).annots() if a.type[1] == "FreeText"]
    assert [b.info["content"] for b in boxes] == ["Final text"]
    win.close()


def test_drag_sets_wrap_width_and_toolbar_restyles_live(app, tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    win, tab, pw = _window_with_page(app, tmp_path)
    win.set_tool(Tool.TEXTBOX)
    QTest.mousePress(pw, Qt.LeftButton, pos=QPoint(50, 50))
    QTest.mouseMove(pw, QPoint(200, 60))
    QTest.mouseRelease(pw, Qt.LeftButton, pos=QPoint(200, 60))
    editor = tab.text_edit["editor"]
    assert editor.fixed_width_px is not None
    win.font_spin.setValue(20)
    assert editor.fontsize == 20
    QTest.keyClicks(editor, "a fairly long sentence that has to wrap inside the dragged box")
    QTest.keyClick(editor, Qt.Key_Escape)
    box = next(a for a in tab.document.page(0).annots() if a.type[1] == "FreeText")
    width_pt = pw.to_pdf_point(QPoint(200, 60)).x - pw.to_pdf_point(QPoint(50, 50)).x
    assert abs(box.rect.width - width_pt) < 1
    assert pdf_ops.freetext_style(box)["fontsize"] == 20
    assert box.rect.height > 2 * 20
    win.close()


def test_bundled_font_library(app):
    fonts.register_custom_fonts()
    lib = fonts.library_fonts()
    assert len(lib) >= 150, len(lib)
    for name in ("Roboto", "Roboto Bold", "Lora Italic", "Caveat", "Dancing Script", "Bebas Neue", "JetBrains Mono"):
        assert name in lib, name
        assert fonts.custom_font_path(name).endswith(".ttf")
    # every family ships with its licence
    for family in fonts.LIBRARY_DIR.glob("*/*"):
        assert any(family.glob("*.txt")), f"no licence file in {family}"
    # a bundled font embeds and round-trips like any other
    doc = fitz.open()
    page = doc.new_page()
    annot = pdf_ops.add_text_box(page, fitz.Point(40, 40), "Bundled font check", QColor("black"), 18, "Caveat")
    assert pdf_ops.freetext_style(annot)["fontname"] == "Caveat"


def test_ribbon_arrow_hides_and_shows_the_toolbars(app, tmp_path):
    from pdfannotator.main_window import MainWindow

    w = MainWindow()
    w.show()
    if not w.ribbon_shown:
        w.act_ribbon.trigger()
    assert w.menuBar().cornerWidget() is w.ribbon_btn and w.nav_toolbar.isVisible() and w.tool_toolbar.isVisible()
    w.ribbon_btn.click()
    assert not w.ribbon_shown and not w.nav_toolbar.isVisible() and not w.tool_toolbar.isVisible()
    assert "Show the ribbon" in w.ribbon_btn.toolTip()
    w.ribbon_btn.click()
    assert w.nav_toolbar.isVisible() and w.tool_toolbar.isVisible()

    # opening a file replaces the untouched "Untitled" tab
    pdf = tmp_path / "a.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf)
    assert w.tabs.count() == 1 and w.tabs.tabText(0).startswith("Untitled")
    w.open_files_as_tabs([str(pdf)])
    assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == ["a.pdf"]
    w.close()


def test_pen_panel_floats_while_the_ribbon_is_hidden(app):
    from PySide6.QtGui import QColor as _QColor
    from pdfannotator.main_window import MainWindow
    from pdfannotator import theme as _theme

    w = MainWindow()
    w.resize(1000, 700)
    w.show()
    if not w.ribbon_shown:
        w.act_ribbon.trigger()
    w.set_tool(Tool.INK)
    assert not w.pen_panel.isVisible()               # ribbon showing: no panel
    w.act_ribbon.trigger()
    assert w.pen_panel.isVisible()                   # pen + hidden ribbon: the panel floats
    w.set_pen_color(_QColor("#2563eb"))
    assert w.tool_styles[Tool.INK]["color"] == (37, 99, 235)
    w.set_pen_width(4.0)
    assert w.tool_styles[Tool.INK]["width"] == 4.0 and w.width_spin.value() == 4.0
    was = w.pen_panel.vertical
    w.pen_panel.toggle_orientation()
    assert w.pen_panel.vertical != was and w.pen_panel.height() <= w.height()
    w.pen_panel.toggle_orientation()
    w.set_tool(Tool.SELECT)
    assert w.pen_panel.isVisible()                   # stays while you pick Select from it
    w.close_pen_panel()
    assert not w.pen_panel.isVisible()
    w.set_tool(Tool.MARKER)
    assert w.pen_panel.isVisible()                   # a pen tool brings it back
    w.act_ribbon.trigger()
    assert not w.pen_panel.isVisible() and w.ribbon_shown
    _theme._settings().setValue("ui/pen_panel_vertical", "false")
    w.close()
