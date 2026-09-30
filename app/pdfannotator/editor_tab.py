"""Base class for the non-PDF tabs (Word documents, LaTeX sources) that sit
in the main window's tab bar next to the PDF tabs.

MainWindow routes the shared commands (save, undo, clipboard, find, print,
zoom) to the active EditorTab and hides the PDF-only tools while one is
active. Subclasses implement write() and the editing hooks they support."""
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QToolButton, QWidget,
)

from . import icons, theme


class EditorTab(QWidget):
    changed = Signal()                 # title, dirty state or status text changed
    open_path_requested = Signal(str)  # ask the main window to open a file in a tab
    icon_name = "note"
    kind_label = "Document"
    save_filters = "All Files (*)"
    default_suffix = ""
    save_suffixes = ()          # extensions write() understands; others get default_suffix
    supports_zoom = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = None
        self._dirty = False

    # ---- identity -----------------------------------------------------------
    @property
    def dirty(self) -> bool:
        return self._dirty

    def set_dirty(self, dirty: bool):
        if dirty != self._dirty:
            self._dirty = dirty
            self.changed.emit()

    def display_name(self) -> str:
        return os.path.basename(self.path) if self.path else f"Untitled {self.kind_label}"

    def status_text(self) -> str:
        return ""

    # ---- saving -------------------------------------------------------------
    def write(self, path):
        raise NotImplementedError

    def save(self) -> bool:
        if not self.path:
            return self.save_as()
        return self._write_to(self.path)

    def save_as(self, directory="") -> bool:
        start = (os.path.join(directory, os.path.basename(self.path) if self.path else self.display_name())
                 if directory else self.path or os.path.join(os.path.expanduser("~"), "Documents", self.display_name()))
        path, chosen = QFileDialog.getSaveFileName(self, f"Save {self.kind_label} As", start, self.save_filters)
        if not path:
            return False
        ext = os.path.splitext(path)[1].lower()
        if self.default_suffix and (not ext or (self.save_suffixes and ext not in self.save_suffixes)):
            path = (os.path.splitext(path)[0] if ext else path) + self.default_suffix
        return self._write_to(path)

    def _write_to(self, path) -> bool:
        try:
            self.write(path)
        except Exception as e:  # noqa: BLE001 - shown to the user
            QMessageBox.critical(self, "Could Not Save", f"Could not save {path}:\n{e}")
            return False
        self.path = path
        self.set_dirty(False)
        self.changed.emit()
        return True

    # ---- commands the main window forwards (override where supported) ----------
    def text_widget(self):
        """The QTextEdit / QPlainTextEdit that edit commands go to."""
        return None

    def undo(self):
        w = self.text_widget()
        if w is not None:
            w.undo()

    def redo(self):
        w = self.text_widget()
        if w is not None:
            w.redo()

    def cut(self):
        w = self.text_widget()
        if w is not None:
            w.cut()

    def copy(self):
        w = self.text_widget()
        if w is not None:
            w.copy()

    def paste(self):
        w = self.text_widget()
        if w is not None:
            w.paste()

    def select_all(self):
        w = self.text_widget()
        if w is not None:
            w.selectAll()

    def show_find(self):
        bar = getattr(self, "find_bar", None)
        if bar is not None:
            bar.open()

    def print_document(self):
        pass

    zoom = 1.0

    def set_zoom(self, zoom):
        pass

    def zoom_in(self):
        self.set_zoom(min(4.0, self.zoom * 1.1))

    def zoom_out(self):
        self.set_zoom(max(0.4, self.zoom / 1.1))

    def reset_zoom(self):
        self.set_zoom(1.0)

    def confirm_close(self) -> bool:
        """True if the tab may close (saved, discarded or clean)."""
        if not self.dirty:
            return True
        resp = QMessageBox.question(self, "Unsaved Changes", f'Save changes to "{self.display_name()}" before closing?',
                                    QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if resp == QMessageBox.Save:
            return self.save()
        return resp == QMessageBox.Discard


class FindReplaceBar(QWidget):
    """Find / replace strip for a QTextEdit or QPlainTextEdit."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setObjectName("findReplaceBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find")
        self.find_edit.setClearButtonEnabled(True)
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Replace with")
        self.case_check = QCheckBox("Match case")
        self.word_check = QCheckBox("Whole words")
        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")

        def button(text, slot, icon=None, tip=None):
            btn = QToolButton()
            if icon:
                btn.setIcon(icons.icon(icon))
            else:
                btn.setText(text)
            btn.setToolTip(tip or text)
            btn.clicked.connect(slot)
            return btn

        row.addWidget(self.find_edit, 2)
        row.addWidget(button("Previous", lambda: self.find(False), "chevron-up", "Previous match (Shift+Enter)"))
        row.addWidget(button("Next", lambda: self.find(True), "chevron-down", "Next match (Enter)"))
        row.addWidget(self.replace_edit, 2)
        row.addWidget(button("Replace", self.replace_one))
        row.addWidget(button("Replace All", self.replace_all))
        row.addWidget(self.case_check)
        row.addWidget(self.word_check)
        row.addWidget(self.count_label)
        row.addWidget(button("Close", self.close_bar, "close", "Close (Esc)"))
        self.find_edit.returnPressed.connect(lambda: self.find(True))
        QShortcut(QKeySequence("Shift+Return"), self.find_edit, lambda: self.find(False))
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close_bar)
        self.hide()

    def open(self):
        selected = self.editor.textCursor().selectedText()
        if selected and " " not in selected:
            self.find_edit.setText(selected)
        self.show()
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def close_bar(self):
        self.hide()
        self.editor.setFocus()

    def _flags(self, forward=True):
        flags = QTextDocument.FindFlag(0)
        if not forward:
            flags |= QTextDocument.FindBackward
        if self.case_check.isChecked():
            flags |= QTextDocument.FindCaseSensitively
        if self.word_check.isChecked():
            flags |= QTextDocument.FindWholeWords
        return flags

    def find(self, forward=True) -> bool:
        query = self.find_edit.text()
        if not query:
            return False
        doc = self.editor.document()
        found = doc.find(query, self.editor.textCursor(), self._flags(forward))
        if found.isNull():  # wrap around
            start = QTextCursor(doc)
            if not forward:
                start.movePosition(QTextCursor.End)
            found = doc.find(query, start, self._flags(forward))
        if found.isNull():
            self.count_label.setText("No matches")
            return False
        self.editor.setTextCursor(found)
        self.editor.ensureCursorVisible()
        self.count_label.setText("")
        return True

    def replace_one(self):
        cursor = self.editor.textCursor()
        query = self.find_edit.text()
        same = (cursor.selectedText() == query if self.case_check.isChecked()
                else cursor.selectedText().lower() == query.lower())
        if cursor.hasSelection() and same:
            cursor.insertText(self.replace_edit.text())
        self.find(True)

    def replace_all(self):
        query = self.find_edit.text()
        if not query:
            return
        doc = self.editor.document()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        count = 0
        found = doc.find(query, cursor, self._flags(True))
        while not found.isNull():
            found.insertText(self.replace_edit.text())
            count += 1
            found = doc.find(query, found, self._flags(True))
        cursor.endEditBlock()
        self.count_label.setText(f"{count} replaced")


class PaperCanvas(QWidget):
    """The backdrop behind a page (Word) or preview pages (LaTeX): the same
    frosted / clay canvas as the PDF view, with a soft shadow under each
    child widget named "paper"."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.CANVAS))
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        for child in self.findChildren(QWidget, "paper"):
            if not child.isVisible():
                continue
            r = child.geometry()
            if child.parentWidget() is not self:
                r.moveTopLeft(child.parentWidget().mapTo(self, r.topLeft()))
            for spread, alpha in ((6, 10), (4, 16), (2, 26)):
                shadow = QColor(theme.PAGE_SHADOW)
                shadow.setAlpha(alpha)
                painter.setBrush(shadow)
                painter.drawRoundedRect(r.adjusted(-spread, -spread + 2, spread, spread + 2), spread, spread)
