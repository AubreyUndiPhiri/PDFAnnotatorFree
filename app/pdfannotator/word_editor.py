"""The Word document tab: view and edit .docx files (and .odt / .html /
.md / .txt) inside the app.

The page is a QTextEdit sized like the document's page and laid on the same
canvas as the PDF view; word_io.py converts to and from .docx. Pages are
shown as one continuous sheet (page breaks are marked with a dashed line);
Print and Export PDF lay the document out on real pages."""
import os
import re

from PySide6.QtCore import QEvent, QMarginsF, QSize, QSizeF, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QDesktopServices, QFont, QImage, QKeySequence, QPageSize, QPainter,
    QPen, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextDocumentWriter, QTextFormat,
    QTextImageFormat, QTextLength, QTextListFormat, QTextTableFormat,
)
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QFileDialog, QFontComboBox, QFormLayout, QHBoxLayout, QInputDialog,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTextEdit, QToolBar, QVBoxLayout, QWidget,
)

from . import icons, word_io
from .dialogs import _button_row, _dialog_layout, _header, _primary
from .editor_tab import EditorTab, FindReplaceBar, PaperCanvas

A4 = word_io.PageSetup(595.3, 841.9, (72.0, 72.0, 72.0, 72.0))
STYLES = ["Normal", "Title", "Heading 1", "Heading 2", "Heading 3", "Heading 4", "Quote", "Code"]
_STYLE_LOOK = {  # name: (heading level, size, bold, italic, colour, family)
    "Title": (1, 26, False, False, "#000000", None),
    "Heading 1": (1, 16, True, False, "#2f5496", None),
    "Heading 2": (2, 13, True, False, "#2f5496", None),
    "Heading 3": (3, 12, True, False, "#1f3763", None),
    "Heading 4": (4, 11, True, True, "#2f5496", None),
    "Quote": (0, None, False, True, "#404040", None),
    "Code": (0, 10, False, False, "#000000", "Consolas"),
}
SIZES = [8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 24, 28, 36, 48, 72]
OPEN_SUFFIXES = (".docx", ".odt", ".html", ".htm", ".md", ".markdown", ".txt")
_WRITER_FORMATS = {".odt": b"ODF", ".html": b"HTML", ".htm": b"HTML", ".md": b"markdown",
                   ".markdown": b"markdown", ".txt": b"plaintext"}


class PageEdit(QTextEdit):
    """The sheet of paper. It grows with its text instead of scrolling, so
    the tab's scroll area scrolls the page on its canvas like the PDF view."""

    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.setObjectName("paper")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameStyle(QTextEdit.NoFrame)
        self.setAcceptRichText(True)
        self.setTabChangesFocus(False)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        self.setMouseTracking(True)
        self.min_height = 1000
        self.key_actions = {}  # "Ctrl+B" -> QAction

    # ---- the tab's shortcuts win over the main window's while typing ------------
    def _action_for(self, event):
        return self.key_actions.get(QKeySequence(event.keyCombination()).toString())

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride and self._action_for(event) is not None:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        action = self._action_for(event)
        if action is not None and action.isEnabled():
            action.trigger()
            return
        super().keyPressEvent(event)

    def setDocument(self, doc):
        super().setDocument(doc)
        doc.documentLayout().documentSizeChanged.connect(lambda _size: self.fit())
        self.fit()

    def fit(self):
        height = max(self.min_height, int(self.document().size().height()) + 4)
        if height != self.height():
            self.setFixedHeight(height)

    # ---- pasting images ----------------------------------------------------
    def canInsertFromMimeData(self, source):
        return source.hasImage() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasImage() and not source.hasHtml():
            self.tab.insert_image(QImage(source.imageData()))
            return
        if source.hasUrls() and all(u.isLocalFile() and QImage(u.toLocalFile()).width() > 0 for u in source.urls()):
            for url in source.urls():
                self.tab.insert_image(QImage(url.toLocalFile()))
            return
        super().insertFromMimeData(source)

    # ---- links: Ctrl+click opens them, like Word ----------------------------------
    def mouseReleaseEvent(self, event):
        href = self.anchorAt(event.position().toPoint())
        if href and event.modifiers() & Qt.ControlModifier and not self.textCursor().hasSelection():
            QDesktopServices.openUrl(QUrl(href))
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        href = self.anchorAt(event.position().toPoint())
        self.viewport().setCursor(Qt.PointingHandCursor if href and event.modifiers() & Qt.ControlModifier
                                  else Qt.IBeamCursor)
        if href:
            self.setToolTip(f"{href}\nCtrl+click to open")
        super().mouseMoveEvent(event)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu(event.pos())
        self.tab.extend_context_menu(menu, self.cursorForPosition(event.pos()))
        menu.exec(event.globalPos())

    # ---- page-break markers ------------------------------------------------
    def paintEvent(self, event):
        super().paintEvent(event)
        doc = self.document()
        layout = doc.documentLayout()
        painter = None
        block = doc.begin()
        while block.isValid():
            if block.blockFormat().pageBreakPolicy() & QTextFormat.PageBreak_AlwaysBefore:
                if painter is None:
                    painter = QPainter(self.viewport())
                    painter.setPen(QPen(QColor(120, 130, 160), 1, Qt.DashLine))
                    font = QFont(self.font())
                    font.setPointSize(7)
                    painter.setFont(font)
                y = int(layout.blockBoundingRect(block).top()) - 3
                painter.drawLine(12, y, self.width() - 12, y)
                painter.drawText(self.width() // 2 - 30, y - 3, "Page break")
            block = block.next()
        if painter is not None:
            painter.end()


class TableSizeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Insert Table")
        layout = _dialog_layout(self)
        layout.addLayout(_header("Insert table"))
        form = QFormLayout()
        self.rows = QSpinBox()
        self.rows.setRange(1, 200)
        self.rows.setValue(3)
        self.cols = QSpinBox()
        self.cols.setRange(1, 30)
        self.cols.setValue(3)
        form.addRow("Rows", self.rows)
        form.addRow("Columns", self.cols)
        layout.addLayout(form)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = _primary("Insert")
        ok.clicked.connect(self.accept)
        layout.addLayout(_button_row(cancel, ok))


class WordTab(EditorTab):
    icon_name = "file-word"
    kind_label = "Word Document"
    save_filters = ("Word Document (*.docx);;OpenDocument Text (*.odt);;Web Page (*.html);;"
                    "Markdown (*.md);;Plain Text (*.txt)")
    default_suffix = ".docx"
    save_suffixes = (".docx",) + tuple(_WRITER_FORMATS)

    def __init__(self, parent=None, path=None):
        super().__init__(parent)
        self.page_setup = A4
        self.base_path = None
        self._images = 0
        self._words = 0

        self.page = PageEdit(self)
        self.canvas = PaperCanvas()
        self.canvas.setObjectName("wordCanvas")
        row = QHBoxLayout(self.canvas)
        row.setContentsMargins(24, 24, 24, 40)
        row.addStretch(1)
        row.addWidget(self.page, 0, Qt.AlignTop)
        row.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("wordScroll")
        self.scroll.setWidget(self.canvas)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameStyle(QScrollArea.NoFrame)

        self.find_bar = FindReplaceBar(self.page, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._build_toolbars(layout)
        layout.addWidget(self.find_bar)
        layout.addWidget(self.scroll, 1)

        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(400)
        self._count_timer.timeout.connect(self._count_words)
        self.page.cursorPositionChanged.connect(self._sync_block_controls)
        self.page.currentCharFormatChanged.connect(self._sync_char_controls)
        self.page.cursorPositionChanged.connect(self._keep_cursor_visible)

        if path:
            self.load(path)
        else:
            self._set_document(self._blank_document(), A4)

    # ------------------------------------------------------------ documents
    def _blank_document(self):
        doc = QTextDocument(self)
        doc.setDefaultFont(QFont(*word_io.DEFAULT_FONT[:1], round(word_io.DEFAULT_FONT[1])))
        cursor = QTextCursor(doc)
        cf = QTextCharFormat()
        cf.setFontFamilies([word_io.DEFAULT_FONT[0]])
        cf.setFontPointSize(word_io.DEFAULT_FONT[1])
        cursor.setBlockCharFormat(cf)
        bf = QTextBlockFormat()
        bf.setProperty(word_io.STYLE_PROP, "Normal")
        bf.setBottomMargin(8 * word_io.PX_PER_PT)
        cursor.setBlockFormat(bf)
        doc.setModified(False)
        return doc

    def load(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".doc":
            raise ValueError("Old Word 97-2003 (.doc) files can't be opened. Open it in Word and save it as .docx.")
        if ext == ".docx":
            doc, page = word_io.load_docx(path)
            doc.setParent(self)
            self.base_path = path
        else:
            doc, page = self._blank_document(), A4
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            if ext in (".html", ".htm"):
                doc.setHtml(text)
            elif ext in (".md", ".markdown"):
                doc.setMarkdown(text)
            elif ext == ".odt":
                raise ValueError("OpenDocument files can be saved from here but not opened. "
                                 "Save it as .docx in LibreOffice first.")
            else:
                doc.setPlainText(text)
            word_io.apply_page(doc, page)
            doc.setModified(False)
        self.path = path
        self._set_document(doc, page)

    def _set_document(self, doc, page):
        self.document = doc
        self.page_setup = page
        word_io.apply_page(doc, page)
        self.page.setFixedWidth(page.width_px)
        self.page.min_height = round(page.height_pt * word_io.PX_PER_PT)
        self.page.setDocument(doc)
        doc.setModified(False)  # laying out the page above counts as a change
        doc.modificationChanged.connect(self.set_dirty)
        doc.contentsChanged.connect(self._count_timer.start)
        self._count_words()
        self.set_dirty(False)
        self._sync_block_controls()
        self._sync_char_controls(self.page.currentCharFormat())

    def write(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".docx":
            base = self.base_path if self.base_path and os.path.isfile(self.base_path) else None
            word_io.save_docx(self.document, path, base=base, page=self.page_setup)
            self.base_path = path
        elif ext in _WRITER_FORMATS:
            writer = QTextDocumentWriter(path, _WRITER_FORMATS[ext])
            if not writer.write(self.document):
                raise OSError(writer.device().errorString() if writer.device() else "write failed")
        else:
            raise ValueError(f"Can't save as {ext or 'a file without an extension'}.")
        self.document.setModified(False)

    def text_widget(self):
        return self.page

    def status_text(self):
        pages = max(1, round(self.document.size().height() / max(1, self.page.min_height) + 0.49))
        return f"{self._words:,} words · about {pages} page{'s' if pages != 1 else ''}"

    def _count_words(self):
        self._words = len(re.findall(r"\w+", self.document.toPlainText()))
        self.changed.emit()

    def _keep_cursor_visible(self):
        rect = self.page.cursorRect()
        pos = self.page.mapTo(self.canvas, rect.center())
        self.scroll.ensureVisible(pos.x(), pos.y(), 40, 60)

    # ------------------------------------------------------------ toolbars
    def _build_toolbars(self, layout):
        def bar():
            tb = QToolBar()
            tb.setObjectName("editorBar")
            tb.setIconSize(QSize(18, 18))
            tb.setMovable(False)
            layout.addWidget(tb)
            return tb

        def act(tb, text, icon, slot, shortcut=None, checkable=False):
            a = QAction(icons.icon(icon), text, self)
            a.setToolTip(text + (f"  ({QKeySequence(shortcut).toString(QKeySequence.NativeText)})" if shortcut else ""))
            if shortcut:  # handled by the page itself, see PageEdit.event
                self.page.key_actions[QKeySequence(shortcut).toString()] = a
            a.setCheckable(checkable)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        top = bar()
        act(top, "Undo", "undo", self.page.undo)
        act(top, "Redo", "redo", self.page.redo)
        top.addSeparator()
        self.style_combo = QComboBox()
        self.style_combo.addItems(STYLES)
        self.style_combo.setToolTip("Paragraph style")
        self.style_combo.setMinimumWidth(110)
        self.style_combo.activated.connect(lambda i: self.apply_style(self.style_combo.itemText(i)))
        top.addWidget(self.style_combo)
        self.font_combo = QFontComboBox()
        self.font_combo.setToolTip("Font")
        self.font_combo.setMaximumWidth(190)
        self.font_combo.currentFontChanged.connect(self._set_family)
        top.addWidget(self.font_combo)
        self.size_combo = QComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.addItems([f"{s:g}" for s in SIZES])
        self.size_combo.setFixedWidth(64)
        self.size_combo.setToolTip("Font size")
        self.size_combo.activated.connect(lambda _i: self._set_size(self.size_combo.currentText()))
        self.size_combo.lineEdit().returnPressed.connect(lambda: self._set_size(self.size_combo.currentText()))
        top.addWidget(self.size_combo)
        top.addSeparator()
        self.bold_act = act(top, "Bold", "bold", self._toggle_bold, "Ctrl+B", True)
        self.italic_act = act(top, "Italic", "italic", lambda c: self._merge(italic=c), "Ctrl+I", True)
        self.underline_act = act(top, "Underline", "text-underline", lambda c: self._merge(underline=c), "Ctrl+U", True)
        self.strike_act = act(top, "Strikethrough", "strikeout", lambda c: self._merge(strike=c), None, True)
        self.super_act = act(top, "Superscript", "superscript", lambda c: self._set_valign(c, True), None, True)
        self.sub_act = act(top, "Subscript", "subscript", lambda c: self._set_valign(c, False), None, True)
        top.addSeparator()
        act(top, "Text Colour...", "text-color", self._pick_text_color)
        act(top, "Highlight...", "highlight", self._pick_highlight)
        act(top, "Clear Formatting", "clear-format", self.clear_formatting, "Ctrl+Space")

        second = bar()
        group = QActionGroup(self)
        self.align_acts = {}
        for text, icon, flag, key in (("Align Left", "align-left", Qt.AlignLeft, "Ctrl+L"),
                                      ("Center", "align-center", Qt.AlignHCenter, "Ctrl+E"),
                                      ("Align Right", "align-right", Qt.AlignRight, "Ctrl+R"),
                                      ("Justify", "align-justify", Qt.AlignJustify, "Ctrl+J")):
            a = act(second, text, icon, lambda _c, f=flag: self._set_alignment(f), key, True)
            group.addAction(a)
            self.align_acts[flag] = a
        second.addSeparator()
        self.bullets_act = act(second, "Bulleted List", "list-bullet", lambda: self.toggle_list(False), None, True)
        self.numbers_act = act(second, "Numbered List", "list-ordered", lambda: self.toggle_list(True), None, True)
        act(second, "Decrease Indent", "outdent", lambda: self.change_indent(-1), "Ctrl+Shift+M")
        act(second, "Increase Indent", "indent", lambda: self.change_indent(1), "Ctrl+M")
        second.addSeparator()
        act(second, "Insert Table...", "table", self.insert_table_dialog)
        act(second, "Insert Picture...", "image", self.insert_image_dialog)
        act(second, "Insert Link...", "link", self.insert_link, "Ctrl+K")
        act(second, "Page Break", "page-break", self.insert_page_break, "Ctrl+Return")
        second.addSeparator()
        act(second, "Find and Replace...", "find", self.show_find, "Ctrl+H")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        second.addWidget(spacer)
        act(second, "Print...", "print", self.print_document)
        act(second, "Export as PDF...", "file-pdf", self.export_pdf)

    # ------------------------------------------------------------ syncing the controls
    def _sync_char_controls(self, fmt):
        self.bold_act.setChecked(fmt.fontWeight() >= QFont.DemiBold)
        self.italic_act.setChecked(fmt.fontItalic())
        self.underline_act.setChecked(fmt.fontUnderline())
        self.strike_act.setChecked(fmt.fontStrikeOut())
        self.super_act.setChecked(fmt.verticalAlignment() == QTextCharFormat.AlignSuperScript)
        self.sub_act.setChecked(fmt.verticalAlignment() == QTextCharFormat.AlignSubScript)
        families = fmt.fontFamilies()
        family = families[0] if families else self.document.defaultFont().family() if hasattr(self, "document") else ""
        self.font_combo.blockSignals(True)
        self.font_combo.setCurrentFont(QFont(family))
        self.font_combo.blockSignals(False)
        size = fmt.fontPointSize() or (self.document.defaultFont().pointSizeF() if hasattr(self, "document") else 11)
        self.size_combo.setEditText(f"{size:g}")

    def _sync_block_controls(self):
        cursor = self.page.textCursor()
        bf = cursor.blockFormat()
        align = bf.alignment() & Qt.AlignHorizontal_Mask
        flag = Qt.AlignLeft if align in (Qt.AlignLeft, Qt.AlignLeading) else align
        if flag in self.align_acts:
            self.align_acts[flag].setChecked(True)
        name = bf.property(word_io.STYLE_PROP)
        if not isinstance(name, str) or name not in STYLES:
            level = bf.headingLevel()
            name = f"Heading {level}" if 1 <= level <= 4 else "Normal"
        self.style_combo.setCurrentText(name)
        lst = cursor.currentList()
        numbered = lst is not None and lst.format().style() in (
            QTextListFormat.ListDecimal, QTextListFormat.ListLowerAlpha, QTextListFormat.ListUpperAlpha,
            QTextListFormat.ListLowerRoman, QTextListFormat.ListUpperRoman)
        self.bullets_act.setChecked(lst is not None and not numbered)
        self.numbers_act.setChecked(numbered)

    # ------------------------------------------------------------ character formatting
    def _merge_format(self, cf):
        cursor = self.page.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        cursor.mergeCharFormat(cf)
        self.page.mergeCurrentCharFormat(cf)
        self.page.setFocus()

    def _toggle_bold(self, checked):
        cf = QTextCharFormat()
        cf.setFontWeight(QFont.Bold if checked else QFont.Normal)
        self._merge_format(cf)

    def _merge(self, italic=None, underline=None, strike=None):
        cf = QTextCharFormat()
        if italic is not None:
            cf.setFontItalic(italic)
        if underline is not None:
            cf.setFontUnderline(underline)
        if strike is not None:
            cf.setFontStrikeOut(strike)
        self._merge_format(cf)

    def _set_valign(self, checked, superscript):
        cf = QTextCharFormat()
        if checked:
            cf.setVerticalAlignment(QTextCharFormat.AlignSuperScript if superscript else QTextCharFormat.AlignSubScript)
        else:
            cf.setVerticalAlignment(QTextCharFormat.AlignNormal)
        self._merge_format(cf)

    def _set_family(self, font):
        cf = QTextCharFormat()
        cf.setFontFamilies([font.family()])
        self._merge_format(cf)

    def _set_size(self, text):
        try:
            size = float(text)
        except ValueError:
            return
        if 1 <= size <= 400:
            cf = QTextCharFormat()
            cf.setFontPointSize(size)
            self._merge_format(cf)

    def _pick_text_color(self):
        color = QColorDialog.getColor(self.page.currentCharFormat().foreground().color(), self, "Text Colour")
        if color.isValid():
            cf = QTextCharFormat()
            cf.setForeground(color)
            self._merge_format(cf)

    def _pick_highlight(self):
        color = QColorDialog.getColor(QColor("#ffff00"), self, "Highlight Colour",
                                      QColorDialog.ShowAlphaChannel)
        if color.isValid():
            cf = QTextCharFormat()
            if color.alpha() == 0:
                cf.setBackground(Qt.NoBrush)
            else:
                cf.setBackground(color)
            self._merge_format(cf)

    def clear_formatting(self):
        cursor = self.page.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.BlockUnderCursor)
        cf = QTextCharFormat()
        cf.setFontFamilies([self.document.defaultFont().family()])
        cf.setFontPointSize(self.document.defaultFont().pointSizeF() or word_io.DEFAULT_FONT[1])
        cursor.setCharFormat(cf)
        self.page.setCurrentCharFormat(cf)

    # ------------------------------------------------------------ paragraph formatting
    def _selected_blocks(self, cursor=None):
        cursor = cursor or self.page.textCursor()
        doc = self.document
        block = doc.findBlock(cursor.selectionStart())
        end = doc.findBlock(cursor.selectionEnd())
        while block.isValid():
            yield block
            if block == end:
                break
            block = block.next()

    def _set_alignment(self, flag):
        self.page.setAlignment(flag)
        self.page.setFocus()

    def apply_style(self, name):
        level, size, bold, italic, color, family = _STYLE_LOOK.get(name, (0, None, False, False, None, None))
        cursor = self.page.textCursor()
        cursor.beginEditBlock()
        base_size = self.document.defaultFont().pointSizeF() or word_io.DEFAULT_FONT[1]
        for block in list(self._selected_blocks(cursor)):
            c = QTextCursor(block)
            bf = block.blockFormat()
            was_quote = bf.property(word_io.STYLE_PROP) == "Quote"
            bf.setHeadingLevel(level)
            bf.setProperty(word_io.STYLE_PROP, name)
            if name == "Quote":
                bf.setLeftMargin(36 * word_io.PX_PER_PT)
            elif was_quote:
                bf.setLeftMargin(0)
            c.setBlockFormat(bf)
            cf = QTextCharFormat()
            cf.setFontPointSize(size or base_size)
            cf.setFontWeight(QFont.Bold if bold else QFont.Normal)
            cf.setFontItalic(italic)
            cf.setForeground(QColor(color or "#000000"))
            cf.setFontFamilies([family or self.document.defaultFont().family()])
            c.setPosition(block.position())
            c.setPosition(block.position() + block.length() - 1, QTextCursor.KeepAnchor)
            c.mergeCharFormat(cf)
            c.mergeBlockCharFormat(cf)
        cursor.endEditBlock()
        self.page.setFocus()

    def toggle_list(self, numbered):
        cursor = self.page.textCursor()
        style = QTextListFormat.ListDecimal if numbered else QTextListFormat.ListDisc
        current = cursor.currentList()
        cursor.beginEditBlock()
        if current is not None and (current.format().style() == style or
                                    (not numbered and current.format().style() in word_io._BULLETS)):
            for block in list(self._selected_blocks(cursor)):
                lst = block.textList()
                if lst is not None:
                    lst.remove(block)
                bf = block.blockFormat()
                bf.setIndent(0)
                bf.clearProperty(word_io.NUM_PROP)
                QTextCursor(block).setBlockFormat(bf)
        elif current is not None:
            fmt = current.format()
            fmt.setStyle(style)
            current.setFormat(fmt)
            self._forget_numbering(cursor)
        else:
            fmt = QTextListFormat()
            fmt.setStyle(style)
            fmt.setIndent(1)
            cursor.createList(fmt)
            self._forget_numbering(cursor)
        cursor.endEditBlock()
        self._sync_block_controls()
        self.page.setFocus()

    def _forget_numbering(self, cursor):
        """A list changed here no longer matches the Word numbering it was read with."""
        for block in list(self._selected_blocks(cursor)):
            bf = block.blockFormat()
            if bf.hasProperty(word_io.NUM_PROP):
                bf.clearProperty(word_io.NUM_PROP)
                QTextCursor(block).setBlockFormat(bf)

    def change_indent(self, step):
        cursor = self.page.textCursor()
        cursor.beginEditBlock()
        lst = cursor.currentList()
        if lst is not None:
            fmt = QTextListFormat(lst.format())
            fmt.setIndent(max(1, fmt.indent() + step))
            if fmt.style() in word_io._BULLETS:
                fmt.setStyle(word_io._BULLETS[(fmt.indent() - 1) % 3])
            cursor.createList(fmt)
            self._forget_numbering(cursor)
        else:
            for block in list(self._selected_blocks(cursor)):
                bf = block.blockFormat()
                bf.setLeftMargin(max(0.0, bf.leftMargin() + step * 36 * word_io.PX_PER_PT))
                QTextCursor(block).setBlockFormat(bf)
        cursor.endEditBlock()
        self.page.setFocus()

    # ------------------------------------------------------------ inserting
    def insert_page_break(self):
        cursor = self.page.textCursor()
        cursor.beginEditBlock()
        cursor.insertBlock()
        bf = cursor.blockFormat()
        bf.setPageBreakPolicy(QTextFormat.PageBreak_AlwaysBefore)
        cursor.setBlockFormat(bf)
        cursor.endEditBlock()
        self.page.viewport().update()

    def insert_table_dialog(self):
        dlg = TableSizeDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        self.insert_table(dlg.rows.value(), dlg.cols.value())

    def insert_table(self, rows, cols):
        fmt = QTextTableFormat()
        fmt.setBorder(1)
        fmt.setBorderBrush(QColor("#9aa0a6"))
        fmt.setBorderCollapse(True)
        fmt.setCellPadding(4)
        fmt.setCellSpacing(0)
        fmt.setWidth(QTextLength(QTextLength.PercentageLength, 100))
        self.page.textCursor().insertTable(rows, cols, fmt)
        self.page.setFocus()

    def insert_image_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Insert Picture", "",
                                              "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if path:
            image = QImage(path)
            if image.isNull():
                QMessageBox.warning(self, "Insert Picture", f"{path} could not be read as an image.")
                return
            self.insert_image(image)

    def insert_image(self, image):
        if image.isNull():
            return
        self._images += 1
        name = f"inserted-image-{self._images}"
        self.document.addResource(QTextDocument.ImageResource, QUrl(name), image)
        fmt = QTextImageFormat()
        fmt.setName(name)
        frame = self.document.rootFrame().frameFormat()
        width = self.page.width() - frame.leftMargin() - frame.rightMargin()
        scale = min(1.0, width / max(1, image.width()))
        fmt.setWidth(image.width() * scale)
        fmt.setHeight(image.height() * scale)
        self.page.textCursor().insertImage(fmt)
        self.page.setFocus()

    def insert_link(self):
        cursor = self.page.textCursor()
        current = cursor.charFormat().anchorHref()
        url, ok = QInputDialog.getText(self, "Insert Link", "Address (web page or e-mail):", text=current or "https://")
        if not ok or not url.strip() or url.strip() == "https://":
            return
        url = url.strip()
        if "@" in url and not url.startswith("mailto:") and "://" not in url:
            url = "mailto:" + url
        cf = QTextCharFormat()
        cf.setAnchor(True)
        cf.setAnchorHref(url)
        cf.setFontUnderline(True)
        cf.setForeground(QColor("#0563c1"))
        if cursor.hasSelection():
            cursor.mergeCharFormat(cf)
        else:
            cursor.insertText(url, cf)
            plain = QTextCharFormat()
            plain.setAnchor(False)
            plain.setFontUnderline(False)
            plain.setForeground(QColor("#000000"))
            self.page.setCurrentCharFormat(plain)

    # ------------------------------------------------------------ context menu (tables, images)
    def _image_at(self, cursor):
        probe = QTextCursor(cursor)
        for move in (QTextCursor.NextCharacter, QTextCursor.PreviousCharacter):
            c = QTextCursor(probe)
            c.movePosition(move, QTextCursor.KeepAnchor)
            if c.charFormat().isImageFormat():
                return c
        return None

    def extend_context_menu(self, menu, cursor):
        image_cursor = self._image_at(cursor)
        if image_cursor is not None:
            menu.addSeparator()
            menu.addAction("Picture Size...", lambda: self._resize_image(image_cursor))
        table = cursor.currentTable()
        if table is not None:
            cell = table.cellAt(cursor)
            menu.addSeparator()
            sub = menu.addMenu(icons.icon("table"), "Table")
            sub.addAction("Insert Row Above", lambda: table.insertRows(cell.row(), 1))
            sub.addAction("Insert Row Below", lambda: table.insertRows(cell.row() + cell.rowSpan(), 1))
            sub.addAction("Insert Column Left", lambda: table.insertColumns(cell.column(), 1))
            sub.addAction("Insert Column Right", lambda: table.insertColumns(cell.column() + cell.columnSpan(), 1))
            sub.addSeparator()
            sub.addAction("Delete Row", lambda: table.removeRows(cell.row(), 1))
            sub.addAction("Delete Column", lambda: table.removeColumns(cell.column(), 1))
            sub.addSeparator()
            merge = sub.addAction("Merge Selected Cells", lambda: table.mergeCells(self.page.textCursor()))
            merge.setEnabled(self.page.textCursor().hasComplexSelection())
            split = sub.addAction("Split Cell", lambda: table.splitCell(cell.row(), cell.column(), 1, 1))
            split.setEnabled(cell.rowSpan() > 1 or cell.columnSpan() > 1)
            sub.addSeparator()
            sub.addAction("Delete Table", lambda: table.removeRows(0, table.rows()))

    def _resize_image(self, cursor):
        fmt = cursor.charFormat().toImageFormat()
        resource = self.document.resource(QTextDocument.ImageResource, QUrl(fmt.name()))
        natural = resource.width() if hasattr(resource, "width") and resource.width() else fmt.width() or 100
        current = round(100 * (fmt.width() or natural) / natural)
        pct, ok = QInputDialog.getInt(self, "Picture Size", "Size (% of the original):", current, 5, 400)
        if not ok:
            return
        ratio = (fmt.height() / fmt.width()) if fmt.width() and fmt.height() else (
            resource.height() / resource.width() if hasattr(resource, "height") and resource.width() else 1)
        fmt.setWidth(natural * pct / 100)
        fmt.setHeight(natural * pct / 100 * ratio)
        cursor.setCharFormat(fmt)

    # ------------------------------------------------------------ printing / PDF
    def _paginated_copy(self):
        """A copy of the document laid out on real pages of the page size."""
        copy = self.document.clone()
        block = self.document.begin()
        while block.isValid():  # clone() doesn't bring the pictures along
            it = block.begin()
            while not it.atEnd():
                cf = it.fragment().charFormat()
                if cf.isImageFormat():
                    name = cf.toImageFormat().name()
                    copy.addResource(QTextDocument.ImageResource, QUrl(name),
                                     self.document.resource(QTextDocument.ImageResource, QUrl(name)))
                it += 1
            block = block.next()
        width = self.page_setup.width_pt * word_io.PX_PER_PT
        height = self.page_setup.height_pt * word_io.PX_PER_PT
        copy.setPageSize(QSizeF(width, height))
        word_io.apply_page(copy, self.page_setup)
        return copy

    def _page_layout(self, printer):
        printer.setPageSize(QPageSize(QSizeF(self.page_setup.width_pt, self.page_setup.height_pt), QPageSize.Point))
        printer.setPageMargins(QMarginsF(0, 0, 0, 0))

    def print_document(self):
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.HighResolution)
        self._page_layout(printer)
        dlg = QPrintDialog(printer, self)
        if dlg.exec() != QDialog.Accepted:
            return
        self._paginated_copy().print_(printer)

    def export_pdf(self, path=None, ask_to_open=True):
        from PySide6.QtGui import QPdfWriter

        if not path:
            start = os.path.splitext(self.path)[0] + ".pdf" if self.path else os.path.join(
                os.path.expanduser("~"), "Documents", "Document.pdf")
            path, _ = QFileDialog.getSaveFileName(self, "Export as PDF", start, "PDF Files (*.pdf)")
            if not path:
                return None
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
        writer = QPdfWriter(path)
        writer.setResolution(300)
        self._page_layout(writer)
        writer.setTitle(os.path.splitext(self.display_name())[0])
        self._paginated_copy().print_(writer)
        if ask_to_open:
            resp = QMessageBox.question(self, "Export as PDF", f"Saved {path}.\n\nOpen it in a PDF tab to annotate it?",
                                        QMessageBox.Yes | QMessageBox.No)
            if resp == QMessageBox.Yes:
                self.open_path_requested.emit(path)
        return path
