import os
import tempfile

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QWidget, QLabel,
    QFormLayout, QLineEdit, QTableWidget, QTableWidgetItem, QDoubleSpinBox,
    QSpinBox, QHeaderView,
)
from PySide6.QtGui import QImage, QPainter, QPen, QColor, QIcon, QPixmap
from PySide6.QtCore import Qt, Signal


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


class PropertiesDialog(QDialog):
    """Edits PDF metadata (title/author/subject/keywords/creator); page count
    and file path are shown read-only."""

    def __init__(self, meta: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Document Properties")
        layout = QFormLayout(self)

        self.title_edit = QLineEdit(meta.get("title", ""))
        self.author_edit = QLineEdit(meta.get("author", ""))
        self.subject_edit = QLineEdit(meta.get("subject", ""))
        self.keywords_edit = QLineEdit(meta.get("keywords", ""))
        self.creator_edit = QLineEdit(meta.get("creator", ""))

        layout.addRow("Title:", self.title_edit)
        layout.addRow("Author:", self.author_edit)
        layout.addRow("Subject:", self.subject_edit)
        layout.addRow("Keywords:", self.keywords_edit)
        layout.addRow("Creator:", self.creator_edit)
        layout.addRow("Pages:", QLabel(str(meta.get("_page_count", "?"))))
        layout.addRow("File:", QLabel(meta.get("_path", "(unsaved)")))

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addRow(btn_row)

    def get_values(self) -> dict:
        return {
            "title": self.title_edit.text(),
            "author": self.author_edit.text(),
            "subject": self.subject_edit.text(),
            "keywords": self.keywords_edit.text(),
            "creator": self.creator_edit.text(),
        }


class ToolStylesDialog(QDialog):
    """Lets the user view/edit the remembered default style (color, width,
    opacity, font size) for every styled tool in one place."""

    def __init__(self, tool_styles: dict, tool_labels: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tool Styles")
        self.resize(520, 360)
        self.tool_styles = tool_styles
        self._rows = []

        layout = QVBoxLayout(self)
        table = QTableWidget(len(tool_styles), 5)
        table.setHorizontalHeaderLabels(["Tool", "Color", "Width", "Opacity", "Font Size"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)

        for row, (tool, style) in enumerate(tool_styles.items()):
            table.setItem(row, 0, QTableWidgetItem(tool_labels.get(tool, tool.name)))
            table.item(row, 0).setFlags(Qt.ItemIsEnabled)

            color_btn = QPushButton()
            color_btn.setFixedSize(24, 24)
            self._paint_color_btn(color_btn, style["color"])
            color_btn.clicked.connect(lambda _, t=tool, b=color_btn: self._pick_color(t, b))
            table.setCellWidget(row, 1, color_btn)

            width_spin = QDoubleSpinBox()
            width_spin.setRange(0.5, 30.0)
            width_spin.setValue(style["width"])
            width_spin.valueChanged.connect(lambda v, t=tool: self.tool_styles[t].__setitem__("width", v))
            table.setCellWidget(row, 2, width_spin)

            opacity_spin = QDoubleSpinBox()
            opacity_spin.setRange(0.05, 1.0)
            opacity_spin.setSingleStep(0.05)
            opacity_spin.setValue(style.get("opacity", 1.0))
            opacity_spin.valueChanged.connect(lambda v, t=tool: self.tool_styles[t].__setitem__("opacity", v))
            table.setCellWidget(row, 3, opacity_spin)

            fontsize_spin = QSpinBox()
            fontsize_spin.setRange(6, 96)
            fontsize_spin.setValue(style.get("fontsize", 12))
            fontsize_spin.valueChanged.connect(lambda v, t=tool: self.tool_styles[t].__setitem__("fontsize", v))
            table.setCellWidget(row, 4, fontsize_spin)

            self._rows.append(tool)

        layout.addWidget(table)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        row_layout = QHBoxLayout()
        row_layout.addStretch()
        row_layout.addWidget(close_btn)
        layout.addLayout(row_layout)

    def _paint_color_btn(self, btn, rgb255):
        pix = QPixmap(18, 18)
        pix.fill(QColor(*rgb255))
        btn.setIcon(QIcon(pix))

    def _pick_color(self, tool, btn):
        from PySide6.QtWidgets import QColorDialog
        current = QColor(*self.tool_styles[tool]["color"])
        color = QColorDialog.getColor(current, self, "Choose Color")
        if color.isValid():
            self.tool_styles[tool]["color"] = (color.red(), color.green(), color.blue())
            self._paint_color_btn(btn, self.tool_styles[tool]["color"])


class FindBar(QDialog):
    """Small non-modal find dialog: Ctrl+F opens it, Find Next/Previous walk
    matches across the active document's pages."""

    findRequested = Signal(str, bool)  # (query, forward)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find")
        self.setWindowFlag(Qt.Tool, True)
        layout = QHBoxLayout(self)
        self.query_edit = QLineEdit()
        self.query_edit.setMinimumWidth(220)
        self.query_edit.returnPressed.connect(lambda: self.findRequested.emit(self.query_edit.text(), True))
        prev_btn = QPushButton("Previous")
        prev_btn.clicked.connect(lambda: self.findRequested.emit(self.query_edit.text(), False))
        next_btn = QPushButton("Find Next")
        next_btn.clicked.connect(lambda: self.findRequested.emit(self.query_edit.text(), True))
        layout.addWidget(QLabel("Find:"))
        layout.addWidget(self.query_edit)
        layout.addWidget(prev_btn)
        layout.addWidget(next_btn)

    def focus_input(self):
        self.query_edit.selectAll()
        self.query_edit.setFocus()
