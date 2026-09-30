"""Pen and marker: smoothing, pressure sensitivity, moving strokes, and the
page-thumbnails button beside the sidebar.

    python -m pytest tests/test_drawing.py
"""
import math
import os
import random
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pymupdf as fitz
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from pdfannotator import pdf_ops, strokes
from pdfannotator.tools import Tool


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
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


def _jitter(path):
    """Mean distance of each point from the midpoint of its neighbours: 0 for a straight / smooth line."""
    return sum(math.dist(b, ((a[0] + c[0]) / 2, (a[1] + c[1]) / 2))
               for a, b, c in zip(path, path[1:], path[2:])) / max(1, len(path) - 2)


def test_smoothing_removes_wobble_but_keeps_the_ends():
    rng = random.Random(1)
    shaky = [(100 + i * 2, 200 + 20 * math.sin(i / 8) + rng.uniform(-1.5, 1.5)) for i in range(80)]
    smooth, pressures = strokes.smooth_stroke(shaky)
    assert _jitter(smooth) < _jitter(shaky) / 4
    assert smooth[0] == shaky[0] and smooth[-1] == shaky[-1]
    assert len(pressures) == len(smooth)
    plain, _ = strokes.smooth_stroke(shaky, strength=0)  # Smooth off: only duplicates go
    assert len(plain) <= len(shaky)


def test_pressure_widths_and_speed():
    assert strokes.width_for(2, 1.0) > strokes.width_for(2, 0.5) > strokes.width_for(2, 0.0) > 0
    sp = strokes.SpeedPressure()
    sp.feed((0, 0), 0)
    slow = [sp.feed((i * 0.1, 0), i * 10) for i in range(1, 30)][-1]
    sp = strokes.SpeedPressure()
    sp.feed((0, 0), 0)
    fast = [sp.feed((i * 8.0, 0), i * 10) for i in range(1, 30)][-1]
    assert slow > fast  # slow strokes come out thicker, like ink


def test_pressure_stroke_survives_move_copy_and_save(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    pts = [(100 + i * 3, 200 + 20 * math.sin(i / 6)) for i in range(40)]
    widths = [strokes.width_for(3, 0.3 + 0.7 * i / 39) for i in range(40)]
    annot = pdf_ops.add_ink(page, pts, QColor(20, 20, 20), 3, widths=widths)
    assert pdf_ops.pressure_widths(annot) == pytest.approx(widths, abs=1e-3)
    pdf_ops.move_annot(annot, 30, 10)
    assert annot.vertices[0][0] == pytest.approx((130, 210), abs=0.01)
    assert pdf_ops.pressure_widths(annot) is not None
    copy = pdf_ops.deserialize_and_add(page, pdf_ops.serialize_annot(annot), offset=(0, 100))
    assert pdf_ops.pressure_widths(copy) == pytest.approx(widths, abs=1e-3)
    out = tmp_path / "ink.pdf"
    doc.save(out)
    saved = fitz.open(out)
    page0 = saved[0]
    assert sum(pdf_ops.pressure_widths(a) is not None for a in page0.annots()) == 2


@pytest.mark.parametrize("kind", ["line", "polygon", "ink"])
def test_point_based_annotations_move(kind):
    doc = fitz.open()
    page = doc.new_page()
    annot = {"line": lambda: page.add_line_annot((100, 100), (200, 150)),
             "polygon": lambda: page.add_polygon_annot([(100, 200), (200, 210), (150, 280)]),
             "ink": lambda: page.add_ink_annot([[(100, 100), (150, 130), (200, 110)]])}[kind]()
    annot.update()
    before = fitz.Rect(annot.rect)
    pdf_ops.move_annot(annot, 40, 15)
    assert annot.rect.x0 == pytest.approx(before.x0 + 40, abs=0.01)
    assert annot.rect.y0 == pytest.approx(before.y0 + 15, abs=0.01)


def _draw(pw, points):
    QTest.mousePress(pw, Qt.LeftButton, pos=points[0])
    for p in points[1:]:
        QTest.mouseMove(pw, p, delay=2)
    QTest.mouseRelease(pw, Qt.LeftButton, pos=points[-1])


def test_pen_toggles_smooth_and_pressure(app, window):
    tab = window.current_tab()
    pw = tab.page_widgets[0]
    window.set_tool(Tool.INK)
    toolbar = window.tool_toolbar
    shown = {a.defaultWidget().defaultAction() for a in toolbar.actions()
             if hasattr(a, "defaultWidget") and a.isVisible() and hasattr(a.defaultWidget(), "defaultAction")}
    assert {window.act_smooth_ink, window.act_pressure_ink} <= shown
    squiggle = [QPoint(100 + i * 4, 200 + round(15 * math.sin(i / 3))) for i in range(40)]
    if not window.act_pressure_ink.isChecked():
        window.act_pressure_ink.trigger()
    _draw(pw, squiggle)
    inks = [a for a in tab.document.page(0).annots() if a.type[1] == "Ink"]
    assert len(inks) == 1 and pdf_ops.pressure_widths(inks[0]) is not None
    window.act_pressure_ink.trigger()  # off again: a plain, even stroke
    _draw(pw, [QPoint(p.x(), p.y() + 120) for p in squiggle])
    inks = [a for a in tab.document.page(0).annots() if a.type[1] == "Ink"]
    assert len(inks) == 2 and sum(pdf_ops.pressure_widths(a) is None for a in inks) == 1
    window.set_tool(Tool.MARKER)  # the marker is smoothed, but has no pressure option
    assert window.act_smooth_ink in [a.defaultWidget().defaultAction() for a in toolbar.actions()
                                     if hasattr(a, "defaultWidget") and a.isVisible()
                                     and hasattr(a.defaultWidget(), "defaultAction")]


def test_thumbnail_button_is_beside_the_sidebar(app, window):
    tab = window.current_tab()
    assert window.act_sidebar not in window.nav_toolbar.actions()
    assert tab.sidebar_button.defaultAction() is window.act_sidebar
    rail, thumbs = tab.sidebar_rail.geometry(), tab.thumbnails.geometry()
    assert rail.right() <= thumbs.left() + 1 and abs(rail.top() - thumbs.top()) < 40
    tab.sidebar_button.click()
    assert not tab.thumbnails.isVisible() and tab.sidebar_rail.isVisible()  # the rail stays to bring it back
    tab.sidebar_button.click()
    assert tab.thumbnails.isVisible()
