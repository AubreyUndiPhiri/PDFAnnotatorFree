import math
import os
import pymupdf as fitz

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFileDialog, QMessageBox,
    QDialog, QInputDialog, QToolButton,
)
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QPixmap, QPainter, QColor
from PySide6.QtCore import QEvent, Qt, QPoint, QPointF, QRectF, QSize, QTimer

from .document import PDFDocument
from .page_widget import PageWidget
from .thumbnail_panel import ThumbnailPanel
from .guide_overlay import GuideLine, LaserDot
from .inline_text import InlineTextEditor
from .formula_editor import FormulaEditor
from . import handles
from .tools import Tool, TOOL_HINTS, UNITS
from . import fonts, icons, pdf_ops, strokes, theme


class PageCanvas(QWidget):
    """Grey backdrop behind the pages that paints a soft shadow under each one."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.CANVAS))
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        for child in self.children():
            if isinstance(child, PageWidget) and child.isVisible():
                r = child.geometry()
                for spread, alpha in ((6, 10), (4, 16), (2, 26)):
                    shadow = QColor(theme.PAGE_SHADOW)
                    shadow.setAlpha(alpha)
                    painter.setBrush(shadow)
                    painter.drawRoundedRect(r.adjusted(-spread, -spread + 2, spread, spread + 2), spread, spread)


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

        self.text_edit = None      # active in-page text editor state (see begin_text_edit)
        self.resize_state = None   # active handle drag on the selected annotation
        self.resize_preview_px = None

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
    def current_fontname(self):
        return self.window.current_fontname

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
    def ink_smoothing(self):
        return getattr(self.window, "ink_smoothing", True)

    @property
    def ink_pressure(self):
        return getattr(self.window, "ink_pressure", False)

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

        self.pages_container = PageCanvas()
        self.pages_container.setObjectName("canvasContents")
        self.pages_layout = QVBoxLayout(self.pages_container)
        self.pages_layout.setSpacing(20)
        self.pages_layout.setContentsMargins(24, 24, 24, 24)
        self.pages_layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("canvas")
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.pages_container)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.update_visible_pages)
        self._install_gestures()

        self.laser_dot = LaserDot(self.pages_container)

        # The page-thumbnails button sits right beside the thumbnails, on a
        # slim rail that stays when they are hidden, so they can be shown again
        self.sidebar_rail = QWidget()
        self.sidebar_rail.setObjectName("sidebarRail")
        rail = QVBoxLayout(self.sidebar_rail)
        rail.setContentsMargins(6, 8, 0, 8)
        rail.setSpacing(4)
        self.sidebar_button = QToolButton()
        self.sidebar_button.setObjectName("sidebarButton")
        self.sidebar_button.setIconSize(QSize(18, 18))
        self.sidebar_button.setAutoRaise(True)
        act = getattr(self.window, "act_sidebar", None)
        if act is not None:
            self.sidebar_button.setDefaultAction(act)
            self.thumbnails.setVisible(act.isChecked())
        rail.addWidget(self.sidebar_button)
        rail.addStretch(1)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.sidebar_rail)
        outer.addWidget(self.thumbnails)
        outer.addWidget(self.scroll_area, 1)

        self.new_document()

    # ---------------------------------------------------------------
    # Document lifecycle
    # ---------------------------------------------------------------

    def new_document(self):
        self.document.new()
        self.selected = []
        self.rebuild_viewer()

    def load(self, path: str, password: str | None = None):
        self.document.load(path, password)
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
        self.finish_text_editing()
        if not self.document.can_undo():
            return
        self.document.undo()
        self.selected = []
        self.rebuild_viewer()

    def redo(self):
        self.finish_text_editing()
        if not self.document.can_redo():
            return
        self.document.redo()
        self.selected = []
        self.rebuild_viewer()

    # ---------------------------------------------------------------
    # Viewer management
    # ---------------------------------------------------------------

    def rebuild_viewer(self):
        self._discard_text_editing()
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
        # Continuous: the page under the middle of the viewport
        if not self.page_widgets:
            return 0
        middle = self.scroll_area.verticalScrollBar().value() + self.scroll_area.viewport().height() // 2
        for i, pw in enumerate(self.page_widgets):
            if middle <= pw.geometry().bottom() + self.pages_layout.spacing():
                return i
        return len(self.page_widgets) - 1

    def update_visible_pages(self):
        if not self.page_widgets:
            return
        if hasattr(self.window, "on_current_page_changed"):
            self.window.on_current_page_changed(self)
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

    def set_compact(self, compact):
        """With the ribbon hidden the pages get more room: smaller gaps around and between them."""
        margin, gap = (10, 12) if compact else (24, 20)
        self.pages_layout.setContentsMargins(margin, margin, margin, margin)
        self.pages_layout.setSpacing(gap)

    # ---- pinch to zoom: trackpad pinches (Ctrl + wheel on Windows, native
    # gestures elsewhere), touch-screen pinches and Ctrl + mouse wheel, all
    # smooth, keeping the spot under the fingers / pointer where it is
    def _install_gestures(self):
        viewport = self.scroll_area.viewport()
        viewport.installEventFilter(self)
        viewport.setAttribute(Qt.WA_AcceptTouchEvents)
        viewport.grabGesture(Qt.PinchGesture)
        self._pinch_target = None     # (zoom, page widget, pixel on it, viewport point) waiting to be applied
        self._pinch_timer = QTimer(self)
        self._pinch_timer.setSingleShot(True)
        self._pinch_timer.setInterval(24)   # a few frames' worth of gesture per re-render
        self._pinch_timer.timeout.connect(self._apply_pinch)

    def eventFilter(self, obj, event):
        if obj is not self.scroll_area.viewport():
            return False
        kind = event.type()
        if kind == QEvent.Wheel and event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                self._pinch(1.0015 ** delta, event.position().toPoint())
            return True
        if kind == QEvent.NativeGesture and event.gestureType() == Qt.ZoomNativeGesture:
            self._pinch(1.0 + event.value(), event.position().toPoint())
            return True
        if kind == QEvent.Gesture:
            pinch = event.gesture(Qt.PinchGesture)
            if pinch is not None:
                center = obj.mapFromGlobal(pinch.centerPoint().toPoint())
                self._pinch(pinch.scaleFactor(), center)
                return True
        return False

    def _pinch(self, factor, viewport_pos):
        base = self._pinch_target[0] if self._pinch_target else self.zoom
        target = max(0.2, min(base * factor, 6.0))
        anchor = self._page_at(viewport_pos)
        self._pinch_target = (target, *anchor, viewport_pos) if anchor else (target, None, None, viewport_pos)
        if not self._pinch_timer.isActive():
            self._pinch_timer.start()

    def _page_at(self, viewport_pos):
        point = self.scroll_area.viewport().mapTo(self.pages_container, viewport_pos)
        for pw in self.page_widgets:
            if pw.geometry().contains(point):
                return pw, point - pw.pos()
        return None

    def _apply_pinch(self):
        if self._pinch_target is None:
            return
        zoom, widget, pixel, viewport_pos = self._pinch_target
        self._pinch_target = None
        self.set_zoom(zoom, widget, pixel, keep_at=viewport_pos)

    def set_zoom(self, zoom, anchor_widget=None, anchor_pixel=None, keep_at=None):
        """`anchor_pixel` on `anchor_widget` ends up in the middle of the view,
        or at `keep_at` (a viewport point) when given: the zoom stays put under the pointer."""
        self.finish_text_editing()
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
                at = keep_at if keep_at is not None else QPoint(viewport.width() // 2, viewport.height() // 2)
                self.scroll_area.horizontalScrollBar().setValue(target.x() - at.x())
                self.scroll_area.verticalScrollBar().setValue(target.y() - at.y())
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
        self.set_hint("Drag on the page to place the image.")

    # ---------------------------------------------------------------
    # Interaction callbacks invoked by PageWidget
    # ---------------------------------------------------------------

    def commit_ink(self, widget, points, pressures=None):
        """A pen stroke: smoothed when Smooth handwriting is on, thicker and
        thinner along its length when `pressures` (0-1 per point) are given."""
        page = widget.page()
        pts, pres = strokes.smooth_stroke([(p.x, p.y) for p in points], pressures,
                                          1.0 if self.ink_smoothing else 0.0)
        if len(pts) < 2:
            return
        widths = [strokes.width_for(self.current_width, p) for p in pres] if pressures else None
        pdf_ops.add_ink(page, pts, self.current_color, self.current_width, widths=widths)
        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    def commit_marker(self, widget, points):
        page = widget.page()
        pts, _ = strokes.smooth_stroke([(p.x, p.y) for p in points], None, 1.0 if self.ink_smoothing else 0.0)
        if len(pts) < 2:
            return
        pdf_ops.add_marker(page, [fitz.Point(*p) for p in pts], self.current_color, self.current_width,
                           self.current_opacity)
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
            elif tool == Tool.STAMP:
                pdf_ops.add_stamp(page, rect, self.current_stamp_name)
                self.window.set_tool(Tool.SELECT)
            elif tool == Tool.IMAGE_STAMP:
                if not self.pending_image_path:
                    return
                with open(self.pending_image_path, "rb") as f:   # movable, and editable later (Edit Photo)
                    pdf_ops.add_image_stamp(page, rect, f.read())
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

    # ---- right-click menu on the page, and Edit Photo
    def show_page_menu(self, widget, pos, global_pos):
        """What you can do with the thing under the pointer (or the page)."""
        from PySide6.QtWidgets import QMenu

        self.finish_text_editing()
        w = self.window
        page = widget.page()
        point = widget.to_pdf_point(pos)
        annot = pdf_ops.find_annot_at(page, point)
        page_image = None if annot is not None else pdf_ops.page_image_at(page, point)
        menu = QMenu(self)

        def add(text, slot, icon=None, enabled=True):
            act = menu.addAction(icons.icon(icon) if icon else QIcon(), text)
            act.triggered.connect(slot)
            act.setEnabled(enabled)
            return act

        if annot is not None:
            if not any(a.xref == annot.xref for i, a in self.selected if i == widget.page_index):
                self.selected = [(widget.page_index, annot)]
                widget.update()
            if pdf_ops.is_image_stamp(annot):
                add("Edit Photo...", lambda: self.edit_photo(widget, annot=annot), "image")
            if pdf_ops.is_text_box(annot):
                add("Edit Text", lambda: self.begin_text_edit(widget, annot=annot), "textbox")
            if pdf_ops.is_formula(annot):
                add("Edit Formula", lambda: self.begin_formula_edit(widget, annot=annot), "formula")
            if annot.type[0] == fitz.PDF_ANNOT_TEXT:
                add("Edit Note...", lambda: self.try_edit_annot_text(widget, pos), "note")
            menu.addSeparator()
            menu.addActions([w.act_cut, w.act_copy])
            menu.addAction(w.act_delete)
            menu.addSeparator()
        elif page_image is not None:
            xref = page_image[0]
            add("Edit Photo...", lambda: self.edit_photo(widget, xref=xref), "image")
            menu.addSeparator()
        menu.addAction(w.act_paste)
        w.act_paste.setEnabled(bool(w.annotation_clipboard))
        add("Add Note Here...", lambda: self.place_note(widget, pos), "note")
        menu.addAction(w.act_image)
        menu.addSeparator()
        add("Select Tool", lambda: w.set_tool(Tool.SELECT), "select")
        menu.addActions([w.act_zoom_in, w.act_zoom_out, w.act_fit_width])
        self.exec_menu(menu, global_pos)
        w.act_paste.setEnabled(True)

    def exec_menu(self, menu, global_pos):
        menu.exec(global_pos)

    def edit_photo(self, widget, annot=None, xref=None):
        """Open the picture (a placed picture stamp, or a picture in the page
        itself) in Edit Photo, and put the result back in the same place."""
        from .image_editor import ImageEditorDialog

        page = widget.page()
        try:
            data = pdf_ops.image_stamp_bytes(annot) if annot is not None else pdf_ops.page_image_bytes(page, xref)
            dlg = ImageEditorDialog(data, self)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Edit Photo", f"This picture can't be edited:\n{e}")
            return
        if dlg.exec() != QDialog.Accepted:
            return
        png = dlg.result_png()
        try:
            if annot is not None:
                new = pdf_ops.replace_image_stamp(page, annot, png)
                self.selected = [(widget.page_index, new)]
            else:
                pdf_ops.replace_page_image(page, xref, png)
                self.selected = []
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Edit Photo", f"The edited picture couldn't be put back:\n{e}")
            return
        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    def try_edit_annot_text(self, widget, pos):
        """Double-click: edit a text box in place, or a sticky note's comment."""
        page = widget.page()
        annot = pdf_ops.find_annot_at(page, widget.to_pdf_point(pos))
        if annot is None:
            return
        if pdf_ops.is_text_box(annot):
            self.begin_text_edit(widget, annot=annot)
            return
        if pdf_ops.is_formula(annot):
            self.begin_formula_edit(widget, annot=annot)
            return
        if pdf_ops.is_image_stamp(annot):
            self.edit_photo(widget, annot=annot)
            return
        if annot.type[0] != fitz.PDF_ANNOT_TEXT:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Edit Note", "Comment:", annot.info.get("content", ""))
        if not ok:
            return
        annot.set_info(content=text)
        annot.update()
        self.document.snapshot()
        widget.render()
        self.refresh_thumbnail(widget.page_index)

    # ---------------------------------------------------------------
    # In-place text editing (Text / Formula tools, double-click a box)
    # ---------------------------------------------------------------

    TEXT_TOOLS = (Tool.TEXTBOX, Tool.FORMULA)

    def px_per_pt(self, widget):
        m = pdf_ops.coord_matrix(widget.page(), self.zoom)
        return math.hypot(m.a, m.b)

    def pdf_to_px(self, widget, point):
        p = fitz.Point(point) * pdf_ops.coord_matrix(widget.page(), self.zoom)
        return QPointF(p.x, p.y)

    def begin_text_edit(self, widget, origin_pdf=None, width_pt=None, annot=None):
        """Open the on-page editor: a new box at `origin_pdf` (wrapping at
        `width_pt` if the box was dragged out), or the existing box `annot`."""
        self.finish_text_editing()
        text, align, flags, original = "", 0, None, None
        if annot is not None:
            style = pdf_ops.freetext_style(annot)
            origin_pdf = fitz.Point(annot.rect.x0, annot.rect.y0)
            width_pt = annot.rect.width if style["fixed_width"] else None
            text, align = style["text"], style["align"]
            if self.current_tool not in self.TEXT_TOOLS:
                self.window.set_tool(Tool.TEXTBOX)
            # The toolbar shows, and live-edits, the style of the box being edited
            tool_style = self.window.tool_styles[self.current_tool]
            if style["fontname"] in fonts.available_fonts():
                tool_style["fontname"] = style["fontname"]
            tool_style["fontsize"] = int(round(style["fontsize"]))
            tool_style["color"] = tuple(round(c * 255) for c in style["color"])
            self.window._refresh_style_controls()
            original = (text, tool_style["fontname"], float(tool_style["fontsize"]), QColor.fromRgbF(*style["color"]).name())
            # Hide the saved box while its live copy is being edited
            flags = annot.flags
            self.document.doc.xref_set_key(annot.xref, "F", str(flags | fitz.PDF_ANNOT_IS_HIDDEN))
            widget.render()

        scale = self.px_per_pt(widget)
        origin_px = self.pdf_to_px(widget, origin_pdf)
        editor = InlineTextEditor(
            widget, QPoint(round(origin_px.x()), round(origin_px.y())), scale,
            self.current_fontname, self.current_fontsize, self.current_color,
            fixed_width_px=width_pt * scale if width_pt else None, text=text,
        )
        editor.finished.connect(self.finish_text_editing)
        editor.show()
        editor.setFocus()
        self.text_edit = {"widget": widget, "editor": editor, "origin": fitz.Point(origin_pdf), "width": width_pt,
                          "annot": annot, "align": align, "flags": flags, "original": original}
        self.set_hint("Type your text. Press Esc or click outside the box to finish.")

    def begin_formula_edit(self, widget, origin_pdf=None, annot=None):
        """Open the formula editor at `origin_pdf`, or on the formula `annot`
        to change it (it is hidden while being edited)."""
        self.finish_text_editing()
        text, flags = "", None
        fontsize, color = self.current_fontsize, self.current_color
        if annot is not None:
            data = pdf_ops.formula_data(annot) or {}
            text = data.get("source", annot.info.get("content", ""))
            fontsize = data.get("fontsize", fontsize)
            color = QColor(*data.get("color", (0, 0, 0)))
            origin_pdf = fitz.Point(annot.rect.x0, annot.rect.y0)
            if self.current_tool != Tool.FORMULA:
                self.window.set_tool(Tool.FORMULA)
            tool_style = self.window.tool_styles[Tool.FORMULA]
            tool_style["fontsize"] = int(round(fontsize))
            tool_style["color"] = (color.red(), color.green(), color.blue())
            self.window._refresh_style_controls()
            flags = annot.flags
            self.document.doc.xref_set_key(annot.xref, "F", str(flags | fitz.PDF_ANNOT_IS_HIDDEN))
            widget.render()
        origin_px = self.pdf_to_px(widget, origin_pdf)
        editor = FormulaEditor(widget, QPoint(round(origin_px.x()), round(origin_px.y())), self.px_per_pt(widget),
                               fontsize, color, text=text)
        editor.finished.connect(self.finish_text_editing)
        editor.show()
        editor.setFocus()
        self.text_edit = {"kind": "formula", "widget": widget, "editor": editor, "origin": fitz.Point(origin_pdf),
                          "annot": annot, "flags": flags,
                          "original": (text, float(fontsize), (color.red(), color.green(), color.blue()))}
        self.set_hint("Type LaTeX maths; the preview shows how it will look. Enter places it, Esc cancels.")

    def _finish_formula(self, state, accept):
        """Place (or update) the formula being edited. If it can't be
        rendered the editor stays open with the error, and None is returned."""
        editor, widget, annot = state["editor"], state["widget"], state["annot"]
        page = widget.page()
        source = editor.text()
        rgb = (editor.color.red(), editor.color.green(), editor.color.blue())
        unchanged = state["original"] == (source, editor.fontsize, rgb)

        def close_editor():
            editor.hide()
            editor.deleteLater()
            if annot is not None:
                self.document.doc.xref_set_key(annot.xref, "F", str(state["flags"]))

        if not accept or (annot is not None and unchanged):
            close_editor()
            widget.render()
            self.set_hint(TOOL_HINTS.get(self.current_tool, ""))
            if annot is not None:
                self.selected = [(widget.page_index, annot)]
                widget.update()
            return annot
        if not source:
            close_editor()
            if annot is not None:  # everything deleted: remove the formula
                page.delete_annot(annot)
                self.document.snapshot()
                self.refresh_thumbnail(widget.page_index)
            widget.render()
            return None
        rendered = editor.result()
        if rendered is None:
            from . import formula

            QGuiApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                rendered = formula.render(source, editor.fontsize, rgb)
            except formula.FormulaError as e:
                self.text_edit = state  # keep typing: show what is wrong
                editor.show_error(str(e))
                editor.setFocus()
                self.set_hint("This formula has an error; fix it, or press Esc to cancel.")
                return None
            finally:
                QGuiApplication.restoreOverrideCursor()
        close_editor()
        try:
            origin = state["origin"]
            if annot is not None:
                page.delete_annot(annot)
            annot = pdf_ops.add_formula(page, origin, rendered, source, editor.fontsize, rgb)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Error", f"Could not add the formula:\n{e}")
            widget.render()
            return None
        self.document.snapshot()
        self.refresh_thumbnail(widget.page_index)
        widget.render()
        self.window.set_tool(Tool.SELECT)
        annot = next((a for a in widget.page().annots() if a.xref == annot.xref), annot)
        self.selected = [(widget.page_index, annot)]
        widget.update()
        return annot

    def update_text_edit_style(self):
        """Toolbar font/size/colour changed: restyle the box being typed in."""
        if self.text_edit is None:
            return
        editor = self.text_edit["editor"]
        editor.set_style(self.current_fontname, self.current_fontsize, self.current_color)
        editor.setFocus()

    def finish_text_editing(self, accept=True):
        """Write the on-page editor's text (or formula) into the PDF, then
        select it so it can be moved or resized straight away. Returns the
        annotation. accept=False (Esc in the formula editor) cancels."""
        state, self.text_edit = self.text_edit, None
        if state is None:
            return None
        if state.get("kind") == "formula":
            return self._finish_formula(state, accept)
        editor, widget, annot = state["editor"], state["widget"], state["annot"]
        text = editor.toPlainText().rstrip()
        fontname, fontsize, color = editor.fontname, editor.fontsize, editor.color
        editor.hide()
        editor.deleteLater()
        page = widget.page()
        if annot is not None:
            self.document.doc.xref_set_key(annot.xref, "F", str(state["flags"]))

        changed = True
        try:
            if annot is not None and not text.strip():
                page.delete_annot(annot)
                annot = None
            elif annot is not None:
                if state["original"] == (text, fontname, fontsize, color.name()):
                    changed = False
                else:
                    w, h = pdf_ops.text_box_size(text, fontsize, fontname, state["width"])
                    o = state["origin"]
                    pdf_ops.set_freetext(annot, text, color, fontsize, fontname,
                                         fitz.Rect(o.x, o.y, o.x + w, o.y + h), align=state["align"])
            elif text.strip():
                annot = pdf_ops.add_text_box(page, state["origin"], text, color, fontsize, fontname,
                                             width=state["width"])
            else:
                widget.render()
                self.set_hint(TOOL_HINTS.get(self.current_tool, ""))
                return None  # empty new box: nothing to add, keep the Text tool
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save the text:\n{e}")
            widget.render()
            return None

        if changed:
            self.document.snapshot()
            self.refresh_thumbnail(widget.page_index)
        widget.render()
        self.window.set_tool(Tool.SELECT)
        if annot is not None:
            self.selected = [(widget.page_index, annot)]
            widget.update()
        return annot

    def _discard_text_editing(self):
        """Drop the editor without writing (the page widgets are about to be
        rebuilt, e.g. after undo or a page change)."""
        state, self.text_edit = self.text_edit, None
        if state is None:
            return
        state["editor"].hide()
        state["editor"].deleteLater()
        if state["annot"] is not None:
            try:
                self.document.doc.xref_set_key(state["annot"].xref, "F", str(state["flags"]))
            except Exception:
                pass

    # ---------------------------------------------------------------
    # Selection frame, resize handles, cursors
    # ---------------------------------------------------------------

    def annot_rect_px(self, widget, annot) -> QRectF:
        r = (fitz.Rect(annot.rect) * pdf_ops.coord_matrix(widget.page(), self.zoom)).normalize()
        return QRectF(r.x0, r.y0, r.width, r.height)

    def single_selection(self, widget):
        if len(self.selected) == 1 and self.selected[0][0] == widget.page_index:
            return self.selected[0][1]
        return None

    def selection_frame(self, widget):
        """(rect_px, show_handles) for the single selected annotation on this
        page, following any in-progress move or resize; None otherwise."""
        annot = self.single_selection(widget)
        if annot is None:
            return None
        if self.resize_state is not None and self.resize_preview_px is not None:
            return self.resize_preview_px, True
        rect = self.annot_rect_px(widget, annot)
        if self.select_dragging:
            rect.translate(self.select_offset_px.x(), self.select_offset_px.y())
        return rect, pdf_ops.is_resizable(annot)

    def handle_under(self, widget, pos):
        annot = self.single_selection(widget)
        if annot is None or not pdf_ops.is_resizable(annot):
            return None
        return handles.handle_at(self.annot_rect_px(widget, annot), pos)

    def hover_cursor(self, widget, pos):
        handle = self.handle_under(widget, pos)
        if handle:
            return handles.CURSORS[handle]
        if pdf_ops.find_annot_at(widget.page(), widget.to_pdf_point(pos)) is not None:
            return Qt.SizeAllCursor
        return Qt.ArrowCursor

    def nudge_selected(self, widget, dx, dy):
        moved = False
        for page_index, annot in self.selected:
            if page_index == widget.page_index:
                pdf_ops.move_annot(annot, dx, dy)
                moved = True
        if moved:
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
        handle = None if additive else self.handle_under(widget, pos)
        if handle:
            start = self.annot_rect_px(widget, self.selected[0][1])
            self.resize_state = {"handle": handle, "start": start}
            self.resize_preview_px = QRectF(start)
            return
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
        if self.resize_state is not None:
            self.resize_preview_px = handles.resized(self.resize_state["start"], self.resize_state["handle"], pos)
            widget.update()
            return
        if not self.select_dragging or not self.selected:
            return
        self.select_offset_px = pos - self._select_start_px
        widget.update()

    def end_select_drag(self, widget, pos):
        if self.resize_state is not None:
            rect_px = self.resize_preview_px
            start = self.resize_state["start"]
            self.resize_state = None
            self.resize_preview_px = None
            annot = self.single_selection(widget)
            if annot is None or rect_px is None or rect_px == start:
                widget.update()
                return
            tl = widget.to_pdf_point(QPoint(round(rect_px.left()), round(rect_px.top())))
            br = widget.to_pdf_point(QPoint(round(rect_px.right()), round(rect_px.bottom())))
            pdf_ops.resize_annot(annot, fitz.Rect(tl, br))
            self.document.snapshot()
            widget.render()
            self.refresh_thumbnail(widget.page_index)
            return
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
