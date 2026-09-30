"""The Formula tool's on-page editor: type LaTeX, see it rendered live,
press Enter to place it on the page.

FormulaEditor is a small card laid over the PageWidget at the click point:
a LaTeX source box on top and the rendered preview underneath (rendered in
a worker thread, a moment after typing stops). Enter emits finished(True),
Shift+Enter starts a new line (for multi-line maths), Esc emits
finished(False). DocumentTab turns the result into a formula annotation."""
from PySide6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QPixmap, QTextCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout

from . import formula

PREVIEW_DELAY_MS = 350
MIN_WIDTH = 280


class _Signals(QObject):
    done = Signal(int, object, str)   # job id, formula.Rendered or None, error message


class _RenderJob(QRunnable):
    def __init__(self, job_id, source, fontsize, color, signals):
        super().__init__()
        self.job_id, self.source, self.fontsize, self.color, self.signals = job_id, source, fontsize, color, signals

    def run(self):
        try:
            result, error = formula.render(self.source, self.fontsize, self.color), ""
        except formula.FormulaError as e:
            result, error = None, str(e)
        except Exception as e:  # noqa: BLE001 - never let a worker thread crash the app
            result, error = None, f"{type(e).__name__}: {e}"
        try:
            self.signals.done.emit(self.job_id, result, error)
        except RuntimeError:
            pass  # the editor closed while this was rendering


class _SourceEdit(QPlainTextEdit):
    def __init__(self, card):
        super().__init__(card)
        self.card = card
        self.setObjectName("formulaSource")
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        for family in ("Cascadia Mono", "Consolas"):
            if family in QFontDatabase.families():
                font = QFont(family)
                break
        font.setPointSize(11)
        self.setFont(font)
        self.setPlaceholderText(r"\frac{a}{b},  x^2 + y^2 = r^2,  \sqrt{x},  \sum_{i=1}^{n} i ...")
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.card.finished.emit(False)
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                self.insertPlainText("\n")  # a new line of maths (use \\ to break it in the formula)
                return
            self.card.finished.emit(True)
            return
        super().keyPressEvent(event)


class FormulaEditor(QFrame):
    finished = Signal(bool)   # True: place the formula, False: cancel

    def __init__(self, page_widget, origin_px, px_per_pt, fontsize, color, text=""):
        super().__init__(page_widget)
        self.setObjectName("formulaEditor")
        self._origin_px = origin_px
        self._px_per_pt = px_per_pt
        self.fontsize, self.color = float(fontsize), QColor(color)
        self._job = 0
        self._result = None            # (source, fontsize, rgb, Rendered) of the newest good render
        self._signals = _Signals()  # no parent: a render still running after the editor closes can still report
        self._signals.done.connect(self._on_rendered)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        header = QHBoxLayout()
        title = QLabel("Σ  LaTeX formula")
        title.setObjectName("formulaTitle")
        self.engine_label = QLabel(f"rendered with {formula.available_engine() or 'nothing (install matplotlib)'}")
        self.engine_label.setObjectName("muted")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.engine_label)
        layout.addLayout(header)
        self.source = _SourceEdit(self)
        layout.addWidget(self.source)
        self.preview = QLabel()
        self.preview.setObjectName("formulaPreview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(36)
        layout.addWidget(self.preview)
        self.status = QLabel("Enter places it  ·  Shift+Enter new line  ·  Esc cancels")
        self.status.setObjectName("formulaStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PREVIEW_DELAY_MS)
        self._timer.timeout.connect(self._request_render)
        self.source.textChanged.connect(self._on_text_changed)
        self.source.setPlainText(text)
        self.source.moveCursor(QTextCursor.End)
        self._fit()
        if text.strip():
            self._request_render()

    # ---- API used by DocumentTab ------------------------------------------------
    def text(self) -> str:
        return self.source.toPlainText().strip()

    def setFocus(self):
        self.source.setFocus()

    def event(self, event):
        # plain typing belongs to the formula, not to the single-letter tool shortcuts
        if event.type() == QEvent.ShortcutOverride and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        # keys typed on the card itself (e.g. after clicking its border) go to
        # the LaTeX box, never on to the page, where letters pick tools
        self.source.setFocus()
        self.source.keyPressEvent(event)

    def _rgb(self):
        return (self.color.red(), self.color.green(), self.color.blue())

    def result(self):
        """The rendered formula for exactly what is typed now, if the preview has it."""
        if self._result and self._result[:3] == (self.text(), self.fontsize, self._rgb()):
            return self._result[3]
        return None

    def set_style(self, _fontname, fontsize, color):
        """Toolbar size / colour changed while editing (the font name doesn't
        apply: maths has its own fonts)."""
        self.fontsize, self.color = float(fontsize), QColor(color)
        self._request_render()

    def show_error(self, message):
        self.status.setText(message)
        self.status.setProperty("error", True)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self._fit()

    # ---- live preview ---------------------------------------------------------
    def _on_text_changed(self):
        self._fit()
        self._timer.start()

    def _request_render(self):
        source = self.text()
        self._job += 1
        if not source:
            self.preview.clear()
            self._set_hint()
            return
        self.status.setText("Rendering...")
        QThreadPool.globalInstance().start(_RenderJob(self._job, source, self.fontsize, self._rgb(), self._signals))

    def _on_rendered(self, job, rendered, error):
        if job != self._job:
            return  # an older render finished after a newer one started
        if rendered is None:
            self.preview.clear()
            self.preview.setText("—")
            self.show_error(error)
            return
        self._result = (self.text(), self.fontsize, self._rgb(), rendered)
        pixmap = QPixmap()
        pixmap.loadFromData(rendered.png)
        dpr = self.devicePixelRatioF()
        width = max(1, round(rendered.width_pt * self._px_per_pt * dpr))
        height = max(1, round(rendered.height_pt * self._px_per_pt * dpr))
        scaled = pixmap.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        self.preview.setPixmap(scaled)
        self._set_hint()
        self._fit()

    def _set_hint(self):
        self.status.setText("Enter places it  ·  Shift+Enter new line  ·  Esc cancels")
        if self.status.property("error"):
            self.status.setProperty("error", False)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    # ---- geometry ---------------------------------------------------------------
    def _fit(self):
        page = self.parentWidget()
        metrics = QFontMetricsF(self.source.font())
        lines = self.source.toPlainText().split("\n") or [""]
        text_w = max((metrics.horizontalAdvance(line) for line in lines), default=0) + 24
        pix = self.preview.pixmap()
        preview_w = pix.width() / pix.devicePixelRatio() + 24 if pix is not None and not pix.isNull() else 0
        width = min(max(MIN_WIDTH, text_w + 20, preview_w + 20), max(MIN_WIDTH, page.width() - 8))
        self.setFixedWidth(round(width))
        doc_lines = max(1, min(8, self.source.document().blockCount()))
        self.source.setFixedHeight(round(metrics.lineSpacing() * doc_lines + 14))
        self.adjustSize()
        x = min(self._origin_px.x(), max(0, page.width() - self.width() - 4))
        y = min(self._origin_px.y(), max(0, page.height() - self.height() - 4))
        self.move(max(0, x), max(0, y))
        self.raise_()
