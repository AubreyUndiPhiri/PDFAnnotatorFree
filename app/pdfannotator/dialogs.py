import os
import tempfile

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QWidget, QLabel
from PySide6.QtGui import QImage, QPainter, QPen, QColor
from PySide6.QtCore import Qt


class SignatureCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(500, 200)
        self.image = QImage(self.size(), QImage.Format_ARGB32)
        self.image.fill(Qt.transparent)
        self._last_point = None

    def clear(self):
        self.image.fill(Qt.transparent)
        self.update()

    def has_ink(self) -> bool:
        for y in range(0, self.image.height(), 4):
            for x in range(0, self.image.width(), 4):
                if self.image.pixelColor(x, y).alpha() > 0:
                    return True
        return False

    def _pos(self, event):
        return event.position().toPoint() if hasattr(event, "position") else event.pos()

    def mousePressEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._last_point = self._pos(event)

    def mouseMoveEvent(self, event):
        if self._last_point is None or not (event.buttons() & Qt.LeftButton):
            return
        pos = self._pos(event)
        painter = QPainter(self.image)
        pen = QPen(QColor(10, 10, 10), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(self._last_point, pos)
        painter.end()
        self._last_point = pos
        self.update()

    def mouseReleaseEvent(self, event):
        self._last_point = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.white)
        painter.drawImage(0, 0, self.image)
        painter.setPen(QColor(180, 180, 180))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class SignaturePadDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Draw Your Signature")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Draw your signature below, then click \"Use Signature\":"))
        self.canvas = SignatureCanvas(self)
        layout.addWidget(self.canvas)
        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.canvas.clear)
        ok_btn = QPushButton("Use Signature")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def is_empty(self) -> bool:
        return not self.canvas.has_ink()

    def save_to_temp_png(self) -> str:
        fd, path = tempfile.mkstemp(suffix=".png", prefix="signature_")
        os.close(fd)
        self.canvas.image.save(path, "PNG")
        return path
