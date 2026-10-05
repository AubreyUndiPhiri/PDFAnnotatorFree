"""Puts AUPedia in the window and plays out what it decides: fly to a
command, circle it and write a note beside it, open its menu, or tap it.

A question goes to Claude when there's a key (each tool call it makes is
played out here, on the UI thread, and the result goes back), otherwise to
the local finder. Clicking the scribble opens the bubble; dragging moves it."""
import math
import random
import re

import pymupdf as fitz
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QComboBox, QDialog, QLabel, QLineEdit, QPlainTextEdit, QRadioButton,
    QTextEdit,
)

from .. import theme
from ..cloud import worker
from . import anim, brain, page_tools
from .catalog import Catalog
from .mascot import REACH, Animator, HitArea, InkLayer, Mascot
from .panel import AskBubble, AupediaSettingsDialog

RISKY = re.compile(r"\b(delete|remove|exit|quit|close|clear|discard|sign out|cancel|melt|overwrite|revert)\b", re.I)
MARGIN = 70            # keep the scribble this far inside the window
HOME_AFTER = 4.0       # seconds after a job before it flies back to its corner
DRAW_SPEED = 520.0     # how fast the nib moves on the paper (pixels a second)
MAX_DRAW_TIME = 9.0    # a big drawing speeds up rather than taking longer than this


def _settings():
    return theme._settings()


class Aupedia(QObject):
    def __init__(self, window, own_actions=()):
        super().__init__(window)
        self.window = window
        self.own_actions = list(own_actions)
        self.ink = InkLayer(window)
        self.mascot = Mascot(window)
        self.hit = HitArea(window)
        self.hit.pressed.connect(self._pressed)
        self.hit.moved.connect(self._dragged)
        self.hit.released.connect(self._released)
        self.hit.hovered.connect(self._hovered)
        self.animator = Animator(self.mascot, self.ink, self.hit, self)
        self.bubble = None
        self.provider = None       # the AI service (Claude or Hugging Face); False: none, use the finder
        self.history = []          # the conversation, kept between questions
        self.history_owner = None  # (service, model) the conversation belongs to
        self._reveal = QTimer(self)    # writing: the letters keep pace with the nib
        self._reveal.setInterval(16)
        self._reveal.timeout.connect(self._reveal_step)
        self._reveal_text = None
        self.gen = 0               # bumped by every new question or Stop: late callbacks then do nothing
        self.catalog = None
        self._press = None         # (start pos, offset, dragging) while the mouse is down on the scribble
        self._home_timer = QTimer(self)
        self._home_timer.setSingleShot(True)
        self._home_timer.timeout.connect(self.go_home)
        self.ink.setGeometry(window.rect())
        self.mascot.place(*self.home())
        window.installEventFilter(self)          # only the window's own events: resizing, showing, hiding
        visible = _settings().value("aupedia/visible", "true") == "true"
        for widget in (self.ink, self.mascot, self.hit):
            widget.setVisible(visible)
        self._raise()

    # ------------------------------------------------------------------
    # where it lives
    # ------------------------------------------------------------------

    def home(self):
        w = self.window
        bottom = w.height() - (w.statusBar().height() if w.statusBar() and w.statusBar().isVisible() else 0)
        saved = _settings().value("aupedia/home")
        if saved:
            try:
                fx, fy = (float(v) for v in str(saved).split(","))
                return self._inside(fx * w.width(), fy * bottom)
            except ValueError:
                pass
        return self._inside(w.width() - 95, bottom - 80)

    def _inside(self, x, y):
        w = self.window
        return (min(max(x, MARGIN), max(MARGIN, w.width() - MARGIN)),
                min(max(y, MARGIN + 20), max(MARGIN + 20, w.height() - MARGIN)))

    def go_home(self):
        if self.mascot.isVisible() and not self.mascot.is_busy():
            self.ink.clear_circles()
            self.mascot.look_at = None
            self.mascot.mood = "idle"
            self.mascot.fly_to(*self.home(), self._place_bubble)

    def _raise(self):
        self.ink.raise_()
        self.mascot.raise_()
        self.hit.raise_()

    def is_shown(self):
        return self.mascot.isVisible()

    def set_shown(self, shown):
        _settings().setValue("aupedia/visible", "true" if shown else "false")
        act = getattr(self.window, "act_show_aupedia", None)
        if act is not None and act.isChecked() != shown:
            act.blockSignals(True)
            act.setChecked(shown)
            act.blockSignals(False)
        for widget in (self.ink, self.mascot, self.hit):
            widget.setVisible(shown)
        if shown:
            self._raise()
            self.mascot.place(*self.home())
            self.mascot.squash()
        else:
            self.stop()
            if self.bubble is not None:
                self.bubble.hide()

    # ------------------------------------------------------------------
    # the mouse on the scribble, and the window changing size
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.Resize:
            self.ink.setGeometry(self.window.rect())
            if not self.mascot.is_busy():
                self.mascot.place(*self.home())
        elif et == QEvent.Hide:
            self.animator.timer.stop()            # nothing to animate in a hidden window
        elif et == QEvent.Show and not self.animator.timer.isActive():
            self.animator.timer.start()
        elif et == QEvent.WindowDeactivate and self.bubble is not None:
            QTimer.singleShot(150, self._hide_bubble_if_app_inactive)
        return False

    def _pressed(self, pos):
        self._press = [pos, QPointF(self.mascot.pos_f[0] - pos.x(), self.mascot.pos_f[1] - pos.y()), False]
        self.mascot.squash()

    def _dragged(self, pos):
        if self._press is None:
            return
        start, offset, dragging = self._press
        if not dragging and (pos - start).manhattanLength() > 5:
            self._press[2] = True
            self.mascot.stop()
        if self._press[2]:
            x, y = self._inside(pos.x() + offset.x(), pos.y() + offset.y())
            old = self.mascot.pos_f
            self.mascot.place(x, y)
            self.hit.follow(x, y)
            self.mascot.vel = ((x - old[0]) * 40, (y - old[1]) * 40)
            if self.bubble is not None and self.bubble.isVisible():
                self._place_bubble()

    def _released(self, pos):
        if self._press is None:
            return
        dragging = self._press[2]
        self._press = None
        if dragging:
            w = self.window
            bottom = w.height() - (w.statusBar().height() if w.statusBar() and w.statusBar().isVisible() else 0)
            _settings().setValue("aupedia/home", f"{self.mascot.pos_f[0] / w.width():.4f},"
                                                 f"{self.mascot.pos_f[1] / max(1, bottom):.4f}")
            self.mascot.landed_at = self.mascot.t
        else:
            self.toggle_bubble()

    def _hovered(self, over):
        self.mascot.hover = over

    # ------------------------------------------------------------------
    # the bubble
    # ------------------------------------------------------------------

    def _make_bubble(self):
        b = AskBubble(self.window)
        b.asked.connect(self.ask)
        b.stopped.connect(self.stop)
        b.closed.connect(self.close_bubble)
        b.reset_requested.connect(self.reset)
        b.settings_requested.connect(self.show_settings)
        b.talking.connect(self._talk)
        self.bubble = b
        return b

    def open_bubble(self):
        if not self.is_shown():
            self.set_shown(True)
        b = self.bubble or self._make_bubble()
        self._update_status()
        self._place_bubble()
        b.show()
        b.raise_()
        b.activateWindow()
        self.mascot.mood = "happy"
        self.mascot.twirl()
        QTimer.singleShot(anim.ms(0.9), lambda: self._idle_if("happy"))

    def close_bubble(self):
        if self.bubble is not None:
            self.bubble.hide()

    def toggle_bubble(self):
        if self.bubble is not None and self.bubble.isVisible():
            self.close_bubble()
        else:
            self.open_bubble()

    def _hide_bubble_if_app_inactive(self):
        if self.bubble is not None and QApplication.activeWindow() is None and QApplication.activeModalWidget() is None:
            self.bubble.hide()

    def _place_bubble(self):
        b = self.bubble
        if b is None:
            return
        tip = self.window.mapToGlobal(QPoint(int(self.mascot.pos_f[0] - 30), int(self.mascot.pos_f[1] - 40)))
        x, y = tip.x() - b.width() + 40, tip.y() - b.height() + 10
        screen = (self.window.screen() or QApplication.primaryScreen()).availableGeometry()
        b.tail_side = "right"
        if x < screen.left() + 8:
            x = tip.x() + 40
            b.tail_side = "left"
        x = min(max(x, screen.left() + 8), screen.right() - b.width() - 8)
        y = min(max(y, screen.top() + 8), screen.bottom() - b.height() - 8)
        b.move(x, y)
        b.update()

    def _update_status(self):
        if self.bubble is not None:
            prov = self._provider()
            if prov is None:
                self.bubble.set_status("offline finder (add a key in settings)")
            else:
                name = "Claude" if prov.kind == brain.CLAUDE else "Hugging Face"
                self.bubble.set_status(f"thinking with {name}: {prov.label()}")

    def _talk(self, level):
        self.mascot.talk = level
        if level <= 0 and self.mascot.mood == "talking":
            self.mascot.mood = "idle"

    def _idle_if(self, mood):
        if self.mascot.mood == mood:
            self.mascot.mood = "idle"

    def show_settings(self):
        dlg = AupediaSettingsDialog(_settings().value("aupedia/visible", "true") == "true", self.window)
        if dlg.exec() == QDialog.Accepted:
            _settings().setValue("aupedia/visible", "true" if dlg.on_start.isChecked() else "false")
            self.provider = None          # the service, model or key may have changed
            self._update_status()

    def reset(self):
        self.stop()
        self.history = []
        if self.bubble is not None:
            self.bubble.reset()

    def _provider(self):
        """The AI service to think with (Claude or Hugging Face), or None to use the offline finder."""
        if self.provider is None:
            self.provider = brain.make_provider() or False
        return self.provider or None

    # ------------------------------------------------------------------
    # a question
    # ------------------------------------------------------------------

    def ask(self, text, mode="do"):
        self.gen += 1
        gen = self.gen
        self._home_timer.stop()
        self._close_menus()
        self.ink.clear_circles()
        b = self.bubble or self._make_bubble()
        b.chips.hide()
        b.set_busy(True)
        self.catalog = Catalog(self.window, exclude=self.own_actions)
        self._raise()
        prov = self._provider()
        if prov is not None:
            if self.history_owner != (prov.kind, prov.model) or len(self.history) > brain.MAX_HISTORY:
                self.history = []          # a new service or model, or a long talk: start afresh
                self.history_owner = (prov.kind, prov.model)
            working = list(self.history)
            prov.add_user(working, brain.user_turn(text, mode, self.catalog.state()))
            self._request(gen, prov, brain.system_prompt(self.catalog), working, mode, 0)
        else:
            self._run_local(gen, brain.local_answer(self.catalog, text, mode))

    def stop(self):
        self.gen += 1
        self.mascot.stop()
        self.mascot.hold_pen(False)
        self.mascot.mood = "idle"
        self.mascot.look_at = None
        self._reveal.stop()
        self._close_menus()
        self.ink.clear_circles()
        self.ink.dry()
        if self.bubble is not None:
            self.bubble.cancel_confirm()
            if self.bubble.busy:
                self.bubble.set_busy(False)
                self.bubble.say("OK, stopped.")
        self._home_timer.start(anim.ms(1.0))

    def _finish(self, gen, happy=True):
        if gen != self.gen:
            return
        if self.bubble is not None:
            self.bubble.set_busy(False)
        if happy:
            self.mascot.mood = "happy"
            self.mascot.twirl()
            self.ink.sparkle(*self.mascot.pos_f)
            QTimer.singleShot(anim.ms(1.2), lambda: self._idle_if("happy"))
        else:
            self.mascot.mood = "puzzled"
            QTimer.singleShot(anim.ms(2.0), lambda: self._idle_if("puzzled"))
        self._home_timer.start(anim.ms(HOME_AFTER))

    def _say(self, text):
        if self.bubble is not None and text.strip():
            self.mascot.mood = "talking"
            self.bubble.say(text)

    # ---- with an AI service: ask, play out the tools it calls, send back what happened, repeat
    def _request(self, gen, prov, system, working, mode, rounds):
        self.mascot.mood = "thinking"

        def done(reply):
            if gen != self.gen:
                return
            prov.add_reply(working, reply)
            if reply.stop == "refusal":
                self._say("Sorry, that's not something I can help with. Ask me about anything in the app!")
                self._finish(gen, happy=False)
                return
            for text in reply.texts:
                self._say(text)
            if reply.stop != "tool":
                if reply.stop == "max_tokens":
                    self._say("(I ran out of room there. Ask me to carry on.)")
                self.history = working          # a complete turn: keep it for follow-ups
                self._finish(gen)
                return
            if rounds + 1 >= brain.MAX_ROUNDS:
                self._say("That's a lot of steps for me. Could you break it into smaller questions?")
                self._finish(gen, happy=False)
                return
            results = []

            def run(i):
                if gen != self.gen:
                    return
                if i == len(reply.calls):
                    prov.add_results(working, results)
                    self._request(gen, prov, system, working, mode, rounds + 1)
                    return
                call_id, name, args = reply.calls[i]

                def got(text, error=False, image=None):
                    results.append(brain.Result(call_id, text, error, image))
                    QTimer.singleShot(anim.ms(0.15), lambda: run(i + 1))

                try:
                    self._tool(gen, name, args, mode, got)
                except page_tools.PageError as exc:
                    got(str(exc), True)

            run(0)

        def failed(exc):
            if gen != self.gen:
                return
            self._say(brain.friendly_error(exc, prov.kind))
            self._finish(gen, happy=False)

        worker.run(lambda: prov.call(system, working), done, failed)

    def _tool(self, gen, name, args, mode, reply):
        if "__invalid__" in args:
            reply("The arguments weren't valid JSON. Try again.", True)
            return
        modal = self._modal()
        if modal is not None:
            reply(f'The dialog "{modal.windowTitle()}" is open, so nothing else can be used until the person '
                  "finishes or closes it.")
            return
        if name in ("point_at", "click"):
            self._command_tool(gen, name, args, mode, reply)
            return
        if name not in ("read_page", "go_to_page") and name not in brain.PAPER_TOOLS:
            reply(f"Unknown tool {name}.", True)
            return
        tab = page_tools.pdf_tab(self.window)
        if tab is None:
            reply("No PDF is open in the current tab, so there's no page to read or mark up. "
                  "Ask the person to open one (File > Open).", True)
            return
        index = page_tools.page_index(tab, args.get("page"))
        if name == "read_page":
            self.read(gen, tab, index, reply)
        elif name == "go_to_page":
            tab.go_to_page(index)
            reply(f"Now showing page {index + 1} of {tab.document.page_count}.")
        elif mode != "do":
            reply("Not done: the person asked to be shown, not for the page to be changed. Explain how instead.",
                  True)
        elif name == "mark_text":
            self.mark(gen, tab, index, args.get("text", ""), args.get("style", "highlight"), bool(args.get("every")),
                      args.get("color"), reply)
        elif name == "write_text":
            self.write(gen, tab, index, args.get("x", 0), args.get("y", 0), args.get("text", ""),
                       args.get("size", 14), args.get("style", "handwriting") != "print", args.get("width"),
                       args.get("color"), reply)
        elif name == "draw":
            strokes = page_tools.clean_strokes(tab.document.page(index), args.get("strokes"))
            self.draw(gen, tab, index, strokes, args.get("color"), args.get("width", 2), reply)
        elif name == "shape":
            self.shape(gen, tab, index, args.get("kind", "rect"), args.get("x0", 0), args.get("y0", 0),
                       args.get("x1", 0), args.get("y1", 0), args.get("color"), args.get("width", 2), reply)

    def _command_tool(self, gen, name, args, mode, reply):
        cmd = self.catalog.get(args.get("target", ""))
        note = str(args.get("note", ""))[:60]
        if cmd is None:
            reply(f"There's no command with the id {args.get('target')!r}. Use an id from the list.", True)
            return
        if name == "point_at":
            self.show(gen, cmd, note, reply)
        elif mode != "do":
            reply("Not clicked: the person asked to be shown, not for it to be done. Point at it instead.", True)
        elif not cmd.action.isEnabled():
            self.show(gen, cmd, "greyed out", lambda _r: reply(f"{cmd.where()} is greyed out right now, so it "
                                                               "wasn't clicked."))
        else:
            self.press(gen, cmd, note, reply)

    # ---- without an AI service
    def _run_local(self, gen, ops):
        def run(i, declined=False):
            if gen != self.gen:
                return
            if i == len(ops):
                self._finish(gen, happy=not declined)
                return
            op = ops[i]
            nxt = lambda *_a, **_k: run(i + 1, declined)      # noqa: E731
            if op[0] == "say":
                self._say("No problem, I left it alone." if declined else op[1])
                QTimer.singleShot(anim.ms(0.1), nxt)
            elif op[0] == "point":
                self.show(gen, op[1], op[2], nxt)
            elif op[0] == "click":
                self.press(gen, op[1], op[2], lambda r: run(i + 1, r.startswith("The person said no")))
            elif op[0] in ("mark", "read"):
                tab = page_tools.pdf_tab(self.window)
                if tab is None:
                    self._say("Open a PDF first, and I'll do it on the page.")
                    QTimer.singleShot(anim.ms(0.1), lambda: run(i + 1, True))
                    return

                def told(text, error=False, image=None):
                    self._say(_local_reply(op[0], text, error))
                    QTimer.singleShot(anim.ms(0.1), lambda: run(i + 1, error))

                try:
                    index = tab.current_page_index()
                    if op[0] == "mark":
                        self.mark(gen, tab, index, op[2], op[1], False, None, told)
                    else:
                        self.read(gen, tab, index, told, picture=False)
                except page_tools.PageError as exc:
                    told(str(exc), True)

        self.mascot.mood = "thinking"
        QTimer.singleShot(anim.ms(0.45), lambda: run(0))       # a moment's thought looks friendlier than instant

    # ------------------------------------------------------------------
    # the paper: reading, marking up, writing and drawing
    # ------------------------------------------------------------------

    def _bring_into_view(self, tab, index, rect):
        """Show page `index` with `rect` (PDF points) in view; returns its page widget."""
        if getattr(tab, "page_layout_mode", "continuous") == "single" and tab.current_single_page_index != index:
            tab.show_single_page(index)
        pw = tab.get_page_widget(index)
        if not pw.rendered:
            pw.render()
        c = tab.pdf_to_px(pw, fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2))
        centre = pw.mapTo(tab.pages_container, QPoint(int(c.x()), int(c.y())))
        view = tab.scroll_area.viewport()
        scale = tab.px_per_pt(pw)
        xm = int(min(view.width() / 2 - 10, rect.width * scale / 2 + 90))
        ym = int(min(view.height() / 2 - 10, rect.height * scale / 2 + 90))
        tab.scroll_area.ensureVisible(centre.x(), centre.y(), max(0, xm), max(0, ym))
        tab.update_visible_pages()
        return pw

    def _on_window(self, tab, pw, x, y):
        px = tab.pdf_to_px(pw, fitz.Point(x, y))
        origin = pw.mapTo(self.window, QPoint(0, 0))
        return origin.x() + px.x(), origin.y() + px.y()

    def _pen_down(self, gen, tab, index, paths, color, width, done, alpha=1.0, flat=False, on_trace=None,
                  speed=None):
        """Fly to the page and draw `paths` (lists of PDF points) with the nib, as wet ink; then done()."""
        pts = [p for path in paths for p in path]
        box = fitz.Rect(min(x for x, _ in pts), min(y for _, y in pts), max(x for x, _ in pts), max(y for _, y in pts))
        pw = self._bring_into_view(tab, index, box)
        scale = tab.px_per_pt(pw)
        on_window = [[self._on_window(tab, pw, x, y) for x, y in path] for path in paths]
        total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for path in on_window for a, b in zip(path, path[1:]))
        speed = speed or max(DRAW_SPEED, total / MAX_DRAW_TIME)
        self.ink.clear_circles()
        self.mascot.look_at = None
        self.mascot.hold_pen(True)
        self.mascot.mood = "drawing"

        def stroke(i):
            if gen != self.gen:
                return
            if i == len(on_window):
                self.mascot.hold_pen(False)
                done()
                return
            path = on_window[i]

            def start():
                if gen != self.gen:
                    return
                if on_trace is None:
                    self.ink.begin_wet(color, width * scale, alpha, flat)
                else:
                    on_trace(i)
                self.mascot.trace(path, speed, lambda: stroke(i + 1))

            self.mascot.fly_nib_to(*path[0], start, quick=i > 0)

        stroke(0)

    def _committed(self, gen, commit, reply, text):
        """The animation's done: put the real annotation on the page, let the wet ink dry, report."""
        if gen != self.gen:
            return
        try:
            commit()
        except page_tools.PageError as exc:
            self.ink.dry()
            reply(str(exc), True)
            return
        QTimer.singleShot(anim.ms(0.05), self.ink.dry)
        self.mascot.mood = "happy"
        QTimer.singleShot(anim.ms(0.6), lambda: self._idle_if("happy"))
        reply(text)

    def read(self, gen, tab, index, reply, picture=True):
        """Fly beside the page, read it (eyes running along the lines), and hand back its text (and a picture)."""
        page = tab.document.page(index)
        pw = self._bring_into_view(tab, index, fitz.Rect(0, 0, page.rect.width, min(page.rect.height, 300)))
        x0, y0 = self._on_window(tab, pw, page.rect.width, 40)
        spot = self._inside(min(x0 + 60, self.window.width() - MARGIN), max(y0, MARGIN + 40))
        self.mascot.look_at = None
        self.mascot.mood = "reading"

        def arrived():
            if gen != self.gen:
                return
            text = page_tools.read_page(tab, index)
            prov = self._provider()
            image = page_tools.page_png(tab, index) if picture and prov is not None and prov.vision else None
            QTimer.singleShot(anim.ms(0.8), lambda: gen == self.gen and reply(text, image=image))

        self.mascot.fly_to(*spot, arrived)

    def mark(self, gen, tab, index, text, style, every, color, reply):
        style = style if style in ("highlight", "underline", "strikeout", "circle") else "highlight"
        quads = page_tools.find_text(tab, index, text)
        if not every:
            quads = _first_occurrence(quads)
        rects = page_tools.group_lines(quads)
        ink = page_tools.color(color, "#fde047" if style == "highlight" else "#e11d48")
        seed = random.randint(0, 9999)
        if style == "highlight":
            paths = [[(r.x0, (r.y0 + r.y1) / 2), (r.x1, (r.y0 + r.y1) / 2)] for r in rects]
            width, alpha, flat = max(r.height for r in rects), 0.45, True
        elif style == "circle":
            paths = [page_tools.hand_loop(r, seed=seed + i) for i, r in enumerate(rects)]
            width, alpha, flat = 2.0, 1.0, False
        else:
            y = (lambda r: r.y1 - 1) if style == "underline" else (lambda r: (r.y0 + r.y1) / 2)
            paths = [[(r.x0, y(r)), (r.x1, y(r))] for r in rects]
            width, alpha, flat = 1.5, 1.0, False
        words = page_tools.clean_words(text)
        self._pen_down(gen, tab, index, paths, ink, width, lambda: self._committed(
            gen, lambda: page_tools.add_markup(tab, index, style, quads, ink, seed=seed), reply,
            f'Marked "{words[:80]}" ({style}, {len(rects)} line{"s" if len(rects) != 1 else ""}) on page {index + 1}.'),
            alpha=alpha, flat=flat)

    def write(self, gen, tab, index, x, y, text, size, handwriting, width, color, reply):
        page = tab.document.page(index)
        layout = page_tools.text_layout(page, x, y, text, size, handwriting, width)
        rect = layout["rect"]
        ink = page_tools.color(color, "#1d4ed8")
        pw = self._bring_into_view(tab, index, rect)
        scale = tab.px_per_pt(pw)
        left, top = self._on_window(tab, pw, rect.x0, rect.y0)
        font = QFont(layout["font"])
        font.setPixelSize(max(6, int(round(layout["size"] * scale))))
        box = QRectF(left, top, rect.width * scale + 4, rect.height * scale + 4)
        # the nib's path: along each line of the text, bobbing up and down like handwriting
        line_h = layout["size"] * 1.25
        rows = max(1, int(round(rect.height / line_h)))
        path = []
        for r in range(rows):
            base = rect.y0 + (r + 0.75) * rect.height / rows
            n = max(6, int(rect.width / 5))
            path += [(rect.x0 + rect.width * k / n, base - abs(math.sin(k * 1.9)) * layout["size"] * 0.45)
                     for k in range(n + 1)]
        content = page_tools.clean_words(text) if "\n" not in str(text) else str(text).strip()

        def begin(_i):
            self.ink.begin_wet_text(content, box, font, ink)
            self._reveal_text = content
            self._reveal.start()

        def finished():
            self._reveal.stop()
            self.ink.set_wet_text_shown(len(content))
            self._committed(gen, lambda: page_tools.add_text(tab, index, layout, text, ink), reply,
                            f"Wrote {content[:60]!r} on page {index + 1} at [{rect.x0:.0f},{rect.y0:.0f},"
                            f"{rect.x1:.0f},{rect.y1:.0f}].")

        seconds = max(1.0, len(content) / 20.0)              # about twenty letters a second
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:])) * scale
        self._pen_down(gen, tab, index, [path], None, 0, finished, on_trace=begin, speed=length / seconds)

    def _reveal_step(self):
        if self._reveal_text is not None:
            self.ink.set_wet_text_shown(len(self._reveal_text) * self.mascot.trace_progress())

    def draw(self, gen, tab, index, strokes, color, width, reply):
        ink = page_tools.color(color, "#1d4ed8")
        width = min(max(float(width or 2), 0.5), 12.0)
        self._pen_down(gen, tab, index, strokes, ink, width, lambda: self._committed(
            gen, lambda: page_tools.add_strokes(tab, index, strokes, ink, width), reply,
            f"Drew {len(strokes)} stroke{'s' if len(strokes) != 1 else ''} on page {index + 1}."))

    def shape(self, gen, tab, index, kind, x0, y0, x1, y1, color, width, reply):
        page = tab.document.page(index)
        (x0, y0), (x1, y1) = page_tools.clean_strokes(page, [[(x0, y0), (x1, y1)]])[0]
        paths = page_tools.shape_strokes(kind, x0, y0, x1, y1)
        ink = page_tools.color(color, "#e11d48")
        width = min(max(float(width or 2), 0.5), 12.0)
        self._pen_down(gen, tab, index, paths, ink, width, lambda: self._committed(
            gen, lambda: page_tools.add_shape(tab, index, kind, x0, y0, x1, y1, ink, width), reply,
            f"Drew a {kind} on page {index + 1} from ({x0:.0f},{y0:.0f}) to ({x1:.0f},{y1:.0f})."))

    # ------------------------------------------------------------------
    # showing and pressing
    # ------------------------------------------------------------------

    def _locate(self, cmd):
        """("button", rect) for a visible toolbar button, ("menu", rect of the menu's title), or None."""
        w = self.window
        for bar in cmd.toolbars:
            btn = bar.widgetForAction(cmd.action)
            if btn is not None and bar.isVisible() and btn.isVisible() and btn.width() > 0:
                return "button", QRectF(QRect(btn.mapTo(w, QPoint(0, 0)), btn.size()))
        if cmd.menus:
            bar = w.menuBar()
            geo = bar.actionGeometry(cmd.menus[0].menuAction())
            if geo.isValid():
                return "menu", QRectF(QRect(bar.mapTo(w, geo.topLeft()), geo.size()))
        return None

    def _spot_near(self, rect: QRectF):
        """Where the scribble sits so its nib touches `rect`: beside it, inside the window."""
        cx, cy = rect.center().x(), rect.center().y()
        for dx, dy in ((1, 0.35), (0.2, 1), (-1, 0.35), (-0.3, 1)):
            x = cx + dx * (rect.width() / 2 + REACH - 4)
            y = cy + dy * (rect.height() / 2 + REACH - 4)
            if MARGIN <= x <= self.window.width() - MARGIN and MARGIN <= y <= self.window.height() - MARGIN:
                return x, y
        return self._inside(cx + rect.width() / 2 + REACH, cy + REACH)

    def show(self, gen, cmd, note, done):
        """Fly to `cmd`, circle it and write `note`; for a menu item, open its menu with it highlighted."""
        self._close_menus()
        self.ink.clear_circles()
        found = self._locate(cmd)
        if found is None:
            done(f"{cmd.where()} isn't on screen right now (the ribbon may be hidden), so I couldn't show it. "
                 "Tell the person where it is instead.")
            return
        kind, rect = found
        self.mascot.mood = "idle"
        self.mascot.look_at = (rect.center().x(), rect.center().y())

        def arrived():
            if gen != self.gen:
                return
            self.mascot.mood = "pointing"
            self.ink.circle(rect)
            if kind == "button":
                self.ink.caption(note, rect, avoid=self.mascot.pos_f)
                QTimer.singleShot(anim.ms(0.75), lambda: gen == self.gen and done(
                    f"Showing {cmd.where()}" + (" (it's on the toolbar)." if not cmd.path else ".")))
            else:
                QTimer.singleShot(anim.ms(0.55), lambda: self._open_menus(gen, cmd, note, done))

        self.mascot.fly_to(*self._spot_near(rect), arrived)

    def _open_menus(self, gen, cmd, note, done):
        if gen != self.gen:
            return
        bar = self.window.menuBar()
        bar.setActiveAction(cmd.menus[0].menuAction())
        for parent, sub in zip(cmd.menus, cmd.menus[1:]):
            parent.setActiveAction(sub.menuAction())
        last = cmd.menus[-1]

        def highlight():
            if gen != self.gen:
                return
            last.setActiveAction(cmd.action)
            item = last.actionGeometry(cmd.action)
            if last.isVisible() and item.isValid():
                rect = QRectF(QRect(self.window.mapFromGlobal(last.mapToGlobal(item.topLeft())), item.size()))
                menu_rect = QRectF(QRect(self.window.mapFromGlobal(last.mapToGlobal(QPoint(0, 0))), last.size()))
                spot = (menu_rect.right() + REACH - 4, rect.center().y())
                if spot[0] <= self.window.width() - MARGIN:
                    self.mascot.look_at = (menu_rect.right(), rect.center().y())
                    self.mascot.fly_to(*spot)
            done(f"Showing {cmd.where()}: its menu is open with the item highlighted, so the person can click it.")

        QTimer.singleShot(anim.ms(0.25), highlight)

    def press(self, gen, cmd, note, done):
        """Show it, tap it, then press it; risky ones ask first. Reports what happened, including any dialog."""
        def go():
            if gen != self.gen:
                return
            self.show(gen, cmd, note, lambda _r: tap())

        def tap():
            if gen != self.gen:
                return
            found = self._locate(cmd)
            if found is not None:
                kind, rect = found
                target = rect.center()
                if kind == "menu" and self.mascot.look_at is not None:
                    target = QPointF(*self.mascot.look_at)
                self.ink.tap(target.x(), target.y())
            self.mascot.squash()
            QTimer.singleShot(anim.ms(0.3), trigger)

        def trigger():
            if gen != self.gen:
                return
            self._close_menus()
            was = cmd.action.isChecked()
            QTimer.singleShot(0, cmd.action.trigger)            # a dialog it opens runs its own loop: don't block on it
            QTimer.singleShot(anim.ms(0.5) + 150, lambda: report(was))

        def report(was):
            if gen != self.gen:
                return
            self.ink.clear_circles()
            modal = self._modal()
            text = f"Clicked {cmd.where()}."
            if cmd.action.isCheckable():
                text += f" It's now {'on' if cmd.action.isChecked() else 'off'}" + \
                        (" (it was already like that before)." if cmd.action.isChecked() == was else ".")
            if modal is not None:
                text += " " + describe_dialog(modal)
            else:
                text += "\nNow: " + Catalog(self.window, exclude=self.own_actions).state(brief=True)
            done(text)

        if RISKY.search(cmd.label) and self.bubble is not None:
            self.mascot.mood = "puzzled"
            self.bubble.confirm(f"Shall I press {cmd.where()}?",
                                lambda yes: go() if yes else done("The person said no, so it wasn't clicked."))
        else:
            go()

    def _close_menus(self):
        for _ in range(10):
            popup = QApplication.activePopupWidget()
            if popup is None:
                break
            popup.close()
        bar = self.window.menuBar()
        if bar is not None:
            bar.setActiveAction(None)

    def _modal(self):
        modal = QApplication.activeModalWidget()
        return modal if modal is not None and modal is not self.bubble else None


def _first_occurrence(quads):
    """The quads of the first place some text appears (it may run on to the next line or two)."""
    out = [quads[0]]
    for q in quads[1:]:
        prev = out[-1].rect
        r = q.rect
        if prev.y1 - 1 <= r.y0 <= prev.y1 + prev.height * 1.3 and r.x0 < prev.x0:
            out.append(q)
        else:
            break
    return out


def _local_reply(op, text, error):
    """What the offline finder says after marking up or reading a page."""
    if error:
        return text
    if op == "mark":
        return "Done! " + text
    lines = [line.split("] ", 1)[1] for line in text.splitlines() if line.startswith("[") and "] " in line]
    if not lines:
        return text.splitlines()[-1] if text else "There's nothing to read on this page."
    start = " / ".join(lines[:3])
    return (f"This page has {len(lines)} line{'s' if len(lines) != 1 else ''} of text. It starts: “{start[:300]}”. "
            "Add a Claude or Hugging Face key in my settings and I can summarise it or answer questions about it.")


def describe_dialog(dialog):
    """What a dialog shows, in a line or two, so Claude can tell the person what to do in it."""
    texts, buttons, fields = [], [], []
    for widget in dialog.findChildren(QLabel):
        if widget.isVisible() and widget.text().strip():
            text = re.sub(r"<[^>]+>", " ", widget.text())
            texts.append(" ".join(text.split())[:140])
    for widget in dialog.findChildren(QAbstractButton):
        if widget.isVisible() and widget.text().strip():
            label = widget.text().replace("&", "")
            if isinstance(widget, (QCheckBox, QRadioButton)):
                label += " (ticked)" if widget.isChecked() else " (not ticked)"
            buttons.append(label)
    for widget in dialog.findChildren(QLineEdit) + dialog.findChildren(QComboBox):
        if widget.isVisible():
            if isinstance(widget, QComboBox):
                fields.append(f"a list showing {widget.currentText()!r}")
            elif widget.placeholderText() or widget.text():
                fields.append(f"a box with {widget.text()!r}" if widget.text() else f"an empty box ({widget.placeholderText()})")
    for widget in dialog.findChildren(QTextEdit) + dialog.findChildren(QPlainTextEdit):
        if widget.isVisible() and not widget.isReadOnly():
            fields.append("a text area")
    parts = [f'A dialog opened: "{dialog.windowTitle()}".']
    if texts:
        parts.append("It says: " + " | ".join(texts[:25]))
    if fields:
        parts.append("Fields: " + "; ".join(fields[:15]))
    if buttons:
        parts.append("Buttons: " + ", ".join(f"[{b}]" for b in buttons[:25]))
    return " ".join(parts)
