import os
import tempfile

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QWidget, QLabel,
    QFormLayout, QLineEdit, QTableWidget, QTableWidgetItem, QDoubleSpinBox,
    QSpinBox, QHeaderView, QComboBox, QColorDialog, QToolButton, QFileDialog, QMessageBox,
)
from PySide6.QtGui import QImage, QPainter, QPen, QColor, QIcon, QPixmap, QShortcut, QKeySequence
from PySide6.QtCore import Qt, Signal, QSize, QRectF

from . import fonts, icons, theme

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
            fonts.fill_font_combo(font_combo, style.get("fontname", fonts.DEFAULT_FONT))
            font_combo.textActivated.connect(lambda v, t=tool, c=font_combo: self._set_font(t, c, v))
            table.setCellWidget(row, 4, font_combo)

            fontsize_spin = QSpinBox()
            fontsize_spin.setRange(6, 96)
            fontsize_spin.setSuffix(" pt")
            fontsize_spin.setValue(style.get("fontsize", 12))
            fontsize_spin.valueChanged.connect(lambda v, t=tool: self.tool_styles[t].__setitem__("fontsize", v))
            table.setCellWidget(row, 5, fontsize_spin)

        for col, width in ((1, 70), (2, 90), (3, 84), (4, 170), (5, 84)):
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

    def _set_font(self, tool, combo, name):
        if name in fonts.available_fonts():
            self.tool_styles[tool]["fontname"] = name
        else:
            combo.setCurrentText(self.tool_styles[tool].get("fontname", fonts.DEFAULT_FONT))

    def _pick_color(self, tool, btn):
        current = QColor(*self.tool_styles[tool]["color"])
        color = QColorDialog.getColor(current, self, "Choose Colour")
        if color.isValid():
            self.tool_styles[tool]["color"] = (color.red(), color.green(), color.blue())
            btn.setIcon(swatch_icon(color))


# ---------------------------------------------------------------------------
# Handwriting font
# ---------------------------------------------------------------------------

class HandwritingFontDialog(QDialog):
    """Guided steps to turn a filled-in glyph sheet into a font the app can
    use straight away: save the sheet, fill it in, choose the scan, create."""

    fontCreated = Signal(str)  # family name

    SAMPLE = "The quick brown fox jumps over the lazy dog 0123456789"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Handwriting Font")
        self.setMinimumWidth(560)
        self.scan_path = None
        layout = _dialog_layout(self)
        layout.addLayout(_header(
            "Create your handwriting font",
            "Turn your own handwriting into a font you can type with. It takes a sheet of paper and a pen.",
        ))

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignTop)

        save_btn = QPushButton(icons.icon("print"), "Save Glyph Sheet...")
        save_btn.clicked.connect(self._save_sheet)
        form.addRow("1. Print", self._step("Save the sheet, then print it at 100% (actual size).", save_btn))
        form.addRow("2. Write", self._step("Write one character in each box with a dark pen, sitting on the "
                                           "lower dashed line. Leave a box empty to skip it."))
        scan_btn = QPushButton(icons.icon("image"), "Choose Scan or Photo...")
        scan_btn.clicked.connect(self._choose_scan)
        self.scan_label = QLabel("No image chosen")
        self.scan_label.setObjectName("muted")
        self.scan_label.setWordWrap(True)
        form.addRow("3. Scan", self._step("Scan it, or photograph it flat and in focus with all four black "
                                          "corner squares visible.", scan_btn, self.scan_label))
        self.name_edit = QLineEdit(fonts.HANDWRITING_FONT)
        self.name_edit.setMaxLength(40)
        form.addRow("Font name", self.name_edit)
        layout.addLayout(form)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setObjectName("muted")
        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setMinimumHeight(0)
        self.preview.setStyleSheet(f"QLabel {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER};"
                                   f" border-radius: 8px; padding: 12px; color: {theme.TEXT}; }}")
        self.preview.hide()
        layout.addWidget(self.preview)
        layout.addWidget(self.result_label)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.create_btn = _primary("Create Font")
        self.create_btn.setEnabled(False)
        self.create_btn.clicked.connect(self._create)
        layout.addLayout(_button_row(self.cancel_btn, self.create_btn))

    @staticmethod
    def _step(text, *widgets):
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        label = QLabel(text)
        label.setWordWrap(True)
        box.addWidget(label)
        for w in widgets:
            box.addWidget(w, alignment=Qt.AlignLeft)
        return holder

    def _save_sheet(self):
        from PySide6.QtCore import QStandardPaths, QUrl
        from PySide6.QtGui import QDesktopServices
        from .handwriting.sheet import make_sheet

        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        name = f"{self.name_edit.text().strip() or fonts.HANDWRITING_FONT}_glyph_sheet.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Save Glyph Sheet", os.path.join(docs, name), "PDF Files (*.pdf)")
        if not path:
            return
        make_sheet(path, self.name_edit.text().strip() or fonts.HANDWRITING_FONT)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _choose_scan(self):
        from PySide6.QtCore import QStandardPaths

        start = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        path, _ = QFileDialog.getOpenFileName(self, "Choose the Scanned Sheet", start,
                                              "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)")
        if not path:
            return
        self.scan_path = path
        self.scan_label.setText(os.path.basename(path))
        self.create_btn.setEnabled(True)

    def _create(self):
        from PySide6.QtWidgets import QApplication
        from .handwriting.builder import BuildError, build_font

        family = "".join(ch for ch in self.name_edit.text().strip() if ch.isalnum() or ch in " -_") or fonts.HANDWRITING_FONT
        out = fonts.USER_FONTS_DIR / f"{family}.ttf"
        if out.exists():
            resp = QMessageBox.question(self, "Replace Font", f'A font named "{family}" already exists. Replace it?')
            if resp != QMessageBox.Yes:
                return
        self.result_label.setText("Reading your handwriting...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            result = build_font(self.scan_path, out, family)
        except BuildError as e:
            self.result_label.setText("")
            QMessageBox.warning(self, "Couldn't Create the Font", str(e))
            return
        except Exception as e:
            self.result_label.setText("")
            QMessageBox.critical(self, "Couldn't Create the Font", f"Something went wrong reading the image:\n{e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        family = fonts.install_font_file(result["path"])
        self.preview.setFont(fonts.preview_font(family, 30))
        self.preview.setText(self.SAMPLE)
        self.preview.show()
        summary = f'"{family}" is ready and now in every font list. {len(result["found"])} characters found.'
        if result["skipped"]:
            summary += f' Empty boxes skipped: {" ".join(result["skipped"])}'
        self.result_label.setText(summary)
        self.cancel_btn.hide()
        self.create_btn.setText("Done")
        self.create_btn.clicked.disconnect()
        self.create_btn.clicked.connect(self.accept)
        self.fontCreated.emit(family)


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
