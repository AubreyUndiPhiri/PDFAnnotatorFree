"""Convert to Word / Convert to LaTeX, and the light (glass) / dark (clay)
theme switch.

    python -m pytest tests/test_convert.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pymupdf as fitz
import pytest
from PySide6.QtWidgets import QApplication

from pdfannotator import docx_export, theme
from pdfannotator.latex import pdf_to_latex


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sample_pdf():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 90), "Quarterly Report", fontsize=24, fontname="hebo")
    page.insert_text((72, 130), "Costs rose 15% & profit was $2,000 for item_#3.", fontsize=11)
    for r, row in enumerate([["Name", "Score"], ["Alice", "91"], ["Bob", "78"]]):
        for c, text in enumerate(row):
            rect = fitz.Rect(72 + c * 120, 160 + r * 20, 192 + c * 120, 180 + r * 20)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 4, rect.y1 - 6), text, fontsize=10)
    picture = page.get_pixmap(dpi=72)
    scan = doc.new_page()  # a picture only, like a scanned page
    scan.insert_image(scan.rect, pixmap=picture)
    return doc.tobytes()


def test_pdf_to_latex_project(sample_pdf, tmp_path):
    converter = pdf_to_latex.PdfToLatex(sample_pdf, ocr=docx_export.OCR_NONE)
    report = converter.run(tmp_path / "proj")
    tex = converter.tex_path.read_text(encoding="utf-8")
    assert report.converted == [1] and report.pictures == [2]
    assert r"\section*{Quarterly Report}" in tex
    assert r"15\% \& profit was \$2,000 for item\_\#3." in tex
    assert r"\begin{tabularx}" in tex and "Alice & 91" in tex
    assert r"\includegraphics" in tex and (tmp_path / "proj" / "images" / "page2.png").exists()
    assert tex.count(r"\begin{document}") == 1 and tex.rstrip().endswith(r"\end{document}")


def test_pdf_to_word(sample_pdf, tmp_path):
    out = tmp_path / "out.docx"
    report = docx_export.PdfToDocx(sample_pdf, ocr=docx_export.OCR_NONE).run(out)
    assert out.stat().st_size > 0 and report.converted == [1] and report.pictures == [2]


def test_got_ocr_latex_keeps_math_and_escapes_the_rest():
    b = "\\"
    text = "\n".join([
        b + "title{Lab Notes}",
        "The energy is " + b + "(E = mc^2" + b + ") and 50% & more.",
        "Broken {brace and x_1 outside math",
    ])
    out = pdf_to_latex.got_latex(text)
    assert b + "section*{Lab Notes}" in out
    assert b + "(E = mc^2" + b + ")" in out and "50" + b + "% " + b + "& more." in out
    assert b + "{brace and x" + b + "_1" in out


def test_theme_switches_live(app):
    from pdfannotator import icons

    start = theme.mode
    try:
        theme.set_mode(theme.DARK)
        dark_icon = icons.icon("save").pixmap(16).toImage()
        assert theme.mode == theme.DARK and theme.WINDOW == "#1e2130"
        theme.set_mode(theme.LIGHT)
        light_icon = icons.icon("save").pixmap(16).toImage()
        assert theme.WINDOW == "#eef1fb" and "qlineargradient" in app.styleSheet()
        assert dark_icon != light_icon  # the same cached QIcon re-tints itself
    finally:
        theme.set_mode(start)
