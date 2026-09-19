from PySide6.QtWidgets import QWidget, QMenu
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import Qt, QPoint


class GuideLine(QWidget):
    """A draggable, non-printing alignment guide (horizontal or vertical),
    purely a view aid -- never written to the PDF."""

    THICKNESS = 6

    def __init__(self, parent, orientation, pos, on_remove):
        super().__init__(parent)
        self.orientation = orientation  # "h" or "v"
        self.on_remove = on_remove
        self._dragging = False
        self.setCursor(Qt.SizeVerCursor if orientation == "h" else Qt.SizeHorCursor)
        self.setMouseTracking(True)
        self.set_position(pos)
        self.show()
        self.raise_()

    def set_position(self, pos):
        parent = self.parentWidget()
        if parent is None:
            return
        if self.orientation == "h":
            self.setGeometry(0, int(pos - self.THICKNESS / 2), parent.width(), self.THICKNESS)
        else:
            self.setGeometry(int(pos - self.THICKNESS / 2), 0, self.THICKNESS, parent.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        color = QColor(0, 200, 220, 160)
        painter.fillRect(self.rect(), Qt.transparent)
        mid = self.THICKNESS // 2
        pen_rect = self.rect()
        if self.orientation == "h":
            painter.fillRect(0, mid - 1, pen_rect.width(), 2, color)
        else:
            painter.fillRect(mid - 1, 0, 2, pen_rect.height(), color)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_start_global = event.globalPosition().toPoint()
            self._drag_start_geo = self.geometry()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        delta = event.globalPosition().toPoint() - self._drag_start_global
        if self.orientation == "h":
            self.move(self._drag_start_geo.x(), self._drag_start_geo.y() + delta.y())
        else:
            self.move(self._drag_start_geo.x() + delta.x(), self._drag_start_geo.y())

    def mouseReleaseEvent(self, event):
        self._dragging = False

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        remove_action = menu.addAction("Remove Guide")
        action = menu.exec(event.globalPos())
        if action == remove_action:
            self.on_remove(self)
            self.deleteLater()


class LaserDot(QWidget):
    """Purely visual presentation aid -- a glowing dot that follows the cursor
    while the Laser Pointer tool is active. Never written to the PDF."""

    RADIUS = 9

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(self.RADIUS * 2, self.RADIUS * 2)
        self.hide()

    def move_to(self, point: QPoint):
        self.move(point.x() - self.RADIUS, point.y() - self.RADIUS)
        self.show()
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 30, 30, 90))
        painter.drawEllipse(0, 0, self.RADIUS * 2, self.RADIUS * 2)
        painter.setBrush(QColor(255, 30, 30, 220))
        painter.drawEllipse(self.RADIUS // 2, self.RADIUS // 2, self.RADIUS, self.RADIUS)
