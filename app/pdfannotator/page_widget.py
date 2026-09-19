import fitz
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QPixmap, QImage, QPen, QColor
from PySide6.QtCore import Qt, QPoint, QRect

from . import pdf_ops
from .tools import Tool

# Tools that collect a freehand point path while the mouse is dragging
PATH_TOOLS = {Tool.INK, Tool.MARKER, Tool.ERASER, Tool.LASSO}

# Tools drawn as a simple rubber-band rectangle/line while dragging, committed
# as a single (p1, p2) pair on release
RUBBERBAND_TOOLS = {
    Tool.RECT, Tool.ELLIPSE, Tool.LINE, Tool.ARROW, Tool.TEXTBOX, Tool.STAMP,
    Tool.IMAGE_STAMP, Tool.HIGHLIGHT, Tool.UNDERLINE, Tool.STRIKEOUT,
    Tool.DIMENSION, Tool.SNAPSHOT, Tool.CROP, Tool.MEASURE, Tool.EXTRACT_TEXT,
    Tool.FORMULA,
}


class PageWidget(QWidget):
    """Renders one PDF page and handles all mouse interaction for annotation tools."""

    def __init__(self, controller, page_index: int, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.page_index = page_index
        self.pixmap = None
        self.rendered = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)

        self._dragging = False
        self._drag_start = None
        self._drag_current = None
        self._path_points = []

        self._polygon_points_px = []
        self._polygon_rubber_px = None

        self._pan_last_pos = None
        self._flash_rect = None

        self.ensure_placeholder()

    def page(self) -> fitz.Page:
        return self.controller.document.page(self.page_index)

    def zoom(self) -> float:
        return self.controller.zoom

    def page_size_px(self):
        page = self.page()
        mat = pdf_ops.coord_matrix(page, self.zoom())
        r = page.rect * mat
        r = fitz.Rect(r).normalize()
        return max(1, int(round(r.width))), max(1, int(round(r.height)))

    def ensure_placeholder(self):
        w, h = self.page_size_px()
        self.setFixedSize(w, h)

    def render(self):
        page = self.page()
        mat = pdf_ops.render_matrix(self.zoom())
        pix = page.get_pixmap(matrix=mat, alpha=False, annots=not self.controller.hide_annotations)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        self.pixmap = QPixmap.fromImage(img)
        self.setFixedSize(self.pixmap.size())
        self.rendered = True
        self.update()

    def invalidate(self):
        self.rendered = False
        self.ensure_placeholder()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.rendered and self.pixmap is not None:
            painter.drawPixmap(0, 0, self.pixmap)
        else:
            painter.fillRect(self.rect(), QColor(235, 235, 235))
            painter.setPen(QColor(160, 160, 160))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        if self.rendered:
            for sel_page, annot in self.controller.selected:
                if sel_page != self.page_index:
                    continue
                try:
                    r = fitz.Rect(annot.rect) * pdf_ops.coord_matrix(self.page(), self.zoom())
                    r = r.normalize()
                    offset = QPoint(0, 0)
                    if self.controller.select_dragging:
                        offset = self.controller.select_offset_px
                    pen = QPen(QColor(0, 120, 255), 2, Qt.DashLine)
                    painter.setPen(pen)
                    painter.drawRect(int(r.x0) + offset.x(), int(r.y0) + offset.y(),
                                      max(1, int(r.width)), max(1, int(r.height)))
                except Exception:
                    pass

        if self._flash_rect is not None and self.rendered:
            try:
                r = fitz.Rect(self._flash_rect) * pdf_ops.coord_matrix(self.page(), self.zoom())
                r = r.normalize()
                pen = QPen(QColor(255, 200, 0), 3)
                painter.setPen(pen)
                painter.drawRect(int(r.x0), int(r.y0), max(1, int(r.width)), max(1, int(r.height)))
            except Exception:
                pass

        if self._polygon_points_px and self.rendered:
            pen = QPen(self.controller.current_color, max(1, int(self.controller.current_width)))
            painter.setPen(pen)
            pts = self._polygon_points_px
            for i in range(1, len(pts)):
                painter.drawLine(pts[i - 1], pts[i])
            if self._polygon_rubber_px is not None:
                painter.drawLine(pts[-1], self._polygon_rubber_px)
            for p in pts:
                painter.drawEllipse(p, 2, 2)

        if self._dragging and self.rendered:
            pen = QPen(self.controller.current_color, max(1, int(self.controller.current_width)))
            painter.setPen(pen)
            tool = self.controller.current_tool
            if tool in PATH_TOOLS and len(self._path_points) > 1:
                for i in range(1, len(self._path_points)):
                    painter.drawLine(self._path_points[i - 1], self._path_points[i])
            elif self._drag_start and self._drag_current:
                r = QRect(self._drag_start, self._drag_current).normalized()
                if tool in (Tool.LINE, Tool.ARROW, Tool.DIMENSION):
                    painter.drawLine(self._drag_start, self._drag_current)
                elif tool == Tool.ELLIPSE:
                    painter.drawEllipse(r)
                else:
                    painter.drawRect(r)
        painter.end()

    def to_pdf_point(self, qpoint: QPoint) -> fitz.Point:
        return pdf_ops.pixel_to_pdf(self.page(), self.zoom(), qpoint.x(), qpoint.y())

    @staticmethod
    def _event_pos(event) -> QPoint:
        return event.position().toPoint() if hasattr(event, "position") else event.pos()

    # ---------------------------------------------------------------
    # Mouse handling
    # ---------------------------------------------------------------

    def mousePressEvent(self, event):
        if not self.rendered or event.button() not in (Qt.LeftButton, Qt.RightButton):
            return
        self.setFocus()
        tool = self.controller.current_tool
        pos = self._event_pos(event)

        if event.button() == Qt.RightButton:
            if tool == Tool.ZOOM:
                self.controller.zoom_click(self, pos, zoom_in=False)
            return

        if tool == Tool.SELECT:
            additive = bool(event.modifiers() & Qt.ControlModifier)
            self.controller.begin_select_drag(self, pos, additive)
            return

        if tool == Tool.NOTE:
            self.controller.place_note(self, pos)
            return

        if tool == Tool.ZOOM:
            self.controller.zoom_click(self, pos, zoom_in=True)
            return

        if tool == Tool.POINTER:
            self.controller.inspect_annot(self, pos)
            return

        if tool == Tool.PAN:
            self._pan_last_pos = pos
            return

        if tool == Tool.POLYGON:
            self._polygon_points_px.append(pos)
            self._polygon_rubber_px = pos
            self.update()
            return

        self._dragging = True
        self._drag_start = pos
        self._drag_current = pos
        if tool in PATH_TOOLS:
            self._path_points = [pos]
        self.update()

    def mouseMoveEvent(self, event):
        pos = self._event_pos(event)
        tool = self.controller.current_tool

        if tool == Tool.LASER_POINTER:
            self.controller.update_laser_pointer(self, pos)

        if tool == Tool.SELECT:
            self.controller.update_select_drag(self, pos)
            return

        if tool == Tool.POLYGON:
            if self._polygon_points_px:
                self._polygon_rubber_px = pos
                self.update()
            return

        if tool == Tool.PAN:
            if self._pan_last_pos is not None and (event.buttons() & Qt.LeftButton):
                delta = pos - self._pan_last_pos
                self.controller.pan_scroll(delta.x(), delta.y())
                self._pan_last_pos = pos
            return

        if not self._dragging:
            return
        self._drag_current = pos
        if tool in PATH_TOOLS:
            self._path_points.append(pos)
        if tool == Tool.MEASURE:
            p1 = self.to_pdf_point(self._drag_start)
            p2 = self.to_pdf_point(pos)
            self.controller.update_measure(self, p1, p2)
        self.update()

    def leaveEvent(self, event):
        if self.controller.current_tool == Tool.LASER_POINTER:
            self.controller.hide_laser_pointer()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if self.controller.current_tool == Tool.POLYGON and self._polygon_points_px:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._finish_polygon()
                return
            if event.key() == Qt.Key_Escape:
                self._polygon_points_px = []
                self._polygon_rubber_px = None
                self.update()
                return
        super().keyPressEvent(event)

    def _finish_polygon(self):
        if len(self._polygon_points_px) < 3:
            self._polygon_points_px = []
            self._polygon_rubber_px = None
            self.update()
            return
        pts = [self.to_pdf_point(p) for p in self._polygon_points_px]
        self._polygon_points_px = []
        self._polygon_rubber_px = None
        self.update()
        self.controller.commit_polygon(self, pts)

    def mouseReleaseEvent(self, event):
        pos = self._event_pos(event)
        tool = self.controller.current_tool

        if tool == Tool.SELECT:
            self.controller.end_select_drag(self, pos)
            return

        if tool == Tool.PAN:
            self._pan_last_pos = None
            return

        if not self._dragging:
            return
        self._dragging = False
        start, end = self._drag_start, pos
        self._drag_start = None
        self._drag_current = None
        path_points = self._path_points
        self._path_points = []
        self.update()

        if tool in PATH_TOOLS:
            if len(path_points) < 2:
                return
            pts = [self.to_pdf_point(p) for p in path_points]
            if tool == Tool.INK:
                self.controller.commit_ink(self, pts)
            elif tool == Tool.MARKER:
                self.controller.commit_marker(self, pts)
            elif tool == Tool.ERASER:
                self.controller.commit_eraser(self, pts)
            elif tool == Tool.LASSO:
                self.controller.commit_lasso(self, pts)
            return

        if (start - end).manhattanLength() < 3 and tool not in (
            Tool.TEXTBOX, Tool.STAMP, Tool.IMAGE_STAMP, Tool.FORMULA,
        ):
            return

        p1 = self.to_pdf_point(start)
        p2 = self.to_pdf_point(end)

        if tool == Tool.DIMENSION:
            self.controller.commit_dimension(self, p1, p2)
        elif tool == Tool.SNAPSHOT:
            self.controller.commit_snapshot(self, p1, p2)
        elif tool == Tool.CROP:
            self.controller.commit_crop(self, p1, p2)
        elif tool == Tool.EXTRACT_TEXT:
            self.controller.commit_extract_text(self, p1, p2)
        elif tool == Tool.MEASURE:
            pass  # live readout only, nothing committed
        else:
            self.controller.commit_drag_tool(self, tool, p1, p2)

    def mouseDoubleClickEvent(self, event):
        pos = self._event_pos(event)
        tool = self.controller.current_tool
        if tool == Tool.SELECT:
            self.controller.try_edit_annot_text(self, pos)
        elif tool == Tool.POLYGON:
            self._finish_polygon()
