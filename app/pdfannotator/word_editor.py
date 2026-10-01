"""The Word document tab: view and edit .docx files (and .odt / .html /
.md / .txt) inside the app.

The page is a QTextEdit sized like the document's page and laid on the same
canvas as the PDF view; word_io.py converts to and from .docx. Pages are
shown as one continuous sheet (page breaks are marked with a dashed line);
Print and Export PDF lay the document out on real pages.

The ribbon follows Word's: Home (clipboard, font, paragraph, styles),
Insert (tables, pictures, links, lines, symbols, the date, page breaks),
Layout (margins, orientation, paper size, indents and spacing) and Review
(word count, find and replace, formatting marks). The dialogs live in
word_dialogs.py."""
import os
import re

from PySide6.QtCore import QEvent, QMarginsF, QSize, QSizeF, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QImage, QKeySequence,
    QPageSize, QPainter, QPen, QPixmap, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument,
    QTextDocumentWriter, QTextFormat, QTextImageFormat, QTextLength, QTextListFormat, QTextOption,
    QTextTableFormat,
)
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFontComboBox, QFontDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QStackedWidget, QTabBar, QTextEdit, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from . import icons, theme, word_io
from .dialogs import _button_row, _dialog_layout, _header, _primary
from .editor_tab import EditorTab, FindReplaceBar, PaperCanvas
from .inline_text import list_icon, list_menu_style, list_style_of
from .pdf_ops import BULLET_STYLES, LIST_STYLES, NUMBER_STYLES
from .word_dialogs import (
    COMMON_SYMBOLS, LINE_SPACINGS, MARGIN_PRESETS, PAGE_SIZES, PT_PER_CM, SPECIAL_CHARACTERS, DateTimeDialog,
    PageSetupDialog, ParagraphDialog, SymbolDialog, WordCountDialog,
)

A4 = word_io.PageSetup(595.3, 841.9, (72.0, 72.0, 72.0, 72.0))
STYLES = ["Normal", "No Spacing", "Title", "Subtitle", "Heading 1", "Heading 2", "Heading 3", "Heading 4",
          "Heading 5", "Heading 6", "Quote", "Intense Quote", "Caption", "Code"]
_STYLE_LOOK = {  # name: (heading level, size, bold, italic, colour, family)
    "Title": (1, 26, False, False, "#000000", None),
    "Subtitle": (0, 14, False, False, "#5a5a5a", None),
    "Heading 1": (1, 16, True, False, "#2f5496", None),
    "Heading 2": (2, 13, True, False, "#2f5496", None),
    "Heading 3": (3, 12, True, False, "#1f3763", None),
    "Heading 4": (4, 11, True, True, "#2f5496", None),
    "Heading 5": (5, 11, False, False, "#2f5496", None),
    "Heading 6": (6, 11, False, True, "#1f3763", None),
    "Quote": (0, None, False, True, "#404040", None),
    "Intense Quote": (0, None, False, True, "#2f5496", None),
    "Caption": (0, 9, False, True, "#44546a", None),
    "Code": (0, 10, False, False, "#000000", "Consolas"),
}
_INDENTED_STYLES = {"Quote": 36, "Intense Quote": 54}   # left indent (pt) of the quote styles
SIZES = [8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 24, 28, 36, 48, 72]
OPEN_SUFFIXES = (".docx", ".odt", ".html", ".htm", ".md", ".markdown", ".txt")
_WRITER_FORMATS = {".odt": b"ODF", ".html": b"HTML", ".htm": b"HTML", ".md": b"markdown",
                   ".markdown": b"markdown", ".txt": b"plaintext"}

TEXT_COLOURS = (("Automatic", None), ("Black", "#000000"), ("Dark Grey", "#595959"), ("Grey", "#a6a6a6"),
                ("Dark Red", "#c00000"), ("Red", "#ff0000"), ("Orange", "#ffc000"), ("Yellow", "#ffff00"),
                ("Light Green", "#92d050"), ("Green", "#00b050"), ("Light Blue", "#00b0f0"),
                ("Blue", "#0070c0"), ("Dark Blue", "#002060"), ("Purple", "#7030a0"))
HIGHLIGHT_COLOURS = (("Yellow", "yellow"), ("Bright Green", "green"), ("Turquoise", "cyan"), ("Pink", "magenta"),
                     ("Blue", "blue"), ("Red", "red"), ("Dark Blue", "darkBlue"), ("Teal", "darkCyan"),
                     ("Green", "darkGreen"), ("Violet", "darkMagenta"), ("Dark Red", "darkRed"),
                     ("Dark Yellow", "darkYellow"), ("Grey 50%", "darkGray"), ("Grey 25%", "lightGray"),
                     ("Black", "black"))
SHADING_COLOURS = (("Light Grey", "#f2f2f2"), ("Grey", "#d9d9d9"), ("Light Blue", "#deeaf6"),
                   ("Light Green", "#e2efd9"), ("Light Yellow", "#fff2cc"), ("Light Orange", "#fbe4d5"),
                   ("Light Purple", "#e6e0ec"))
UNDERLINE_STYLES = (("Single", QTextCharFormat.SingleUnderline), ("Dotted", QTextCharFormat.DotLine),
                    ("Dashed", QTextCharFormat.DashUnderline), ("Dot-Dash", QTextCharFormat.DashDotLine),
                    ("Dot-Dot-Dash", QTextCharFormat.DashDotDotLine), ("Wave", QTextCharFormat.WaveUnderline))
CASES = (("Sentence case.", "sentence"), ("lowercase", "lower"), ("UPPERCASE", "upper"),
         ("Capitalize Each Word", "title"), ("tOGGLE cASE", "toggle"))
# multilevel lists: the style of each level, repeating
OUTLINES = {"number": ("number", "lower-alpha", "lower-roman"),
            "bullet": ("bullet", "bullet-circle", "bullet-square")}
_QT_LIST = {"disc": QTextListFormat.ListDisc, "circle": QTextListFormat.ListCircle,
            "square": QTextListFormat.ListSquare, "decimal": QTextListFormat.ListDecimal,
            "lower-alpha": QTextListFormat.ListLowerAlpha, "upper-alpha": QTextListFormat.ListUpperAlpha,
            "lower-roman": QTextListFormat.ListLowerRoman, "upper-roman": QTextListFormat.ListUpperRoman}
PX = word_io.PX_PER_PT


def _swatch(color, size=16, outline=False):
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(0, 0, 0, 90), 1))
    if color is None:
        p.setBrush(Qt.NoBrush)
        p.drawRect(2, 2, size - 5, size - 5)
        p.drawLine(3, size - 3, size - 3, 3)
    else:
        p.setBrush(QColor(color))
        p.drawRoundedRect(1.5, 1.5, size - 3, size - 3, 3, 3)
    p.end()
    return QIcon(pix)


def _list_format(key, indent=1, start=1):
    _label, kind, prefix, suffix = LIST_STYLES[key]
    fmt = QTextListFormat()
    fmt.setStyle(_QT_LIST[kind])
    fmt.setIndent(max(1, indent))
    if kind not in ("disc", "circle", "square"):
        fmt.setNumberPrefix(prefix)
        fmt.setNumberSuffix(suffix)
    if start != 1:
        fmt.setStart(start)
    return fmt


def _change_case(text, mode):
    if mode == "lower":
        return text.lower()
    if mode == "upper":
        return text.upper()
    if mode == "toggle":
        return text.swapcase()
    if mode == "title":
        return re.sub(r"[^\W_][\w'’]*", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), text)
    # sentence case: lower, with a capital after the start and every . ! ? or new paragraph
    out, capital = [], True
    for ch in text.lower():
        if capital and ch.isalpha():
            out.append(ch.upper())
            capital = False
        else:
            out.append(ch)
        if ch in ".!? \n":
            capital = True
    return "".join(out)


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
        cursor = self.textCursor()
        if event.key() in (Qt.Key_Tab, Qt.Key_Backtab) and cursor.currentList() is not None and \
                (cursor.atBlockStart() or cursor.hasSelection()) and cursor.currentTable() is None:
            # Tab / Shift+Tab at the start of a list item: one level down / up, as in Word
            self.tab.change_indent(-1 if event.key() == Qt.Key_Backtab or event.modifiers() & Qt.ShiftModifier
                                   else 1)
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
        if self.tab.painter_format is not None and self.textCursor().hasSelection():
            self.tab.apply_painted_format()

    def mouseMoveEvent(self, event):
        href = self.anchorAt(event.position().toPoint())
        if self.tab.painter_format is not None:
            self.viewport().setCursor(Qt.CrossCursor)   # Format Painter: brush over the text to format
        else:
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
    RIBBON_TABS = ("Home", "Insert", "Layout", "Review")

    def __init__(self, parent=None, path=None):
        super().__init__(parent)
        self.page_setup = A4
        self.base_path = None
        self._images = 0
        self._words = 0
        self.painter_format = None     # (char format) while the Format Painter is loaded
        self._last_bullet, self._last_number = "bullet", "number"
        self._text_colour, self._highlight = QColor("#ff0000"), QColor(word_io._HIGHLIGHTS["yellow"])
        self._shading = QColor("#f2f2f2")

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
        bf.setBottomMargin(8 * PX)
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
        self.page.min_height = round(page.height_pt * PX)
        self.page.setDocument(doc)
        self._apply_marks()
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

    def _page_count(self):
        return max(1, round(self.document.size().height() / max(1, self.page.min_height) + 0.49))

    def status_text(self):
        pages = self._page_count()
        return f"{self._words:,} words · about {pages} page{'s' if pages != 1 else ''}"

    def _count_words(self):
        self._words = len(re.findall(r"\w+", self.document.toPlainText()))
        self.changed.emit()

    def _keep_cursor_visible(self):
        rect = self.page.cursorRect()
        pos = self.page.mapTo(self.canvas, rect.center())
        self.scroll.ensureVisible(pos.x(), pos.y(), 40, 60)

    # ------------------------------------------------------------ the ribbon
    def _build_toolbars(self, layout):
        """Home / Insert / Layout / Review, each a page of toolbar rows; Print
        and Export PDF stay at the right of the tab row."""
        head = QWidget()
        head.setObjectName("wordRibbonHead")
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(6, 0, 6, 0)
        head_row.setSpacing(2)
        self.ribbon_tabs = QTabBar()
        self.ribbon_tabs.setObjectName("wordRibbonTabs")
        self.ribbon_tabs.setExpanding(False)
        self.ribbon_tabs.setDrawBase(False)
        for name in self.RIBBON_TABS:
            self.ribbon_tabs.addTab(name)
        head_row.addWidget(self.ribbon_tabs)
        head_row.addStretch(1)
        self.ribbon = QStackedWidget()
        self.ribbon.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout.addWidget(head)
        layout.addWidget(self.ribbon)
        self.ribbon_tabs.currentChanged.connect(self._ribbon_switched)

        pages = {}
        for name in self.RIBBON_TABS:
            page = QWidget()
            box = QVBoxLayout(page)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(0)
            self.ribbon.addWidget(page)
            pages[name] = box

        def bar(name):
            tb = QToolBar()
            tb.setObjectName("editorBar")
            tb.setIconSize(QSize(18, 18))
            tb.setMovable(False)
            pages[name].addWidget(tb)
            return tb

        def act(tb, text, icon, slot, shortcut=None, checkable=False, menu=None, keys=()):
            a = QAction(icons.icon(icon) if icon else QIcon(), text, self)
            shown = QKeySequence(shortcut).toString(QKeySequence.NativeText) if shortcut else ""
            a.setToolTip(text.replace("...", "") + (f"  ({shown})" if shown else ""))
            for key in ((shortcut,) if shortcut else ()) + tuple(keys):   # handled by the page itself
                self.page.key_actions[QKeySequence(key).toString()] = a
            a.setCheckable(checkable)
            a.triggered.connect(slot)
            if tb is not None:
                tb.addAction(a)
                if menu is not None:   # a split button: click does it, the arrow offers more
                    button = tb.widgetForAction(a)
                    button.setMenu(menu)
                    button.setPopupMode(QToolButton.MenuButtonPopup)
            return a

        def menu_button(tb, text, icon, menu, labelled=False):
            button = QToolButton()
            if labelled:   # Margins, Orientation, Size: named, as in Word
                button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setIcon(icons.icon(icon))
            button.setToolTip(text)
            button.setText(text)
            button.setMenu(menu)
            button.setPopupMode(QToolButton.InstantPopup)
            tb.addWidget(button)
            return button

        self._build_home(bar, act, menu_button)
        self._build_insert(bar, act, menu_button)
        self._build_layout(bar, act, menu_button)
        self._build_review(bar, act, menu_button)

        for text, icon, slot in (("Print...", "print", self.print_document),
                                 ("Export as PDF...", "file-pdf", self.export_pdf)):
            button = QToolButton()
            button.setAutoRaise(True)
            button.setIcon(icons.icon(icon))
            button.setIconSize(QSize(18, 18))
            button.setToolTip(text.replace("...", ""))
            button.clicked.connect(lambda _c=False, f=slot: f())
            head_row.addWidget(button)
        start = theme._settings().value("word/ribbon_tab", "Home")
        self.ribbon_tabs.setCurrentIndex(self.RIBBON_TABS.index(start) if start in self.RIBBON_TABS else 0)
        self._ribbon_switched(self.ribbon_tabs.currentIndex())

    def _ribbon_switched(self, index):
        self.ribbon.setCurrentIndex(index)
        for i in range(self.ribbon.count()):   # only the page shown takes up height
            self.ribbon.widget(i).setSizePolicy(QSizePolicy.Preferred,
                                                QSizePolicy.Preferred if i == index else QSizePolicy.Ignored)
        self.ribbon.adjustSize()
        theme._settings().setValue("word/ribbon_tab", self.RIBBON_TABS[index])

    def _menu(self, title=""):
        return QMenu(title, self)

    # ---- Home
    def _build_home(self, bar, act, menu_button):
        top = bar("Home")
        act(top, "Undo", "undo", self.page.undo)
        act(top, "Redo", "redo", self.page.redo)
        top.addSeparator()
        paste_menu = self._menu()
        paste_menu.addAction(icons.icon("paste"), "Paste", self.page.paste)
        paste_menu.addAction(icons.icon("paste-text"), "Paste Text Only", self.paste_plain)
        act(top, "Paste", "paste", self.page.paste, menu=paste_menu)
        act(top, "Cut", "cut", self.page.cut)
        act(top, "Copy", "copy", self.page.copy)
        self.painter_act = act(top, "Format Painter", "format-painter", self.toggle_format_painter, checkable=True)
        self.painter_act.setToolTip("Format Painter: click it, then select text to give it the formatting "
                                    "where the cursor was")
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
        act(top, "Increase Font Size", "font-grow", lambda: self.grow_font(1), "Ctrl+]", keys=("Ctrl+>",))
        act(top, "Decrease Font Size", "font-shrink", lambda: self.grow_font(-1), "Ctrl+[", keys=("Ctrl+<",))
        case_menu = self._menu("Change Case")
        for label, mode in CASES:
            case_menu.addAction(label, lambda m=mode: self.change_case(m))
        case_menu.addSeparator()
        self.small_caps_act = case_menu.addAction("Small Caps", lambda: self.set_capitals(QFont.SmallCaps))
        self.all_caps_act = case_menu.addAction("All Caps (formatting)", lambda: self.set_capitals(QFont.AllUppercase))
        self.small_caps_act.setCheckable(True)
        self.all_caps_act.setCheckable(True)
        menu_button(top, "Change Case", "change-case", case_menu)
        act(None, "Cycle Case", None, self.cycle_case, "Shift+F3")
        act(top, "Font...", "font-dialog", self.font_dialog, "Ctrl+D")
        act(top, "Clear Formatting", "clear-format", self.clear_formatting, "Ctrl+Space")

        second = bar("Home")
        self.bold_act = act(second, "Bold", "bold", self._toggle_bold, "Ctrl+B", True)
        self.italic_act = act(second, "Italic", "italic", lambda c: self._merge(italic=c), "Ctrl+I", True)
        underline_menu = self._menu("Underline")
        self.underline_group = QActionGroup(self)
        for label, style in UNDERLINE_STYLES:
            a = underline_menu.addAction(label, lambda s=style: self.set_underline_style(s))
            a.setCheckable(True)
            a.setData(style)
            self.underline_group.addAction(a)
        underline_menu.addSeparator()
        underline_menu.addAction("No Underline", lambda: self._merge(underline=False))
        self.underline_act = act(second, "Underline", "text-underline", lambda c: self._merge(underline=c), "Ctrl+U",
                                 True, menu=underline_menu)
        self.strike_act = act(second, "Strikethrough", "strikeout", lambda c: self._merge(strike=c), None, True)
        self.sub_act = act(second, "Subscript", "subscript", lambda c: self._set_valign(c, False), "Ctrl+=", True)
        self.super_act = act(second, "Superscript", "superscript", lambda c: self._set_valign(c, True),
                             "Ctrl+Shift+=", True, keys=("Ctrl+Shift++", "Ctrl++"))
        second.addSeparator()
        colour_menu = self._menu("Font Colour")
        for label, color in TEXT_COLOURS:
            colour_menu.addAction(_swatch(color), label, lambda c=color: self.set_text_colour(c))
        colour_menu.addSeparator()
        colour_menu.addAction(icons.icon("palette"), "More Colours...", self._pick_text_color)
        self.colour_act = act(second, "Font Colour", "text-color", lambda: self.set_text_colour(self._text_colour),
                              menu=colour_menu)
        highlight_menu = self._menu("Highlight")
        for label, name in HIGHLIGHT_COLOURS:
            color = word_io._HIGHLIGHTS[name]
            highlight_menu.addAction(_swatch(color), label, lambda c=color: self.set_highlight(c))
        highlight_menu.addSeparator()
        highlight_menu.addAction(_swatch(None), "No Colour", lambda: self.set_highlight(None))
        highlight_menu.addAction(icons.icon("palette"), "More Colours...", self._pick_highlight)
        self.highlight_act = act(second, "Text Highlight Colour", "highlight",
                                 lambda: self.set_highlight(self._highlight), menu=highlight_menu)
        second.addSeparator()
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
        self.list_actions = {}
        self.list_group = QActionGroup(self)
        self.list_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        bullet_menu = self._list_menu("Bullet Library", BULLET_STYLES)
        number_menu = self._list_menu("Numbering Library", NUMBER_STYLES)
        for menu in (number_menu,):
            menu.addSeparator()
            menu.addAction("Restart at 1", self.restart_numbering)
            menu.addAction("Continue Numbering", self.continue_numbering)
            menu.addAction("Set Numbering Value...", self.set_numbering_value)
        self.bullets_act = act(second, "Bullets", "list-bullet", lambda: self.toggle_list(False), "Ctrl+Shift+L",
                               True, menu=bullet_menu)
        self.numbers_act = act(second, "Numbering", "list-ordered", lambda: self.toggle_list(True), None, True,
                               menu=number_menu)
        multi_menu = self._menu("Multilevel List")
        multi_menu.addAction("1.  a.  i.   (numbered outline)", lambda: self.apply_outline("number"))
        multi_menu.addAction("•  ◦  ▪   (bulleted outline)", lambda: self.apply_outline("bullet"))
        multi_menu.addSeparator()
        multi_menu.addAction(icons.icon("indent"), "Demote One Level  (Tab)", lambda: self.change_indent(1))
        multi_menu.addAction(icons.icon("outdent"), "Promote One Level  (Shift+Tab)", lambda: self.change_indent(-1))
        multi_menu.addSeparator()
        multi_menu.addAction(icons.icon("close"), "Remove List", lambda: self.apply_list("none"))
        menu_button(second, "Multilevel List", "list-multilevel", multi_menu)
        act(second, "Decrease Indent", "outdent", lambda: self.change_indent(-1), "Ctrl+Shift+M")
        act(second, "Increase Indent", "indent", lambda: self.change_indent(1), "Ctrl+M")
        second.addSeparator()
        spacing_menu = self._menu("Line and Paragraph Spacing")
        self.spacing_group = QActionGroup(self)
        for label, value in LINE_SPACINGS:
            a = spacing_menu.addAction(f"{value:g}", lambda v=value: self.set_line_spacing(v))
            a.setCheckable(True)
            a.setData(value)
            self.spacing_group.addAction(a)
        spacing_menu.addSeparator()
        spacing_menu.addAction("Line Spacing Options...", self.paragraph_dialog)
        spacing_menu.addSeparator()
        self.space_before_act = spacing_menu.addAction("Add Space Before Paragraph", lambda: self.toggle_space(True))
        self.space_after_act = spacing_menu.addAction("Remove Space After Paragraph", lambda: self.toggle_space(False))
        spacing_menu.aboutToShow.connect(self._sync_spacing_menu)
        menu_button(second, "Line and Paragraph Spacing", "line-spacing", spacing_menu)
        act(None, "Single Spacing", None, lambda: self.set_line_spacing(1.0), "Ctrl+1")
        act(None, "Double Spacing", None, lambda: self.set_line_spacing(2.0), "Ctrl+2")
        act(None, "1.5 Spacing", None, lambda: self.set_line_spacing(1.5), "Ctrl+5")
        shading_menu = self._menu("Shading")
        for label, color in SHADING_COLOURS:
            shading_menu.addAction(_swatch(color), label, lambda c=color: self.set_shading(c))
        shading_menu.addSeparator()
        shading_menu.addAction(_swatch(None), "No Colour", lambda: self.set_shading(None))
        shading_menu.addAction(icons.icon("palette"), "More Colours...", self._pick_shading)
        act(second, "Shading", "shading", lambda: self.set_shading(self._shading), menu=shading_menu)
        act(second, "Paragraph...", "paragraph-dialog", self.paragraph_dialog)
        second.addSeparator()
        self.marks_act = act(second, "Show Formatting Marks", "pilcrow", self.show_marks, "Ctrl+Shift+8", True)
        act(second, "Find and Replace...", "find", self.show_find, "Ctrl+H")
        for key, name in (("Ctrl+Shift+N", "Normal"), ("Ctrl+Alt+1", "Heading 1"), ("Ctrl+Alt+2", "Heading 2"),
                          ("Ctrl+Alt+3", "Heading 3")):
            act(None, name, None, lambda _c=False, n=name: self.apply_style(n), key)

    def _list_menu(self, title, keys):
        menu = self._menu(title)
        list_menu_style(menu)
        menu.addSection(title)
        for key in keys:
            a = menu.addAction(list_icon(key), LIST_STYLES[key][0], lambda k=key: self.apply_list(k))
            a.setCheckable(True)
            self.list_group.addAction(a)
            self.list_actions[key] = a
        menu.addSeparator()
        menu.addAction("None", lambda: self.apply_list("none"))
        menu.aboutToShow.connect(lambda: [self.list_actions[k].setIcon(list_icon(k)) for k in keys])
        return menu

    # ---- Insert
    def _build_insert(self, bar, act, menu_button):
        tb = bar("Insert")
        table_menu = self._menu("Table")
        table_menu.aboutToShow.connect(lambda: self._fill_table_menu(table_menu))
        self._fill_table_menu(table_menu)
        act(tb, "Insert Table...", "table", self.insert_table_dialog, menu=table_menu)
        act(tb, "Insert Picture...", "image", self.insert_image_dialog)
        act(tb, "Insert Link...", "link", self.insert_link, "Ctrl+K")
        tb.addSeparator()
        act(tb, "Horizontal Line", "horizontal-rule", self.insert_horizontal_line)
        symbol_menu = self._menu("Symbol")
        for ch in COMMON_SYMBOLS:
            symbol_menu.addAction(f"{ch}    U+{ord(ch):04X}", lambda c=ch: self.insert_text(c))
        symbol_menu.addSeparator()
        special = symbol_menu.addMenu("Special Characters")
        for name, ch in SPECIAL_CHARACTERS:
            special.addAction(name, lambda c=ch: self.insert_text(c))
        symbol_menu.addAction(icons.icon("sigma"), "More Symbols...", self.symbol_dialog)
        act(tb, "Symbol...", "sigma", self.symbol_dialog, menu=symbol_menu)
        act(tb, "Date and Time...", "calendar", self.insert_date_time, "Alt+Shift+D")
        tb.addSeparator()
        act(tb, "Page Break", "page-break", self.insert_page_break, "Ctrl+Return")

    # ---- Layout
    def _build_layout(self, bar, act, menu_button):
        tb = bar("Layout")
        margins_menu = self._menu("Margins")
        for name, margins in MARGIN_PRESETS.items():
            margins_menu.addAction(name, lambda m=margins: self.set_page(margins=m))
        margins_menu.addSeparator()
        margins_menu.addAction("Custom Margins...", self.page_setup_dialog)
        menu_button(tb, "Margins", "margins", margins_menu, labelled=True)
        orient_menu = self._menu("Orientation")
        self.portrait_act = orient_menu.addAction("Portrait", lambda: self.set_orientation(False))
        self.landscape_act = orient_menu.addAction("Landscape", lambda: self.set_orientation(True))
        orient_group = QActionGroup(self)
        for a in (self.portrait_act, self.landscape_act):
            a.setCheckable(True)
            orient_group.addAction(a)
        menu_button(tb, "Orientation", "orientation", orient_menu, labelled=True)
        size_menu = self._menu("Size")
        for name, size in PAGE_SIZES.items():
            size_menu.addAction(name, lambda s=size: self.set_paper_size(*s))
        size_menu.addSeparator()
        size_menu.addAction("More Paper Sizes...", self.page_setup_dialog)
        menu_button(tb, "Size", "page-size", size_menu, labelled=True)
        act(tb, "Page Setup...", "settings", self.page_setup_dialog)
        tb.addSeparator()

        def spin(label, suffix, low, high, step, setter):
            box = QDoubleSpinBox()
            box.setRange(low, high)
            box.setDecimals(1 if suffix == " pt" else 2)
            box.setSingleStep(step)
            box.setSuffix(suffix)
            box.setFixedWidth(100)
            box.setToolTip(label)
            box.setKeyboardTracking(False)
            box.valueChanged.connect(setter)
            tb.addWidget(QLabel(f" {label} "))
            tb.addWidget(box)
            return box

        self.indent_left = spin("Indent left", " cm", 0, 30, 0.25, lambda v: self._set_block_margin("left", v))
        self.indent_right = spin("Right", " cm", 0, 30, 0.25, lambda v: self._set_block_margin("right", v))
        tb.addSeparator()
        self.space_before = spin("Spacing before", " pt", 0, 600, 6, lambda v: self._set_block_margin("before", v))
        self.space_after = spin("After", " pt", 0, 600, 6, lambda v: self._set_block_margin("after", v))
        tb.addSeparator()
        act(tb, "Page Break", "page-break", self.insert_page_break)

    # ---- Review
    def _build_review(self, bar, act, menu_button):
        tb = bar("Review")
        act(tb, "Word Count...", "word-count", self.word_count_dialog, "Ctrl+Shift+G")
        act(tb, "Find and Replace...", "find", self.show_find)
        act(tb, "Select All", "select-all", self.page.selectAll)
        tb.addAction(self.marks_act)

    # ------------------------------------------------------------ syncing the controls
    def _sync_char_controls(self, fmt):
        self.bold_act.setChecked(fmt.fontWeight() >= QFont.DemiBold)
        self.italic_act.setChecked(fmt.fontItalic())
        self.underline_act.setChecked(fmt.fontUnderline())
        for a in self.underline_group.actions():
            a.setChecked(fmt.fontUnderline() and a.data() == (fmt.underlineStyle()
                                                                if fmt.underlineStyle() != QTextCharFormat.NoUnderline
                                                                else QTextCharFormat.SingleUnderline))
        self.strike_act.setChecked(fmt.fontStrikeOut())
        self.super_act.setChecked(fmt.verticalAlignment() == QTextCharFormat.AlignSuperScript)
        self.sub_act.setChecked(fmt.verticalAlignment() == QTextCharFormat.AlignSubScript)
        self.small_caps_act.setChecked(fmt.fontCapitalization() == QFont.SmallCaps)
        self.all_caps_act.setChecked(fmt.fontCapitalization() == QFont.AllUppercase)
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
            name = f"Heading {level}" if 1 <= level <= 6 else "Normal"
        self.style_combo.setCurrentText(name)
        lst = cursor.currentList()
        key = list_style_of(lst.format()) if lst is not None else ""
        self.bullets_act.setChecked(key in BULLET_STYLES or (lst is not None and not key and
                                                              lst.format().style() in word_io._BULLETS))
        self.numbers_act.setChecked(lst is not None and not self.bullets_act.isChecked())
        for k, a in self.list_actions.items():
            a.setChecked(k == key)
        for box, value in ((self.indent_left, bf.leftMargin() / PX / PT_PER_CM),
                           (self.indent_right, bf.rightMargin() / PX / PT_PER_CM),
                           (self.space_before, bf.topMargin() / PX), (self.space_after, bf.bottomMargin() / PX)):
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)
        landscape = self.page_setup.width_pt > self.page_setup.height_pt
        self.landscape_act.setChecked(landscape)
        self.portrait_act.setChecked(not landscape)

    def _sync_spacing_menu(self):
        bf = self.page.textCursor().blockFormat()
        line = bf.lineHeight() / 100 if bf.lineHeightType() == word_io._PROPORTIONAL and bf.lineHeight() else 1.0
        for a in self.spacing_group.actions():
            a.setChecked(abs(a.data() - line) < 1e-3)
        self.space_before_act.setText("Remove Space Before Paragraph" if bf.topMargin() > 0
                                      else "Add Space Before Paragraph")
        self.space_after_act.setText("Remove Space After Paragraph" if bf.bottomMargin() > 0
                                     else "Add Space After Paragraph")

    # ------------------------------------------------------------ character formatting
    def _merge_format(self, cf):
        cursor = self.page.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        cursor.mergeCharFormat(cf)
        self.page.mergeCurrentCharFormat(cf)
        self.page.setFocus()

    def _pieces(self, cursor):
        """(start, end, char format) of every run of text in the selection."""
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        pieces = []
        block = self.document.findBlock(start)
        while block.isValid() and block.position() <= end:
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                a, b = max(start, frag.position()), min(end, frag.position() + frag.length())
                if a < b:
                    pieces.append((a, b, frag.charFormat()))
                it += 1
            block = block.next()
        return pieces

    def _selection_or_word(self):
        cursor = self.page.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        return cursor

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
            if not underline:
                cf.setUnderlineStyle(QTextCharFormat.NoUnderline)
        if strike is not None:
            cf.setFontStrikeOut(strike)
        self._merge_format(cf)
        self._sync_char_controls(self.page.currentCharFormat())

    def set_underline_style(self, style):
        cf = QTextCharFormat()
        cf.setUnderlineStyle(style)
        self._merge_format(cf)
        self._sync_char_controls(self.page.currentCharFormat())

    def _set_valign(self, checked, superscript):
        cf = QTextCharFormat()
        if checked:
            cf.setVerticalAlignment(QTextCharFormat.AlignSuperScript if superscript else QTextCharFormat.AlignSubScript)
        else:
            cf.setVerticalAlignment(QTextCharFormat.AlignNormal)
        self._merge_format(cf)
        self._sync_char_controls(self.page.currentCharFormat())

    def set_capitals(self, capitalization):
        current = self.page.currentCharFormat().fontCapitalization()
        cf = QTextCharFormat()
        cf.setFontCapitalization(QFont.MixedCase if current == capitalization else capitalization)
        self._merge_format(cf)
        self._sync_char_controls(self.page.currentCharFormat())

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

    def grow_font(self, step):
        """Each run one size up (or down) the size list, as Word's buttons do."""
        cursor = self._selection_or_word()
        default = self.document.defaultFont().pointSizeF() or word_io.DEFAULT_FONT[1]

        def bumped(size):
            if step > 0:
                return next((s for s in SIZES if s > size + 1e-6), min(400, size + 10))
            return next((s for s in reversed(SIZES) if s < size - 1e-6), max(1, size - 1))

        if not cursor.hasSelection():
            cf = QTextCharFormat()
            cf.setFontPointSize(bumped(self.page.currentCharFormat().fontPointSize() or default))
            self.page.mergeCurrentCharFormat(cf)
        else:
            cursor.beginEditBlock()
            for a, b, fmt in self._pieces(cursor):
                c = QTextCursor(self.document)
                c.setPosition(a)
                c.setPosition(b, QTextCursor.KeepAnchor)
                cf = QTextCharFormat()
                cf.setFontPointSize(bumped(fmt.fontPointSize() or default))
                c.mergeCharFormat(cf)
            cursor.endEditBlock()
        self._sync_char_controls(self.page.currentCharFormat())
        self.page.setFocus()

    def change_case(self, mode):
        """UPPERCASE, lowercase, Sentence case... of the selection (or the
        word at the cursor), keeping its formatting."""
        cursor = self._selection_or_word()
        if not cursor.hasSelection():
            return
        start = cursor.selectionStart()
        text = cursor.selectedText()
        changed = _change_case(text, mode)
        if changed == text:
            return
        cursor.beginEditBlock()
        if len(changed) == len(text):
            for a, b, fmt in reversed(self._pieces(cursor)):
                if fmt.isImageFormat():
                    continue
                c = QTextCursor(self.document)
                c.setPosition(a)
                c.setPosition(b, QTextCursor.KeepAnchor)
                c.insertText(changed[a - start:b - start], fmt)
        else:   # the text changed length (ß -> SS): one run per piece, case by case
            for a, b, fmt in reversed(self._pieces(cursor)):
                if fmt.isImageFormat():
                    continue
                c = QTextCursor(self.document)
                c.setPosition(a)
                c.setPosition(b, QTextCursor.KeepAnchor)
                c.insertText(_change_case(c.selectedText(), mode), fmt)
        cursor.endEditBlock()
        cursor.setPosition(start)
        cursor.setPosition(min(self.document.characterCount() - 1, start + len(changed)), QTextCursor.KeepAnchor)
        self.page.setTextCursor(cursor)
        self.page.setFocus()

    def cycle_case(self):
        """Shift+F3: lowercase -> UPPERCASE -> Capitalize Each Word -> lowercase."""
        text = self._selection_or_word().selectedText()
        if text == text.upper() and text != text.lower():
            self.change_case("title")
        elif text == text.lower():
            self.change_case("upper")
        else:
            self.change_case("lower")

    def set_text_colour(self, color):
        cf = QTextCharFormat()
        if color is None:   # Automatic: black
            cf.setForeground(QColor("#000000"))
        else:
            self._text_colour = QColor(color)
            cf.setForeground(self._text_colour)
            self.colour_act.setIcon(_swatch(self._text_colour.name()))
        self._merge_format(cf)

    def _pick_text_color(self):
        color = QColorDialog.getColor(self.page.currentCharFormat().foreground().color(), self, "Font Colour")
        if color.isValid():
            self.set_text_colour(color.name())

    def set_highlight(self, color):
        cursor = self.page.textCursor()
        cf = QTextCharFormat()
        if color is None:
            cf.setBackground(Qt.NoBrush)
        else:
            self._highlight = QColor(color)
            cf.setBackground(self._highlight)
        if cursor.hasSelection():
            cursor.mergeCharFormat(cf)
        else:
            self.page.mergeCurrentCharFormat(cf)
        self.page.setFocus()

    def _pick_highlight(self):
        color = QColorDialog.getColor(QColor("#ffff00"), self, "Highlight Colour")
        if color.isValid():
            self.set_highlight(color.name())

    def font_dialog(self):
        cursor = self.page.textCursor()
        fmt = cursor.charFormat() if cursor.hasSelection() else self.page.currentCharFormat()
        font = fmt.font()
        if fmt.fontPointSize() <= 0:
            font.setPointSizeF(self.document.defaultFont().pointSizeF() or word_io.DEFAULT_FONT[1])
        ok, chosen = QFontDialog.getFont(font, self, "Font")
        if isinstance(ok, QFont):   # PySide returns (ok, font) or (font, ok) by version
            ok, chosen = chosen, ok
        if not ok:
            return
        cf = QTextCharFormat()
        cf.setFontFamilies([chosen.family()])
        cf.setFontPointSize(chosen.pointSizeF())
        cf.setFontWeight(QFont.Bold if chosen.bold() else QFont.Normal)
        cf.setFontItalic(chosen.italic())
        cf.setFontUnderline(chosen.underline())
        cf.setFontStrikeOut(chosen.strikeOut())
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

    # ---- clipboard extras
    def paste_plain(self):
        self.page.insertPlainText(QGuiApplication.clipboard().text())
        self.page.setFocus()

    def toggle_format_painter(self, checked):
        """Pick up the formatting at the cursor; the next selection gets it."""
        if checked:
            cursor = self.page.textCursor()
            self.painter_format = QTextCharFormat(cursor.charFormat() if cursor.hasSelection()
                                                  else self.page.currentCharFormat())
            window = self.window()
            if hasattr(window, "statusBar"):
                window.statusBar().showMessage("Format Painter: select the text to give this formatting", 4000)
        else:
            self.painter_format = None
        self.page.viewport().setCursor(Qt.CrossCursor if checked else Qt.IBeamCursor)
        self.page.setFocus()

    def apply_painted_format(self):
        fmt, self.painter_format = self.painter_format, None
        cursor = self.page.textCursor()
        if fmt is not None and cursor.hasSelection():
            clean = QTextCharFormat(fmt)
            clean.setAnchor(False)
            clean.clearProperty(QTextFormat.AnchorHref)
            cursor.setCharFormat(clean)
        self.painter_act.setChecked(False)
        self.page.viewport().setCursor(Qt.IBeamCursor)

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

    def _each_block_format(self, change):
        """change(QTextBlockFormat) for each selected paragraph, as one undo step."""
        cursor = self.page.textCursor()
        cursor.beginEditBlock()
        for block in list(self._selected_blocks(cursor)):
            bf = block.blockFormat()
            change(bf)
            QTextCursor(block).setBlockFormat(bf)
        cursor.endEditBlock()
        self._sync_block_controls()

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
            was_indented = bf.property(word_io.STYLE_PROP) in _INDENTED_STYLES
            bf.setHeadingLevel(level if name.startswith("Heading") or name == "Title" else 0)
            bf.setProperty(word_io.STYLE_PROP, name)
            if name in _INDENTED_STYLES:
                bf.setLeftMargin(_INDENTED_STYLES[name] * PX)
            elif was_indented:
                bf.setLeftMargin(0)
            if name == "No Spacing":
                bf.setTopMargin(0)
                bf.setBottomMargin(0)
            elif name == "Normal" and bf.bottomMargin() == 0:
                bf.setBottomMargin(8 * PX)
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
        self._sync_block_controls()
        self.page.setFocus()

    # ---- lists
    def toggle_list(self, numbered):
        """The Bullets / Numbering buttons: the last style picked, or off."""
        key = self._last_number if numbered else self._last_bullet
        current = self.page.textCursor().currentList()
        kind_now = list_style_of(current.format()) if current is not None else ""
        same_family = current is not None and ((kind_now in NUMBER_STYLES) == numbered if kind_now else
                                               (current.format().style() not in word_io._BULLETS) == numbered)
        self.apply_list("none" if same_family else key)

    def apply_list(self, key):
        """A bullet or numbering style (a pdf_ops.LIST_STYLES key) on the
        selected paragraphs, or "none" to take them out of their list."""
        cursor = self.page.textCursor()
        current = cursor.currentList()
        cursor.beginEditBlock()
        if key == "none" or (current is not None and list_style_of(current.format()) == key and
                             not current.format().hasProperty(word_io.OUTLINE_PROP)):
            for block in list(self._selected_blocks(cursor)):
                lst = block.textList()
                if lst is not None:
                    lst.remove(block)
                bf = block.blockFormat()
                bf.setIndent(0)
                bf.clearProperty(word_io.NUM_PROP)
                QTextCursor(block).setBlockFormat(bf)
        elif current is not None:   # the list takes the new style (at its own level)
            fmt = _list_format(key, current.format().indent(), current.format().start())
            current.setFormat(fmt)
            self._forget_numbering(cursor)
        else:
            cursor.createList(_list_format(key))
            self._forget_numbering(cursor)
        cursor.endEditBlock()
        if key in BULLET_STYLES:
            self._last_bullet = key
        elif key in NUMBER_STYLES:
            self._last_number = key
        self._sync_block_controls()
        self.page.setFocus()

    def apply_outline(self, kind):
        """A multilevel list: each level (Tab / Shift+Tab) its own style."""
        cursor = self.page.textCursor()
        current = cursor.currentList()
        level = current.format().indent() if current is not None else 1
        fmt = _list_format(OUTLINES[kind][(level - 1) % 3], level)
        fmt.setProperty(word_io.OUTLINE_PROP, kind)
        cursor.beginEditBlock()
        if current is not None:
            current.setFormat(fmt)
        else:
            cursor.createList(fmt)
        self._forget_numbering(cursor)
        cursor.endEditBlock()
        self._sync_block_controls()
        self.page.setFocus()

    def _list_blocks(self, lst):
        return [lst.item(i) for i in range(lst.count())]

    def restart_numbering(self):
        """This item starts again at 1; the ones after it follow on from it."""
        cursor = self.page.textCursor()
        lst = cursor.currentList()
        if lst is None:
            return
        block = cursor.block()
        after = [b for b in self._list_blocks(lst) if b.position() > block.position()]
        cursor.beginEditBlock()
        fmt = QTextListFormat(lst.format())
        fmt.setStart(1)
        fresh = QTextCursor(block).createList(fmt)
        for b in after:
            lst.remove(b)
            fresh.add(b)
        self._forget_numbering(cursor)
        cursor.endEditBlock()

    def continue_numbering(self):
        """Join this list to the list of the same kind above it."""
        cursor = self.page.textCursor()
        lst = cursor.currentList()
        if lst is None:
            return
        first = lst.item(0)
        block = first.previous()
        while block.isValid() and (block.textList() is None or block.textList().format().style() != lst.format().style()
                                   or block.textList().format().indent() != lst.format().indent()):
            block = block.previous()
        if not block.isValid():
            return
        target = block.textList()
        cursor.beginEditBlock()
        for b in self._list_blocks(lst):
            lst.remove(b)
            target.add(b)
        self._forget_numbering(cursor)
        cursor.endEditBlock()

    def set_numbering_value(self):
        cursor = self.page.textCursor()
        lst = cursor.currentList()
        if lst is None:
            QMessageBox.information(self, "Set Numbering Value", "Put the cursor in a numbered list first.")
            return
        fmt = QTextListFormat(lst.format())
        value, ok = QInputDialog.getInt(self, "Set Numbering Value", "Start the list at:", max(1, fmt.start()), 0, 9999)
        if ok:
            fmt.setStart(value)
            lst.setFormat(fmt)
            self._forget_numbering(cursor)

    def _forget_numbering(self, cursor):
        """A list changed here no longer matches the Word numbering it was read with."""
        for block in list(self._selected_blocks(cursor)):
            lst = block.textList()
            for b in ([lst.item(i) for i in range(lst.count())] if lst is not None else [block]):
                bf = b.blockFormat()
                if bf.hasProperty(word_io.NUM_PROP):
                    bf.clearProperty(word_io.NUM_PROP)
                    QTextCursor(b).setBlockFormat(bf)

    def change_indent(self, step):
        cursor = self.page.textCursor()
        cursor.beginEditBlock()
        lst = cursor.currentList()
        if lst is not None:
            fmt = QTextListFormat(lst.format())
            fmt.setIndent(max(1, min(9, fmt.indent() + step)))
            outline = fmt.property(word_io.OUTLINE_PROP)
            if isinstance(outline, str) and outline in OUTLINES:
                styled = _list_format(OUTLINES[outline][(fmt.indent() - 1) % 3], fmt.indent())
                styled.setProperty(word_io.OUTLINE_PROP, outline)
                fmt = styled
            elif fmt.style() in word_io._BULLETS:
                fmt.setStyle(word_io._BULLETS[(fmt.indent() - 1) % 3])
            cursor.createList(fmt)
            self._forget_numbering(cursor)
        else:
            for block in list(self._selected_blocks(cursor)):
                bf = block.blockFormat()
                bf.setLeftMargin(max(0.0, bf.leftMargin() + step * 36 * PX))
                QTextCursor(block).setBlockFormat(bf)
        cursor.endEditBlock()
        self._sync_block_controls()
        self.page.setFocus()

    # ---- spacing, indents and shading
    def set_line_spacing(self, value):
        def change(bf):
            if abs(value - 1.0) < 1e-3:
                bf.setLineHeight(100, word_io._PROPORTIONAL)
            else:
                bf.setLineHeight(value * 100, word_io._PROPORTIONAL)
        self._each_block_format(change)
        self.page.setFocus()

    def toggle_space(self, before):
        """Add / Remove Space Before (12 pt) or After (8 pt) the paragraph."""
        bf_now = self.page.textCursor().blockFormat()
        if before:
            on = bf_now.topMargin() <= 0
            self._each_block_format(lambda bf: bf.setTopMargin(12 * PX if on else 0))
        else:
            on = bf_now.bottomMargin() <= 0
            self._each_block_format(lambda bf: bf.setBottomMargin(8 * PX if on else 0))
        self.page.setFocus()

    def _set_block_margin(self, which, value):
        px = value * PT_PER_CM * PX if which in ("left", "right") else value * PX
        setter = {"left": QTextBlockFormat.setLeftMargin, "right": QTextBlockFormat.setRightMargin,
                  "before": QTextBlockFormat.setTopMargin, "after": QTextBlockFormat.setBottomMargin}[which]
        self._each_block_format(lambda bf: setter(bf, px))

    def set_shading(self, color):
        """The paragraph's background (Word's Shading button)."""
        if color is not None:
            self._shading = QColor(color)

        def change(bf):
            if color is None:
                bf.clearBackground()
            else:
                bf.setBackground(QColor(color))
        self._each_block_format(change)
        self.page.setFocus()

    def _pick_shading(self):
        color = QColorDialog.getColor(self._shading, self, "Shading")
        if color.isValid():
            self.set_shading(color.name())

    def paragraph_dialog(self):
        bf = self.page.textCursor().blockFormat()
        align = bf.alignment() & Qt.AlignHorizontal_Mask
        line = bf.lineHeight() / 100 if bf.lineHeightType() == word_io._PROPORTIONAL and bf.lineHeight() else 1.0
        dlg = ParagraphDialog({"align": Qt.AlignLeft if align in (Qt.AlignLeft, Qt.AlignLeading) else align,
                               "left": bf.leftMargin() / PX, "right": bf.rightMargin() / PX,
                               "first": bf.textIndent() / PX, "before": bf.topMargin() / PX,
                               "after": bf.bottomMargin() / PX, "line": line,
                               "page_break": bool(bf.pageBreakPolicy() & QTextFormat.PageBreak_AlwaysBefore)}, self)
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()

        def change(b):
            b.setAlignment(v["align"])
            b.setLeftMargin(v["left"] * PX)
            b.setRightMargin(v["right"] * PX)
            b.setTextIndent(v["first"] * PX)
            b.setTopMargin(v["before"] * PX)
            b.setBottomMargin(v["after"] * PX)
            b.setLineHeight(v["line"] * 100, word_io._PROPORTIONAL)
            b.setPageBreakPolicy(QTextFormat.PageBreak_AlwaysBefore if v["page_break"] else QTextFormat.PageBreak_Auto)
        self._each_block_format(change)
        self.page.viewport().update()

    def show_marks(self, checked):
        """Show spaces, tabs and paragraph ends (¶), as Word's button does."""
        self._marks = bool(checked)
        theme._settings().setValue("word/show_marks", "true" if self._marks else "false")
        self._apply_marks()

    def _apply_marks(self):
        if not hasattr(self, "_marks"):
            self._marks = theme._settings().value("word/show_marks", "false") == "true"
        modified = self.document.isModified()
        option = self.document.defaultTextOption()
        flags = option.flags()
        marks = QTextOption.ShowTabsAndSpaces | QTextOption.ShowLineAndParagraphSeparators
        option.setFlags(flags | marks if self._marks else flags & ~marks)
        self.document.setDefaultTextOption(option)
        self.document.setModified(modified)
        self.marks_act.setChecked(self._marks)

    # ------------------------------------------------------------ page layout
    def set_page(self, width=None, height=None, margins=None):
        page = word_io.PageSetup(width or self.page_setup.width_pt, height or self.page_setup.height_pt,
                                 tuple(margins) if margins else self.page_setup.margins_pt)
        self.page_setup = page
        word_io.apply_page(self.document, page)
        self.page.setFixedWidth(page.width_px)
        self.page.min_height = round(page.height_pt * PX)
        self.page.fit()
        self.document.setModified(True)
        self._sync_block_controls()
        self.changed.emit()

    def set_orientation(self, landscape):
        w, h = self.page_setup.width_pt, self.page_setup.height_pt
        if (w > h) != landscape:
            left, top, right, bottom = self.page_setup.margins_pt
            self.set_page(h, w, (top, right, bottom, left) if landscape else (bottom, left, top, right))

    def set_paper_size(self, width, height):
        if self.page_setup.width_pt > self.page_setup.height_pt:
            width, height = height, width   # keep landscape
        self.set_page(width, height)

    def page_setup_dialog(self):
        dlg = PageSetupDialog(self.page_setup.width_pt, self.page_setup.height_pt, self.page_setup.margins_pt, self)
        if dlg.exec() == QDialog.Accepted:
            self.set_page(*dlg.values())

    # ------------------------------------------------------------ review
    def text_stats(self, text=None):
        cursor = self.page.textCursor()
        selected = text is None and cursor.hasSelection()
        if text is None:
            text = cursor.selectedText().replace(" ", "\n") if selected else self.document.toPlainText()
        lines = 0
        block = self.document.begin()
        while block.isValid():
            if not selected or (block.position() + block.length() > cursor.selectionStart()
                                and block.position() <= cursor.selectionEnd()):
                lines += max(1, block.layout().lineCount()) if block.layout() else 1
            block = block.next()
        stats = {"Pages": self._page_count(), "Words": len(re.findall(r"\w+", text)),
                 "Characters (no spaces)": len(re.sub(r"\s", "", text)),
                 "Characters (with spaces)": len(text.replace("\n", "")),
                 "Paragraphs": len([p for p in text.split("\n") if p.strip()]), "Lines": lines}
        if selected:
            stats["_scope"] = "In the selected text"
        return stats

    def word_count_dialog(self):
        WordCountDialog(self.text_stats(), self).exec()

    # ------------------------------------------------------------ inserting
    def insert_text(self, text):
        self.page.textCursor().insertText(text)
        self.page.setFocus()

    def symbol_dialog(self):
        family = (self.page.currentCharFormat().fontFamilies() or [""])[0]
        SymbolDialog(self.insert_text, family, self).exec()

    def insert_date_time(self):
        dlg = DateTimeDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.text():
            self.insert_text(dlg.text())

    def insert_horizontal_line(self):
        cursor = self.page.textCursor()
        cursor.beginEditBlock()
        if cursor.block().length() > 1:
            cursor.movePosition(QTextCursor.EndOfBlock)
            cursor.insertBlock()
        bf = QTextBlockFormat()
        bf.setProperty(word_io.HR_PROP, QTextLength(QTextLength.PercentageLength, 100))
        cursor.setBlockFormat(bf)
        after = QTextBlockFormat()
        after.setProperty(word_io.STYLE_PROP, "Normal")
        after.setBottomMargin(8 * PX)
        cursor.insertBlock(after)
        cursor.endEditBlock()
        self.page.setTextCursor(cursor)
        self.page.setFocus()

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

    def _fill_table_menu(self, menu):
        menu.clear()
        menu.addAction(icons.icon("table"), "Insert Table...", self.insert_table_dialog)
        quick = menu.addMenu("Quick Table")
        for rows, cols in ((2, 2), (3, 3), (4, 4), (5, 5), (3, 2), (5, 3), (10, 4)):
            quick.addAction(f"{rows} rows x {cols} columns", lambda r=rows, c=cols: self.insert_table(r, c))
        cursor = self.page.textCursor()
        if cursor.currentTable() is not None:
            self._table_items(menu, cursor)

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
        menu.addSeparator()
        menu.addAction(icons.icon("font-dialog"), "Font...", self.font_dialog)
        menu.addAction(icons.icon("paragraph-dialog"), "Paragraph...", self.paragraph_dialog)
        if cursor.currentList() is not None:
            lists = menu.addMenu(icons.icon("list-ordered"), "List")
            lists.addAction("Restart at 1", self.restart_numbering)
            lists.addAction("Continue Numbering", self.continue_numbering)
            lists.addAction("Set Numbering Value...", self.set_numbering_value)
            lists.addSeparator()
            lists.addAction("Remove List", lambda: self.apply_list("none"))
        if cursor.currentTable() is not None:
            menu.addSeparator()
            self._table_items(menu.addMenu(icons.icon("table"), "Table"), cursor)

    def _table_items(self, sub, cursor):
        table = cursor.currentTable()
        cell = table.cellAt(cursor)
        sub.addSeparator()
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
        sub.addAction("Cell Shading...", lambda: self._shade_cells(table, cell))
        sub.addSeparator()
        sub.addAction("Delete Table", lambda: table.removeRows(0, table.rows()))

    def _shade_cells(self, table, cell):
        color = QColorDialog.getColor(QColor("#d9e2f3"), self, "Cell Shading")
        if not color.isValid():
            return
        cursor = self.page.textCursor()
        if cursor.hasComplexSelection():
            row, rows, col, cols = cursor.selectedTableCells()
            cells = [table.cellAt(r, c) for r in range(row, row + rows) for c in range(col, col + cols)]
        else:
            cells = [cell]
        for c in cells:
            fmt = c.format().toTableCellFormat()
            fmt.setBackground(color)
            c.setFormat(fmt)

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
        option = copy.defaultTextOption()   # formatting marks are for the screen only
        option.setFlags(option.flags() & ~(QTextOption.ShowTabsAndSpaces | QTextOption.ShowLineAndParagraphSeparators))
        copy.setDefaultTextOption(option)
        width = self.page_setup.width_pt * PX
        height = self.page_setup.height_pt * PX
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
                os.path.expanduser("~"), "Documents", os.path.splitext(self.display_name())[0] + ".pdf")
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
