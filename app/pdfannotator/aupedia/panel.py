"""The speech bubble: type a question or a job, pick "Show me" or "Do it for
me", and AUPedia's answers type themselves out (its mouth moves as they do).
Plus its settings: the Claude API key."""
import html
import math
import re

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import icons, theme
from ..dialogs import _button_row, _dialog_layout, _header, _primary
from . import anim, brain

HAND_FONT = "Caveat"
SUGGESTIONS = ["Sign this document", "Convert this PDF to Word", "How do I highlight text?",
               "Turn on dark mode", "Combine two PDFs", "Rotate this page"]
CHARS_PER_SECOND = 240


def hand_font(size):
    font = QFont(HAND_FONT)
    font.setPixelSize(size)
    return font


def rich(text):
    """Plain text with **bold** and numbered lines, as HTML."""
    out = html.escape(text.strip())
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    return out.replace("\n", "<br>")


class AskBubble(QWidget):
    """A floating card with a hand-drawn edge and a tail pointing at the scribble."""
    asked = Signal(str, str)          # text, mode ("do" or "show")
    stopped = Signal()
    settings_requested = Signal()
    reset_requested = Signal()
    closed = Signal()
    talking = Signal(float)           # how open the mouth is while text types out
    WIDTH, HEIGHT = 380, 430
    TAIL = 18

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self.setObjectName("aupediaBubble")
        self.resize(self.WIDTH, self.HEIGHT)
        self.tail_side = "right"      # the tail points down-right, at the scribble
        self.busy = False
        self._queue = []              # (html, plain) still to type out
        self._typing = None           # [html, plain, shown]
        self._log = []                # finished HTML blocks
        self._confirm_cb = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14 + self.TAIL)
        outer.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("AUPedia")
        title.setFont(hand_font(26))
        title.setObjectName("aupediaTitle")
        head.addWidget(title)
        self.status = QLabel()
        self.status.setObjectName("muted")
        head.addWidget(self.status, 1, Qt.AlignBottom)
        for name, tip, signal in (("rotate-left", "Start over", self.reset_requested),
                                  ("settings", "AUPedia settings", self.settings_requested),
                                  ("close", "Close (Esc)", self.closed)):
            btn = QToolButton()
            btn.setIcon(icons.icon(name))
            btn.setAutoRaise(True)
            btn.setToolTip(tip)
            btn.clicked.connect(signal.emit)
            head.addWidget(btn)
        outer.addLayout(head)

        self.view = QTextBrowser()
        self.view.setObjectName("aupediaLog")
        self.view.setOpenExternalLinks(True)
        self.view.setFrameShape(QTextBrowser.NoFrame)
        self.view.viewport().setAutoFillBackground(False)
        self.view.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        outer.addWidget(self.view, 1)

        self.chips = QWidget()
        chip_box = QVBoxLayout(self.chips)
        chip_box.setContentsMargins(0, 0, 0, 0)
        chip_box.setSpacing(4)
        row = None
        for i, text in enumerate(SUGGESTIONS):
            if i % 2 == 0:
                row = QHBoxLayout()
                row.setSpacing(4)
                chip_box.addLayout(row)
            chip = QPushButton(text)
            chip.setObjectName("aupediaChip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _c=False, t=text: self._ask(t))
            row.addWidget(chip)
        outer.addWidget(self.chips)

        self.confirm_bar = QWidget()
        cb = QHBoxLayout(self.confirm_bar)
        cb.setContentsMargins(0, 0, 0, 0)
        self.confirm_text = QLabel()
        self.confirm_text.setWordWrap(True)
        cb.addWidget(self.confirm_text, 1)
        no = QPushButton("No")
        no.clicked.connect(lambda: self._answer_confirm(False))
        yes = _primary("Yes, go ahead")
        yes.clicked.connect(lambda: self._answer_confirm(True))
        cb.addWidget(no)
        cb.addWidget(yes)
        self.confirm_bar.hide()
        outer.addWidget(self.confirm_bar)

        modes = QHBoxLayout()
        modes.setSpacing(4)
        self.mode_group = QButtonGroup(self)
        self.show_btn = QPushButton("Show me")
        self.do_btn = QPushButton("Do it for me")
        for btn, tip in ((self.show_btn, "I point at things and tell you the steps"),
                         (self.do_btn, "I click the buttons for you")):
            btn.setCheckable(True)
            btn.setObjectName("aupediaMode")
            btn.setToolTip(tip)
            self.mode_group.addButton(btn)
            modes.addWidget(btn)
        self.do_btn.setChecked(True)
        modes.addStretch()
        outer.addLayout(modes)

        ask = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask me anything, or tell me what to do...")
        self.input.returnPressed.connect(lambda: self._ask(self.input.text()))
        ask.addWidget(self.input, 1)
        self.send_btn = QToolButton()
        self.send_btn.setIcon(icons.icon("send"))
        self.send_btn.setToolTip("Send (Enter)")
        self.send_btn.clicked.connect(self._send_or_stop)
        ask.addWidget(self.send_btn)
        outer.addLayout(ask)

        self.type_timer = QTimer(self)
        self.type_timer.setInterval(16)
        self.type_timer.timeout.connect(self._type_step)
        self._greet()

    # ---- conversation
    def mode(self):
        return "do" if self.do_btn.isChecked() else "show"

    def set_status(self, text):
        self.status.setText(text)

    def _greet(self):
        self._log = []
        self.view.clear()
        self.chips.show()
        self.say("Hi! I'm **AUPedia**. Ask me how to do anything here, or tell me what you want done and "
                 "I'll do it for you.")

    def reset(self):
        self._queue.clear()
        self._typing = None
        self.type_timer.stop()
        self._greet()

    def _ask(self, text):
        text = text.strip()
        if not text:
            return
        self.input.clear()
        self.chips.hide()
        self._flush_typing()
        self._log.append(f'<p align="right" style="color:{theme.TEXT_MUTED}; margin:8px 0 2px 40px">{rich(text)}</p>')
        self._render()
        self.asked.emit(text, self.mode())

    def _send_or_stop(self):
        if self.busy:
            self.stopped.emit()
        else:
            self._ask(self.input.text())

    def set_busy(self, busy):
        self.busy = busy
        self.send_btn.setIcon(icons.icon("stop" if busy else "send"))
        self.send_btn.setToolTip("Stop" if busy else "Send (Enter)")

    def say(self, text):
        """AUPedia's words: typed out a few characters a frame."""
        self._queue.append((rich(text), text))
        if self._typing is None:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = None
            self.type_timer.stop()
            self.talking.emit(0.0)
            return
        html_text, plain = self._queue.pop(0)
        self._typing = [html_text, plain, 0.0, anim.now()]
        self.type_timer.start()

    def _type_step(self):
        if self._typing is None:
            return
        html_text, plain, _shown, started = self._typing
        n = (anim.now() - started) * CHARS_PER_SECOND
        if n >= len(plain):
            self._log.append(self._para(html_text))
            self._typing = None
            self._render()
            self._next()
            return
        self._typing[2] = n
        shown = plain[:int(n)]
        if shown.count("**") % 2:          # halfway through something bold: show it bold already
            shown += "**"
        self._render(partial=rich(shown))
        self.talking.emit(0.5 + 0.5 * abs(math.sin(n / 3.0)))

    def _flush_typing(self):
        if self._typing is not None:
            self._log.append(self._para(self._typing[0]))
            self._typing = None
        while self._queue:
            self._log.append(self._para(self._queue.pop(0)[0]))
        self.type_timer.stop()
        self.talking.emit(0.0)

    def _para(self, html_text):
        return f'<p style="margin:6px 0">{html_text}</p>'

    def _render(self, partial=None):
        body = "".join(self._log) + (self._para(partial + "▏") if partial is not None else "")
        self.view.setHtml(f'<div style="color:{theme.TEXT}; font-size:10.5pt">{body}</div>')
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def is_typing(self):
        return self._typing is not None or bool(self._queue)

    # ---- "are you sure?"
    def confirm(self, text, callback):
        self._confirm_cb = callback
        self.confirm_text.setText(text)
        self.confirm_bar.show()

    def _answer_confirm(self, yes):
        self.confirm_bar.hide()
        cb, self._confirm_cb = self._confirm_cb, None
        if cb:
            cb(yes)

    def cancel_confirm(self):
        if self._confirm_cb is not None:
            self._answer_confirm(False)

    # ---- the card
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.closed.emit()
            return
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self._restyle()
        self.input.setFocus()

    def _restyle(self):
        """Chips and the mode switch, in the current theme's colours (light or dark)."""
        self.setStyleSheet(f"""
            QLabel#aupediaTitle {{ color: {theme.TEXT}; }}
            QPushButton#aupediaChip {{ background: {theme.ACCENT_SOFT}; border: 1px solid {theme.ACCENT_SOFT_BORDER};
                color: {theme.TEXT}; border-radius: 12px; padding: 4px 10px; text-align: left; }}
            QPushButton#aupediaChip:hover {{ border-color: {theme.ACCENT}; }}
            QPushButton#aupediaMode {{ background: transparent; border: 1px solid {theme.BORDER_STRONG};
                color: {theme.TEXT_MUTED}; border-radius: 12px; padding: 3px 12px; }}
            QPushButton#aupediaMode:checked {{ background: {theme.ACCENT}; border-color: {theme.ACCENT};
                color: #ffffff; }}
        """)
        self._render()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6 - self.TAIL)
        path = QPainterPath()
        path.addRoundedRect(r, 18, 18)
        tail = QPainterPath()
        tx = r.right() - 46 if self.tail_side == "right" else r.left() + 46
        tip = QPointF(tx + (22 if self.tail_side == "right" else -22), r.bottom() + self.TAIL)
        tail.moveTo(tx - 14, r.bottom() - 2)
        tail.quadTo(QPointF(tx + 2, r.bottom() + 8), tip)
        tail.quadTo(QPointF(tx + 6, r.bottom() + 4), QPointF(tx + 12, r.bottom() - 2))
        tail.closeSubpath()
        path = path.united(tail)
        # soft shadow, paper, then two passes of a slightly shaky pen round the edge
        for k, a in ((5, 14), (3, 20), (1, 26)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, a))
            p.drawPath(path.translated(0, k))
        paper = QColor(theme.SURFACE)
        p.setBrush(paper)
        p.drawPath(path)
        ink = QColor(theme.TEXT)
        p.setBrush(Qt.NoBrush)
        for k, (dx, dy, alpha, width) in enumerate(((0, 0, 230, 2.0), (0.8, -0.6, 90, 1.2))):
            c = QColor(ink)
            c.setAlpha(alpha)
            p.setPen(QPen(c, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path.translated(dx, dy))
        p.end()


class AupediaSettingsDialog(QDialog):
    """The Claude API key (the person's own) and whether AUPedia shows up on start."""

    def __init__(self, show_on_start=True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AUPedia Settings")
        self.setMinimumWidth(500)
        layout = _dialog_layout(self)
        layout.addLayout(_header("AUPedia", "Without a key, AUPedia finds buttons for you by their names. With a "
                                            "Claude API key it answers any question about the app and does "
                                            "multi-step jobs for you."))
        self.key = QLineEdit(brain.saved_key())
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("sk-ant-...")
        row = QHBoxLayout()
        row.addWidget(QLabel("Claude API key"))
        row.addWidget(self.key, 1)
        get = QPushButton("Get a Key...")
        get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(brain.KEYS_URL)))
        row.addWidget(get)
        layout.addLayout(row)
        env = not brain.saved_key() and bool(brain.api_key())
        note = ("Using the ANTHROPIC_API_KEY set on this computer. Paste a key here to use a different one.\n"
                if env else "")
        self.status = QLabel(note + "Your key is stored encrypted for your Windows account. Questions, the list "
                             "of menus, and what's open (file names, page, tool) are sent to Anthropic to answer "
                             "them; your documents' contents are not. Each question costs a little on your "
                             "Claude account.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.on_start = QCheckBox("Show AUPedia when the app starts")
        self.on_start.setChecked(show_on_start)
        layout.addWidget(self.on_start)
        test = QPushButton("Test Key")
        test.clicked.connect(self._test)
        forget = QPushButton("Remove Key")
        forget.clicked.connect(lambda: self.key.clear())
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = _primary("Save")
        ok.clicked.connect(self.accept)
        layout.addLayout(_button_row(cancel, ok, leading=[test, forget]))

    def _test(self):
        from ..cloud import worker

        key = self.key.text().strip() or brain.api_key()
        if not key:
            self.status.setText("Paste a key first.")
            return
        self.status.setText("Checking the key...")
        worker.run(lambda: brain.Claude(key).check(),
                   lambda _r: self.status.setText("The key works. AUPedia is ready to think."),
                   lambda exc: self.status.setText(brain.friendly_error(exc)))

    def accept(self):
        key = self.key.text().strip()
        if key:
            brain.save_key(key)
        else:
            brain.forget_key()
        super().accept()
