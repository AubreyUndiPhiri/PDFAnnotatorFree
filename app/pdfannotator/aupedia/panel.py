"""The speech bubble: type a question or a job, pick "Show me" or "Do it for
me", and AUPedia's answers type themselves out (its mouth moves as they do).
Plus its settings: the Claude API key."""
import html
import math
import re

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QRadioButton, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from .. import icons, theme
from ..dialogs import _button_row, _dialog_layout, _header, _primary
from . import anim, brain

HAND_FONT = "Caveat"
SUGGESTIONS = ["Summarise this page", "Highlight the title", "Write a note in the margin",
               "Draw a star in the corner", "Sign this document", "Convert this PDF to Word"]
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
    listen_toggled = Signal()
    WIDTH, HEIGHT = 380, 430
    TAIL = 18
    PLACEHOLDER = "Ask me anything, or tell me what to do..."

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
        self._partial = None          # words heard so far, shown in the input box

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
        self.mic_btn = QToolButton()
        self.mic_btn.setObjectName("aupediaMic")
        self.mic_btn.setIcon(icons.icon("mic"))
        self.mic_btn.setCheckable(True)
        self.mic_btn.setToolTip("Talk to me (Ctrl+Shift+Space): I listen, and your words appear as you speak")
        self.mic_btn.clicked.connect(lambda: self.listen_toggled.emit())
        ask.addWidget(self.mic_btn)
        self.input = QLineEdit()
        self.input.setPlaceholderText(self.PLACEHOLDER)
        self.input.returnPressed.connect(lambda: self._ask(self.input.text()))
        self.input.textEdited.connect(self._typed)
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
        self._log.append(f'<p align="right" style="color:{theme.TEXT_MUTED}; margin:8px 0 2px 40px">'
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
                                       "listening": "Listening... just talk to me",
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
            QToolButton#aupediaMic {{ border-radius: 14px; padding: 4px; }}
            QToolButton#aupediaMic:checked {{ background: {theme.ACCENT_SOFT}; border: 1px solid {theme.ACCENT}; }}
            QToolButton#aupediaMic[live="true"] {{ background: #ffe1e6; border: 1px solid #e11d48; }}
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
    """Which AI AUPedia thinks with (Claude or Hugging Face), its key and model, and whether it
    shows up on start."""

    SEES = "  \U0001F441"             # marks Hugging Face models that can see pictures of pages

    def __init__(self, show_on_start=True, parent=None, hf_fetch=None):
        super().__init__(parent)
        self.setWindowTitle("AUPedia Settings")
        self.setMinimumWidth(560)
        self.hf_fetch = hf_fetch or brain.hf_models
        self.hf_vision = {mid: sees for mid, sees in brain.HF_MODELS}
        chosen = brain.choices()
        layout = _dialog_layout(self)
        layout.addLayout(_header("AUPedia", "Without a key, AUPedia finds buttons and marks text up by itself. "
                                            "With an AI it answers anything, reads your pages, writes and draws on "
                                            "them, and does multi-step jobs. Use your own key from either service."))

        pick = QHBoxLayout()
        self.provider_group = QButtonGroup(self)
        self.use_claude = QRadioButton("Claude (Anthropic)")
        self.use_hf = QRadioButton("Hugging Face")
        for btn in (self.use_claude, self.use_hf):
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

        self.status = QLabel("Keys are stored encrypted for your Windows account. Questions, the list of menus "
                             "and what's open (file names, page, tool) go to the service you choose; when AUPedia "
                             "reads or marks up a page, that page's text (and a picture of it, for models that "
                             "can see) goes too. Each question uses a little of your account's credit.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.on_start = QCheckBox("Show AUPedia when the app starts")
        self.on_start.setChecked(show_on_start)
        layout.addWidget(self.on_start)
        test = QPushButton("Test")
        test.setToolTip("Check the key (or token) of the service chosen above")
        test.clicked.connect(self._test)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = _primary("Save")
        ok.clicked.connect(self.accept)
        layout.addLayout(_button_row(cancel, ok, leading=[test]))

        (self.use_hf if chosen["provider"] == brain.HUGGINGFACE else self.use_claude).setChecked(True)
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
        return brain.HUGGINGFACE if self.use_hf.isChecked() else brain.CLAUDE

    def _show_provider(self):
        self.claude_box.setVisible(self.provider() == brain.CLAUDE)
        self.hf_box.setVisible(self.provider() == brain.HUGGINGFACE)
        self.adjustSize()

    def _test(self):
        from ..cloud import worker

        if self.provider() == brain.CLAUDE:
            key = self.claude_key.text().strip() or brain.api_key(brain.CLAUDE)
            prov = brain.Claude(key, self.claude_model.currentData()) if key else None
        else:
            key = self.hf_token.text().strip() or brain.api_key(brain.HUGGINGFACE)
            prov = brain.HuggingFace(key, self.hf_model_id()) if key else None
        if prov is None:
            self.status.setText("Paste a key first.")
            return
        self.status.setText("Checking...")
        worker.run(prov.check, lambda _r: self.status.setText("It works. AUPedia is ready to think."),
                   lambda exc: self.status.setText(brain.friendly_error(exc, prov.kind)))

    def accept(self):
        for kind, field in ((brain.CLAUDE, self.claude_key), (brain.HUGGINGFACE, self.hf_token)):
            key = field.text().strip()
            if key:
                brain.save_key(key, kind)
            else:
                brain.forget_key(kind)
        mid = self.hf_model_id() or brain.HF_DEFAULT
        brain.save_choices(provider=self.provider(), claude_model=self.claude_model.currentData(),
                           hf_model=mid, hf_vision=bool(self.hf_vision.get(mid)))
        super().accept()
