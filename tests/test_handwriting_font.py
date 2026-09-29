"""End-to-end check of the in-app handwriting font builder: fill in the real
glyph sheet, rasterise it like a scan (slightly rotated), build the font,
install it and use it for a text box.

    python -m pytest tests
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import fitz
import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from pdfannotator import fonts, pdf_ops
from pdfannotator.handwriting import layout as L
from pdfannotator.handwriting.builder import BuildError, build_font
from pdfannotator.handwriting.sheet import make_sheet


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _filled_scan(tmp_path, skip="&/"):
    sheet = tmp_path / "sheet.pdf"
    make_sheet(sheet)
    doc = fitz.open(sheet)
    page = doc[0]
    for i, ch in enumerate(L.CHARACTERS):
        if ch in skip:
            continue
        x0, _, _, _ = L.drawing_rect(i)
        page.insert_text((x0 + 18, L.baseline_y(i)), ch, fontsize=46, fontname="hebo", color=(0.1, 0.1, 0.45))
    # "Scan" it at 150 dpi, slightly rotated, like a photo that isn't square on
    pix = page.get_pixmap(dpi=150, matrix=fitz.Matrix(1, 1).prerotate(2))
    scan = tmp_path / "scan.png"
    pix.save(scan)
    return scan


def test_build_install_and_type(app, tmp_path, monkeypatch):
    monkeypatch.setattr(fonts, "USER_FONTS_DIR", tmp_path / "userfonts")
    result = build_font(_filled_scan(tmp_path), fonts.USER_FONTS_DIR / "AUPedean.ttf")
    assert result["found"].startswith("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    assert result["skipped"] == "&/"

    from fontTools.ttLib import TTFont

    font = TTFont(result["path"])
    cmap = font.getBestCmap()
    assert ord("A") in cmap and ord("a") in cmap and cmap[ord("a")] == cmap[ord("A")]
    assert font["glyf"][cmap[ord("B")]].numberOfContours >= 3   # B has two holes

    family = fonts.install_font_file(result["path"])
    assert family == "AUPedean" and fonts.has_handwriting_font()
    assert fonts.available_fonts()[0] == "AUPedean"

    doc = fitz.open()
    page = doc.new_page()
    annot = pdf_ops.add_text_box(page, fitz.Point(40, 40), "Hello from my handwriting", QColor("black"), 20, family)
    assert pdf_ops.freetext_style(annot)["fontname"] == "AUPedean"
    saved = fitz.open(stream=doc.tobytes(garbage=4))
    assert any("AUPedean" in f[3] for x in range(1, saved.xref_length())
               for f in [(0, 0, 0, saved.xref_get_key(x, "BaseFont")[1])])


def test_blank_photo_gives_a_clear_error(app, tmp_path):
    blank = tmp_path / "blank.png"
    pix = fitz.open().new_page().get_pixmap(dpi=72)
    pix.save(blank)
    with pytest.raises(BuildError, match="corner squares"):
        build_font(blank, tmp_path / "x.ttf")


def test_empty_sheet_gives_a_clear_error(app, tmp_path):
    with pytest.raises(BuildError, match="No handwriting"):
        build_font(_filled_scan(tmp_path, skip="".join(L.CHARACTERS)), tmp_path / "x.ttf")
