"""In-place text editing on the page.

InlineTextEditor is a borderless text area laid directly over a PageWidget,
outlined with a dashed box. It grows with the text (or wraps at a fixed
width when the box was dragged out), previews the chosen font, size and
colour at the current zoom, and emits `finished` on Esc or Ctrl+Enter.
Clicking elsewhere on the page also finishes it (handled by DocumentTab)."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QTextCursor, QTextOption
from PySide6.QtWidgets import QFrame, QTextEdit

from . import fonts
from .pdf_ops import FIRST_BASELINE

BORDER = 1          # px, the dashed outline drawn by the style sheet
CARET_ROOM = 6      # px kept free after the longest line so the caret shows


class InlineTextEditor(QTextEdit):
    finished = Signal()

    def __init__(self, page_widget, origin_px, px_per_pt, fontname, fontsize, color,
                 fixed_width_px=None, text=""):
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

        self.set_style(fontname, fontsize, color)
        self.setPlainText(text)
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
            longest = max((metrics.horizontalAdvance(line) for line in self.toPlainText().split("\n")), default=0)
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
        super().keyPressEvent(event)
