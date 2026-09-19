import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import fitz
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPoint

from pdfannotator.main_window import MainWindow
from pdfannotator.tools import Tool


def make_test_pdf(path):
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_text((50, 50), "Hello world this is a line of sample text.", fontsize=12)
    doc.save(path)
    doc.close()


def main():
    app = QApplication(sys.argv)
    win = MainWindow()

    tmp_dir = tempfile.mkdtemp(prefix="pdfannot_smoke_")
    src_pdf = os.path.join(tmp_dir, "sample.pdf")
    make_test_pdf(src_pdf)

    win._open_path(src_pdf)
    assert win.document.page_count == 1, "expected 1 page"

    pw = win.page_widgets[0]
    pw.render()
    assert pw.rendered, "page did not render"
    print("PASS: document loaded and rendered, size(px)=", pw.width(), pw.height())

    page = win.document.page(0)
    assert sum(1 for _ in page.annots()) == 0

    # --- Highlight ---
    win.set_tool(Tool.HIGHLIGHT)
    p1 = QPoint(int(45 * win.zoom), int(40 * win.zoom))
    p2 = QPoint(int(260 * win.zoom), int(58 * win.zoom))
    QTest.mousePress(pw, Qt.LeftButton, pos=p1)
    QTest.mouseMove(pw, p2)
    QTest.mouseRelease(pw, Qt.LeftButton, pos=p2)
    count = sum(1 for _ in page.annots())
    assert count == 1, f"expected 1 annot after highlight, got {count}"
    print("PASS: highlight created via simulated drag")

    # --- Ink (freehand) ---
    win.set_tool(Tool.INK)
    QTest.mousePress(pw, Qt.LeftButton, pos=QPoint(20, 300))
    QTest.mouseMove(pw, QPoint(60, 340))
    QTest.mouseMove(pw, QPoint(100, 300))
    QTest.mouseRelease(pw, Qt.LeftButton, pos=QPoint(100, 300))
    count = sum(1 for _ in page.annots())
    assert count == 2, f"expected 2 annots after ink, got {count}"
    print("PASS: ink annotation created via simulated drag")

    # --- Rect ---
    win.set_tool(Tool.RECT)
    QTest.mousePress(pw, Qt.LeftButton, pos=QPoint(20, 400))
    QTest.mouseMove(pw, QPoint(150, 460))
    QTest.mouseRelease(pw, Qt.LeftButton, pos=QPoint(150, 460))
    count = sum(1 for _ in page.annots())
    assert count == 3, f"expected 3 annots after rect, got {count}"
    rect_annot = list(page.annots())[-1]
    orig_rect = fitz.Rect(rect_annot.rect)
    print("PASS: rect annotation created via simulated drag, rect=", orig_rect)

    # --- Select + move ---
    win.set_tool(Tool.SELECT)
    mid_px = QPoint(int((20 + 150) / 2 * 1), int((400 + 460) / 2 * 1))
    # rect widget coords used above were already widget-pixel coords (not pdf), reuse same click point
    click_pt = QPoint(85, 430)
    QTest.mousePress(pw, Qt.LeftButton, pos=click_pt)
    assert win.selected is not None, "expected an annotation to be selected"
    sel_page_idx, sel_annot = win.selected
    assert sel_page_idx == 0
    drag_to = click_pt + QPoint(30, 15)
    QTest.mouseMove(pw, drag_to)
    QTest.mouseRelease(pw, Qt.LeftButton, pos=drag_to)
    moved_rect = fitz.Rect(sel_annot.rect)
    assert moved_rect != orig_rect, "expected annotation to have moved"
    count = sum(1 for _ in page.annots())
    assert count == 3, f"move should not change annot count, got {count}"
    print("PASS: select + drag move works, rect moved from", orig_rect, "to", moved_rect)

    # --- Select + delete ---
    win.delete_selected()
    count = sum(1 for _ in page.annots())
    assert count == 2, f"expected 2 annots after delete, got {count}"
    print("PASS: delete selected annotation works")

    # --- Undo / redo ---
    assert win.document.can_undo()
    win.undo()
    page = win.document.page(0)
    count = sum(1 for _ in page.annots())
    assert count == 3, f"expected 3 annots after undo, got {count}"
    print("PASS: undo restored deleted annotation")

    win.redo()
    page = win.document.page(0)
    count = sum(1 for _ in page.annots())
    assert count == 2, f"expected 2 annots after redo, got {count}"
    print("PASS: redo re-applied delete")

    # --- Page tools: rotate, insert blank, delete page ---
    win.rotate_page(0, 90)
    assert win.document.page(0).rotation == 90
    print("PASS: rotate page works")

    win.insert_blank_page(1)
    assert win.document.page_count == 2
    print("PASS: insert blank page works")

    win.delete_page(1)
    assert win.document.page_count == 1
    print("PASS: delete page works")

    # --- Save ---
    out_path = os.path.join(tmp_dir, "out.pdf")
    win.document.save(out_path)
    reopened = fitz.open(out_path)
    assert reopened.page_count == 1
    reopened_count = sum(1 for _ in reopened[0].annots())
    assert reopened_count == 2, f"expected 2 annots in saved file, got {reopened_count}"
    print("PASS: save produced a valid PDF with annotations intact, rotation=", reopened[0].rotation)
    reopened.close()

    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
