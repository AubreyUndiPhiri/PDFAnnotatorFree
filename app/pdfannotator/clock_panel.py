"""The Clock panel: Clock (an analogue face, the date and world clocks),
Timer (a countdown on a ring, with presets), Stopwatch (laps, best and
worst lap) and Alarms (repeat once, every day, on weekdays or at weekends;
snooze). Timers and alarms keep going while the panel is closed; the
status bar shows a running timer, and when one is up a card says so and a
soft chime plays until it is dismissed.

Everything is painted from the theme's colours, so it follows the light /
dark look."""
import json
import math
import os
import struct
import sys
import tempfile
import time
import wave

from PySide6.QtCore import QDateTime, QElapsedTimer, QPointF, QRectF, QSize, Qt, QTime, QTimer, QTimeZone, Signal
from PySide6.QtGui import QColor, QConicalGradient, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QSpinBox, QStackedWidget, QTimeEdit, QToolButton,
    QVBoxLayout, QWidget,
)

from . import icons, theme

WORLD_CITIES = {
    "Lusaka": "Africa/Lusaka", "Johannesburg": "Africa/Johannesburg", "Nairobi": "Africa/Nairobi",
    "Lagos": "Africa/Lagos", "Cairo": "Africa/Cairo", "Kigali": "Africa/Kigali", "Accra": "Africa/Accra",
    "London": "Europe/London", "Paris": "Europe/Paris", "Berlin": "Europe/Berlin", "Madrid": "Europe/Madrid",
    "Rome": "Europe/Rome", "Moscow": "Europe/Moscow", "Istanbul": "Europe/Istanbul", "Dubai": "Asia/Dubai",
    "Mumbai": "Asia/Kolkata", "Dhaka": "Asia/Dhaka", "Bangkok": "Asia/Bangkok", "Singapore": "Asia/Singapore",
    "Beijing": "Asia/Shanghai", "Hong Kong": "Asia/Hong_Kong", "Tokyo": "Asia/Tokyo", "Seoul": "Asia/Seoul",
    "Sydney": "Australia/Sydney", "Auckland": "Pacific/Auckland", "Honolulu": "Pacific/Honolulu",
    "Los Angeles": "America/Los_Angeles", "Denver": "America/Denver", "Chicago": "America/Chicago",
    "New York": "America/New_York", "Toronto": "America/Toronto", "Mexico City": "America/Mexico_City",
    "Bogotá": "America/Bogota", "São Paulo": "America/Sao_Paulo", "Buenos Aires": "America/Argentina/Buenos_Aires",
    "UTC": "UTC",
}
TIMER_PRESETS = ((1, "1 min"), (3, "3 min"), (5, "5 min"), (10, "10 min"), (15, "15 min"), (25, "25 min"),
                 (30, "30 min"), (60, "1 hour"))
REPEATS = ("Once", "Every day", "Weekdays", "Weekends")


def _font(size, weight=QFont.Normal):
    font = QFont(theme.FONT_FAMILY)
    font.setStyleHint(QFont.SansSerif)
    font.setPointSizeF(size)
    font.setWeight(weight)
    return font


def _hms(seconds, tenths=False):
    seconds = max(0.0, seconds)
    whole = int(seconds + (0 if tenths else 0.999))
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    text = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    if tenths:
        text += f".{int((seconds % 1) * 100):02d}"
    return text


# ---------------------------------------------------------------------------
class Chime:
    """A soft two-note bell, played over and over until stopped."""

    _path = None

    @classmethod
    def _file(cls):
        if cls._path and os.path.exists(cls._path):
            return cls._path
        rate, out = 44100, []
        notes = ((0.0, 880.0), (0.42, 1318.5), (1.05, 880.0), (1.47, 1318.5))
        total = 2.6
        for i in range(int(rate * total)):
            t = i / rate
            v = 0.0
            for start, freq in notes:
                dt = t - start
                if dt < 0 or dt > 1.6:
                    continue
                env = math.exp(-dt * 4.2) * min(1.0, dt * 400)   # a soft strike, then a long fade
                v += env * (math.sin(2 * math.pi * freq * dt) + 0.32 * math.sin(2 * math.pi * freq * 2.005 * dt)
                            + 0.12 * math.sin(2 * math.pi * freq * 3.01 * dt))
            out.append(int(max(-1.0, min(1.0, v * 0.23)) * 32767))
        path = os.path.join(tempfile.gettempdir(), "aupedean-chime.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(struct.pack(f"<{len(out)}h", *out))
        cls._path = path
        return path

    def __init__(self):
        self._beeper = QTimer()
        self._beeper.setInterval(1200)
        self._beeper.timeout.connect(QApplication.beep)
        self.playing = False

    def start(self):
        if self.playing:
            return
        self.playing = True
        if sys.platform == "win32":
            try:
                import winsound

                winsound.PlaySound(self._file(), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
                return
            except Exception:   # no sound device: fall back to the system beep
                pass
        QApplication.beep()
        self._beeper.start()

    def stop(self):
        if not self.playing:
            return
        self.playing = False
        self._beeper.stop()
        if sys.platform == "win32":
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass


# ---------------------------------------------------------------------------
class ClockFace(QWidget):
    """An analogue clock with a sweeping second hand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(170, 170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.zone = None

    def sizeHint(self):
        return QSize(220, 220)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 8
        c = QPointF(self.width() / 2, self.height() / 2)
        r = side / 2
        dark = theme.mode == theme.DARK
        # the face: a soft dome with a fine rim
        for spread, alpha in ((6, 12), (3, 20)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, alpha * (2 if dark else 1)))
            p.drawEllipse(c + QPointF(0, 2), r + spread * 0.5, r + spread * 0.5)
        face = QRadialGradient(c + QPointF(-r * 0.3, -r * 0.4), r * 1.6)
        face.setColorAt(0, QColor("#ffffff") if not dark else QColor("#2a2a33"))
        face.setColorAt(1, QColor("#eceefa") if not dark else QColor("#121217"))
        p.setBrush(face)
        p.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        p.drawEllipse(c, r, r)
        ink = QColor(theme.TEXT)
        muted = QColor(theme.TEXT_MUTED)
        for i in range(60):
            a = math.radians(i * 6)
            long = i % 5 == 0
            outer, inner = r - 8, r - (20 if long else 13)
            p.setPen(QPen(ink if long else muted, 2.2 if long else 1, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(c + QPointF(math.sin(a) * inner, -math.cos(a) * inner),
                       c + QPointF(math.sin(a) * outer, -math.cos(a) * outer))
        p.setFont(_font(max(7.0, r / 10), QFont.DemiBold))
        p.setPen(ink)
        for h in (12, 3, 6, 9):
            a = math.radians(h * 30)
            at = c + QPointF(math.sin(a) * (r - 34), -math.cos(a) * (r - 34))
            p.drawText(QRectF(at.x() - 16, at.y() - 10, 32, 20), Qt.AlignCenter, str(h))
        now = QDateTime.currentDateTime()
        if self.zone is not None:
            now = now.toTimeZone(self.zone)
        t = now.time()
        secs = t.second() + t.msec() / 1000
        mins = t.minute() + secs / 60
        hours = (t.hour() % 12) + mins / 60

        def hand(angle_deg, length, width, color, tail=0.0):
            a = math.radians(angle_deg)
            d = QPointF(math.sin(a), -math.cos(a))
            p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(c - d * tail, c + d * length)

        hand(hours * 30, r * 0.5, 5.5, ink)
        hand(mins * 6, r * 0.74, 3.8, ink)
        accent = QColor(theme.ACCENT)
        hand(secs * 6, r * 0.82, 1.6, accent, tail=r * 0.16)
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawEllipse(c, 5, 5)
        p.setBrush(QColor("#ffffff") if not dark else QColor("#16161b"))
        p.drawEllipse(c, 2, 2)
        p.end()


class Ring(QWidget):
    """The timer's ring: the time left as a glowing arc, the digits inside."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fraction = 1.0
        self.text = "00:00"
        self.caption = ""
        self.alert = False
        self.setMinimumSize(190, 190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def sizeHint(self):
        return QSize(230, 230)

    def mousePressEvent(self, event):
        self.clicked.emit()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 16
        c = QPointF(self.width() / 2, self.height() / 2)
        r = side / 2
        width = max(8.0, r * 0.1)
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        track = QColor(theme.BORDER)
        p.setPen(QPen(track, width, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(rect)
        accent = QColor(theme.DANGER if self.alert else theme.ACCENT)
        if self.fraction > 0.0005:
            grad = QConicalGradient(c, 90)
            grad.setColorAt(0, accent.lighter(135))
            grad.setColorAt(max(0.001, 1 - self.fraction), accent)
            grad.setColorAt(1, accent.lighter(135))
            glow = QColor(accent)
            glow.setAlpha(50)
            p.setPen(QPen(glow, width + 8, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, 90 * 16, int(self.fraction * 360 * 16))
            p.setPen(QPen(grad, width, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, 90 * 16, int(self.fraction * 360 * 16))
            end = math.radians(90 + self.fraction * 360)
            dot = c + QPointF(math.cos(end) * r, -math.sin(end) * r)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(dot, width * 0.28, width * 0.28)
        p.setPen(QColor(theme.TEXT))
        p.setFont(_font(max(14.0, r / 4.2), QFont.Light))
        p.drawText(QRectF(c.x() - r, c.y() - r * 0.36, 2 * r, r * 0.6), Qt.AlignCenter, self.text)
        p.setPen(QColor(theme.TEXT_MUTED))
        p.setFont(_font(8.5))
        p.drawText(QRectF(c.x() - r, c.y() + r * 0.24, 2 * r, r * 0.3), Qt.AlignCenter, self.caption)
        p.end()


def _button(text, kind="secondary", icon=None):
    btn = QPushButton(text)
    btn.setObjectName("clockButton")
    btn.setProperty("kind", kind)
    if icon:
        btn.setIcon(icons.icon(icon, "#ffffff" if kind == "primary" else None))
    btn.setCursor(Qt.PointingHandCursor)
    btn.setMinimumHeight(34)
    return btn


def _repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# ---------------------------------------------------------------------------
class ClockView(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 4, 0, 0)
        box.setSpacing(6)
        self.face = ClockFace()
        box.addWidget(self.face, 0, Qt.AlignHCenter)
        self.time_label = QLabel()
        self.time_label.setObjectName("clockBig")
        self.time_label.setAlignment(Qt.AlignCenter)
        box.addWidget(self.time_label)
        self.date_label = QLabel()
        self.date_label.setObjectName("clockMuted")
        self.date_label.setAlignment(Qt.AlignCenter)
        box.addWidget(self.date_label)
        head = QHBoxLayout()
        title = QLabel("World clocks")
        title.setObjectName("clockSection")
        head.addWidget(title)
        head.addStretch(1)
        self.city_combo = QComboBox()
        self.city_combo.addItem("Add a city...")
        self.city_combo.addItems(sorted(WORLD_CITIES))
        self.city_combo.activated.connect(self._add_city)
        head.addWidget(self.city_combo)
        box.addLayout(head)
        self.cities = QListWidget()
        self.cities.setObjectName("clockList")
        box.addWidget(self.cities, 1)
        try:
            saved = json.loads(theme._settings().value("clock/cities", "") or "[]")
        except ValueError:
            saved = []
        self.city_names = [c for c in saved if c in WORLD_CITIES] or ["London", "New York", "Tokyo"]
        self._rows = []
        self._rebuild()

    def _add_city(self, index):
        if index <= 0:
            return
        name = self.city_combo.itemText(index)
        self.city_combo.setCurrentIndex(0)
        if name not in self.city_names:
            self.city_names.append(name)
            self._save()
            self._rebuild()

    def _remove_city(self, name):
        if name in self.city_names:
            self.city_names.remove(name)
            self._save()
            self._rebuild()

    def _save(self):
        theme._settings().setValue("clock/cities", json.dumps(self.city_names))

    def _rebuild(self):
        self.cities.clear()
        self._rows = []
        for name in self.city_names:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(8, 4, 4, 4)
            left = QVBoxLayout()
            left.setSpacing(0)
            city = QLabel(name)
            city.setObjectName("clockCity")
            offset = QLabel()
            offset.setObjectName("clockMuted")
            left.addWidget(city)
            left.addWidget(offset)
            line.addLayout(left, 1)
            when = QLabel()
            when.setObjectName("clockCityTime")
            line.addWidget(when)
            remove = QToolButton()
            remove.setIcon(icons.icon("close"))
            remove.setAutoRaise(True)
            remove.setToolTip(f"Remove {name}")
            remove.clicked.connect(lambda _c=False, n=name: self._remove_city(n))
            line.addWidget(remove)
            item = QListWidgetItem()
            item.setSizeHint(row.sizeHint())
            self.cities.addItem(item)
            self.cities.setItemWidget(item, row)
            self._rows.append((QTimeZone(WORLD_CITIES[name].encode()), offset, when))
        self.tick()

    def tick(self):
        now = QDateTime.currentDateTime()
        self.time_label.setText(now.toString("HH:mm:ss"))
        self.date_label.setText(now.toString("dddd, d MMMM yyyy"))
        self.face.update()
        here = now.offsetFromUtc()
        for zone, offset, when in self._rows:
            there = now.toTimeZone(zone)
            diff = (zone.offsetFromUtc(now) - here) / 3600
            day = "Today" if there.date() == now.date() else "Tomorrow" if there.date() > now.date() else "Yesterday"
            hours = f"{diff:+g} h" if diff else "same time"
            night = there.time().hour() < 6 or there.time().hour() >= 19
            offset.setText(f"{day}, {hours}" + ("  ·  night" if night else ""))
            when.setText(there.toString("HH:mm"))


# ---------------------------------------------------------------------------
class TimerView(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.duration = float(theme._settings().value("clock/timer", 300) or 300)
        self.deadline = None       # time.monotonic() when it ends (running)
        self.remaining = self.duration
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 4, 0, 0)
        box.setSpacing(10)
        self.ring = Ring()
        self.ring.clicked.connect(self.toggle)
        box.addWidget(self.ring, 0, Qt.AlignHCenter)

        self.setter = QWidget()
        grid = QHBoxLayout(self.setter)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.spins = []
        for label, maximum in (("h", 23), ("min", 59), ("s", 59)):
            spin = QSpinBox()
            spin.setObjectName("clockSpin")
            spin.setRange(0, maximum)
            spin.setSuffix(f" {label}")
            spin.setAlignment(Qt.AlignCenter)
            spin.valueChanged.connect(self._spins_changed)
            grid.addWidget(spin)
            self.spins.append(spin)
        box.addWidget(self.setter)

        presets = QGridLayout()
        presets.setSpacing(6)
        for i, (minutes, label) in enumerate(TIMER_PRESETS):
            chip = QPushButton(label)
            chip.setObjectName("clockChip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _c=False, m=minutes: self.set_duration(m * 60, start=True))
            presets.addWidget(chip, i // 4, i % 4)
        box.addLayout(presets)

        row = QHBoxLayout()
        self.start_btn = _button("Start", "primary", "play")
        self.start_btn.clicked.connect(self.toggle)
        self.plus_btn = _button("+1 min")
        self.plus_btn.clicked.connect(lambda: self.add(60))
        self.reset_btn = _button("Reset")
        self.reset_btn.clicked.connect(self.reset)
        row.addWidget(self.reset_btn)
        row.addWidget(self.plus_btn)
        row.addWidget(self.start_btn, 1)
        box.addLayout(row)
        box.addStretch(1)
        self._show_duration_in_spins()
        self.tick()

    @property
    def running(self):
        return self.deadline is not None

    def _show_duration_in_spins(self):
        total = int(self.duration)
        for spin, value in zip(self.spins, (total // 3600, (total % 3600) // 60, total % 60)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _spins_changed(self):
        h, m, s = (spin.value() for spin in self.spins)
        self.set_duration(h * 3600 + m * 60 + s)

    def set_duration(self, seconds, start=False):
        self.deadline = None
        self.duration = float(seconds)
        self.remaining = self.duration
        theme._settings().setValue("clock/timer", self.duration)
        self._show_duration_in_spins()
        if start and seconds > 0:
            self.start()
        self.tick()

    def toggle(self):
        if self.running:
            self.pause()
        elif self.remaining > 0:
            self.start()

    def start(self):
        if self.remaining <= 0:
            self.remaining = self.duration
        if self.remaining <= 0:
            return
        self.deadline = time.monotonic() + self.remaining
        self.panel.timer_alert_dismissed()
        self.tick()

    def pause(self):
        if self.running:
            self.remaining = max(0.0, self.deadline - time.monotonic())
            self.deadline = None
        self.tick()

    def add(self, seconds):
        if self.running:
            self.deadline += seconds
        else:
            self.remaining += seconds
            if self.remaining > self.duration:
                self.duration = self.remaining
        self.tick()

    def reset(self):
        self.deadline = None
        self.remaining = self.duration
        self.ring.alert = False
        self.panel.timer_alert_dismissed()
        self.tick()

    def tick(self):
        if self.running:
            self.remaining = max(0.0, self.deadline - time.monotonic())
            if self.remaining <= 0:
                self.deadline = None
                self.remaining = 0.0
                self.ring.alert = True
                self.panel.timer_finished(self.duration)
        self.ring.fraction = (self.remaining / self.duration) if self.duration > 0 else 0.0
        self.ring.text = _hms(self.remaining)
        if self.running:
            end = QTime.currentTime().addSecs(int(self.remaining))
            self.ring.caption = f"ends at {end.toString('HH:mm')}"
        elif self.remaining <= 0 and self.duration > 0:
            self.ring.caption = "time's up"
        elif self.remaining < self.duration:
            self.ring.caption = "paused"
        else:
            self.ring.caption = "tap to start"
        self.ring.update()
        self.setter.setVisible(not self.running and self.remaining == self.duration)
        self.start_btn.setText("Pause" if self.running else "Resume" if 0 < self.remaining < self.duration else "Start")
        self.start_btn.setIcon(icons.icon("stop" if self.running else "play", "#ffffff"))

    def status(self):
        if self.running:
            return f"⏱ {_hms(self.remaining)}"
        return ""


# ---------------------------------------------------------------------------
class StopwatchView(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.clock = QElapsedTimer()
        self.banked = 0.0          # seconds before the last start
        self.laps = []             # lap durations
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 18, 0, 0)
        box.setSpacing(10)
        self.display = QLabel("00:00.00")
        self.display.setObjectName("clockHuge")
        self.display.setAlignment(Qt.AlignCenter)
        box.addWidget(self.display)
        self.lap_now = QLabel("")
        self.lap_now.setObjectName("clockMuted")
        self.lap_now.setAlignment(Qt.AlignCenter)
        box.addWidget(self.lap_now)
        row = QHBoxLayout()
        self.lap_btn = _button("Lap")
        self.lap_btn.clicked.connect(self.lap_or_reset)
        self.start_btn = _button("Start", "primary", "play")
        self.start_btn.clicked.connect(self.toggle)
        row.addWidget(self.lap_btn, 1)
        row.addWidget(self.start_btn, 1)
        box.addLayout(row)
        self.list = QListWidget()
        self.list.setObjectName("clockList")
        box.addWidget(self.list, 1)
        self._refresh_buttons()

    @property
    def running(self):
        return self.clock.isValid()

    def elapsed(self):
        return self.banked + (self.clock.elapsed() / 1000 if self.running else 0.0)

    def toggle(self):
        if self.running:
            self.banked = self.elapsed()
            self.clock.invalidate()
        else:
            self.clock.start()
        self._refresh_buttons()
        self.tick()

    def lap_or_reset(self):
        if self.running:
            total = self.elapsed()
            self.laps.append(total - sum(self.laps))
            self._fill_laps()
        else:
            self.banked = 0.0
            self.laps = []
            self.list.clear()
            self.tick()
        self._refresh_buttons()

    def _fill_laps(self):
        self.list.clear()
        best = min(self.laps) if len(self.laps) > 1 else None
        worst = max(self.laps) if len(self.laps) > 1 else None
        total = 0.0
        rows = []
        for i, lap in enumerate(self.laps, 1):
            total += lap
            mark = "   fastest" if lap == best else "   slowest" if lap == worst else ""
            rows.append((f"Lap {i:<3}  {_hms(lap, True):>10}   {_hms(total, True):>10}{mark}", mark))
        for text, mark in reversed(rows):
            item = QListWidgetItem(text)
            if mark:
                item.setForeground(QColor(theme.ACCENT if "fastest" in mark else theme.DANGER))
            self.list.addItem(item)

    def _refresh_buttons(self):
        self.start_btn.setText("Stop" if self.running else "Start")
        self.start_btn.setIcon(icons.icon("stop" if self.running else "play", "#ffffff"))
        self.start_btn.setProperty("kind", "danger" if self.running else "primary")
        _repolish(self.start_btn)
        self.lap_btn.setText("Lap" if self.running or not self.elapsed() else "Reset")
        self.lap_btn.setEnabled(self.running or self.elapsed() > 0)

    def tick(self):
        total = self.elapsed()
        self.display.setText(_hms(total, True))
        self.lap_now.setText(f"Lap {len(self.laps) + 1}  {_hms(total - sum(self.laps), True)}" if self.laps else "")

    def status(self):
        return f"⏲ {_hms(self.elapsed())}" if self.running else ""


# ---------------------------------------------------------------------------
class AlarmView(QWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        try:
            self.alarms = json.loads(theme._settings().value("clock/alarms", "") or "[]")
        except ValueError:
            self.alarms = []
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 4, 0, 0)
        box.setSpacing(8)
        editor = QFrame()
        editor.setObjectName("clockCard")
        form = QGridLayout(editor)
        form.setContentsMargins(12, 10, 12, 10)
        form.setHorizontalSpacing(8)
        self.time_edit = QTimeEdit(QTime.currentTime().addSecs(3600))
        self.time_edit.setObjectName("clockTimeEdit")
        self.time_edit.setDisplayFormat("HH:mm")
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Label (optional)")
        self.repeat = QComboBox()
        self.repeat.addItems(REPEATS)
        add = _button("Add alarm", "primary")
        add.clicked.connect(self.add_alarm)
        form.addWidget(self.time_edit, 0, 0, 2, 1)
        form.addWidget(self.label_edit, 0, 1)
        form.addWidget(self.repeat, 1, 1)
        form.addWidget(add, 2, 0, 1, 2)
        box.addWidget(editor)
        self.list = QListWidget()
        self.list.setObjectName("clockList")
        box.addWidget(self.list, 1)
        self.next_label = QLabel("")
        self.next_label.setObjectName("clockMuted")
        self.next_label.setAlignment(Qt.AlignCenter)
        box.addWidget(self.next_label)
        self._fired = set()        # (alarm index, yyyy-mm-dd HH:mm) already rung
        self._rebuild()

    def _save(self):
        theme._settings().setValue("clock/alarms", json.dumps(self.alarms))

    def add_alarm(self):
        self.alarms.append({"time": self.time_edit.time().toString("HH:mm"), "label": self.label_edit.text().strip(),
                            "repeat": self.repeat.currentText(), "on": True})
        self.alarms.sort(key=lambda a: a["time"])
        self.label_edit.clear()
        self._save()
        self._rebuild()

    def _rebuild(self):
        self.list.clear()
        for i, alarm in enumerate(self.alarms):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(10, 6, 4, 6)
            left = QVBoxLayout()
            left.setSpacing(0)
            big = QLabel(alarm["time"])
            big.setObjectName("clockCityTime")
            sub = QLabel(" · ".join(x for x in (alarm.get("label"), alarm.get("repeat", "Once")) if x))
            sub.setObjectName("clockMuted")
            left.addWidget(big)
            left.addWidget(sub)
            line.addLayout(left, 1)
            switch = QCheckBox()
            switch.setObjectName("clockSwitch")
            switch.setChecked(alarm.get("on", True))
            switch.setToolTip("On / off")
            switch.toggled.connect(lambda on, i=i: self._switch(i, on))
            line.addWidget(switch)
            remove = QToolButton()
            remove.setIcon(icons.icon("delete"))
            remove.setAutoRaise(True)
            remove.setToolTip("Delete this alarm")
            remove.clicked.connect(lambda _c=False, i=i: self._delete(i))
            line.addWidget(remove)
            item = QListWidgetItem()
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        self._show_next()

    def _switch(self, index, on):
        if 0 <= index < len(self.alarms):
            self.alarms[index]["on"] = on
            self._save()
            self._show_next()

    def _delete(self, index):
        if 0 <= index < len(self.alarms):
            del self.alarms[index]
            self._save()
            self._rebuild()

    @staticmethod
    def _rings_today(alarm, weekday):
        repeat = alarm.get("repeat", "Once")
        return (repeat in ("Once", "Every day") or (repeat == "Weekdays" and weekday < 5)
                or (repeat == "Weekends" and weekday >= 5))

    def _show_next(self):
        now = QDateTime.currentDateTime()
        best = None
        for alarm in self.alarms:
            if not alarm.get("on", True):
                continue
            t = QTime.fromString(alarm["time"], "HH:mm")
            for days in range(0, 8):
                when = QDateTime(now.date().addDays(days), t)
                if when > now and self._rings_today(alarm, when.date().dayOfWeek() - 1):
                    if best is None or when < best:
                        best = when
                    break
        if best is None:
            self.next_label.setText("No alarms on")
        else:
            secs = now.secsTo(best)
            h, m = secs // 3600, (secs % 3600) // 60
            self.next_label.setText(f"Next alarm in {h} h {m:02d} min" if h else f"Next alarm in {m + 1} min")

    def tick(self):
        now = QDateTime.currentDateTime()
        stamp = now.toString("yyyy-MM-dd HH:mm")
        hm = now.toString("HH:mm")
        weekday = now.date().dayOfWeek() - 1
        changed = False
        for i, alarm in enumerate(self.alarms):
            if not alarm.get("on", True) or alarm["time"] != hm or (i, stamp) in self._fired:
                continue
            if not self._rings_today(alarm, weekday):
                continue
            self._fired.add((i, stamp))
            if alarm.get("repeat", "Once") == "Once":
                alarm["on"] = False
                changed = True
            self.panel.alarm_ringing(alarm)
        if changed:
            self._save()
            self._rebuild()
        if now.time().second() == 0:
            self._show_next()

    def snooze(self, alarm, minutes=5):
        when = QTime.currentTime().addSecs(minutes * 60).toString("HH:mm")
        self.alarms.append({"time": when, "label": (alarm.get("label") or "Alarm") + " (snoozed)",
                            "repeat": "Once", "on": True, "snooze": True})
        self._save()
        self._rebuild()


# ---------------------------------------------------------------------------
class AlertCard(QFrame):
    """'Time's up' / an alarm: a card over the window until dismissed."""

    def __init__(self, window, title, message, actions):
        super().__init__(window)
        self.setObjectName("clockAlert")
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 16, 20, 16)
        box.setSpacing(8)
        head = QLabel(title)
        head.setObjectName("clockAlertTitle")
        box.addWidget(head)
        body = QLabel(message)
        body.setObjectName("clockMuted")
        body.setWordWrap(True)
        box.addWidget(body)
        row = QHBoxLayout()
        row.addStretch(1)
        for text, kind, slot in actions:
            btn = _button(text, kind)
            btn.clicked.connect(lambda _c=False, f=slot: (f(), self.close_card()))
            row.addWidget(btn)
        box.addLayout(row)
        self.setFixedWidth(340)
        self.adjustSize()
        self._place()
        window.installEventFilter(self)
        self.show()
        self.raise_()

    def _place(self):
        window = self.parentWidget()
        self.move(window.width() - self.width() - 24, window.height() - self.height() - 48)

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == event.Type.Resize:
            self._place()
        return False

    def close_card(self):
        self.parentWidget().removeEventFilter(self)
        self.hide()
        self.deleteLater()


# ---------------------------------------------------------------------------
class ClockPanel(QWidget):
    """Clock, Timer, Stopwatch and Alarms, for the side panel."""

    status_changed = Signal(str)
    PAGES = ("Clock", "Timer", "Stopwatch", "Alarms")

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setObjectName("clockPanel")
        self.chime = Chime()
        self._timer_card = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)
        segments = QFrame()
        segments.setObjectName("segmentBar")
        seg_row = QHBoxLayout(segments)
        seg_row.setContentsMargins(3, 3, 3, 3)
        seg_row.setSpacing(2)
        self.segment_group = QButtonGroup(self)
        self.stack = QStackedWidget()
        self.clock_view = ClockView(self)
        self.timer_view = TimerView(self)
        self.stopwatch_view = StopwatchView(self)
        self.alarm_view = AlarmView(self)
        for i, (name, view) in enumerate(zip(self.PAGES, (self.clock_view, self.timer_view, self.stopwatch_view,
                                                         self.alarm_view))):
            btn = QPushButton(name)
            btn.setObjectName("segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            self.segment_group.addButton(btn, i)
            seg_row.addWidget(btn)
            self.stack.addWidget(view)
        self.segment_group.idClicked.connect(self.show_page)
        outer.addWidget(segments)
        outer.addWidget(self.stack, 1)
        start = theme._settings().value("clock/page", "Clock")
        self.show_page(self.PAGES.index(start) if start in self.PAGES else 0)

        self._fast = QTimer(self)          # smooth hands and digits while showing
        self._fast.setInterval(50)
        self._fast.timeout.connect(self._tick_fast)
        self._fast.start()
        self._slow = QTimer(self)          # alarms and the status bar, always
        self._slow.setInterval(500)
        self._slow.timeout.connect(self._tick_slow)
        self._slow.start()
        self._last_status = None

    def show_page(self, index):
        self.stack.setCurrentIndex(index)
        self.segment_group.button(index).setChecked(True)
        theme._settings().setValue("clock/page", self.PAGES[index])

    def _tick_fast(self):
        if not self.isVisible():
            if self.timer_view.running:
                self.timer_view.tick()
            return
        page = self.stack.currentWidget()
        if page is self.clock_view:
            self.clock_view.face.update()
        if page is self.timer_view or self.timer_view.running:
            self.timer_view.tick()
        if page is self.stopwatch_view and self.stopwatch_view.running:
            self.stopwatch_view.tick()

    def _tick_slow(self):
        if self.isVisible() and self.stack.currentWidget() is self.clock_view:
            self.clock_view.tick()
        if self.timer_view.running:
            self.timer_view.tick()
        self.alarm_view.tick()
        status = "   ".join(x for x in (self.timer_view.status(), self.stopwatch_view.status()) if x)
        if status != self._last_status:
            self._last_status = status
            self.status_changed.emit(status)

    # ---- ringing
    def timer_finished(self, duration):
        self.chime.start()
        if self._timer_card is None:
            self._timer_card = AlertCard(self.window, "Time's up",
                                         f"Your {_hms(duration)} timer has finished.",
                                         [("Restart", "secondary", self._restart_timer),
                                          ("Dismiss", "primary", self.timer_alert_dismissed)])
            self._timer_card.destroyed.connect(lambda *_: setattr(self, "_timer_card", None))

    def _restart_timer(self):
        self.chime.stop()
        self.timer_view.ring.alert = False
        self.timer_view.reset()
        self.timer_view.start()

    def timer_alert_dismissed(self):
        self.chime.stop()
        self.timer_view.ring.alert = False
        if self._timer_card is not None:
            card, self._timer_card = self._timer_card, None
            card.close_card()

    def alarm_ringing(self, alarm):
        self.chime.start()
        AlertCard(self.window, f"Alarm  {alarm['time']}", alarm.get("label") or "It's time.",
                  [("Snooze 5 min", "secondary", lambda: (self.chime.stop(), self.alarm_view.snooze(alarm))),
                   ("Dismiss", "primary", self.chime.stop)])

    def shutdown(self):
        self.chime.stop()
