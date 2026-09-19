import os
import fitz

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QScrollArea, QSplitter, QFileDialog, QMessageBox,
    QInputDialog,
)
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtCore import Qt, QPoint, QTimer

from .document import PDFDocument
from .page_widget import PageWidget
from .thumbnail_panel import ThumbnailPanel
from .guide_overlay import GuideLine, LaserDot
from .tools import Tool, TOOL_HINTS, UNITS
from . import pdf_ops


class DocumentTab(QWidget):
    """Everything specific to ONE open PDF: the document itself, its viewer,
    thumbnails, zoom/selection state, and every tool-commit callback that
    PageWidget calls into. Shared state (current tool, style, clipboard) lives
    on the owning MainWindow and is reached through the properties below."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.document = PDFDocument()

        self.zoom = 1.2
        self.selected = []  # list[(page_index, fitz.Annot)]
        self.select_dragging = False
        self.select_offset_px = QPoint(0, 0)
        self._select_start_px = None

        self.page_widgets = []
        self.page_layout_mode = "continuous"  # or "single"
        self.current_single_page_index = 0

        self.unit_index = 0  # into tools.UNITS
        self.hide_annotations = False
        self.guides = []

        self._build_ui()

    # ---------------------------------------------------------------
    # Shared style state, proxied from the owning MainWindow
    # ---------------------------------------------------------------

    @property
    def current_tool(self):
        return self.window.current_tool

    @property
    def current_color(self):
        return self.window.current_color

    @property
    def current_width(self):
        return self.window.current_width

    @property
    def current_fontsize(self):
        return self.window.current_fontsize

    @property
    def current_opacity(self):
        return self.window.current_opacity

    @property
    def current_stamp_name(self):
        return self.window.current_stamp_name

    @property
    def pending_image_path(self):
        return self.window.pending_image_path

    @pending_image_path.setter
    def pending_image_path(self, value):
        self.window.pending_image_path = value

    @property
    def unit_name(self):
        return UNITS[self.unit_index][0]

    @property
    def points_per_unit(self):
        return UNITS[self.unit_index][1]

    def set_hint(self, text):
        self.window.hint_label.setText(text)

    # ---------------------------------------------------------------
    # UI construction
    # ---------------------------------------------------------------

    def _build_ui(self):
        self.thumbnails = ThumbnailPanel(self)
        self.thumbnails.pageActivated.connect(self.go_to_page)
        self.thumbnails.pagesReordered.connect(self.reorder_pages)

        self.pages_container = QWidget()
        self.pages_layout = QVBoxLayout(self.pages_container)
        self.pages_layout.setSpacing(16)
        self.pages_layout.setContentsMargins(16, 16, 16, 16)
        self.pages_layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.pages_container)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.update_visible_pages)

        self.laser_dot = LaserDot(self.pages_container)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.thumbnails)
        splitter.addWidget(self.scroll_area)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        self.new_document()

    # ---------------------------------------------------------------
    # Document lifecycle
    # ---------------------------------------------------------------

    def new_document(self):
        self.document.new()
        self.selected = []
        self.rebuild_viewer()

    def load(self, path: str):
        self.document.load(path)
        self.selected = []
        self.rebuild_viewer()

    def save(self, path=None):
        self.document.save(path)

    def display_name(self) -> str:
        if self.document.path:
            return os.path.basename(self.document.path)
        return "Untitled"

    # ---------------------------------------------------------------
    # Undo / redo
    # ---------------------------------------------------------------

    def undo(self):
        if not self.document.can_undo():
            return
        self.document.undo()
        self.selected = []
        self.rebuild_viewer()

    def redo(self):
        if not self.document.can_redo():
            return
        self.document.redo()
        self.selected = []
        self.rebuild_viewer()

    # ---------------------------------------------------------------
    # Viewer management
    # ---------------------------------------------------------------

    def rebuild_viewer(self):
        self.document.invalidate_page_cache()
        while self.pages_layout.count():
            item = self.pages_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.page_widgets = []
        if self.document.is_open:
            for i in range(self.document.page_count):
                pw = PageWidget(self, i)
                self.page_widgets.append(pw)
            if self.page_layout_mode == "continuous":
                for pw in self.page_widgets:
                    self.pages_layout.addWidget(pw, alignment=Qt.AlignHCenter)
            else:
                self.current_single_page_index = min(self.current_single_page_index, len(self.page_widgets) - 1)
                self.pages_layout.addWidget(
                    self.page_widgets[self.current_single_page_index], alignment=Qt.AlignHCenter
                )
        self.thumbnails.rebuild()
        self.window.on_tab_content_changed(self)
        QTimer.singleShot(0, self.update_visible_pages)

    def get_page_widget(self, index):
        return self.page_widgets[index]

    def current_page_index(self):
        if self.selected:
            return self.selected[0][0]
        if self.page_layout_mode == "single":
            return self.current_single_page_index
        return 0

    def update_visible_pages(self):
        if not self.page_widgets:
            return
        if self.page_layout_mode == "single":
            pw = self.page_widgets[self.current_single_page_index]
            if not pw.rendered:
                pw.render()
            return
        viewport_h = self.scroll_area.viewport().height()
        top = self.scroll_area.verticalScrollBar().value()
        bottom = top + viewport_h
        buffer = viewport_h
        for pw in self.page_widgets:
            g = pw.geometry()
            if g.bottom() >= top - buffer and g.top() <= bottom + buffer:
                if not pw.rendered:
                    pw.render()

    def go_to_page(self, index):
        if not (0 <= index < len(self.page_widgets)):
            return
        if self.page_layout_mode == "single":
            self.show_single_page(index)
        else:
            self.scroll_area.ensureWidgetVisible(self.page_widgets[index], 0, 0)
            self.update_visible_pages()

    def show_single_page(self, index):
        if not (0 <= index < len(self.page_widgets)):
            return
        while self.pages_layout.count():
            self.pages_layout.takeAt(0)
        for i, pw in enumerate(self.page_widgets):
            pw.setVisible(i == index)
        self.current_single_page_index = index
        self.pages_layout.addWidget(self.page_widgets[index], alignment=Qt.AlignHCenter)
        self.update_visible_pages()

    def set_page_layout_mode(self, mode):
        if mode == self.page_layout_mode:
            return
        self.page_layout_mode = mode
        while self.pages_layout.count():
            self.pages_layout.takeAt(0)
        if mode == "continuous":
            for pw in self.page_widgets:
                pw.setVisible(True)
                self.pages_layout.addWidget(pw, alignment=Qt.AlignHCenter)
        else:
            self.show_single_page(self.current_single_page_index)
        QTimer.singleShot(0, self.update_visible_pages)

    def refresh_thumbnail(self, index):
        self.thumbnails.refresh_one(index)

    def toggle_hide_annotations(self, hidden):
        self.hide_annotations = hidden
        for pw in self.page_widgets:
            if pw.rendered:
                pw.render()

    # ---------------------------------------------------------------
    # Zoom
    # ---------------------------------------------------------------

    def set_zoom(self, zoom, anchor_widget=None, anchor_pixel=None):
        zoom = max(0.2, min(zoom, 6.0))
        if abs(zoom - self.zoom) < 1e-6:
            return
        anchor_pdf_point = None
        if anchor_widget is not None and anchor_pixel is not None:
            anchor_pdf_point = anchor_widget.to_pdf_point(anchor_pixel)
            page_index = anchor_widget.page_index
        self.zoom = zoom
        self.selected = []
        for pw in self.page_widgets:
            pw.invalidate()
        self.window.on_zoom_changed(zoom)
        QTimer.singleShot(0, self.update_visible_pages)
        if anchor_pdf_point is not None:
            def _rescroll():
                widget = self.page_widgets[page_index]
                widget.render()
                mat = pdf_ops.coord_matrix(widget.page(), self.zoom)
                new_pixel_point = anchor_pdf_point * mat
                target = widget.mapTo(
                    self.pages_container, QPoint(int(new_pixel_point.x), int(new_pixel_point.y))
                )
                viewport = self.scroll_area.viewport()
                self.scroll_area.horizontalScrollBar().setValue(target.x() - viewport.width() // 2)
                self.scroll_area.verticalScrollBar().setValue(target.y() - viewport.height() // 2)
            QTimer.singleShot(0, _rescroll)

    def zoom_in(self):
        self.set_zoom(self.zoom * 1.2)

    def zoom_out(self):
        self.set_zoom(self.zoom / 1.2)

    def zoom_reset(self):
        self.set_zoom(1.2)

    def actual_size(self):
        self.set_zoom(1.0)

    def fit_to_width(self):
        if not self.page_widgets:
            return
        page = self.page_widgets[self.current_page_index()].page()
        viewport_w = self.scroll_area.viewport().width() - 32
        page_w = page.rect.width
        if page_w > 0:
            self.set_zoom(viewport_w / page_w)

    def fit_to_size(self):
        if not self.page_widgets:
            return
        page = self.page_widgets[self.current_page_index()].page()
        viewport_w = self.scroll_area.viewport().width() - 32
        viewport_h = self.scroll_area.viewport().height() - 32
        page_w, page_h = page.rect.width, page.rect.height
        if page_w > 0 and page_h > 0:
            self.set_zoom(min(viewport_w / page_w, viewport_h / page_h))

    # ---------------------------------------------------------------
    # Page tools
    # ---------------------------------------------------------------

    def rotate_page(self, index, degrees):
        if not (0 <= index < self.document.page_count):
            return
        page = self.document.page(index)
        page.set_rotation((page.rotation + degrees) % 360)
        self.document.snapshot()
        self.selected = []
        self.get_page_widget(index).render()
        self.thumbnails.refresh_one(index)
        self.update_visible_pages()
        self.window.on_tab_content_changed(self)

    def delete_page(self, index):
        if not (0 <= index < self.document.page_count):
            return
        if self.document.page_count <= 1:
            QMessageBox.warning(self, "Cannot Delete", "The document must have at least one page.")
            return
        self.document.doc.delete_page(index)
        self.document.snapshot()
        self.selected = []
        self.rebuild_viewer()

    def insert_blank_page(self, index):
        if not self.document.is_open:
            return
        w, h = fitz.paper_size("a4")
        if self.document.page_count:
            ref = self.document.page(max(0, min(index - 1, self.document.page_count - 1)))
            w, h = ref.rect.width, ref.rect.height
        index = max(0, min(index, self.document.page_count))
        self.document.doc.new_page(pno=index, width=w, height=h)
        self.document.snapshot()
        self.selected = []
        self.rebuild_viewer()

    def extract_page(self, index):
        if not (0 <= index < self.document.page_count):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Extract Page As", "", "PDF Files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        new_doc = fitz.open()
        new_doc.insert_pdf(self.document.doc, from_page=index, to_page=index)
        new_doc.save(path)
        new_doc.close()
        QMessageBox.information(self, "Extracted", f"Page {index + 1} saved to {path}")

    def reorder_pages(self, order):
        if not self.document.is_open:
            return
        self.document.doc.select(order)
        self.document.snapshot()
        self.selected = []
        self.rebuild_viewer()

    def merge_pdfs(self, paths):
        for p in paths:
            with open(p, "rb") as f:
                data = f.read()
            other = fitz.open(stream=data, filetype="pdf")
            self.document.doc.insert_pdf(other)
            other.close()
        self.document.snapshot()
        self.selected = []
        self.rebuild_viewer()

    def split_pdf(self, directory):
        base = "page"
        if self.document.path:
            base = os.path.splitext(os.path.basename(self.document.path))[0]
        count = self.document.page_count
        for i in range(count):
            new_doc = fitz.open()
            new_doc.insert_pdf(self.document.doc, from_page=i, to_page=i)
            out_path = os.path.join(directory, f"{base}_p{i + 1:03d}.pdf")
            new_doc.save(out_path)
            new_doc.close()
        return count

    # ---------------------------------------------------------------
    # Selection: Select All / Deselect / Invert (current page)
    # ---------------------------------------------------------------

    def select_all_on_page(self, index=None):
        index = self.current_page_index() if index is None else index
        if not (0 <= index < self.document.page_count):
            return
        page = self.document.page(index)
        self.selected = [(index, a) for a in page.annots()]
        self.get_page_widget(index).update()

    def deselect_all(self):
        self.selected = []
        for pw in self.page_widgets:
            pw.update()

    def invert_selection_on_page(self, index=None):
        index = self.current_page_index() if index is None else index
        if not (0 <= index < self.document.page_count):
            return
        page = self.document.page(index)
        selected_xrefs = {a.xref for pidx, a in self.selected if pidx == index}
        self.selected = [(pidx, a) for pidx, a in self.selected if pidx != index]
        for a in page.annots():
            if a.xref not in selected_xrefs:
                self.selected.append((index, a))
        self.get_page_widget(index).update()

    # ---------------------------------------------------------------
    # Whole-document operations
    # ---------------------------------------------------------------

    def remove_all_annotations(self):
        self.document.remove_all_annotations()
        self.selected = []
        self.rebuild_viewer()

    def melt_all_annotations(self):
        self.document.melt_all_annotations()
        self.selected = []
        self.rebuild_viewer()

    def show_properties_data(self):
        meta = dict(self.document.metadata)
        meta["_page_count"] = self.document.page_count
        meta["_path"] = self.document.path or "(unsaved)"
        return meta

    def apply_properties_data(self, meta: dict):
        keys = ("title", "author", "subject", "keywords", "creator")
        self.document.set_metadata({k: meta.get(k, "") for k in keys})

    # ---------------------------------------------------------------
    # Cut / Copy / Paste
    # ---------------------------------------------------------------

    def copy_selected(self):
        if not self.selected:
            return
        self.window.annotation_clipboard = [pdf_ops.serialize_annot(a) for _, a in self.selected]
        self.window.paste_count = 0

    def cut_selected(self):
        if not self.selected:
            return
        self.copy_selected()
        self.delete_selected()

    def paste(self, override_style=False):
        if not self.window.annotation_clipboard:
            return
        index = self.current_page_index()
        if not (0 <= index < self.document.page_count):
            return
        page = self.document.page(index)
        self.window.paste_count += 1
        off = 10 * self.window.paste_count
        override_color = pdf_ops.color_to_rgb(self.current_color) if override_style else None
        override_width = self.current_width if override_style else None
        new_selected = []
        for data in self.window.annotation_clipboard:
            annot = pdf_ops.deserialize_and_add(
                page, data, offset=(off, off),
                override_color=override_color, override_width=override_width,
            )
            if annot is not None:
                new_selected.append((index, annot))
        if not new_selected:
            return
        self.selected = new_selected
        self.document.snapshot()
        self.get_page_widget(index).render()
        self.refresh_thumbnail(index)

    # ---------------------------------------------------------------
    # Image / signature insertion
    # ---------------------------------------------------------------

    def start_image_stamp(self, path):
        self.pending_image_path = path
        self.window.set_tool(Tool.IMAGE_STAMP)
        self.set_hint(f"Drag on the page to place the image.")

    # ---------------------------------------------------------------
    # Interaction callbacks invoked by PageWidget
    # ---------------------------------------------------------------

    def commit_ink(self, widget, points):
        page = widget.page()
        pdf_ops.add_ink(page, points, self.current_color, self.current_width)
        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    def commit_marker(self, widget, points):
        page = widget.page()
        pdf_ops.add_marker(page, points, self.current_color, self.current_width, self.current_opacity)
        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    def commit_eraser(self, widget, points):
        page = widget.page()
        removed = pdf_ops.erase_along_path(page, points)
        if removed:
            self.document.snapshot()
            self.selected = [s for s in self.selected if s[0] != widget.page_index]
            widget.render()
            self.refresh_thumbnail(widget.page_index)

    def commit_lasso(self, widget, points):
        page = widget.page()
        matches = pdf_ops.annots_in_lasso(page, points)
        self.selected = [(widget.page_index, a) for a in matches]
        widget.update()

    def commit_extract_text(self, widget, p1, p2):
        page = widget.page()
        text = pdf_ops.extract_text(page, p1, p2)
        if text.strip():
            QGuiApplication.clipboard().setText(text)
            self.set_hint(f"Copied {len(text)} character(s) to the clipboard.")
        else:
            self.set_hint("No text found in that area.")

    def commit_snapshot(self, widget, p1, p2):
        page = widget.page()
        pix = pdf_ops.snapshot_pixmap(page, p1, p2, self.zoom)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        QGuiApplication.clipboard().setPixmap(QPixmap.fromImage(img))
        self.set_hint("Copied the selected area to the clipboard as an image.")

    def commit_crop(self, widget, p1, p2):
        rect = fitz.Rect(p1, p2)
        rect.normalize()
        if rect.width < 4 or rect.height < 4:
            return
        resp = QMessageBox.question(
            self, "Crop Page", "Crop this page to the selected area?",
            QMessageBox.Yes | QMessageBox.Cancel,
        )
        if resp != QMessageBox.Yes:
            return
        page = widget.page()
        pdf_ops.crop_page(page, p1, p2)
        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    def update_measure(self, widget, p1, p2):
        dist = pdf_ops.distance_in_units(p1, p2, self.points_per_unit)
        self.set_hint(f"Distance: {dist:.2f} {self.unit_name}")

    def commit_polygon(self, widget, points):
        page = widget.page()
        pdf_ops.add_polygon(page, points, self.current_color, self.current_width)
        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    def commit_dimension(self, widget, p1, p2):
        page = widget.page()
        pdf_ops.add_dimension(page, p1, p2, self.current_color, self.current_width,
                               self.unit_name, self.points_per_unit)
        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    def pan_scroll(self, dx_px, dy_px):
        hbar = self.scroll_area.horizontalScrollBar()
        vbar = self.scroll_area.verticalScrollBar()
        hbar.setValue(hbar.value() - dx_px)
        vbar.setValue(vbar.value() - dy_px)

    def zoom_click(self, widget, pos, zoom_in):
        factor = 1.4 if zoom_in else 1 / 1.4
        self.set_zoom(self.zoom * factor, anchor_widget=widget, anchor_pixel=pos)

    def update_laser_pointer(self, widget, pos):
        target = widget.mapTo(self.pages_container, pos)
        self.laser_dot.move_to(target)

    def hide_laser_pointer(self):
        self.laser_dot.hide()

    def inspect_annot(self, widget, pos):
        page = widget.page()
        pdf_pt = widget.to_pdf_point(pos)
        annot = pdf_ops.find_annot_at(page, pdf_pt)
        if annot is None:
            self.set_hint("")
            return
        info = annot.info
        self.set_hint(f"{annot.type[1]} — {info.get('content', '') or '(no content)'}")

    def commit_drag_tool(self, widget, tool, p1, p2):
        page = widget.page()
        rect = fitz.Rect(p1, p2)
        rect.normalize()
        if rect.width < 4 and rect.height < 4 and tool in (
            Tool.RECT, Tool.ELLIPSE, Tool.TEXTBOX, Tool.STAMP, Tool.IMAGE_STAMP, Tool.FORMULA,
        ):
            default_w, default_h = (150, 40) if tool in (Tool.TEXTBOX, Tool.FORMULA) else (120, 60)
            rect = fitz.Rect(p1.x, p1.y, p1.x + default_w, p1.y + default_h)

        try:
            if tool == Tool.HIGHLIGHT:
                pdf_ops.add_highlight(page, p1, p2, self.current_color)
            elif tool == Tool.UNDERLINE:
                pdf_ops.add_underline(page, p1, p2, self.current_color)
            elif tool == Tool.STRIKEOUT:
                pdf_ops.add_strikeout(page, p1, p2, self.current_color)
            elif tool == Tool.RECT:
                pdf_ops.add_rect(page, rect, self.current_color, self.current_width)
            elif tool == Tool.ELLIPSE:
                pdf_ops.add_ellipse(page, rect, self.current_color, self.current_width)
            elif tool == Tool.LINE:
                pdf_ops.add_line(page, p1, p2, self.current_color, self.current_width, arrow=False)
            elif tool == Tool.ARROW:
                pdf_ops.add_line(page, p1, p2, self.current_color, self.current_width, arrow=True)
            elif tool == Tool.TEXTBOX:
                text, ok = QInputDialog.getMultiLineText(self, "Add Text Box", "Text:")
                if not ok or not text.strip():
                    return
                pdf_ops.add_freetext(page, rect, text, self.current_color, self.current_fontsize)
                self.window.set_tool(Tool.SELECT)
            elif tool == Tool.FORMULA:
                text, ok = QInputDialog.getMultiLineText(self, "Add Formula", "Enter formula/equation text:")
                if not ok or not text.strip():
                    return
                pdf_ops.add_freetext(page, rect, text, self.current_color, self.current_fontsize)
                self.window.set_tool(Tool.SELECT)
            elif tool == Tool.STAMP:
                pdf_ops.add_stamp(page, rect, self.current_stamp_name)
                self.window.set_tool(Tool.SELECT)
            elif tool == Tool.IMAGE_STAMP:
                if not self.pending_image_path:
                    return
                pdf_ops.insert_image(page, rect, image_path=self.pending_image_path)
                self.pending_image_path = None
                self.window.set_tool(Tool.SELECT)
            else:
                return
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not add annotation:\n{e}")
            return

        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    def place_note(self, widget, pos):
        text, ok = QInputDialog.getMultiLineText(self, "Add Note", "Comment:")
        if not ok or not text.strip():
            return
        page = widget.page()
        pdf_pt = widget.to_pdf_point(pos)
        pdf_ops.add_note(page, pdf_pt, text, self.current_color)
        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    def try_edit_annot_text(self, widget, pos):
        page = widget.page()
        pdf_pt = widget.to_pdf_point(pos)
        annot = pdf_ops.find_annot_at(page, pdf_pt)
        if annot is None:
            return
        if annot.type[0] not in (fitz.PDF_ANNOT_FREETEXT, fitz.PDF_ANNOT_TEXT):
            return
        old_text = annot.info.get("content", "")
        text, ok = QInputDialog.getMultiLineText(self, "Edit Text", "Content:", old_text)
        if ok:
            annot.set_info(content=text)
            annot.update()
            self.document.snapshot()
            widget.render()
            self.refresh_thumbnail(widget.page_index)

    def delete_selected(self):
        if not self.selected:
            return
        touched_pages = set()
        for page_index, annot in self.selected:
            page = self.document.page(page_index)
            try:
                page.delete_annot(annot)
            except Exception:
                pass
            touched_pages.add(page_index)
        self.document.snapshot()
        self.selected = []
        for idx in touched_pages:
            self.get_page_widget(idx).render()
            self.refresh_thumbnail(idx)

    # ---------------------------------------------------------------
    # Select tool: click to select (Ctrl+click multi-select), drag to move
    # ---------------------------------------------------------------

    def begin_select_drag(self, widget, pos, additive=False):
        page = widget.page()
        pdf_pt = widget.to_pdf_point(pos)
        annot = pdf_ops.find_annot_at(page, pdf_pt)
        if annot is None:
            if not additive:
                self.selected = []
            self.select_dragging = False
            widget.update()
            return
        if additive:
            existing = [s for s in self.selected if s[0] == widget.page_index and s[1].xref == annot.xref]
            if existing:
                self.selected = [s for s in self.selected
                                  if not (s[0] == widget.page_index and s[1].xref == annot.xref)]
            else:
                self.selected = self.selected + [(widget.page_index, annot)]
        else:
            already = any(s[0] == widget.page_index and s[1].xref == annot.xref for s in self.selected)
            if not already:
                self.selected = [(widget.page_index, annot)]
        self.select_dragging = True
        self._select_start_px = pos
        self.select_offset_px = QPoint(0, 0)
        widget.update()

    def update_select_drag(self, widget, pos):
        if not self.select_dragging or not self.selected:
            return
        self.select_offset_px = pos - self._select_start_px
        widget.update()

    def end_select_drag(self, widget, pos):
        if not self.select_dragging or not self.selected:
            self.select_dragging = False
            return
        offset = pos - self._select_start_px
        self.select_dragging = False
        self.select_offset_px = QPoint(0, 0)
        if offset.manhattanLength() < 3:
            widget.update()
            return
        p0 = widget.to_pdf_point(QPoint(0, 0))
        p1 = widget.to_pdf_point(QPoint(offset.x(), offset.y()))
        dx, dy = p1.x - p0.x, p1.y - p0.y
        touched_pages = set()
        for page_index, annot in self.selected:
            if page_index != widget.page_index:
                continue
            try:
                pdf_ops.move_annot(annot, dx, dy)
                touched_pages.add(page_index)
            except Exception:
                pass
        if not touched_pages:
            widget.update()
            return
        self.document.snapshot()
        for idx in touched_pages:
            self.get_page_widget(idx).render()
            self.refresh_thumbnail(idx)

    # ---------------------------------------------------------------
    # Find
    # ---------------------------------------------------------------

    def find_text(self, query, forward=True):
        if not query:
            return
        if query != getattr(self, "_last_find_query", None):
            self._find_matches = []
            for i in range(self.document.page_count):
                page = self.document.page(i)
                for quad in page.search_for(query):
                    self._find_matches.append((i, fitz.Rect(quad)))
            self._find_index = -1
            self._last_find_query = query
        if not self._find_matches:
            self.set_hint(f'"{query}" not found.')
            return
        self._find_index = (self._find_index + (1 if forward else -1)) % len(self._find_matches)
        page_index, rect = self._find_matches[self._find_index]
        self.go_to_page(page_index)
        self.set_hint(f"Match {self._find_index + 1} of {len(self._find_matches)}")
        self._flash_rect(page_index, rect)

    def _flash_rect(self, page_index, rect):
        widget = self.get_page_widget(page_index)
        widget._flash_rect = rect
        widget.update()
        QTimer.singleShot(1500, lambda: self._clear_flash(widget))

    def _clear_flash(self, widget):
        widget._flash_rect = None
        widget.update()

    # ---------------------------------------------------------------
    # Auxiliary Lines (view-only guides)
    # ---------------------------------------------------------------

    def add_guide(self, orientation):
        parent = self.pages_container
        pos = parent.height() // 2 if orientation == "h" else parent.width() // 2
        guide = GuideLine(parent, orientation, pos, self._remove_guide)
        self.guides.append(guide)

    def _remove_guide(self, guide):
        if guide in self.guides:
            self.guides.remove(guide)

    def clear_guides(self):
        for g in self.guides:
            g.deleteLater()
        self.guides = []
