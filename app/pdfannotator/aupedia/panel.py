"""Talking with AUPedea: the chat (type a question or a job, pick "Show me" or
"Do it for me"; answers type themselves out as its mouth moves), the little
speech bubble over its head (what it hears and does while the chat is
tucked away), and its settings (which AI, key and model)."""
import html
import math
import os
import re
import sys

from PySide6.QtCore import (
    QEasingCurve, QObject, QParallelAnimationGroup, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFormLayout, QGraphicsOpacityEffect, QHBoxLayout,
    QLabel, QLineEdit, QProgressBar, QPushButton, QRadioButton, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from .. import icons, theme
from ..dialogs import _button_row, _dialog_layout, _header, _primary
from . import anim, brain

HAND_FONT = "Caveat"
SUGGESTIONS = ["Summarise this page", "Highlight the title", "Draw a star", "Convert to Word"]
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
    """The chat: a small see-through card with a hand-drawn edge and a tail pointing at the scribble.
    It grows with the conversation (up to a point), turns solid while you're over it or typing,
    fades in and out, and can be dragged by its top."""
    asked = Signal(str, str)          # text, mode ("do" or "show")
    stopped = Signal()
    settings_requested = Signal()
    reset_requested = Signal()
    closed = Signal()
    talking = Signal(float)           # how open the mouth is while text types out
    listen_toggled = Signal()
    WIDTH = 318
    TAIL = 14
    HEAD = 34                          # the draggable top strip
    LOG_MIN, LOG_MAX = 40, 200
    REST, ACTIVE = 0.9, 1.0            # see-through until you're over it or typing
    PLACEHOLDER = "Ask, or tell me what to do..."

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setObjectName("aupediaBubble")
        self.setFixedWidth(self.WIDTH)
        self.tail_side = "right"      # the tail points down-right, at the scribble
        self.busy = False
        self._queue = []              # (html, plain) still to type out
        self._typing = None           # [html, plain, shown, started]
        self._log = []                # finished HTML blocks
        self._confirm_cb = None
        self._partial = None          # words heard so far, shown in the input box
        self._drag = None
        self._hover = False
        self.moved_by_hand = False
        self._anim = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 10 + self.TAIL)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(2)
        title = QLabel("AUPedea")
        title.setFont(hand_font(22))
        title.setObjectName("aupediaTitle")
        head.addWidget(title)
        self.status = QLabel()
        self.status.setObjectName("aupediaStatus")
        head.addWidget(self.status, 1, Qt.AlignBottom)
        for name, tip, signal in (("rotate-left", "Start over", self.reset_requested),
                                  ("settings", "AUPedea settings", self.settings_requested),
                                  ("close", "Close (Esc)", self.closed)):
            btn = QToolButton()
            btn.setObjectName("aupediaHeadButton")
            btn.setIcon(icons.icon(name))
            btn.setIconSize(QSize(14, 14))
            btn.setAutoRaise(True)
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(signal.emit)
            head.addWidget(btn)
        outer.addLayout(head)

        self.view = QTextBrowser()
        self.view.setObjectName("aupediaLog")
        self.view.setOpenExternalLinks(True)
        self.view.setFrameShape(QTextBrowser.NoFrame)
        self.view.viewport().setAutoFillBackground(False)
        self.view.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)       # on only once it's at its tallest
        self.view.setFixedHeight(self.LOG_MIN)
        outer.addWidget(self.view)

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
        yes = _primary("Yes")
        yes.clicked.connect(lambda: self._answer_confirm(True))
        cb.addWidget(no)
        cb.addWidget(yes)
        self.confirm_bar.hide()
        outer.addWidget(self.confirm_bar)

        ask = QHBoxLayout()
        ask.setSpacing(4)
        self.mic_btn = QToolButton()
        self.mic_btn.setObjectName("aupediaMic")
        self.mic_btn.setIcon(icons.icon("mic"))
        self.mic_btn.setCheckable(True)
        self.mic_btn.setCursor(Qt.PointingHandCursor)
        self.mic_btn.setToolTip("Talk to me (Ctrl+Shift+Space): I listen, and your words appear as you speak")
        self.mic_btn.clicked.connect(lambda: self.listen_toggled.emit())
        ask.addWidget(self.mic_btn)
        self.input = QLineEdit()
        self.input.setObjectName("aupediaInput")
        self.input.setPlaceholderText(self.PLACEHOLDER)
        self.input.returnPressed.connect(lambda: self._ask(self.input.text()))
        self.input.textEdited.connect(self._typed)
        ask.addWidget(self.input, 1)
        self.send_btn = QToolButton()
        self.send_btn.setObjectName("aupediaSend")
        self.send_btn.setIcon(icons.icon("send"))
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setToolTip("Send (Enter)")
        self.send_btn.clicked.connect(self._send_or_stop)
        ask.addWidget(self.send_btn)
        outer.addLayout(ask)

        modes = QHBoxLayout()
        modes.setSpacing(3)
        self.mode_group = QButtonGroup(self)
        self.show_btn = QPushButton("Show me")
        self.do_btn = QPushButton("Do it for me")
        for btn, tip in ((self.show_btn, "I point at things and tell you the steps"),
                         (self.do_btn, "I click, mark up, write and draw for you")):
            btn.setCheckable(True)
            btn.setObjectName("aupediaMode")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            self.mode_group.addButton(btn)
            modes.addWidget(btn)
        self.do_btn.setChecked(True)
        modes.addStretch()
        outer.addLayout(modes)
        self.input.setToolTip("Enter to send, Esc to close")

        self.type_timer = QTimer(self)
        self.type_timer.setInterval(16)
        self.type_timer.timeout.connect(self._type_step)
        QApplication.instance().focusChanged.connect(self._focus_changed)
        self._greet()

    # ---- conversation
    def mode(self):
        return "do" if self.do_btn.isChecked() else "show"

    def set_status(self, text):
        self.status.setToolTip(text)
        self.status.setText(self.status.fontMetrics().elidedText(text, Qt.ElideRight, 150))

    def _greet(self):
        self._log = []
        self.view.clear()
        self.chips.show()
        self.say("Hi! I'm **AUPedea**. Ask me anything, or tell me what to do.", instant=True)

    def reset(self):
        self._queue.clear()
        self._typing = None
        self.type_timer.stop()
        self._greet()

    def _ask(self, text):
        text = text.strip()
        if not text:
            return
        self.show_user(text)
        self.asked.emit(text, self.mode())

    def show_user(self, text, spoken=False):
        """The person's words in the conversation (spoken ones get a little microphone)."""
        self.input.clear()
        self._partial = None
        self._set_input_style(False)
        self.chips.hide()
        self._flush_typing()
        mark = "\U0001F3A4 " if spoken else ""
        self._log.append(f'<p align="right" style="color:{theme.TEXT_MUTED}; margin:6px 0 2px 36px">'
                         f'{mark}{rich(text)}</p>')
        self._render()

    # ---- listening
    def set_listening(self, status, detail=""):
        """off, loading, listening or paused (detail: e.g. download progress)."""
        on = status in ("loading", "listening", "paused")
        self.mic_btn.setChecked(on)
        self.mic_btn.setProperty("live", status == "listening")
        self.mic_btn.style().unpolish(self.mic_btn)
        self.mic_btn.style().polish(self.mic_btn)
        self.input.setPlaceholderText({"loading": detail or "Getting my ears ready...",
                                       "listening": "Listening... just talk",
                                       "paused": "Paused while you're in another app"}.get(status, self.PLACEHOLDER))
        if not on:
            self.show_partial("")

    def show_partial(self, text):
        """Words heard so far, live in the box (never over something the person typed)."""
        current = self.input.text()
        if current and current != (self._partial or ""):
            return
        self._partial = text or None
        self.input.setText(text)
        self._set_input_style(bool(text))

    def _typed(self, _text):
        self._partial = None
        self._set_input_style(False)

    def _set_input_style(self, heard):
        font = self.input.font()
        font.setItalic(heard)
        self.input.setFont(font)

    def _send_or_stop(self):
        if self.busy:
            self.stopped.emit()
        else:
            self._ask(self.input.text())

    def set_busy(self, busy):
        self.busy = busy
        self.send_btn.setIcon(icons.icon("stop" if busy else "send"))
        self.send_btn.setToolTip("Stop" if busy else "Send (Enter)")

    def say(self, text, instant=False):
        """AUPedea's words: typed out a few characters a frame (or straight in, when it's tucked away)."""
        if instant:
            self._flush_typing()
            self._log.append(self._para(rich(text)))
            self._render()
            return
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
        return f'<p style="margin:4px 0">{html_text}</p>'

    def _render(self, partial=None):
        body = "".join(self._log) + (self._para(partial + "▏") if partial is not None else "")
        self.view.setHtml(f'<div style="color:{theme.TEXT}; font-size:9.5pt">{body}</div>')
        self._fit()
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _fit(self):
        """Grow (or shrink) with the conversation, keeping the bottom (and the tail) where it is."""
        doc = self.view.document()
        doc.setTextWidth(self.WIDTH - 24 - 14)          # the text's width inside the card (no scroll bar)
        need = doc.size().height() + 8
        want = int(min(self.LOG_MAX, max(self.LOG_MIN, need)))
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if need > self.LOG_MAX else Qt.ScrollBarAlwaysOff)
        if want == self.view.height():
            return
        bottom = self.geometry().bottom()
        self.view.setFixedHeight(want)
        self.adjustSize()
        if self.isVisible():
            self.move(self.x(), bottom - self.height() + 1)

    def is_typing(self):
        return self._typing is not None or bool(self._queue)

    # ---- "are you sure?"
    def confirm(self, text, callback):
        self._confirm_cb = callback
        self.confirm_text.setText(text)
        self.confirm_bar.show()
        self.adjustSize()

    def waiting_for_answer(self):
        return self._confirm_cb is not None

    def _answer_confirm(self, yes):
        self.confirm_bar.hide()
        self.adjustSize()
        cb, self._confirm_cb = self._confirm_cb, None
        if cb:
            cb(yes)

    def cancel_confirm(self):
        if self._confirm_cb is not None:
            self._answer_confirm(False)

    # ---- appearing, fading, and being moved
    def appear(self, pos):
        """Fade and float in at `pos`."""
        self.adjustSize()
        self._stop_anim()
        start = QPoint(pos.x(), pos.y() + 10)
        self.move(start)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self._animate([(b"pos", start, pos), (b"windowOpacity", 0.0, self._resting())], 180)

    def vanish(self):
        """Fade and float out, then hide."""
        if not self.isVisible():
            return
        self._stop_anim()
        here = self.pos()
        group = self._animate([(b"pos", here, QPoint(here.x(), here.y() + 8)),
                               (b"windowOpacity", self.windowOpacity(), 0.0)], 140)
        group.finished.connect(self.hide)

    def _animate(self, props, ms):
        group = QParallelAnimationGroup(self)
        for name, a, b in props:
            prop = QPropertyAnimation(self, name, group)
            prop.setDuration(anim.ms(ms / 1000) or 1)
            prop.setStartValue(a)
            prop.setEndValue(b)
            prop.setEasingCurve(QEasingCurve.OutCubic)
            group.addAnimation(prop)
        self._anim = group
        group.start()
        return group

    def _stop_anim(self):
        if self._anim is not None:
            self._anim.stop()
            self._anim = None

    def _resting(self):
        return self.ACTIVE if self._hover or self._has_focus() else self.REST

    def _has_focus(self):
        w = QApplication.focusWidget()
        return w is not None and (w is self or self.isAncestorOf(w))

    def _settle(self):
        if self.isVisible() and (self._anim is None or self._anim.state() != QParallelAnimationGroup.Running):
            prop = QPropertyAnimation(self, b"windowOpacity", self)
            prop.setDuration(anim.ms(0.15) or 1)
            prop.setEndValue(self._resting())
            prop.start(QPropertyAnimation.DeleteWhenStopped)

    def _focus_changed(self, _old, _new):
        self._settle()

    def enterEvent(self, event):
        self._hover = True
        self._settle()

    def leaveEvent(self, event):
        self._hover = False
        self._settle()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() < self.HEAD:
            self._drag = event.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)
            self.moved_by_hand = True
        elif event.position().y() < self.HEAD:
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.unsetCursor()

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
        """Chips, the mode switch and the buttons, in the current theme's colours (light or dark)."""
        self.setStyleSheet(f"""
            QLabel#aupediaTitle {{ color: {theme.TEXT}; }}
            QLabel#aupediaStatus, QLabel#aupediaHint {{ color: {theme.TEXT_MUTED}; font-size: 8pt; }}
            QToolButton#aupediaHeadButton {{ border-radius: 9px; padding: 3px; }}
            QToolButton#aupediaHeadButton:hover {{ background: {theme.ACCENT_SOFT}; }}
            QPushButton#aupediaChip {{ background: {theme.ACCENT_SOFT}; border: 1px solid {theme.ACCENT_SOFT_BORDER};
                color: {theme.TEXT}; border-radius: 10px; padding: 3px 8px; font-size: 8.5pt; text-align: left; }}
            QPushButton#aupediaChip:hover {{ border-color: {theme.ACCENT}; background: {theme.SURFACE}; }}
            QPushButton#aupediaChip:pressed {{ background: {theme.ACCENT}; color: #ffffff; }}
            QPushButton#aupediaMode {{ background: transparent; border: 1px solid {theme.BORDER_STRONG};
                color: {theme.TEXT_MUTED}; border-radius: 9px; padding: 1px 9px; font-size: 8.5pt; }}
            QPushButton#aupediaMode:hover {{ border-color: {theme.ACCENT}; }}
            QPushButton#aupediaMode:checked {{ background: {theme.ACCENT}; border-color: {theme.ACCENT};
                color: #ffffff; }}
            QLineEdit#aupediaInput {{ border-radius: 14px; padding: 4px 10px; }}
            QToolButton#aupediaMic, QToolButton#aupediaSend {{ border-radius: 14px; padding: 5px; }}
            QToolButton#aupediaMic:hover, QToolButton#aupediaSend:hover {{ background: {theme.ACCENT_SOFT}; }}
            QToolButton#aupediaMic:checked {{ background: {theme.ACCENT_SOFT}; border: 1px solid {theme.ACCENT}; }}
            QToolButton#aupediaMic[live="true"] {{ background: #ffe1e6; border: 1px solid #e11d48; }}
        """)
        self._render()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = _card_path(QRectF(self.rect()).adjusted(5, 5, -5, -5 - self.TAIL), self.tail_side, self.TAIL, 15)
        _paint_card(p, path, 236)
        p.end()


def _card_path(r, side, tail, radius):
    """A rounded card with a speech tail at the bottom, towards `side` (where the scribble is)."""
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    t = QPainterPath()
    tx = r.right() - 40 if side == "right" else r.left() + 40
    tip = QPointF(tx + (18 if side == "right" else -18), r.bottom() + tail)
    t.moveTo(tx - 11, r.bottom() - 2)
    t.quadTo(QPointF(tx + 2, r.bottom() + 6), tip)
    t.quadTo(QPointF(tx + 5, r.bottom() + 3), QPointF(tx + 10, r.bottom() - 2))
    t.closeSubpath()
    return path.united(t)


def _paint_card(p, path, alpha):
    """Soft shadow, see-through paper, and a slightly shaky pen line round the edge."""
    for k, a in ((4, 12), (2, 18)):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, a))
        p.drawPath(path.translated(0, k))
    paper = QColor(theme.SURFACE)
    paper.setAlpha(alpha)
    p.setBrush(paper)
    p.drawPath(path)
    p.setBrush(Qt.NoBrush)
    for dx, dy, a, width in ((0, 0, 210, 1.7), (0.8, -0.6, 80, 1.0)):
        c = QColor(theme.TEXT)
        c.setAlpha(a)
        p.setPen(QPen(c, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path.translated(dx, dy))


class MiniBubble(QWidget):
    """A little speech bubble over the scribble's head: what it hears, what it's doing, short
    replies, and "Shall I...?". It follows the scribble around; click it to open the chat."""
    clicked = Signal()
    talking = Signal(float)
    MAXW = 250
    TAIL = 10
    MAX_CHARS = 220

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to open the chat")
        self.doc = QTextDocument()
        self.doc.setDocumentMargin(0)
        self.text = ""                # what it says right now (plain), for tests and screen readers
        self.kind = None              # say, heard, status, ask
        self._full = ""
        self._typed_at = None
        self.anchor = (0.0, 0.0)
        self._fade = QGraphicsOpacityEffect(self)
        self._fade.setOpacity(0.0)
        self.setGraphicsEffect(self._fade)
        self._fade_anim = QPropertyAnimation(self._fade, b"opacity", self)
        self._fade_anim.finished.connect(self._faded)
        self._type = QTimer(self)
        self._type.setInterval(16)
        self._type.timeout.connect(self._type_step)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.clear)
        self.hide()

    def say(self, text, kind="say"):
        """say: typed out, mouth moving, then fades after a read; heard: live words; status: until
        replaced; ask: a question, until answered."""
        text = " ".join(str(text).split())
        if len(text) > self.MAX_CHARS:
            text = text[: self.MAX_CHARS].rsplit(" ", 1)[0] + "… (click for more)"
        self.kind, self._full = kind, text
        self._hide_timer.stop()
        if kind == "say":
            self._typed_at = anim.now()
            self._type.start()
            self._set(text, 1)
        else:
            self._type.stop()
            self._set(text, len(text))
            if kind == "heard":
                self._hide_timer.start(anim.ms(4.0))
        self._fade_to(1.0)

    def clear(self, kind=None):
        if kind is not None and self.kind != kind:
            return
        self._type.stop()
        self.talking.emit(0.0)
        self.kind = None
        if self.isVisible():
            self._fade_to(0.0)

    def _set(self, text, shown):
        self.text = text
        part = text[:shown]
        if part.count("**") % 2:
            part += "**"
        self.doc.setDefaultFont(self.font())
        self.doc.setHtml(f'<div style="color:{theme.TEXT}; font-size:9pt">{rich(part)}</div>')
        self.doc.setTextWidth(-1)
        width = min(self.MAXW - 20, max(40.0, self.doc.idealWidth()))
        self.doc.setTextWidth(width)
        full = QTextDocument()
        full.setDefaultFont(self.font())
        full.setHtml(f'<div style="font-size:9pt">{rich(text)}</div>')
        full.setTextWidth(-1)
        w = int(min(self.MAXW, max(60, full.idealWidth() + 22)))
        full.setTextWidth(w - 22)
        self.doc.setTextWidth(w - 22)
        self.resize(w, int(full.size().height()) + 16 + self.TAIL)
        self.follow(*self.anchor)
        self.update()

    def _type_step(self):
        n = (anim.now() - self._typed_at) * CHARS_PER_SECOND * 0.7
        if n >= len(self._full):
            self._type.stop()
            self._set(self._full, len(self._full))
            self.talking.emit(0.0)
            self._hide_timer.start(anim.ms(min(12.0, 3.5 + len(self._full) / 22)))
            return
        self._set(self._full, int(n))
        self.talking.emit(0.5 + 0.5 * abs(math.sin(n / 3.0)))

    def _fade_to(self, opacity):
        if opacity > 0:
            self.show()
            self.raise_()
        self._fade_anim.stop()
        self._fade_anim.setDuration(anim.ms(0.16) or 1)
        self._fade_anim.setStartValue(self._fade.opacity())
        self._fade_anim.setEndValue(opacity)
        self._fade_anim.start()

    def _faded(self):
        if self._fade.opacity() <= 0.01:
            self.hide()

    def follow(self, x, y):
        """Sit above the scribble at (x, y) (its head), inside the window."""
        self.anchor = (x, y)
        parent = self.parentWidget()
        if parent is None:
            return
        bx = x - self.width() + 46
        by = y - self.height() - 2
        bx = min(max(bx, 6), parent.width() - self.width() - 6)
        if by < 6:                                   # no room above: hang below instead
            by = y + 70
        self.move(int(bx), int(by))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = "right" if self.anchor[0] >= self.x() + self.width() / 2 else "left"
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3 - self.TAIL)
        _paint_card(p, _card_path(r, side, self.TAIL, 11), 238)
        p.translate(r.left() + 8, r.top() + 5)
        self.doc.drawContents(p)
        p.end()


def _run_installer(path):
    os.startfile(path)            # Ollama's own installer window; it starts Ollama when it finishes


class _PullProgress(QObject):
    """Download progress from a worker thread, delivered on the UI thread."""
    update = Signal(object, str)


class AupediaSettingsDialog(QDialog):
    """Which AI AUPedea thinks with (Claude or Hugging Face), its key and model, and whether it
    shows up on start."""

    SEES = "  \U0001F441"             # marks Hugging Face models that can see pictures of pages

    def __init__(self, show_on_start=True, parent=None, hf_fetch=None, local_fetch=None, local_caps=None,
                 local_pull=None, setup_download=None, run_setup=None):
        super().__init__(parent)
        self.setup_download = setup_download or brain.download_ollama_setup
        self.run_setup = run_setup or _run_installer
        self._setup_timer = None          # checks for Ollama while its installer runs
        self.local_fetch = local_fetch or brain.ollama_models
        self.local_caps = local_caps or (lambda model: brain.Ollama(model).capabilities())
        self.local_pull = local_pull or brain.ollama_pull
        self.local_installed = {}         # model name -> size in GB
        self.local_vision = {}            # model name -> can see pictures
        self.local_running = None
        self.setWindowTitle("AUPedea Settings")
        self.setMinimumWidth(560)
        self.hf_fetch = hf_fetch or brain.hf_models
        self.hf_vision = {mid: sees for mid, sees in brain.HF_MODELS}
        chosen = brain.choices()
        layout = _dialog_layout(self)
        layout.addLayout(_header("AUPedea", "Built in (no AI, no key), AUPedea finds buttons and marks text up "
                                            "by itself, instantly. With an AI it answers anything, reads your "
                                            "pages, writes and draws on them, and does multi-step jobs. An online "
                                            "AI without its key uses the built-in finder, never the other service."))

        pick = QHBoxLayout()
        self.provider_group = QButtonGroup(self)
        self.use_none = QRadioButton("Built in")
        self.use_none.setToolTip("No AI and no key: finds buttons from your words and marks text up "
                                 "(\"highlight 500 dollars\"). Instant and free")
        self.use_claude = QRadioButton("Claude (Anthropic)")
        self.use_hf = QRadioButton("Hugging Face")
        self.use_local = QRadioButton("On this computer")
        self.use_local.setToolTip("A model running on your own computer with Ollama: free, works offline, and "
                                  "nothing you ask leaves the computer")
        for btn in (self.use_none, self.use_claude, self.use_hf, self.use_local):
            self.provider_group.addButton(btn)
            pick.addWidget(btn)
        pick.addStretch()
        layout.addLayout(pick)

        # ---- Claude
        self.claude_box = QWidget()
        form = QFormLayout(self.claude_box)
        form.setContentsMargins(0, 0, 0, 0)
        self.claude_key = QLineEdit(brain.saved_key(brain.CLAUDE))
        self.claude_key.setEchoMode(QLineEdit.Password)
        self.claude_key.setPlaceholderText("sk-ant-..." if not brain.api_key(brain.CLAUDE)
                                           else "using ANTHROPIC_API_KEY from this computer")
        key_row = QHBoxLayout()
        key_row.addWidget(self.claude_key, 1)
        get = QPushButton("Get a Key...")
        get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(brain.CLAUDE_KEYS_URL)))
        key_row.addWidget(get)
        form.addRow("API key", key_row)
        self.claude_model = QComboBox()
        for mid, name in brain.CLAUDE_MODELS:
            self.claude_model.addItem(name, mid)
        i = self.claude_model.findData(chosen["claude_model"])
        self.claude_model.setCurrentIndex(max(0, i))
        form.addRow("Model", self.claude_model)
        layout.addWidget(self.claude_box)

        # ---- Hugging Face
        self.hf_box = QWidget()
        form = QFormLayout(self.hf_box)
        form.setContentsMargins(0, 0, 0, 0)
        self.hf_token = QLineEdit(brain.saved_key(brain.HUGGINGFACE))
        self.hf_token.setEchoMode(QLineEdit.Password)
        self.hf_token.setPlaceholderText("hf_..." if not brain.api_key(brain.HUGGINGFACE)
                                         else "using HF_TOKEN from this computer")
        tok_row = QHBoxLayout()
        tok_row.addWidget(self.hf_token, 1)
        get = QPushButton("Get a Token...")
        get.setToolTip("A fine-grained token with \"Make calls to Inference Providers\"")
        get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(brain.HF_TOKENS_URL)))
        tok_row.addWidget(get)
        form.addRow("Access token", tok_row)
        self.hf_model = QComboBox()
        self.hf_model.setEditable(True)            # any model id on the Hub works if a provider serves it
        self.hf_model.setMinimumWidth(300)
        self._fill_hf_models(brain.HF_MODELS, chosen["hf_model"])
        model_row = QHBoxLayout()
        model_row.addWidget(self.hf_model, 1)
        self.refresh_btn = QPushButton("Refresh List")
        self.refresh_btn.setToolTip("Fetch the models Hugging Face serves right now that can use tools")
        self.refresh_btn.clicked.connect(self.refresh_models)
        model_row.addWidget(self.refresh_btn)
        form.addRow("Model", model_row)
        self.hf_note = QLabel()
        self.hf_note.setObjectName("muted")
        form.addRow("", self.hf_note)
        self.hf_model.currentTextChanged.connect(self._update_hf_note)
        layout.addWidget(self.hf_box)

        # ---- on this computer (Ollama)
        self.local_box = QWidget()
        form = QFormLayout(self.local_box)
        form.setContentsMargins(0, 0, 0, 0)
        self.ollama_state = QLabel("Checking for Ollama...")
        self.ollama_state.setObjectName("muted")
        self.ollama_state.setWordWrap(True)
        state_row = QHBoxLayout()
        state_row.addWidget(self.ollama_state, 1)
        self.install_btn = QPushButton("Get Ollama...")
        self.install_btn.setToolTip("Download and install Ollama, which runs AI models on your own computer "
                                    "(free, about 1.6 GB)")
        self.install_btn.clicked.connect(self.install_ollama)
        state_row.addWidget(self.install_btn)
        recheck = QPushButton("Check Again")
        recheck.clicked.connect(self.check_local)
        state_row.addWidget(recheck)
        form.addRow("Ollama", state_row)
        self.local_model = QComboBox()
        self.local_model.setEditable(True)        # any model from ollama.com/library
        self.local_model.setMinimumWidth(300)
        model_row = QHBoxLayout()
        model_row.addWidget(self.local_model, 1)
        self.pull_btn = QPushButton("Download")
        self.pull_btn.setToolTip("Download this model into Ollama (once)")
        self.pull_btn.clicked.connect(self.download_local)
        model_row.addWidget(self.pull_btn)
        form.addRow("Model", model_row)
        self.pull_bar = QProgressBar()
        self.pull_bar.setRange(0, 1000)
        self.pull_bar.setTextVisible(False)
        self.pull_bar.setFixedHeight(6)
        self.pull_bar.hide()
        form.addRow("", self.pull_bar)
        self.local_note = QLabel()
        self.local_note.setObjectName("muted")
        self.local_note.setWordWrap(True)
        form.addRow("", self.local_note)
        self._chosen_local = chosen["local_model"]
        if chosen["local_vision"]:                 # otherwise asked of the model once Ollama answers
            self.local_vision[chosen["local_model"]] = True
        self._fill_local_models()
        self.local_model.currentTextChanged.connect(self._local_changed)
        layout.addWidget(self.local_box)

        self.status = QLabel("Keys are stored encrypted for your Windows account. Questions, the list of menus "
                             "and what's open (file names, page, tool) go to the service you choose; when AUPedea "
                             "reads or marks up a page, that page's text (and a picture of it, for models that "
                             "can see) goes too. Each question uses a little of your account's credit.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.on_start = QCheckBox("Show AUPedea when the app starts")
        self.on_start.setChecked(show_on_start)
        layout.addWidget(self.on_start)
        test = self.test_btn = QPushButton("Test")
        test.setToolTip("Check the key (or token) of the service chosen above")
        test.clicked.connect(self._test)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = _primary("Save")
        ok.clicked.connect(self.accept)
        layout.addLayout(_button_row(cancel, ok, leading=[test]))

        {brain.HUGGINGFACE: self.use_hf, brain.OLLAMA: self.use_local,
         brain.BUILT_IN: self.use_none}.get(chosen["provider"], self.use_claude).setChecked(True)
        self.provider_group.buttonToggled.connect(lambda *_: self._show_provider())
        self._show_provider()
        self._update_hf_note()

    # ---- Hugging Face models
    def _fill_hf_models(self, models, current):
        self.hf_model.blockSignals(True)
        self.hf_model.clear()
        for mid, sees in models:
            self.hf_vision[mid] = sees
            self.hf_model.addItem(mid + (self.SEES if sees else ""), mid)
        i = self.hf_model.findData(current)
        if i >= 0:
            self.hf_model.setCurrentIndex(i)
        else:
            self.hf_model.setEditText(current)
        self.hf_model.blockSignals(False)

    def hf_model_id(self):
        i = self.hf_model.currentIndex()
        text = self.hf_model.currentText().replace(self.SEES, "").strip()
        if i >= 0 and self.hf_model.itemText(i) == self.hf_model.currentText():
            return self.hf_model.itemData(i) or text
        return text

    def _update_hf_note(self, *_):
        mid = self.hf_model_id()
        sees = self.hf_vision.get(mid)
        self.hf_note.setText("Can see pictures of your pages, as well as read them." if sees else
                             "Reads your pages' text (it can't see pictures)." if sees is False else
                             "A model not in the list: it needs a provider that supports tools.")

    def refresh_models(self):
        from ..cloud import worker

        self.refresh_btn.setEnabled(False)
        self.hf_note.setText("Fetching the models Hugging Face serves...")
        current = self.hf_model_id()

        def done(models):
            self.refresh_btn.setEnabled(True)
            if models:
                self._fill_hf_models(models, current)
            self._update_hf_note()

        def failed(exc):
            self.refresh_btn.setEnabled(True)
            self.hf_note.setText(brain.friendly_error(exc, brain.HUGGINGFACE))

        worker.run(self.hf_fetch, done, failed)

    # ---- the rest
    def provider(self):
        if self.use_none.isChecked():
            return brain.BUILT_IN
        if self.use_local.isChecked():
            return brain.OLLAMA
        return brain.HUGGINGFACE if self.use_hf.isChecked() else brain.CLAUDE

    def _show_provider(self):
        self.test_btn.setEnabled(self.provider() != brain.BUILT_IN)
        self.claude_box.setVisible(self.provider() == brain.CLAUDE)
        self.hf_box.setVisible(self.provider() == brain.HUGGINGFACE)
        self.local_box.setVisible(self.provider() == brain.OLLAMA)
        if self.provider() == brain.OLLAMA and self.local_running is None:
            self.check_local()
        self.adjustSize()

    # ---- on this computer
    def local_model_id(self):
        i = self.local_model.currentIndex()
        text = self.local_model.currentText().strip()
        if i >= 0 and self.local_model.itemText(i) == text:
            return self.local_model.itemData(i) or text
        return text.split("  (")[0].lstrip("\u2713 ").strip()

    def _is_installed(self, name):
        return name in self.local_installed or f"{name}:latest" in self.local_installed

    def _fill_local_models(self):
        current = self.local_model_id() if self.local_model.count() else self._chosen_local
        names = list(self.local_installed) + [m for m, _d in brain.OLLAMA_MODELS if not self._is_installed(m)]
        if current and current not in names and not self._is_installed(current):
            names.append(current)
        sizes = {m: d.split("about ")[1].split(",")[0] for m, d in brain.OLLAMA_MODELS}
        self.local_model.blockSignals(True)
        self.local_model.clear()
        for name in names:
            if self._is_installed(name):
                label = f"\u2713 {name}" + (self.SEES if self.local_vision.get(name) else "")
            else:
                label = f"{name}  (download, {sizes.get(name, 'size unknown')})"
            self.local_model.addItem(label, name)
        i = self.local_model.findData(current)
        self.local_model.setCurrentIndex(i if i >= 0 else 0)
        self.local_model.blockSignals(False)
        self._local_changed()

    def _local_changed(self, *_):
        name = self.local_model_id()
        about = dict(brain.OLLAMA_MODELS).get(name, "")
        total, free = brain.memory_gb()
        best = brain.recommended_local_model(total)
        if self.local_running is False:
            self.pull_btn.setEnabled(False)
            self.local_note.setText("Click Get Ollama to download and install it (free, about 1.6 GB), then "
                                    "pick a model (Qwen, Llama, Google's Gemma or OpenAI's gpt-oss) and click "
                                    "Download.")
            return
        installed = self._is_installed(name)
        self.pull_btn.setEnabled(bool(name) and not installed)
        self.pull_btn.setText("Downloaded" if installed else "Download")
        sees = self.local_vision.get(name)
        parts = [about] if about else []
        need = brain.OLLAMA_NEEDS_GB.get(name)
        if total and need and need > total * 0.4 and name != best:
            parts.append(f"This computer has {total:.0f} GB of memory: {best} will be much quicker here.")
        elif name == best and total:
            parts.append(f"A good fit for this computer's {total:.0f} GB of memory.")
        parts.append("Ready to use." if installed else "Not downloaded yet: click Download (once).")
        if installed and sees is not None:
            parts.append("It can see pictures of your pages." if sees else "It reads your pages' text.")
        if name == "qwen3:4b":
            parts.append(f"Note: this one thinks out loud before every answer, which takes minutes on a laptop; "
                         f"{brain.OLLAMA_DEFAULT} is the same size and answers straight away.")
        elif brain.always_thinks(name):
            parts.append("It always reasons a little before answering (kept short), so each answer takes longer.")
        parts.append("Runs on this computer: nothing you ask leaves it. Slower than the online AIs, and best at "
                     "simpler jobs.")
        self.local_note.setText(" ".join(parts))
        if installed and name not in self.local_vision:
            self._learn_caps(name)

    def _learn_caps(self, name):
        from ..cloud import worker

        def done(caps):
            self.local_vision[name] = "vision" in caps
            if self.local_model_id() == name:
                self._fill_local_models()

        worker.run(lambda: set(self.local_caps(name)), done, lambda _e: None)

    def check_local(self):
        from ..cloud import worker

        self.ollama_state.setText("Checking for Ollama...")

        def done(models):
            self.local_running = True
            self._stop_setup_checks()
            self.local_installed = {name: size for name, size in models}
            count = len(models)
            self.ollama_state.setText(f"Running, with {count} model{'s' if count != 1 else ''} downloaded."
                                      if count else "Running. No models downloaded yet.")
            self.install_btn.setVisible(False)
            self._fill_local_models()

        def failed(_exc):
            self.local_running = False
            self.ollama_state.setText("Not found: Ollama isn't installed, or isn't running.")
            self.install_btn.setVisible(True)
            self._local_changed()

        worker.run(self.local_fetch, done, failed)

    def install_ollama(self):
        """Download Ollama's installer and start it (on Windows); elsewhere, open its download page."""
        from ..cloud import worker

        if not sys.platform.startswith("win"):
            QDesktopServices.openUrl(QUrl(brain.OLLAMA_DOWNLOAD_URL))
            return
        progress = _PullProgress(self)
        progress.update.connect(lambda f, _s: self._setup_progress(f))
        self.install_btn.setEnabled(False)
        self.pull_bar.setValue(0)
        self.pull_bar.show()
        self.ollama_state.setText("Downloading Ollama... (about 1.6 GB; you can keep using the app)")

        def done(path):
            self.pull_bar.hide()
            try:
                self.run_setup(path)
            except OSError:
                self.install_btn.setEnabled(True)
                self.ollama_state.setText(f"Downloaded, but it wouldn't start. Run it yourself: {path}")
                return
            self.ollama_state.setText("Installing Ollama: follow its window. AUPedea notices when it's ready.")
            self._start_setup_checks()

        def failed(_exc):
            self.pull_bar.hide()
            self.install_btn.setEnabled(True)
            self.ollama_state.setText("Couldn't download Ollama. Check the internet connection and try again, "
                                      "or get it from ollama.com.")

        worker.run(lambda: self.setup_download(lambda f: progress.update.emit(f, "")), done, failed)

    def _setup_progress(self, fraction):
        if fraction is not None:
            self.pull_bar.setValue(int(fraction * 1000))
            self.ollama_state.setText(f"Downloading Ollama... {int(fraction * 100)}%")

    def _start_setup_checks(self):
        self._stop_setup_checks()
        self.install_btn.setEnabled(False)        # one installer at a time
        self._setup_timer = QTimer(self)
        self._setup_timer.timeout.connect(self._check_quietly)
        self._setup_timer.start(4000)

    def _stop_setup_checks(self):
        if self._setup_timer is not None:
            self._setup_timer.stop()
            self._setup_timer = None
        self.install_btn.setEnabled(True)

    def _check_quietly(self):
        """While the installer runs: has Ollama started answering yet? (Nothing changes on screen until it has.)"""
        from ..cloud import worker

        def done(models):
            if self._setup_timer is not None:
                self.check_local()

        worker.run(self.local_fetch, done, lambda _e: None)

    def done(self, result):
        self._stop_setup_checks()
        super().done(result)

    def download_local(self):
        from ..cloud import worker

        name = self.local_model_id()
        if not name or self._is_installed(name):
            return
        progress = _PullProgress(self)
        progress.update.connect(self._pull_progress)
        self.pull_btn.setEnabled(False)
        self.pull_bar.setValue(0)
        self.pull_bar.show()
        self.local_note.setText(f"Downloading {name}... (you can keep using the app)")

        def done(_r):
            self.pull_bar.hide()
            self.check_local()

        def failed(exc):
            self.pull_bar.hide()
            self.pull_btn.setEnabled(True)
            self.local_note.setText(brain.friendly_error(exc, brain.OLLAMA))

        worker.run(lambda: self.local_pull(name, progress.update.emit), done, failed)

    def _pull_progress(self, fraction, status):
        if fraction is not None:
            self.pull_bar.setValue(int(fraction * 1000))
            self.local_note.setText(f"Downloading {self.local_model_id()}... {int(fraction * 100)}%")
        elif status:
            self.local_note.setText(f"Downloading {self.local_model_id()}... ({status})")

    def _test(self):
        from ..cloud import worker

        if self.provider() == brain.OLLAMA:
            prov = brain.Ollama(self.local_model_id())
        elif self.provider() == brain.CLAUDE:
            key = self.claude_key.text().strip() or brain.api_key(brain.CLAUDE)
            prov = brain.Claude(key, self.claude_model.currentData()) if key else None
        else:
            key = self.hf_token.text().strip() or brain.api_key(brain.HUGGINGFACE)
            prov = brain.HuggingFace(key, self.hf_model_id()) if key else None
        if prov is None:
            self.status.setText("Paste a key first.")
            return
        self.status.setText("Checking...")
        worker.run(prov.check, lambda _r: self.status.setText("It works. AUPedea is ready to think."),
                   lambda exc: self.status.setText(brain.friendly_error(exc, prov.kind)))

    def accept(self):
        for kind, field in ((brain.CLAUDE, self.claude_key), (brain.HUGGINGFACE, self.hf_token)):
            key = field.text().strip()
            if key:
                brain.save_key(key, kind)
            else:
                brain.forget_key(kind)
        mid = self.hf_model_id() or brain.HF_DEFAULT
        local = self.local_model_id() or brain.OLLAMA_DEFAULT
        brain.save_choices(provider=self.provider(), claude_model=self.claude_model.currentData(),
                           hf_model=mid, hf_vision=bool(self.hf_vision.get(mid)), local_model=local,
                           local_vision=bool(self.local_vision.get(local)))
        super().accept()
