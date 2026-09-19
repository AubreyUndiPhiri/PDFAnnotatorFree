import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import fitz
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPoint

from pdfannotator.main_window import MainWindow
from pdfannotator.tools import Tool

MOCK_RESPONSE = [QMessageBox.Yes]
QMessageBox.question = staticmethod(lambda *a, **k: MOCK_RESPONSE[0])


def make_test_pdf(path):
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_text((50, 50), "Hello world this is a line of sample text.", fontsize=12)
    doc.save(path)
    doc.close()


def drag(pw, p1, p2):
    QTest.mousePress(pw, Qt.LeftButton, pos=p1)
    QTest.mouseMove(pw, p2)
    QTest.mouseRelease(pw, Qt.LeftButton, pos=p2)


def annot_count(page):
    return sum(1 for _ in page.annots())


def main():
    app = QApplication(sys.argv)
    win = MainWindow()

    tmp_dir = tempfile.mkdtemp(prefix="pdfannot_smoke_")
    src_pdf = os.path.join(tmp_dir, "sample.pdf")
    make_test_pdf(src_pdf)

    tab = win.current_tab()
    tab.load(src_pdf)
    assert tab.document.page_count == 1

    pw = tab.page_widgets[0]
    pw.render()
    assert pw.rendered
    print("PASS: document loaded and rendered")

    page = tab.document.page(0)
    assert annot_count(page) == 0

    # --- Highlight ---
    win.set_tool(Tool.HIGHLIGHT)
    drag(pw, QPoint(int(45 * tab.zoom), int(40 * tab.zoom)), QPoint(int(260 * tab.zoom), int(58 * tab.zoom)))
    assert annot_count(page) == 1
    print("PASS: highlight")

    # --- Ink ---
    win.set_tool(Tool.INK)
    drag(pw, QPoint(20, 300), QPoint(60, 340))
    assert annot_count(page) == 2
    print("PASS: ink (pen)")

    # --- Rect ---
    win.set_tool(Tool.RECT)
    drag(pw, QPoint(20, 400), QPoint(150, 460))
    assert annot_count(page) == 3
    rect_annot = list(page.annots())[-1]
    orig_rect = fitz.Rect(rect_annot.rect)
    print("PASS: rect")

    # --- Select + move (single) ---
    win.set_tool(Tool.SELECT)
    click_pt = QPoint(85, 430)
    QTest.mousePress(pw, Qt.LeftButton, pos=click_pt)
    assert len(tab.selected) == 1
    drag_to = click_pt + QPoint(30, 15)
    QTest.mouseMove(pw, drag_to)
    QTest.mouseRelease(pw, Qt.LeftButton, pos=drag_to)
    moved_rect = fitz.Rect(rect_annot.rect)
    assert moved_rect != orig_rect
    assert annot_count(page) == 3
    print("PASS: select + drag move")

    # --- Select + delete ---
    win.delete_selected()
    assert annot_count(page) == 2
    print("PASS: delete selected")

    # --- Undo / redo ---
    win.undo()
    # undo()/redo() close and reopen the underlying fitz.Document and rebuild
    # all PageWidgets, so any previously held page/widget references go stale.
    page = tab.document.page(0)
    pw = tab.page_widgets[0]
    pw.render()
    assert annot_count(page) == 3
    win.redo()
    page = tab.document.page(0)
    pw = tab.page_widgets[0]
    pw.render()
    assert annot_count(page) == 2
    print("PASS: undo/redo")

    # --- Marker (translucent highlighter) ---
    win.set_tool(Tool.MARKER)
    drag(pw, QPoint(20, 480), QPoint(100, 500))
    assert annot_count(page) == 3
    marker_annot = list(page.annots())[-1]
    assert marker_annot.opacity < 1.0
    print("PASS: marker (opacity", marker_annot.opacity, ")")

    # --- Polygon ---
    win.set_tool(Tool.POLYGON)
    QTest.mouseClick(pw, Qt.LeftButton, pos=QPoint(200, 300))
    QTest.mouseClick(pw, Qt.LeftButton, pos=QPoint(240, 300))
    QTest.mouseClick(pw, Qt.LeftButton, pos=QPoint(220, 340))
    QTest.keyClick(pw, Qt.Key_Return)
    assert annot_count(page) == 4
    poly_annot = list(page.annots())[-1]
    assert poly_annot.type[1] == "Polygon"
    print("PASS: polygon (click x3 + Enter)")

    # --- Dimension (line + label) ---
    win.set_tool(Tool.DIMENSION)
    before = annot_count(page)
    drag(pw, QPoint(20, 550), QPoint(20 + int(72 * tab.zoom), 550))
    after = annot_count(page)
    assert after == before + 2, f"expected +2 annots (line+label), got +{after - before}"
    label_annot = list(page.annots())[-1]
    assert tab.unit_name in label_annot.info["content"]
    print("PASS: dimension ->", label_annot.info["content"])

    # --- Eraser ---
    win.set_tool(Tool.RECT)
    drag(pw, QPoint(300, 400), QPoint(360, 440))
    before = annot_count(page)
    win.set_tool(Tool.ERASER)
    drag(pw, QPoint(310, 410), QPoint(340, 430))
    after = annot_count(page)
    assert after == before - 1, f"expected -1 annot from eraser, got {after - before}"
    print("PASS: eraser")

    # --- Lasso multi-select + bulk delete ---
    win.set_tool(Tool.RECT)
    drag(pw, QPoint(10, 10), QPoint(40, 40))
    drag(pw, QPoint(60, 10), QPoint(90, 40))
    before = annot_count(page)
    # Note: QPoint(0, 0).isNull() is True, and QTest's mouse simulators treat a
    # null pos as "use the widget's default (center)" -- so every corner here
    # is nudged to (2, 2) instead of (0, 0) to avoid that silent override.
    win.set_tool(Tool.LASSO)
    QTest.mousePress(pw, Qt.LeftButton, pos=QPoint(2, 2))
    QTest.mouseMove(pw, QPoint(100, 2))
    QTest.mouseMove(pw, QPoint(100, 50))
    QTest.mouseMove(pw, QPoint(2, 50))
    QTest.mouseRelease(pw, Qt.LeftButton, pos=QPoint(2, 2))
    assert len(tab.selected) == 2, f"expected 2 lassoed annots, got {len(tab.selected)}"
    win.delete_selected()
    after = annot_count(page)
    assert after == before - 2
    print("PASS: lasso select + bulk delete")

    # --- Extract Text / Snapshot (clipboard; tolerate headless clipboard limits) ---
    win.set_tool(Tool.EXTRACT_TEXT)
    drag(pw, QPoint(int(45 * tab.zoom), int(35 * tab.zoom)), QPoint(int(300 * tab.zoom), int(60 * tab.zoom)))
    print("PASS: extract text ran without error")

    win.set_tool(Tool.SNAPSHOT)
    drag(pw, QPoint(10, 10), QPoint(60, 60))
    print("PASS: snapshot ran without error")

    # --- Pan / Zoom tools ---
    win.set_tool(Tool.PAN)
    tab.pan_scroll(5, 5)
    print("PASS: pan ran without error")

    win.set_tool(Tool.ZOOM)
    zoom_before = tab.zoom
    QTest.mousePress(pw, Qt.LeftButton, pos=QPoint(100, 100))
    assert tab.zoom > zoom_before
    print("PASS: zoom tool (zoom in on click) ->", tab.zoom)

    # Restore the original zoom so subsequent pixel-position assumptions
    # (e.g. "click inside the polygon at (220, 330)") stay valid; set_zoom()
    # defers re-rendering via QTimer, which needs a manual render() here since
    # no Qt event loop is spinning in this offscreen test.
    tab.zoom_reset()
    pw.render()

    # --- Cut / Copy / Paste ---
    win.set_tool(Tool.SELECT)
    QTest.mousePress(pw, Qt.LeftButton, pos=QPoint(220, 330))  # inside the polygon
    QTest.mouseRelease(pw, Qt.LeftButton, pos=QPoint(220, 330))
    assert len(tab.selected) == 1
    before = annot_count(page)
    win.copy_selected()
    win.paste()
    after = annot_count(page)
    assert after == before + 1
    print("PASS: copy + paste")

    win.paste_without_formatting()
    after2 = annot_count(page)
    assert after2 == after + 1
    print("PASS: paste without formatting")

    # --- Properties (metadata) round trip, no exec() ---
    tab.apply_properties_data({"title": "Smoke Test Doc", "author": "Tester", "subject": "", "keywords": "", "creator": ""})
    assert tab.document.metadata.get("title") == "Smoke Test Doc"
    print("PASS: properties metadata round trip")

    # --- Melt All Annotations ---
    MOCK_RESPONSE[0] = QMessageBox.Yes
    before_melt = annot_count(page)
    assert before_melt > 0
    win.melt_all_annotations()
    page = tab.document.page(0)
    assert annot_count(page) == 0
    assert "Hello" in page.get_text()
    print("PASS: melt all annotations (baked", before_melt, "annots, text preserved)")

    # --- Remove All Annotations (on fresh annots) ---
    win.set_tool(Tool.RECT)
    drag(pw, QPoint(10, 10), QPoint(40, 40))
    drag(pw, QPoint(60, 10), QPoint(90, 40))
    assert annot_count(page) == 2
    win.remove_all_annotations()
    assert annot_count(tab.document.page(0)) == 0
    print("PASS: remove all annotations")

    # --- Crop ---
    win.set_tool(Tool.CROP)
    MOCK_RESPONSE[0] = QMessageBox.Yes
    old_rect = fitz.Rect(tab.document.page(0).rect)
    drag(pw, QPoint(0, 0), QPoint(200, 300))
    new_rect = fitz.Rect(tab.document.page(0).rect)
    assert new_rect != old_rect
    print("PASS: crop changed page rect from", old_rect, "to", new_rect)

    # --- Page tools: rotate, insert blank, delete page ---
    win.set_tool(Tool.SELECT)
    tab.rotate_page(0, 90)
    assert tab.document.page(0).rotation == 90
    tab.insert_blank_page(1)
    assert tab.document.page_count == 2
    tab.delete_page(1)
    assert tab.document.page_count == 1
    print("PASS: rotate / insert / delete page")

    # --- Multi-tab isolation ---
    tab2 = win.new_tab()
    assert win.tabs.count() == 2
    tab2.page_widgets[0].render()
    win.set_tool(Tool.RECT)
    drag(tab2.page_widgets[0], QPoint(10, 10), QPoint(50, 50))
    assert annot_count(tab2.document.page(0)) == 1
    assert annot_count(tab.document.page(0)) == 0, "edit in tab2 leaked into tab1"
    print("PASS: multi-tab isolation")

    MOCK_RESPONSE[0] = QMessageBox.Discard
    win.close_tab(win.tabs.indexOf(tab2))
    assert win.tabs.count() == 1
    print("PASS: close tab (with discard prompt)")

    # --- Save / reload ---
    out_path = os.path.join(tmp_dir, "out.pdf")
    tab.save(out_path)
    reopened = fitz.open(out_path)
    assert reopened.page_count == 1
    assert reopened[0].rotation == 90
    reopened.close()
    print("PASS: save produced a valid PDF")

    # --- Print (render to a PDF file via QPrinter, bypassing the modal dialog) ---
    from PySide6.QtPrintSupport import QPrinter
    print_out = os.path.join(tmp_dir, "printed.pdf")
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(print_out)
    win._render_to_printer(tab, printer)
    assert os.path.exists(print_out) and os.path.getsize(print_out) > 500
    print("PASS: print-to-PDF pipeline produced a non-trivial file (", os.path.getsize(print_out), "bytes )")

    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
