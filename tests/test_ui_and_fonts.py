"""pytest checks for the icon set, theme and custom-font support.

    python -m pytest tests
"""
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import fitz
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


def test_custom_font_is_listed_and_embedded(app, tmp_path, monkeypatch):
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    _make_test_font(font_dir / "TestHand.ttf")
    monkeypatch.setattr(fonts, "FONTS_DIR", font_dir)
    monkeypatch.setattr(fonts, "_custom_fonts", {})

    fonts.register_custom_fonts()
    assert "TestHand" in fonts.available_fonts()
    assert fonts.is_custom_font("TestHand")

    doc = fitz.open()
    page = doc.new_page()
    result = pdf_ops.add_freetext(page, fitz.Rect(50, 50, 60, 60), "HELLO WORLD\nSECOND LINE", QColor("black"), 14, "TestHand")
    assert result is None  # drawn into the page, not an annotation
    out = tmp_path / "out.pdf"
    doc.save(out)

    reopened = fitz.open(out)
    embedded = [f[3] for f in reopened[0].get_fonts()]
    assert any("TestHand" in name for name in embedded), embedded
    assert "HELLO WORLD" in reopened[0].get_text().upper()


def test_base14_fonts_stay_editable_annotations():
    doc = fitz.open()
    page = doc.new_page()
    annot = pdf_ops.add_freetext(page, fitz.Rect(50, 50, 250, 90), "Times text", QColor("black"), 12, "Times", align=1)
    assert annot is not None and annot.type[1] == "FreeText"
    assert annot.info["content"] == "Times text"
