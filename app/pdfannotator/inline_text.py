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
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextOption
from PySide6.QtWidgets import QFrame, QHBoxLayout, QTextEdit, QToolButton

from . import fonts
from .pdf_ops import FIRST_BASELINE, normalize_runs

BORDER = 1          # px, the dashed outline drawn by the style sheet
CARET_ROOM = 6      # px kept free after the longest line so the caret shows
_ALIGN = {0: Qt.AlignLeft, 1: Qt.AlignHCenter, 2: Qt.AlignRight}


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

    def __init__(self, page_widget, actions, editor):
        super().__init__(page_widget)
        self.setObjectName("formatBar")
        self.editor = editor
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(1)
        for kind, act in actions.items():
            if kind == "align0":
                rule = QFrame(self)
                rule.setObjectName("penRule")
                rule.setFixedSize(1, 18)
                row.addSpacing(3)
                row.addWidget(rule)
                row.addSpacing(3)
            button = QToolButton(self)
            button.setDefaultAction(act)
            button.setFocusPolicy(Qt.NoFocus)   # typing carries on
            button.setAutoRaise(True)
            button.setIconSize(QSize(16, 16))
            row.addWidget(button)
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
                 fixed_width_px=None, text="", runs=None, align=0):
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

        self.set_style(fontname, fontsize, color)
        self.setPlainText(text)
        if runs:
            self.set_runs(runs)
        self.set_alignment(align)
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

    def set_alignment(self, align):
        self.align = int(align or 0)
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.Document)
        block = QTextBlockFormat()
        block.setAlignment(_ALIGN.get(self.align, Qt.AlignLeft))
        cursor.mergeBlockFormat(block)

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
