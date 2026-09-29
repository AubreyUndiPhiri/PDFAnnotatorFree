import os
import tempfile

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QWidget, QLabel, QFrame,
    QFormLayout, QLineEdit, QTableWidget, QTableWidgetItem, QDoubleSpinBox,
    QSpinBox, QHeaderView, QComboBox, QTextEdit, QColorDialog, QToolButton,
    QButtonGroup,
)
from PySide6.QtGui import QImage, QPainter, QPen, QColor, QIcon, QPixmap, QShortcut, QKeySequence
from PySide6.QtCore import Qt, Signal, QSize, QRectF

from . import fonts, icons, theme

ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT = 0, 1, 2  # PDF /Q values


def swatch_icon(color, size=36) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QColor(0, 0, 0, 40))
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size * 0.22, size * 0.22)
    painter.end()
    return QIcon(pix)


def _icon_button(icon_name, tooltip, checkable=False):
    btn = QToolButton()
    btn.setIcon(icons.icon(icon_name))
    btn.setIconSize(QSize(18, 18))
    btn.setToolTip(tooltip)
    btn.setCheckable(checkable)
    return btn


def _button_row(*buttons, leading=()):
    """Dialog footer: optional left-aligned buttons, then right-aligned ones."""
    row = QHBoxLayout()
    row.setSpacing(8)
    for btn in leading:
        row.addWidget(btn)
    row.addStretch()
    for btn in buttons:
        row.addWidget(btn)
    return row


def _primary(text):
    btn = QPushButton(text)
    btn.setObjectName("primary")
    btn.setDefault(True)
    return btn


def _header(title, subtitle=None):
    box = QVBoxLayout()
    box.setSpacing(2)
    heading = QLabel(title)
    heading.setObjectName("dialogTitle")
    box.addWidget(heading)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        box.addWidget(sub)
    return box


def _dialog_layout(dialog):
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(14)
    return layout


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------

class SignatureCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(520, 200)
        self.setCursor(Qt.CrossCursor)
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
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(15, 23, 42), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(self._last_point, pos)
        painter.end()
        self._last_point = pos
        self.update()

    def mouseReleaseEvent(self, event):
        self._last_point = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor(theme.BORDER_STRONG))
        painter.setBrush(QColor(theme.SURFACE))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        # signing line (not part of the saved image)
        baseline = self.height() - 48
        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1, Qt.DashLine))
        painter.drawLine(32, baseline, self.width() - 32, baseline)
        painter.setPen(QColor(theme.ICON_DISABLED))
        painter.drawText(32, baseline + 22, "Sign above the line")
        painter.drawImage(0, 0, self.image)


class SignaturePadDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Draw Signature")
        layout = _dialog_layout(self)
        layout.addLayout(_header("Draw your signature", "Use your mouse, pen or touchpad. You'll place it on the page next."))
        self.canvas = SignatureCanvas(self)
        layout.addWidget(self.canvas)
        clear_btn = QPushButton(icons.icon("rotate-left"), "Clear")
        clear_btn.clicked.connect(self.canvas.clear)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = _primary("Use Signature")
        ok_btn.clicked.connect(self.accept)
        layout.addLayout(_button_row(cancel_btn, ok_btn, leading=[clear_btn]))

    def is_empty(self) -> bool:
        return not self.canvas.has_ink()

    def save_to_temp_png(self) -> str:
        fd, path = tempfile.mkstemp(suffix=".png", prefix="signature_")
        os.close(fd)
        self.canvas.image.save(path, "PNG")
        return path


# ---------------------------------------------------------------------------
# Document properties
# ---------------------------------------------------------------------------

class PropertiesDialog(QDialog):
    """Edits PDF metadata (title/author/subject/keywords/creator); page count
    and file path are shown read-only."""

    def __init__(self, meta: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Document Properties")
        self.setMinimumWidth(460)
        layout = _dialog_layout(self)
        layout.addLayout(_header("Document properties"))

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.title_edit = QLineEdit(meta.get("title", ""))
        self.author_edit = QLineEdit(meta.get("author", ""))
        self.subject_edit = QLineEdit(meta.get("subject", ""))
        self.keywords_edit = QLineEdit(meta.get("keywords", ""))
        self.keywords_edit.setPlaceholderText("Separate keywords with commas")
        self.creator_edit = QLineEdit(meta.get("creator", ""))

        form.addRow("Title", self.title_edit)
        form.addRow("Author", self.author_edit)
        form.addRow("Subject", self.subject_edit)
        form.addRow("Keywords", self.keywords_edit)
        form.addRow("Creator", self.creator_edit)
        for label_text, value in (("Pages", str(meta.get("_page_count", "?"))),
                                  ("File", meta.get("_path") or "Not saved yet")):
            value_label = QLabel(value)
            value_label.setObjectName("muted")
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(label_text, value_label)
        layout.addLayout(form)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = _primary("Save")
        ok_btn.clicked.connect(self.accept)
        layout.addLayout(_button_row(cancel_btn, ok_btn))

    def get_values(self) -> dict:
        return {
            "title": self.title_edit.text(),
            "author": self.author_edit.text(),
            "subject": self.subject_edit.text(),
            "keywords": self.keywords_edit.text(),
            "creator": self.creator_edit.text(),
        }


# ---------------------------------------------------------------------------
# Tool styles
# ---------------------------------------------------------------------------

class ToolStylesDialog(QDialog):
    """Lets the user view/edit the remembered default style (color, width,
    opacity, font, font size) for every styled tool in one place."""

    def __init__(self, tool_styles: dict, tool_labels: dict, parent=None):
        from .tools import TOOL_ICONS

        super().__init__(parent)
        self.setWindowTitle("Tool Styles")
        self.resize(720, 520)
        self.tool_styles = tool_styles

        layout = _dialog_layout(self)
        layout.addLayout(_header("Tool styles", "Default colour, size and font each tool starts with."))

        table = QTableWidget(len(tool_styles), 6)
        table.setHorizontalHeaderLabels(["Tool", "Colour", "Width", "Opacity", "Font", "Font size"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(40)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.setShowGrid(False)
        table.setIconSize(QSize(18, 18))

        for row, (tool, style) in enumerate(tool_styles.items()):
            item = QTableWidgetItem(icons.icon(TOOL_ICONS[tool]), tool_labels.get(tool, tool.name))
            item.setFlags(Qt.ItemIsEnabled)
            table.setItem(row, 0, item)

            color_btn = QToolButton()
            color_btn.setObjectName("swatch")
            color_btn.setIconSize(QSize(18, 18))
            color_btn.setIcon(swatch_icon(QColor(*style["color"])))
            color_btn.clicked.connect(lambda _, t=tool, b=color_btn: self._pick_color(t, b))
            table.setCellWidget(row, 1, self._centered(color_btn))

            width_spin = QDoubleSpinBox()
            width_spin.setRange(0.5, 30.0)
            width_spin.setSuffix(" pt")
            width_spin.setDecimals(1)
            width_spin.setValue(style["width"])
            width_spin.valueChanged.connect(lambda v, t=tool: self.tool_styles[t].__setitem__("width", v))
            table.setCellWidget(row, 2, width_spin)

            opacity_spin = QSpinBox()
            opacity_spin.setRange(5, 100)
            opacity_spin.setSingleStep(5)
            opacity_spin.setSuffix(" %")
            opacity_spin.setValue(round(style.get("opacity", 1.0) * 100))
            opacity_spin.valueChanged.connect(lambda v, t=tool: self.tool_styles[t].__setitem__("opacity", v / 100))
            table.setCellWidget(row, 3, opacity_spin)

            font_combo = QComboBox()
            font_combo.addItems(fonts.available_fonts())
            font_combo.setCurrentText(style.get("fontname", fonts.DEFAULT_FONT))
            font_combo.currentTextChanged.connect(lambda v, t=tool: self.tool_styles[t].__setitem__("fontname", v))
            table.setCellWidget(row, 4, font_combo)

            fontsize_spin = QSpinBox()
            fontsize_spin.setRange(6, 96)
            fontsize_spin.setSuffix(" pt")
            fontsize_spin.setValue(style.get("fontsize", 12))
            fontsize_spin.valueChanged.connect(lambda v, t=tool: self.tool_styles[t].__setitem__("fontsize", v))
            table.setCellWidget(row, 5, fontsize_spin)

        for col, width in ((1, 70), (2, 90), (3, 84), (4, 130), (5, 84)):
            table.setColumnWidth(col, width)
        layout.addWidget(table)

        close_btn = _primary("Done")
        close_btn.clicked.connect(self.accept)
        layout.addLayout(_button_row(close_btn))

    @staticmethod
    def _centered(widget):
        holder = QWidget()
        box = QHBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(widget, alignment=Qt.AlignCenter)
        return holder

    def _pick_color(self, tool, btn):
        current = QColor(*self.tool_styles[tool]["color"])
        color = QColorDialog.getColor(current, self, "Choose Colour")
        if color.isValid():
            self.tool_styles[tool]["color"] = (color.red(), color.green(), color.blue())
            btn.setIcon(swatch_icon(color))


# ---------------------------------------------------------------------------
# Text editors
# ---------------------------------------------------------------------------

class AdvancedTextEditorDialog(QDialog):
    """Text editor used for text boxes, formulas and editing existing text.
    Offers only options that are actually written into the PDF: font, size,
    colour and alignment."""

    def __init__(self, initial_text="", initial_color=None, initial_fontsize=12, initial_fontname=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Text")
        self.resize(600, 380)
        self.selected_color_value = QColor(initial_color or theme.TEXT)
        self.selected_font_size_value = int(initial_fontsize or 12)

        self.layout = _dialog_layout(self)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        # Only fonts that can actually be written into the PDF are offered
        self.font_combo = QComboBox()
        self.font_combo.setMinimumWidth(150)
        self.font_combo.setToolTip("Font")
        self.font_combo.addItems(fonts.available_fonts())
        self.font_combo.setCurrentText(initial_fontname or fonts.DEFAULT_FONT)
        self.font_combo.currentTextChanged.connect(self._preview_font)
        self.size_combo = QComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.setToolTip("Font size (pt)")
        self.size_combo.addItems([str(v) for v in (8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 72)])
        self.size_combo.setCurrentText(str(self.selected_font_size_value))
        self.size_combo.setFixedWidth(72)
        self.size_combo.currentTextChanged.connect(self._preview_size)

        self.color_btn = QToolButton()
        self.color_btn.setObjectName("swatch")
        self.color_btn.setIconSize(QSize(18, 18))
        self.color_btn.setToolTip("Text colour")
        self.color_btn.setIcon(swatch_icon(self.selected_color_value))
        self.color_btn.clicked.connect(self._pick_color)

        self.align_group = QButtonGroup(self)
        self.left_align_btn = _icon_button("align-left", "Align left", checkable=True)
        self.center_align_btn = _icon_button("align-center", "Center", checkable=True)
        self.right_align_btn = _icon_button("align-right", "Align right", checkable=True)
        for value, btn in ((ALIGN_LEFT, self.left_align_btn), (ALIGN_CENTER, self.center_align_btn),
                           (ALIGN_RIGHT, self.right_align_btn)):
            self.align_group.addButton(btn, value)
        self.left_align_btn.setChecked(True)
        self.align_group.idClicked.connect(self._preview_alignment)

        toolbar.addWidget(self.font_combo)
        toolbar.addWidget(self.size_combo)
        toolbar.addSpacing(6)
        toolbar.addWidget(self.color_btn)
        toolbar.addSpacing(6)
        toolbar.addWidget(self.left_align_btn)
        toolbar.addWidget(self.center_align_btn)
        toolbar.addWidget(self.right_align_btn)
        toolbar.addStretch()
        self.layout.addLayout(toolbar)

        self.editor = QTextEdit(self)
        self.editor.setAcceptRichText(False)
        self.editor.setPlainText(initial_text)
        self.editor.setPlaceholderText("Type your text...")
        self.layout.addWidget(self.editor)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = _primary("Apply")
        ok_btn.clicked.connect(self.accept)
        self.layout.addLayout(_button_row(cancel_btn, ok_btn))

        self._apply_editor_defaults()

    def _apply_editor_defaults(self):
        self._preview_font(self.font_combo.currentText())
        self._preview_color()

    def _preview_font(self, name):
        font = self.editor.font()
        font.setFamilies(fonts.preview_families(name))
        font.setPointSize(self.selected_fontsize())
        self.editor.setFont(font)

    def _preview_size(self, _text):
        self._preview_font(self.font_combo.currentText())

    def _preview_color(self):
        self.editor.setStyleSheet(f"QTextEdit {{ color: {self.selected_color_value.name()}; padding: 8px; }}")

    def _preview_alignment(self, value):
        qt_align = {ALIGN_LEFT: Qt.AlignLeft, ALIGN_CENTER: Qt.AlignHCenter, ALIGN_RIGHT: Qt.AlignRight}[value]
        cursor = self.editor.textCursor()
        self.editor.selectAll()
        self.editor.setAlignment(qt_align)
        self.editor.setTextCursor(cursor)

    def _pick_color(self):
        color = QColorDialog.getColor(self.selected_color_value, self, "Choose Text Colour")
        if color.isValid():
            self.selected_color_value = color
            self.color_btn.setIcon(swatch_icon(color))
            self._preview_color()

    def text_value(self):
        return self.editor.toPlainText().strip()

    def selected_color(self):
        return self.selected_color_value

    def selected_fontsize(self):
        try:
            return int(self.size_combo.currentText())
        except ValueError:
            return int(self.selected_font_size_value)

    def selected_fontname(self):
        return self.font_combo.currentText()

    def selected_alignment(self):
        return self.align_group.checkedId()


class FloatingTextEditor(QFrame):
    """Compact card for typing text straight onto the page."""

    def __init__(self, parent=None, initial_text="", initial_color=None, initial_fontsize=12, initial_fontname=None):
        super().__init__(parent)
        self.setObjectName("floatingEditor")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.apply_callback = None
        self.cancel_callback = None
        self.selected_color_value = QColor(initial_color or theme.TEXT)
        self.selected_font_size_value = int(initial_fontsize or 12)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.editor = QTextEdit(self)
        self.editor.setPlainText(initial_text)
        self.editor.setPlaceholderText("Type your annotation...")
        self.editor.setMinimumSize(240, 100)
        self.editor.setAcceptRichText(False)
        preview_font = self.editor.font()
        preview_font.setFamilies(fonts.preview_families(initial_fontname or fonts.DEFAULT_FONT))
        preview_font.setPointSize(self.selected_font_size_value)
        self.editor.setFont(preview_font)
        self._preview_color()
        layout.addWidget(self.editor)

        self.color_btn = QToolButton()
        self.color_btn.setObjectName("swatch")
        self.color_btn.setIconSize(QSize(16, 16))
        self.color_btn.setToolTip("Text colour")
        self.color_btn.setIcon(swatch_icon(self.selected_color_value))
        self.color_btn.clicked.connect(self._pick_color)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel)
        self.apply_btn = _primary("Add")
        self.apply_btn.clicked.connect(self._apply)
        layout.addLayout(_button_row(self.cancel_btn, self.apply_btn, leading=[self.color_btn]))

    def set_apply_callback(self, callback):
        self.apply_callback = callback

    def set_cancel_callback(self, callback):
        self.cancel_callback = callback

    def _preview_color(self):
        self.editor.setStyleSheet(f"QTextEdit {{ color: {self.selected_color_value.name()}; }}")

    def _pick_color(self):
        color = QColorDialog.getColor(self.selected_color_value, self, "Choose Text Colour")
        if color.isValid():
            self.selected_color_value = color
            self.color_btn.setIcon(swatch_icon(color))
            self._preview_color()

    def text_value(self):
        return self.editor.toPlainText().strip()

    def selected_color(self):
        return self.selected_color_value

    def selected_fontsize(self):
        return self.selected_font_size_value

    def _apply(self):
        if self.apply_callback:
            self.apply_callback(self.text_value(), self.selected_color(), self.selected_fontsize())
        self.close()

    def _cancel(self):
        if self.cancel_callback:
            self.cancel_callback()
        self.close()


# ---------------------------------------------------------------------------
# Find
# ---------------------------------------------------------------------------

class FindBar(QDialog):
    """Small non-modal find dialog: Ctrl+F opens it, Find Next/Previous walk
    matches across the active document's pages."""

    findRequested = Signal(str, bool)  # (query, forward)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find")
        self.setWindowFlag(Qt.Tool, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        self.query_edit = QLineEdit()
        self.query_edit.setMinimumWidth(260)
        self.query_edit.setPlaceholderText("Find in document")
        self.query_edit.setClearButtonEnabled(True)
        self.query_edit.addAction(icons.icon("find"), QLineEdit.LeadingPosition)
        self.query_edit.returnPressed.connect(lambda: self.findRequested.emit(self.query_edit.text(), True))
        prev_btn = _icon_button("chevron-up", "Previous match (Shift+Enter)")
        back = QShortcut(QKeySequence("Shift+Return"), self.query_edit, context=Qt.WidgetShortcut)
        back.activated.connect(lambda: self.findRequested.emit(self.query_edit.text(), False))
        prev_btn.clicked.connect(lambda: self.findRequested.emit(self.query_edit.text(), False))
        next_btn = _icon_button("chevron-down", "Next match (Enter)")
        next_btn.clicked.connect(lambda: self.findRequested.emit(self.query_edit.text(), True))
        layout.addWidget(self.query_edit)
        layout.addWidget(prev_btn)
        layout.addWidget(next_btn)

    def focus_input(self):
        self.query_edit.selectAll()
        self.query_edit.setFocus()
