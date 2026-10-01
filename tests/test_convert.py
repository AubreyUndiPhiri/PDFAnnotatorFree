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


def test_pdf_to_latex_project_flowing(sample_pdf, tmp_path):
    converter = pdf_to_latex.PdfToLatex(sample_pdf, ocr=docx_export.OCR_NONE, layout=pdf_to_latex.LAYOUT_FLOW)
    report = converter.run(tmp_path / "proj")
    tex = converter.tex_path.read_text(encoding="utf-8")
    assert report.converted == [1] and report.pictures == [2]
    assert r"\section*{Quarterly Report}" in tex
    assert r"15\% \& profit was \$2,000 for item\_\#3." in tex
    assert r"\begin{tabularx}" in tex and "Alice & 91" in tex
    assert r"\includegraphics" in tex and (tmp_path / "proj" / "images" / "page2.png").exists()
    assert tex.count(r"\begin{document}") == 1 and tex.rstrip().endswith(r"\end{document}")


def test_pdf_to_word_flowing(sample_pdf, tmp_path):
    out = tmp_path / "out.docx"
    report = docx_export.PdfToDocx(sample_pdf, ocr=docx_export.OCR_NONE, layout=docx_export.LAYOUT_FLOW).run(out)
    assert out.stat().st_size > 0 and report.converted == [1] and report.pictures == [2]


def test_pdf_to_latex_exact_layout(sample_pdf, tmp_path):
    from pdfannotator.latex import engines

    converter = pdf_to_latex.PdfToLatex(sample_pdf, ocr=docx_export.OCR_NONE)
    report = converter.run(tmp_path / "exact")
    tex = converter.tex_path.read_text(encoding="utf-8")
    assert report.converted == [1] and report.pictures == [2]
    assert r"\PDFPage{1}{%" in tex and r"\PDFPage{2}{%" in tex        # both pages have a background
    assert "Quarterly Report}%" in tex and r"15\% \& profit was \$2,000 for item\_\#3." in tex
    assert r"\PTW{72}{752}" in tex                                    # 842 - 90: baseline from the bottom
    bg = fitz.open(tmp_path / "exact" / "background.pdf")
    assert bg.page_count == 2 and not bg[0].get_text().strip()          # the text is typed, not in the picture
    assert bg[0].get_drawings()                                         # the table's borders are kept
    xelatex = engines.find_program("xelatex")
    if xelatex:   # it really compiles, and the text lands where it was
        import subprocess

        subprocess.run([xelatex, "-interaction=nonstopmode", "main.tex"], cwd=tmp_path / "exact",
                       capture_output=True, timeout=240)
        out = fitz.open(tmp_path / "exact" / "main.pdf")
        assert out.page_count == 2
        words = {w[4]: w for w in out[0].get_text("words")}
        assert abs(words["Quarterly"][0] - 72) < 0.6 and abs(words["Alice"][0] - 76) < 0.6


def test_pdf_to_word_exact_layout(sample_pdf, tmp_path):
    from docx import Document

    out = tmp_path / "exact.docx"
    report = docx_export.PdfToDocx(sample_pdf, ocr=docx_export.OCR_NONE).run(out)
    assert report.converted == [1] and report.pictures == [2]
    word = Document(out)
    xml = word.element.body.xml
    assert xml.count("w:framePr") >= 6                                 # one frame per line or table cell row
    assert "Quarterly" in xml and "behindDoc=\"1\"" in xml            # text, over its background picture
    assert len(word.sections) == 2 and round(word.sections[0].page_width.pt) == 595


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
        assert theme.mode == theme.DARK and theme.WINDOW == "#0b0b0e" and "qradialgradient" in app.styleSheet()
        theme.set_mode(theme.LIGHT)
        light_icon = icons.icon("save").pixmap(16).toImage()
        assert theme.WINDOW == "#f6f7fc" and "qlineargradient" in app.styleSheet()
        assert dark_icon != light_icon  # the same cached QIcon re-tints itself
    finally:
        theme.set_mode(start)
