"""In-place text editing on the page.

InlineTextEditor is a borderless text area laid directly over a PageWidget,
outlined with a dashed box. It grows with the text (or wraps at a fixed
width when the box was dragged out), previews the chosen font, size and
colour at the current zoom, and emits `finished` on Esc or Ctrl+Enter.
Clicking elsewhere on the page also finishes it (handled by DocumentTab).

Parts of the text can be bold, italic, underlined, struck through,
superscript or subscript (toggle() and the usual shortcuts: Ctrl+B / I / U,
Ctrl+Shift+= superscript, Ctrl+= subscript); runs() hands that formatting
to pdf_ops as runs."""
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QIcon, QPainter, QPen, QPixmap, QTextBlockFormat, QTextCharFormat, QTextCursor,
    QTextListFormat, QTextOption,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QTextEdit, QToolButton

from . import fonts, icons, theme
from .pdf_ops import FIRST_BASELINE, LIST_INDENT, LIST_STYLES, list_marker, normalize_runs

BORDER = 1          # px, the dashed outline drawn by the style sheet
CARET_ROOM = 6      # px kept free after the longest line so the caret shows
_ALIGN = {0: Qt.AlignLeft, 1: Qt.AlignHCenter, 2: Qt.AlignRight, 3: Qt.AlignJustify}
_QT_LIST = {"disc": QTextListFormat.ListDisc, "circle": QTextListFormat.ListCircle,
            "square": QTextListFormat.ListSquare, "decimal": QTextListFormat.ListDecimal,
            "lower-alpha": QTextListFormat.ListLowerAlpha, "upper-alpha": QTextListFormat.ListUpperAlpha,
            "lower-roman": QTextListFormat.ListLowerRoman, "upper-roman": QTextListFormat.ListUpperRoman}


def list_format(style):
    """The Qt list format that previews a pdf_ops.LIST_STYLES style."""
    _label, kind, prefix, suffix = LIST_STYLES[style]
    fmt = QTextListFormat()
    fmt.setStyle(_QT_LIST[kind])
    fmt.setNumberPrefix(prefix)
    fmt.setNumberSuffix(suffix or (" " if kind in ("disc", "circle", "square") else "."))
    return fmt


def list_icon(style, width=40, height=40):
    """A preview of a list style for its menu: three items, each a marker
    and a grey line, the way Word's bullet and numbering libraries show them.
    The bullets are drawn, so any interface font will do. Show it at
    LIST_ICON_SIZE (style a menu with list_menu_style())."""
    ratio = 2.0
    pix = QPixmap(round(width * ratio), round(height * ratio))
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    ink = QColor(theme.TEXT)
    font = QFont()
    font.setPixelSize(10)
    font.setBold(True)
    p.setFont(font)
    _label, kind, _prefix, _suffix = LIST_STYLES[style]
    for i in range(3):
        y = 7 + i * 13
        if kind == "disc":
            p.setPen(Qt.NoPen)
            p.setBrush(ink)
            p.drawEllipse(QPointF(10, y), 2.6, 2.6)
        elif kind == "circle":
            p.setPen(QPen(ink, 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(10, y), 2.6, 2.6)
        elif kind == "square":
            p.setPen(Qt.NoPen)
            p.setBrush(ink)
            p.drawRect(QRectF(7.6, y - 2.4, 4.8, 4.8))
        else:
            p.setPen(ink)
            p.drawText(QRectF(0, y - 7, 21, 14), Qt.AlignRight | Qt.AlignVCenter, list_marker(style, i + 1))
        line = QColor(theme.TEXT_MUTED)
        line.setAlpha(140)
        p.setPen(QPen(line, 2.2, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(25, y), QPointF(width - 4, y))
    p.end()
    return QIcon(pix)


LIST_ICON_SIZE = 40


def list_menu_style(menu):
    """Big enough icons in `menu` for the list previews to be read."""
    menu.setStyleSheet(f"QMenu {{ icon-size: {LIST_ICON_SIZE}px; }}")


def list_style_of(fmt):
    """The LIST_STYLES key a Qt list format shows ("" if none matches)."""
    for key, (_label, kind, prefix, suffix) in LIST_STYLES.items():
        if _QT_LIST[kind] == fmt.style() and (kind in ("disc", "circle", "square") or
                                              (fmt.numberPrefix() == prefix and (fmt.numberSuffix() or ".") == suffix)):
            return key
    return ""


def char_format(run):
    fmt = QTextCharFormat()
    fmt.setFontWeight(QFont.Bold if run.get("b") else QFont.Normal)
    fmt.setFontItalic(bool(run.get("i")))
    fmt.setFontUnderline(bool(run.get("u")))
    fmt.setFontStrikeOut(bool(run.get("s")))
    fmt.setVerticalAlignment({1: QTextCharFormat.AlignSuperScript, -1: QTextCharFormat.AlignSubScript}.get(
        run.get("v", 0), QTextCharFormat.AlignNormal))
    return fmt


def run_style(fmt):
    """The run flags of a character format."""
    v = fmt.verticalAlignment()
    style = {}
    if fmt.fontWeight() >= QFont.Bold:
        style["b"] = 1
    if fmt.fontItalic():
        style["i"] = 1
    if fmt.fontUnderline():
        style["u"] = 1
    if fmt.fontStrikeOut():
        style["s"] = 1
    if v == QTextCharFormat.AlignSuperScript:
        style["v"] = 1
    elif v == QTextCharFormat.AlignSubScript:
        style["v"] = -1
    return style


class FormatBar(QFrame):
    """The formatting buttons floating just above the box being typed in (or
    below it near the top of the page), following it as it grows. They are
    the main window's text-format actions, so the toolbar agrees."""

    def __init__(self, page_widget, actions, editor, spacing_menu=None, list_menu=None):
        super().__init__(page_widget)
        self.setObjectName("formatBar")
        self.editor = editor
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(1)
        def menu_button(icon, tip, menu):
            button = QToolButton(self)
            button.setIcon(icons.icon(icon))
            button.setToolTip(tip)
            button.setMenu(menu)
            button.setPopupMode(QToolButton.InstantPopup)
            button.setFocusPolicy(Qt.NoFocus)
            button.setAutoRaise(True)
            button.setIconSize(QSize(16, 16))
            row.addWidget(button)
            return button

        def rule():
            line = QFrame(self)
            line.setObjectName("penRule")
            line.setFixedSize(1, 18)
            row.addSpacing(3)
            row.addWidget(line)
            row.addSpacing(3)

        for kind, act in actions.items():
            if list_menu is not None and kind in LIST_STYLES:
                if kind == "bullet":   # every list style: one button with a menu
                    rule()
                    menu_button("list-bullet", "Bullets and numbering", list_menu)
                continue
            if kind in ("align0", "bullet"):
                rule()
            button = QToolButton(self)
            button.setDefaultAction(act)
            button.setFocusPolicy(Qt.NoFocus)   # typing carries on
            button.setAutoRaise(True)
            button.setIconSize(QSize(16, 16))
            row.addWidget(button)
        if spacing_menu is not None:
            menu_button("line-spacing", "Line spacing", spacing_menu)
        editor.installEventFilter(self)
        self.follow()
        self.show()

    def follow(self):
        self.adjustSize()
        box = self.editor.geometry()
        page = self.parentWidget()
        y = box.y() - self.height() - 6
        if y < 2:
            y = box.bottom() + 6
        x = min(max(2, box.x()), max(2, page.width() - self.width() - 2))
        self.move(x, y)
        self.raise_()

    def eventFilter(self, obj, event):
        if obj is self.editor and event.type() in (QEvent.Move, QEvent.Resize):
            self.follow()
        return False


class InlineTextEditor(QTextEdit):
    finished = Signal()

    def __init__(self, page_widget, origin_px, px_per_pt, fontname, fontsize, color,
                 fixed_width_px=None, text="", runs=None, align=0, paras=None, spacing=1.0):
        super().__init__(page_widget)
        self.setObjectName("inlineText")
        self.setAcceptRichText(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 0, 0, 0)
        self.document().setDocumentMargin(0)
        self.setWordWrapMode(QTextOption.WordWrap)
        self.setCursor(Qt.IBeamCursor)

        self._origin_px = origin_px          # where the text's top-left sits on the page
        self._px_per_pt = px_per_pt
        self.fixed_width_px = fixed_width_px
        self.fontname, self.fontsize, self.color = fontname, fontsize, QColor(color)
        self.align = 0
        self.spacing = 1.0

        self.set_style(fontname, fontsize, color)
        self.setPlainText(text)
        if runs:
            self.set_runs(runs)
        self.set_paras(paras)
        self.set_alignment(align)
        self.set_line_spacing(spacing)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.setTextCursor(cursor)
        self.textChanged.connect(self._fit)
        self._fit()

    # ---- style
    def set_style(self, fontname, fontsize, color):
        self.fontname, self.fontsize, self.color = fontname, float(fontsize), QColor(color)
        font = fonts.preview_font(fontname, round(self.fontsize * self._px_per_pt))
        self.setFont(font)
        self.document().setDefaultFont(font)
        self.document().setIndentWidth(LIST_INDENT * self.fontsize * self._px_per_pt)   # lists indent as in the PDF
        self.setStyleSheet(f"QTextEdit#inlineText {{ color: {self.color.name()}; }}")
        self._fit()

    # ---- formatting
    def set_runs(self, runs):
        self.clear()
        cursor = QTextCursor(self.document())
        for run in normalize_runs(runs):
            fmt = char_format(run)
            for k, part in enumerate(run["t"].split("\n")):
                if k:
                    cursor.insertBlock()
                cursor.insertText(part, fmt)

    def runs(self):
        """The text as runs: [{"t": text, "b": 1, ...}] (lines joined by newlines)."""
        out = []
        doc = self.document()
        block = doc.begin()
        first = True
        while block.isValid():
            if not first:
                out.append({"t": "\n"})
            first = False
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    out.append({"t": frag.text(), **run_style(frag.charFormat())})
                it += 1
            block = block.next()
        return normalize_runs(out)

    def toggle(self, kind):
        """Bold "b", italic "i", underline "u", strike "s", superscript "sup" or
        subscript "sub": on the selection, or for what is typed next."""
        cur = self.currentCharFormat()
        fmt = QTextCharFormat()
        if kind == "b":
            fmt.setFontWeight(QFont.Normal if cur.fontWeight() >= QFont.Bold else QFont.Bold)
        elif kind == "i":
            fmt.setFontItalic(not cur.fontItalic())
        elif kind == "u":
            fmt.setFontUnderline(not cur.fontUnderline())
        elif kind == "s":
            fmt.setFontStrikeOut(not cur.fontStrikeOut())
        elif kind in ("sup", "sub"):
            want = QTextCharFormat.AlignSuperScript if kind == "sup" else QTextCharFormat.AlignSubScript
            fmt.setVerticalAlignment(QTextCharFormat.AlignNormal if cur.verticalAlignment() == want else want)
        else:
            return
        self.mergeCurrentCharFormat(fmt)
        self._fit()

    # ---- lists and line spacing (paragraph-level)
    def paras(self):
        """Per paragraph: a pdf_ops.LIST_STYLES key, or ""."""
        out = []
        block = self.document().begin()
        while block.isValid():
            lst = block.textList()
            out.append(list_style_of(lst.format()) if lst else "")
            block = block.next()
        return out

    def set_paras(self, paras):
        block, current, kind_of = self.document().begin(), None, None
        for kind in paras or []:
            if not block.isValid():
                break
            if kind in LIST_STYLES:
                if current is not None and kind_of == kind:
                    current.add(block)
                else:
                    current, kind_of = QTextCursor(block).createList(list_format(kind)), kind
            else:
                current = kind_of = None
            block = block.next()
        self._fit()

    def toggle_list(self, kind):
        """Bullets or numbering (`kind`: a LIST_STYLES key, or "none") on the
        paragraphs of the selection; off again when they already have it."""
        cursor = self.textCursor()
        lst = cursor.currentList()
        if kind == "none" or (lst is not None and list_style_of(lst.format()) == kind):
            start, end = sorted((cursor.selectionStart(), cursor.selectionEnd()))
            block = self.document().findBlock(start)
            while block.isValid() and block.position() <= end:
                if block.textList() is not None:
                    block.textList().remove(block)
                    fmt = block.blockFormat()
                    fmt.setIndent(0)
                    QTextCursor(block).setBlockFormat(fmt)
                block = block.next()
        elif lst is not None:   # another style: the whole list changes to it
            lst.setFormat(list_format(kind))
        else:
            cursor.createList(list_format(kind))
        self.currentCharFormatChanged.emit(self.currentCharFormat())   # the buttons follow
        self._fit()

    def set_line_spacing(self, spacing):
        self.spacing = float(spacing or 1.0)
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.Document)
        block = QTextBlockFormat()
        block.setLineHeight(self.spacing * 100, QTextBlockFormat.ProportionalHeight.value)
        cursor.mergeBlockFormat(block)
        self._fit()

    def set_alignment(self, align):
        self.align = int(align or 0)
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.Document)
        block = QTextBlockFormat()
        block.setAlignment(_ALIGN.get(self.align, Qt.AlignLeft))
        cursor.mergeBlockFormat(block)
        self._fit()

    # ---- geometry
    def _fit(self):
        page = self.parentWidget()
        metrics = QFontMetricsF(self.font())
        # Line the preview's first baseline up with where the PDF will draw it
        lift = metrics.ascent() - FIRST_BASELINE * self.fontsize * self._px_per_pt
        x = self._origin_px.x() - BORDER
        y = round(self._origin_px.y() - BORDER - lift)
        max_w = max(40, page.width() - x - 2)
        if self.fixed_width_px:
            self.setLineWrapMode(QTextEdit.FixedPixelWidth)
            self.setLineWrapColumnOrWidth(round(self.fixed_width_px))
            width = self.fixed_width_px + CARET_ROOM
        else:
            self.setLineWrapMode(QTextEdit.NoWrap)
            self.document().setTextWidth(-1)
            longest = self.document().idealWidth()   # measures bold and scripts as shown
            width = max(longest + CARET_ROOM, metrics.horizontalAdvance("M") * 4)
        width = min(width, max_w)
        self.document().setTextWidth(width if self.fixed_width_px else -1)
        height = max(self.document().size().height(), metrics.lineSpacing())
        self.setGeometry(x, y, round(width) + 2 * BORDER, round(height) + 2 * BORDER)

    # ---- keys
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape or (
            event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier
        ):
            self.finished.emit()
            return
        if event.modifiers() & Qt.ControlModifier:
            kind = {Qt.Key_B: "b", Qt.Key_I: "i", Qt.Key_U: "u", Qt.Key_Plus: "sup",
                    Qt.Key_Equal: "sub"}.get(event.key())
            if kind == "sub" and event.modifiers() & Qt.ShiftModifier:
                kind = "sup"   # Ctrl+Shift+= on keyboards that report "="
            if kind:
                self.toggle(kind)
                return
        super().keyPressEvent(event)
