import fitz
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QPixmap, QImage, QPen, QColor
from PySide6.QtCore import Qt, QPoint, QRect

from . import pdf_ops
from .tools import Tool


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
        self._ink_points = []

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
        pix = page.get_pixmap(matrix=mat, alpha=False)
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

        sel = self.controller.selected
        if sel is not None and sel[0] == self.page_index and self.rendered:
            _, annot = sel
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

        if self._dragging and self.rendered:
            pen = QPen(self.controller.current_color, max(1, int(self.controller.current_width)))
            painter.setPen(pen)
            tool = self.controller.current_tool
            if tool == Tool.INK and len(self._ink_points) > 1:
                for i in range(1, len(self._ink_points)):
                    painter.drawLine(self._ink_points[i - 1], self._ink_points[i])
            elif self._drag_start and self._drag_current:
                r = QRect(self._drag_start, self._drag_current).normalized()
                if tool in (Tool.LINE, Tool.ARROW):
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

    def mousePressEvent(self, event):
        if not self.rendered or event.button() != Qt.LeftButton:
            return
        self.setFocus()
        tool = self.controller.current_tool
        pos = self._event_pos(event)

        if tool == Tool.SELECT:
            self.controller.begin_select_drag(self, pos)
            return

        if tool == Tool.NOTE:
            self.controller.place_note(self, pos)
            return

        self._dragging = True
        self._drag_start = pos
        self._drag_current = pos
        if tool == Tool.INK:
            self._ink_points = [pos]
        self.update()

    def mouseMoveEvent(self, event):
        pos = self._event_pos(event)
        tool = self.controller.current_tool

        if tool == Tool.SELECT:
            self.controller.update_select_drag(self, pos)
            return

        if not self._dragging:
            return
        self._drag_current = pos
        if tool == Tool.INK:
            self._ink_points.append(pos)
        self.update()

    def mouseReleaseEvent(self, event):
        pos = self._event_pos(event)
        tool = self.controller.current_tool

        if tool == Tool.SELECT:
            self.controller.end_select_drag(self, pos)
            return

        if not self._dragging:
            return
        self._dragging = False
        start, end = self._drag_start, pos
        self._drag_start = None
        self._drag_current = None
        ink_points = self._ink_points
        self._ink_points = []
        self.update()

        if tool == Tool.INK:
            if len(ink_points) < 2:
                return
            pts = [self.to_pdf_point(p) for p in ink_points]
            self.controller.commit_ink(self, pts)
            return

        if (start - end).manhattanLength() < 3 and tool not in (
            Tool.TEXTBOX, Tool.STAMP, Tool.IMAGE_STAMP,
        ):
            return

        p1 = self.to_pdf_point(start)
        p2 = self.to_pdf_point(end)
        self.controller.commit_drag_tool(self, tool, p1, p2)

    def mouseDoubleClickEvent(self, event):
        pos = self._event_pos(event)
        if self.controller.current_tool == Tool.SELECT:
            self.controller.try_edit_annot_text(self, pos)
