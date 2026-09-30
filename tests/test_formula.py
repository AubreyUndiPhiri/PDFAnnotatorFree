"""The Formula (Σ) tool: type LaTeX, press Enter, get rendered maths on the
page that can be moved, edited, copied and saved.

    python -m pytest tests/test_formula.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pymupdf as fitz
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from pdfannotator import formula, pdf_ops
from pdfannotator.tools import Tool

HAS_TEX = formula.tex_program() is not None


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: errors.append(a[2:])))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    src = tmp_path / "doc.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(src)
    from pdfannotator.main_window import MainWindow

    win = MainWindow()
    win.resize(1200, 800)
    win.show()
    win.current_tab().load(str(src))
    app.processEvents()
    yield win
    win.close()
    assert not errors, errors


def _wait_for_preview(app, editor, seconds=60):
    deadline = time.time() + seconds
    while editor.result() is None and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    return editor.result()


def _formulas(tab):
    return [a for a in tab.document.page(0).annots() if pdf_ops.is_formula(a)]


@pytest.mark.parametrize("engine", [formula.ENGINE_MATHTEXT] + ([formula.ENGINE_LATEX] if HAS_TEX else []))
def test_render_is_transparent_and_sized_by_font_size(app, engine):
    small = formula.render(r"\frac{a}{b} + \sqrt{x}", 10, (0, 0, 0), engine=engine)
    big = formula.render(r"\frac{a}{b} + \sqrt{x}", 20, (0, 0, 0), engine=engine)
    assert small.engine == engine and 1.8 < big.height_pt / small.height_pt < 2.2
    image = QImage.fromData(small.png)
    assert not image.isNull() and image.pixelColor(0, 0).alpha() == 0


def test_delimiters_and_bad_input(app):
    assert formula.strip_delimiters("$x^2$") == "x^2" and formula.strip_delimiters(r"\[x\]") == "x"
    assert formula.tex_body(r"a &= b \\ &= c").startswith(r"\begin{align*}")
    assert formula.tex_body(r"f(x)=\begin{cases}1 & x>0\\0\end{cases}").startswith(r"$\displaystyle")
    with pytest.raises(formula.FormulaError):
        formula.render(r"\frac{a}{", 12, engine=formula.ENGINE_MATHTEXT)
    with pytest.raises(formula.FormulaError):
        formula.render("   ", 12)


def test_type_latex_press_enter_to_place_then_edit_copy_and_save(app, window, tmp_path):
    tab = window.current_tab()
    window.set_tool(Tool.FORMULA)
    pw = tab.page_widgets[0]
    QTest.mouseClick(pw, Qt.LeftButton, pos=pw.rect().center())
    editor = tab.text_edit["editor"]
    QTest.keyClicks(editor.source, r"E = mc^2")
    assert _wait_for_preview(app, editor) is not None and editor.preview.pixmap() is not None
    QTest.keyClick(editor.source, Qt.Key_Return)
    placed = _formulas(tab)
    assert len(placed) == 1 and tab.text_edit is None
    assert pdf_ops.formula_data(placed[0])["source"] == "E = mc^2"
    assert tab.selected and tab.selected[0][1].xref == placed[0].xref  # selected, ready to move

    # edit it again: the source comes back, Enter replaces it in place
    rect = placed[0].rect
    tab.begin_formula_edit(pw, annot=placed[0])
    editor = tab.text_edit["editor"]
    assert editor.text() == "E = mc^2"
    editor.source.selectAll()
    QTest.keyClicks(editor.source, r"\sum_{i=1}^{n} i")
    _wait_for_preview(app, editor)
    QTest.keyClick(editor.source, Qt.Key_Return)
    placed = _formulas(tab)
    assert len(placed) == 1 and pdf_ops.formula_data(placed[0])["source"] == r"\sum_{i=1}^{n} i"
    assert abs(placed[0].rect.x0 - rect.x0) < 0.01 and abs(placed[0].rect.y0 - rect.y0) < 0.01

    # copy / paste re-creates it from its LaTeX
    copy = pdf_ops.deserialize_and_add(tab.document.page(0), pdf_ops.serialize_annot(placed[0]), offset=(0, 80))
    assert pdf_ops.is_formula(copy)

    # saved and reopened, the formula keeps its picture and its source
    out = tmp_path / "saved.pdf"
    tab.save(str(out))
    reopened = fitz.open(out)
    sources = [pdf_ops.formula_data(a)["source"] for a in reopened[0].annots() if pdf_ops.is_formula(a)]
    assert sources == [r"\sum_{i=1}^{n} i"] * 2


def test_errors_keep_the_editor_open_and_esc_cancels(app, window):
    tab = window.current_tab()
    window.set_tool(Tool.FORMULA)
    pw = tab.page_widgets[0]
    tab.begin_formula_edit(pw, origin_pdf=fitz.Point(100, 100))
    editor = tab.text_edit["editor"]
    QTest.keyClicks(editor.source, r"\frac{a}{")
    QTest.keyClick(editor.source, Qt.Key_Return)
    assert tab.text_edit is not None and editor.status.property("error")   # still open, showing the error
    assert not _formulas(tab)
    QTest.keyClick(editor.source, Qt.Key_Escape)
    assert tab.text_edit is None and not _formulas(tab)


def test_shift_enter_adds_a_line(app, window):
    tab = window.current_tab()
    window.set_tool(Tool.FORMULA)
    tab.begin_formula_edit(tab.page_widgets[0], origin_pdf=fitz.Point(100, 100))
    editor = tab.text_edit["editor"]
    QTest.keyClicks(editor.source, r"a &= b \\")
    QTest.keyClick(editor.source, Qt.Key_Return, Qt.ShiftModifier)
    QTest.keyClicks(editor.source, r"&= c")
    assert editor.text() == "a &= b \\\\\n&= c" and tab.text_edit is not None
    QTest.keyClick(editor.source, Qt.Key_Escape)
