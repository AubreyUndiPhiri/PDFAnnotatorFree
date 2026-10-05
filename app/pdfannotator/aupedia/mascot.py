"""The scribble: a ball scribbled with a pen (two and a half loops of ink
that "boil" like hand-drawn animation), with eyes, a mouth and a tail that
ends in a pen nib. It flies on arcs (squashing before it leaves, stretching
as it goes, wobbling when it lands) and leaves a drying ink trail; it circles
what it's talking about and taps what it clicks.

Both widgets let every click through to the window: the director's event
filter decides what touches the scribble itself."""
import math
import random

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF, QRegion
from PySide6.QtWidgets import QWidget

from .. import theme
from . import anim

R = 24                 # body radius, before SCALE
SCALE = 1.2            # how big it's drawn
SIZE = 180             # the widget: room for squash, the tail and the thinking dots
CENTER = (SIZE / 2, SIZE / 2 + 4)
REACH = (R + 38) * SCALE   # from the body's centre to the tip of the nib
PEN_ANGLE = math.radians(128)  # holding the pen: the body up and to the right of the nib, like a hand


def ink_color():
    return QColor(theme.TEXT)


def paper_color():
    return QColor(theme.SURFACE)


def accent_color():
    return QColor(theme.ACCENT)


def _scribble_ball(seed, turns=2.45, steps=96):
    """One hand-drawn variant of the body: a continuous loop-de-loop of ink."""
    rnd = random.Random(seed)
    w1, w2 = anim.Wobble(rnd.random()), anim.Wobble(rnd.random())
    start = rnd.uniform(0, math.tau)
    pts = []
    for i in range(steps + 1):
        u = i / steps
        a = start + u * turns * math.tau
        r = R * (1 + 0.055 * math.sin(3 * a + seed) + 0.035 * w1(a)) * (0.9 + 0.1 * math.sin(u * math.pi))
        pts.append((math.cos(a) * r + 1.6 * w2(u * 6), math.sin(a) * r * 0.94 + 1.6 * w2(u * 5 + 2)))
    return pts


def _wobbly_loop(cx, cy, rx, ry, seed, turns=1.14, steps=90):
    """A pen circle drawn around something: not quite closed, not quite round."""
    rnd = random.Random(seed)
    w = anim.Wobble(seed)
    start = math.radians(rnd.uniform(195, 235))
    pts = []
    for i in range(steps + 1):
        u = i / steps
        a = start + u * turns * math.tau
        grow = 1 + 0.07 * u + 0.025 * w(u * 9)
        pts.append((cx + math.cos(a) * rx * grow, cy + math.sin(a) * ry * grow))
    return pts


def draw_tapered(painter, pts, color, width, start_u=0.0, end_u=1.0):
    """Ink that's thin where the pen lands and lifts, fuller in the middle."""
    n = len(pts)
    if n < 2:
        return
    pen = QPen(color)
    pen.setCapStyle(Qt.RoundCap if color.alpha() == 255 else Qt.FlatCap)   # see-through ink: no beads where caps overlap
    for i in range(1, n):
        u = start_u + (end_u - start_u) * i / (n - 1)
        pen.setWidthF(max(0.6, width * (0.35 + 0.65 * math.sin(math.pi * min(1.0, max(0.0, u))) ** 0.6)))
        painter.setPen(pen)
        painter.drawLine(QPointF(*pts[i - 1]), QPointF(*pts[i]))


class Mascot(QWidget):
    """The scribble. Positions are its body's centre in the parent's coordinates."""
    arrived = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(SIZE, SIZE)
        self.boil = [_scribble_ball(seed) for seed in (3, 11, 29)]
        self.pos_f = (0.0, 0.0)
        self.vel = (0.0, 0.0)
        self.t = anim.now()
        self.mood = "idle"            # idle, thinking, talking, happy, puzzled, pointing
        self.talk = 0.0               # how open the mouth is (0..1)
        self.hover = False
        self.look_at = None           # a point to look (and point the nib) at, else the mouse
        self.flight = None
        self.anticipate = None        # (start time, flight) before a jump
        self.landed_at = -10.0
        self.squash_at = -10.0        # a little press (a tap, a click on it)
        self.spin_from = None         # a happy twirl
        self.tail_angle = math.radians(40)
        self.next_blink = self.t + 2.5
        self.blink_at = -10.0
        self.next_trick = self.t + 9.0
        self.trick = None
        self.on_land = None
        self.pen = False              # holding the pen: the nib stays put relative to the body
        self.tracing = None           # following a path with the nib (drawing, writing)

    # ---- where it is
    def place(self, x, y):
        self.pos_f = (float(x), float(y))
        self._sync_geometry()

    def nib_pos(self):
        """The tip of the pen, in the parent's coordinates (it draws the trail, and the ink)."""
        tip = self._tail_geometry(self.tail_angle, self._sway())[-1]
        return (self.pos_f[0] + tip[0] * SCALE, self.pos_f[1] + self._bob() + tip[1] * SCALE)

    def _nib_offset(self):
        tip = self._tail_geometry(PEN_ANGLE, 0.0)[-1]
        return tip[0] * SCALE, tip[1] * SCALE

    def _sync_geometry(self):
        x = self.pos_f[0] - CENTER[0]
        y = self.pos_f[1] - CENTER[1]
        self._frac = (x - math.floor(x), y - math.floor(y))
        self.move(int(math.floor(x)), int(math.floor(y)))

    # ---- moving
    def fly_to(self, x, y, on_done=None, quick=False):
        """An arc there: squash, leap, stretch along the way, wobble on landing. Quick: a low, fast hop
        with no wind-up (lifting the pen between strokes)."""
        x0, y0 = self.pos_f
        dist = math.hypot(x - x0, y - y0)
        if dist < 3:
            self.place(x, y)
            if on_done:
                QTimer.singleShot(0, on_done)
            return
        if quick:
            lift = min(40.0, 6 + dist * 0.18)
            dur = anim.clamp(0.1 + dist / 2600, 0.1, 0.4)
        else:
            lift = min(140.0, 30 + dist * 0.28)
            dur = anim.clamp(0.42 + dist / 1500, 0.45, 1.05)
        side = -1 if x >= x0 else 1                       # bow upwards, the way a thrown thing goes
        nx, ny = (y - y0) / dist * side, -(x - x0) / dist * side
        if ny > 0:
            nx, ny = -nx, -ny
        p1 = (x0 + (x - x0) * 0.25 + nx * lift, y0 + (y - y0) * 0.25 + ny * lift)
        p2 = (x0 + (x - x0) * 0.75 + nx * lift * 0.8, y0 + (y - y0) * 0.75 + ny * lift * 0.8)
        flight = {"p": ((x0, y0), p1, p2, (x, y)), "dur": dur, "start": None}
        self.on_land = on_done
        self.flight = None
        if quick:
            flight["start"] = self.t
            self.flight, self.anticipate = flight, None
        else:
            self.anticipate = (self.t, flight)

    def stop(self):
        self.flight = self.anticipate = self.tracing = None
        self.on_land = None

    def is_busy(self):
        return self.flight is not None or self.anticipate is not None or self.tracing is not None

    def hold_pen(self, holding):
        self.pen = holding
        if holding:
            self.tail_angle = PEN_ANGLE

    def fly_nib_to(self, x, y, on_done=None, quick=False):
        """Fly so the tip of the pen lands on (x, y)."""
        ox, oy = self._nib_offset()
        self.fly_to(x - ox, y - oy, on_done, quick)

    def trace(self, points, speed, on_done=None):
        """Move the tip of the pen along `points` (parent coordinates) at `speed` pixels a second."""
        pts = [(float(x), float(y)) for x, y in points]
        cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        ox, oy = self._nib_offset()
        self.pos_f = (pts[0][0] - ox, pts[0][1] - oy)
        self.tracing = {"pts": pts, "cum": cum, "start": self.t, "speed": max(1.0, speed), "done": on_done, "u": 0.0}

    def trace_progress(self):
        return self.tracing["u"] if self.tracing else 1.0

    def _step_trace(self, t, dt):
        tr = self.tracing
        pts, cum = tr["pts"], tr["cum"]
        d = (t - tr["start"]) * tr["speed"]
        total = cum[-1]
        if d >= total or total <= 0:
            x, y = pts[-1]
            finished = True
        else:
            i = max(1, next(k for k in range(1, len(cum)) if cum[k] >= d))
            seg = cum[i] - cum[i - 1] or 1.0
            u = (d - cum[i - 1]) / seg
            x = pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * u
            y = pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * u
            finished = False
        tr["u"] = 1.0 if finished else d / total
        ox, oy = self._nib_offset()
        old = self.pos_f
        self.pos_f = (x - ox, y - oy)
        if dt > 0:
            self.vel = ((self.pos_f[0] - old[0]) / dt, (self.pos_f[1] - old[1]) / dt)
        if finished:
            self.tracing = None
            self.vel = (0.0, 0.0)
            if tr["done"]:
                QTimer.singleShot(0, tr["done"])

    def squash(self):
        self.squash_at = self.t

    def twirl(self):
        self.spin_from = self.t

    # ---- the clock
    def step(self, t):
        dt = max(0.0, t - self.t)
        self.t = t
        if self.anticipate is not None:
            started, flight = self.anticipate
            if t - started >= 0.13:
                self.anticipate = None
                flight["start"] = t
                self.flight = flight
        if self.tracing is not None:
            self._step_trace(t, dt)
        elif self.flight is not None:
            f = self.flight
            u = (t - f["start"]) / f["dur"]
            old = self.pos_f
            p = anim.cubic(*f["p"], anim.ease_in_out_cubic(u))
            self.pos_f = p
            if dt > 0:
                self.vel = ((p[0] - old[0]) / dt, (p[1] - old[1]) / dt)
            if u >= 1:
                self.flight = None
                self.vel = (0.0, 0.0)
                self.landed_at = t
                done, self.on_land = self.on_land, None
                self.arrived.emit()
                if done:
                    QTimer.singleShot(0, done)
        else:
            self.vel = (self.vel[0] * 0.8, self.vel[1] * 0.8)
        self._steer_tail(dt)
        if t >= self.next_blink:
            self.blink_at = t
            self.next_blink = t + random.uniform(2.2, 5.5) * (0.5 if random.random() < 0.2 else 1)   # sometimes twice
        if self.mood == "idle" and not self.is_busy() and t >= self.next_trick:
            self.trick = (random.choice(["hop", "twirl", "look"]), t)
            if self.trick[0] == "twirl":
                self.twirl()
            self.next_trick = t + random.uniform(9, 16)
        self._sync_geometry()
        self.update()

    def _steer_tail(self, dt):
        if self.pen:
            self.tail_angle = PEN_ANGLE
            return
        speed = math.hypot(*self.vel)
        if speed > 60:
            want = math.atan2(self.vel[1], self.vel[0]) + math.pi          # streams out behind
        elif self.look_at is not None:
            want = math.atan2(self.look_at[1] - self.pos_f[1], self.look_at[0] - self.pos_f[0])
        else:
            want = math.radians(40) + 0.18 * math.sin(self.t * 1.7)        # idle sway
        diff = (want - self.tail_angle + math.pi) % math.tau - math.pi
        self.tail_angle += diff * min(1.0, dt * 9)

    def _bob(self):
        if self.is_busy():
            return 0.0
        bob = 2.4 * math.sin(self.t * 2.2)
        if self.trick and self.trick[0] == "hop":
            u = (self.t - self.trick[1]) / 0.55
            if u < 1:
                bob -= 16 * math.sin(math.pi * u)
        return bob

    def _squash_scale(self):
        """(sx, sy) about its bottom: anticipation, landing wobble, taps, breathing."""
        t = self.t
        sy = 1 + 0.025 * math.sin(t * 2.2 + 1.2)
        if self.anticipate is not None:
            u = (t - self.anticipate[0]) / 0.13
            sy -= 0.16 * anim.ease_out_cubic(u)
        sy -= anim.spring(t - self.landed_at, 0.2)
        sy -= anim.spring(t - self.squash_at, 0.14, freq=24, damping=9)
        return 1 / max(0.6, sy) ** 0.5, sy

    # ---- drawing
    def paintEvent(self, event):
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fx, fy = getattr(self, "_frac", (0, 0))
        cx, cy = CENTER[0] + fx, CENTER[1] + fy
        ink, paper, accent = ink_color(), paper_color(), accent_color()
        bob = self._bob()

        # the shadow stays on the ground while it bobs and hops
        lift = max(0.0, -bob)
        shadow = QColor(0, 0, 0, int(max(0.0, 46 - lift * 1.6)))
        p.setPen(Qt.NoPen)
        p.setBrush(shadow)
        sw = R * (1.45 - min(0.5, lift / 40))
        p.drawEllipse(QPointF(cx, cy + (R + 9) * SCALE), sw * SCALE, 4.2 * SCALE)

        p.translate(cx, cy + bob)
        p.scale(SCALE, SCALE)
        speed = 0.0 if self.pen else math.hypot(*self.vel)      # holding the pen: steady, so the nib is exact
        tilt = 0.0 if self.pen else 4 * math.sin(t * 1.3) if speed < 60 else max(-14, min(14, self.vel[0] / 90))
        p.rotate(tilt)

        # squash and stretch: along the flight when moving, else about its bottom
        if self.pen:
            pass
        elif speed > 60:
            ang = math.degrees(math.atan2(self.vel[1], self.vel[0]))
            stretch = 1 + min(0.32, speed / 2600)
            p.rotate(ang)
            p.scale(stretch, 1 / stretch)
            p.rotate(-ang)
        else:
            sx, sy = self._squash_scale()
            p.translate(0, R)
            p.scale(sx, sy)
            p.translate(0, -R)

        self._draw_tail(p, ink, tilt)

        # paper behind the ink, so it reads over anything
        p.setPen(Qt.NoPen)
        paper.setAlpha(240)
        p.setBrush(paper)
        p.drawEllipse(QPointF(0, 0), R * 1.02, R * 0.97)

        # the scribble: spins while thinking, twirls when happy
        p.save()
        spin = 0.0
        if self.mood == "thinking":
            spin = t * 260
        if self.spin_from is not None:
            u = (t - self.spin_from) / 0.7
            if u < 1:
                spin += 360 * anim.ease_in_out_cubic(u)
            else:
                self.spin_from = None
        p.rotate(spin)
        frame = self.boil[anim.boil_frame(t)]
        draw_tapered(p, frame, ink, 2.7 if not self.hover else 3.1)
        p.restore()

        self._draw_face(p, ink, accent)
        if self.mood == "thinking":
            self._draw_thought(p, ink)
        p.end()

    def _sway(self):
        return 0.0 if self.is_busy() or self.pen else 7 * math.sin(self.t * 3.1)

    @staticmethod
    def _tail_geometry(a, sway):
        """The tail's curve (unscaled, about the body's centre), the nib's direction, and its tip."""
        bx, by = math.cos(a) * R * 0.9, math.sin(a) * R * 0.9
        ex, ey = math.cos(a) * (R + 30), math.sin(a) * (R + 30)
        nx, ny = -math.sin(a), math.cos(a)
        c1 = (bx + math.cos(a) * 10 + nx * (9 + sway), by + math.sin(a) * 10 + ny * (9 + sway))
        c2 = (ex - math.cos(a) * 10 - nx * (6 - sway), ey - math.sin(a) * 10 - ny * (6 - sway))
        pts = [anim.cubic((bx, by), c1, c2, (ex, ey), i / 18) for i in range(19)]
        dx, dy = ex - pts[-3][0], ey - pts[-3][1]
        d = math.hypot(dx, dy) or 1
        dx, dy = dx / d, dy / d
        return pts, (ex, ey), (dx, dy), (ex + dx * 10, ey + dy * 10)

    def _draw_tail(self, p, ink, tilt):
        """From the body, a curl of ink out to a pen nib."""
        pts, (ex, ey), (dx, dy), tip = self._tail_geometry(self.tail_angle - math.radians(tilt), self._sway())
        draw_tapered(p, pts, ink, 2.3, 0.15, 0.85)
        poly = QPolygonF([QPointF(ex - dy * 3.6, ey + dx * 3.6), QPointF(*tip), QPointF(ex + dy * 3.6, ey - dx * 3.6)])
        p.setPen(QPen(ink, 1.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(ink)
        p.drawPolygon(poly)
        p.setPen(QPen(paper_color(), 0.9))
        p.drawLine(QPointF(ex + dx * 2, ey + dy * 2), QPointF(ex + dx * 6.5, ey + dy * 6.5))

    def _eye_offset(self):
        target = self.look_at
        if target is None:
            parent = self.parentWidget()
            cur = parent.mapFromGlobal(QCursor.pos()) if parent else None
            target = (cur.x(), cur.y()) if cur is not None else None
        if self.mood == "thinking":
            return 1.6, -1.8
        if self.mood == "reading":                      # eyes running along the lines
            return 2.0 * math.sin(self.t * 4.2), 1.2 + 0.6 * math.sin(self.t * 0.9)
        if self.mood == "drawing":                      # watching the nib
            return math.cos(PEN_ANGLE) * 2.0, math.sin(PEN_ANGLE) * 2.0
        if target is None:
            return 0.0, 0.0
        dx, dy = target[0] - self.pos_f[0], target[1] - self.pos_f[1]
        d = math.hypot(dx, dy) or 1
        k = min(1.0, d / 120) * 2.0
        return dx / d * k, dy / d * k

    def _draw_face(self, p, ink, accent):
        t = self.t
        ox, oy = self._eye_offset()
        blink = 1.0
        bu = (t - self.blink_at) / 0.16
        if 0 <= bu < 1:
            blink = max(0.08, abs(1 - 2 * bu))
        happy = self.mood == "happy"
        big = 1.18 if self.hover or self.mood == "puzzled" else 1.0
        for side in (-1, 1):
            ex, ey = side * 8.2 + ox * 0.6, -4 + oy * 0.5
            p.setPen(Qt.NoPen)
            if happy and blink > 0.5:
                # ^ ^ eyes
                p.setPen(QPen(ink, 2.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                path = QPainterPath(QPointF(ex - 3.6, ey + 1.2))
                path.quadTo(QPointF(ex, ey - 4.4), QPointF(ex + 3.6, ey + 1.2))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
                continue
            p.setBrush(ink)
            p.drawEllipse(QPointF(ex + ox * 0.5, ey + oy * 0.4), 2.9 * big, 3.9 * big * blink)
            if blink > 0.5:
                p.setBrush(paper_color())
                p.drawEllipse(QPointF(ex + ox * 0.5 + 1.0, ey + oy * 0.4 - 1.3), 0.95, 0.95)
        if self.mood == "puzzled":                      # one eyebrow up
            p.setPen(QPen(ink, 1.8, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(4.5, -12.5), QPointF(11.5, -14.5))
        # blush when happy or hovered
        if happy or self.hover:
            blush = QColor("#ff7aa2")
            blush.setAlpha(150)
            p.setPen(QPen(blush, 1.5, Qt.SolidLine, Qt.RoundCap))
            for side in (-1, 1):
                bx = side * 14.5
                p.drawLine(QPointF(bx - 2.4, 3.6), QPointF(bx - 0.6, 1.2))
                p.drawLine(QPointF(bx + 0.4, 3.9), QPointF(bx + 2.2, 1.5))
        # the mouth
        p.setPen(QPen(ink, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        mx, my = ox * 0.4, 7.2 + oy * 0.3
        if self.mood == "talking" or self.talk > 0.05:
            open_ = 1.2 + 4.2 * self.talk
            p.setBrush(ink)
            p.drawEllipse(QPointF(mx, my + 0.5), 3.2, open_ / 2 + 0.4)
        elif self.mood == "thinking":
            path = QPainterPath(QPointF(mx - 4.5, my))
            path.cubicTo(QPointF(mx - 2, my - 2.4), QPointF(mx + 0.5, my + 2.4), QPointF(mx + 4.5, my - 0.4))
            p.drawPath(path)
        elif self.mood == "puzzled":
            p.drawLine(QPointF(mx - 3.5, my + 0.6), QPointF(mx + 3.5, my - 0.6))
        elif self.mood == "drawing":                    # concentrating: tongue out at the corner
            path = QPainterPath(QPointF(mx - 4.0, my))
            path.quadTo(QPointF(mx, my + 1.6), QPointF(mx + 4.0, my - 0.4))
            p.drawPath(path)
            tongue = QColor("#ff7aa2")
            p.setPen(QPen(ink, 1.0))
            p.setBrush(tongue)
            wag = 0.6 * math.sin(self.t * 9)
            p.drawEllipse(QPointF(mx + 2.6 + wag, my + 2.2), 1.9, 2.4)
        else:
            wide = 6.5 if happy else 5.0
            path = QPainterPath(QPointF(mx - wide, my - 1.2))
            path.quadTo(QPointF(mx, my + (5.6 if happy else 3.6)), QPointF(mx + wide, my - 1.2))
            if happy:
                p.setBrush(ink)
            p.drawPath(path)

    def _draw_thought(self, p, ink):
        """Three ink dots chasing each other above its head."""
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        for i in range(3):
            a = self.t * 5 - i * 0.65
            r = 2.6 - i * 0.55
            p.drawEllipse(QPointF(math.cos(a) * 15, -R - 14 + math.sin(a) * 5), r, r)


class InkLayer(QWidget):
    """Ink on the window: the trail the nib leaves, circles round things,
    taps and sparkles. Only the parts that change are repainted."""

    TRAIL_LIFE = 0.7

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.t = anim.now()
        self.trail = []        # (x, y, time)
        self.circles = []      # {"pts", "start", "dur", "fade", "seed"}
        self.taps = []         # (x, y, start)
        self.sparkles = []     # (x, y, vx, vy, start, size)
        self.captions = []     # {"text", "rect", "start", "fade"}
        self.wet = []          # strokes being drawn: {"pts", "color", "width", "fade", "flat"}
        self.wet_text = []     # text being written: {"text", "rect", "font", "color", "shown", "fade"}
        self._dirty = QRect()
        self.caption_font = QFont("Caveat")
        self.caption_font.setPixelSize(22)

    def busy(self):
        return bool(self.trail or self.circles or self.taps or self.sparkles or self.captions or self.wet
                    or self.wet_text)

    # ---- ink going onto the paper, before it's really there
    def begin_wet(self, color, width, alpha=1.0, flat=False):
        c = QColor(color)
        c.setAlphaF(alpha)
        self.wet.append({"pts": [], "color": c, "width": max(0.8, width), "fade": None, "flat": flat})

    def wet_point(self, x, y):
        if self.wet and self.wet[-1]["fade"] is None:
            pts = self.wet[-1]["pts"]
            if not pts or abs(pts[-1][0] - x) + abs(pts[-1][1] - y) > 0.6:
                pts.append((x, y))

    def begin_wet_text(self, text, rect: QRectF, font, color):
        self.wet_text.append({"text": text, "rect": rect, "font": font, "color": QColor(color), "shown": 0,
                              "fade": None})

    def set_wet_text_shown(self, n):
        if self.wet_text:
            self.wet_text[-1]["shown"] = n

    def dry(self):
        """The ink is on the paper now: the wet copy fades away."""
        for item in self.wet + self.wet_text:
            if item["fade"] is None:
                item["fade"] = self.t

    def caption(self, text, near: QRectF, avoid=None):
        """Write `text` beside `near` by hand (written left to right), inside the window and clear of
        `avoid` (where the scribble is)."""
        if not text:
            return
        m = QFontMetricsF(self.caption_font)
        w, h = m.horizontalAdvance(text) + 8, m.height() + 4
        bounds = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        for x, y in ((near.center().x() - w / 2, near.bottom() + 12), (near.right() + 16, near.center().y() - h / 2),
                     (near.left() - 16 - w, near.center().y() - h / 2), (near.center().x() - w / 2, near.top() - 12 - h)):
            rect = QRectF(x, y, w, h)
            clear = avoid is None or not rect.adjusted(-42, -42, 42, 42).contains(QPointF(*avoid))
            if bounds.contains(rect) and clear:
                break
        else:
            rect = QRectF(min(max(bounds.left(), x), bounds.right() - w), min(max(bounds.top(), y), bounds.bottom() - h), w, h)
        self.captions.append({"text": text, "rect": rect, "start": self.t, "fade": None})

    def add_trail_point(self, x, y):
        if self.trail and math.hypot(x - self.trail[-1][0], y - self.trail[-1][1]) < 2:
            return
        self.trail.append((x, y, self.t))

    def circle(self, rect: QRectF, seed=None):
        """Circle `rect` (parent coordinates) like with a pen. Returns how long it takes to draw."""
        seed = random.randint(0, 9999) if seed is None else seed
        pad = 8
        rx, ry = rect.width() / 2 + pad + 4, rect.height() / 2 + pad
        rx, ry = max(rx, 18), max(ry, 14)
        pts = _wobbly_loop(rect.center().x(), rect.center().y(), rx, ry, seed)
        self.circles.append({"pts": pts, "start": self.t, "dur": 0.5, "fade": None})
        return 0.5

    def clear_circles(self):
        for c in self.circles + self.captions:
            if c["fade"] is None:
                c["fade"] = self.t

    def tap(self, x, y):
        self.taps.append((x, y, self.t))

    def sparkle(self, x, y, n=6):
        for _ in range(n):
            a = random.uniform(-math.pi * 0.95, -math.pi * 0.05)
            v = random.uniform(70, 150)
            self.sparkles.append((x, y, math.cos(a) * v, math.sin(a) * v, self.t + random.uniform(0, 0.15),
                                  random.uniform(3, 5.5)))

    def step(self, t):
        self.t = t
        self.trail = [pt for pt in self.trail if t - pt[2] < self.TRAIL_LIFE]
        self.circles = [c for c in self.circles if c["fade"] is None or t - c["fade"] < 0.4]
        self.taps = [tp for tp in self.taps if t - tp[2] < 0.55]
        self.sparkles = [s for s in self.sparkles if t - s[4] < 0.9]
        self.captions = [c for c in self.captions if c["fade"] is None or t - c["fade"] < 0.4]
        self.wet = [w for w in self.wet if w["fade"] is None or t - w["fade"] < 0.35]
        self.wet_text = [w for w in self.wet_text if w["fade"] is None or t - w["fade"] < 0.35]
        box = self._bounds()
        dirty = box.united(self._dirty)
        self._dirty = box
        if not dirty.isEmpty():
            self.update(dirty)

    def _bounds(self):
        xs, ys = [], []
        for x, y, _t in self.trail:
            xs.append(x)
            ys.append(y)
        for c in self.circles:
            for x, y in (c["pts"][0], c["pts"][len(c["pts"]) // 4], c["pts"][len(c["pts"]) // 2],
                         c["pts"][3 * len(c["pts"]) // 4], c["pts"][-1]):
                xs.append(x)
                ys.append(y)
        for x, y, _s in self.taps:
            xs += [x - 34, x + 34]
            ys += [y - 34, y + 34]
        for x, y, vx, vy, _s, _z in self.sparkles:
            xs += [x, x + vx]
            ys += [y, y + vy + 30]
        for c in self.captions + self.wet_text:
            xs += [c["rect"].left(), c["rect"].right()]
            ys += [c["rect"].top(), c["rect"].bottom()]
        for w in self.wet:
            if w["pts"]:
                pad = w["width"]
                xs += [min(x for x, _ in w["pts"]) - pad, max(x for x, _ in w["pts"]) + pad]
                ys += [min(y for _, y in w["pts"]) - pad, max(y for _, y in w["pts"]) + pad]
        if not xs:
            return QRect()
        return QRect(int(min(xs)) - 14, int(min(ys)) - 14, int(max(xs) - min(xs)) + 28, int(max(ys) - min(ys)) + 28)

    def paintEvent(self, event):
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ink, accent = ink_color(), accent_color()
        # the trail dries away from the old end
        pen = QPen(ink)
        pen.setCapStyle(Qt.FlatCap)
        for i in range(1, len(self.trail)):
            x0, y0, _t0 = self.trail[i - 1]
            x1, y1, t1 = self.trail[i]
            life = 1 - (t - t1) / self.TRAIL_LIFE
            if life <= 0:
                continue
            c = QColor(ink)
            c.setAlphaF(max(0.0, min(1.0, life ** 1.4 * 0.75)))
            pen.setColor(c)
            pen.setWidthF(0.6 + 2.6 * life)
            p.setPen(pen)
            p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        # circles, drawn on as if by hand, then faded away
        for c in self.circles:
            u = anim.ease_out_cubic((t - c["start"]) / c["dur"])
            n = max(2, int(len(c["pts"]) * u))
            col = QColor(accent)
            if c["fade"] is not None:
                col.setAlphaF(max(0.0, 1 - (t - c["fade"]) / 0.4))
            draw_tapered(p, c["pts"][:n], col, 3.4, 0.0, u)
        # taps: a ring and little ink dashes flying out
        for x, y, s in self.taps:
            u = anim.clamp((t - s) / 0.55)
            e = anim.ease_out_cubic(u)
            col = QColor(accent)
            col.setAlphaF(max(0.0, 1 - u))
            p.setPen(QPen(col, 2.4 * (1 - u) + 0.6, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(x, y), 6 + 16 * e, 6 + 16 * e)
            for k in range(7):
                a = k * math.tau / 7 + 0.3
                r0, r1 = 12 + 10 * e, 16 + 16 * e
                p.drawLine(QPointF(x + math.cos(a) * r0, y + math.sin(a) * r0),
                           QPointF(x + math.cos(a) * r1, y + math.sin(a) * r1))
        # wet ink: one smooth path per stroke (so see-through ink, like a highlighter, stays even)
        for w in self.wet:
            if len(w["pts"]) < 2:
                continue
            col = QColor(w["color"])
            if w["fade"] is not None:
                col.setAlphaF(col.alphaF() * max(0.0, 1 - (t - w["fade"]) / 0.35))
            path = QPainterPath(QPointF(*w["pts"][0]))
            for x, y in w["pts"][1:]:
                path.lineTo(x, y)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col, w["width"], Qt.SolidLine, Qt.FlatCap if w["flat"] else Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
        for w in self.wet_text:
            col = QColor(w["color"])
            if w["fade"] is not None:
                col.setAlphaF(max(0.0, 1 - (t - w["fade"]) / 0.35))
            p.setFont(w["font"])
            p.setPen(col)
            p.drawText(w["rect"], Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, w["text"][:int(w["shown"])])
        # captions, written on left to right
        p.setFont(self.caption_font)
        for c in self.captions:
            u = anim.ease_out_cubic((t - c["start"] - 0.25) / 0.5)
            if u <= 0:
                continue
            col = QColor(accent)
            if c["fade"] is not None:
                col.setAlphaF(max(0.0, 1 - (t - c["fade"]) / 0.4))
            r = c["rect"]
            p.save()
            p.setClipRect(QRectF(r.left() - 2, r.top() - 4, r.width() * u + 4, r.height() + 8))
            backing = paper_color()
            backing.setAlphaF(0.88 * col.alphaF())
            p.setPen(Qt.NoPen)
            p.setBrush(backing)
            p.drawRoundedRect(r.adjusted(-2, 0, 2, 0), 7, 7)
            p.setPen(col)
            p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, " " + c["text"])
            p.restore()
        # sparkles: little ink stars that float up and fade
        for x, y, vx, vy, s, size in self.sparkles:
            u = (t - s) / 0.9
            if not 0 <= u <= 1:
                continue
            px, py = x + vx * u, y + vy * u + 40 * u * u
            col = QColor(accent if int(size * 10) % 2 else ink)
            col.setAlphaF(max(0.0, 1 - u) ** 1.2)
            p.setPen(QPen(col, 1.6, Qt.SolidLine, Qt.RoundCap))
            r = size * (1 - 0.4 * u)
            for k in range(4):
                a = k * math.pi / 4 + u * 3
                p.drawLine(QPointF(px - math.cos(a) * r, py - math.sin(a) * r),
                           QPointF(px + math.cos(a) * r, py + math.sin(a) * r))
        p.end()


class HitArea(QWidget):
    """An invisible circle over the scribble's body that takes its clicks, drags and hover (the
    drawn widgets let every click through). Positions it reports are in the window's coordinates."""
    pressed = Signal(QPointF)
    moved = Signal(QPointF)
    released = Signal(QPointF)
    hovered = Signal(bool)

    def __init__(self, parent):
        super().__init__(parent)
        d = int(2 * (R + 6) * SCALE)
        self.resize(d, d)
        self.setMask(QRegion(0, 0, d, d, QRegion.Ellipse))
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("AUPedia: click to ask, drag to move")

    def follow(self, x, y):
        self.move(int(x - self.width() / 2), int(y - self.height() / 2))

    def _window_pos(self, event):
        return QPointF(self.mapTo(self.parentWidget(), event.position().toPoint()))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.pressed.emit(self._window_pos(event))

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.moved.emit(self._window_pos(event))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.released.emit(self._window_pos(event))

    def enterEvent(self, event):
        self.hovered.emit(True)

    def leaveEvent(self, event):
        self.hovered.emit(False)


class Animator(QObject):
    """One clock: 60 frames a second while anything moves, 30 while it just breathes."""

    def __init__(self, mascot, ink, hit=None, parent=None):
        super().__init__(parent)
        self.mascot, self.ink, self.hit = mascot, ink, hit
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    def tick(self):
        if not self.mascot.isVisible():
            return
        t = anim.now()
        tracing = self.mascot.tracing is not None
        self.mascot.step(t)
        if self.hit is not None:
            self.hit.follow(self.mascot.pos_f[0], self.mascot.pos_f[1] + self.mascot._bob())
        if tracing:                                          # the nib is on the paper: wet ink, no trail
            self.ink.wet_point(*self.mascot.nib_pos())       # (including where the stroke ends)
        elif self.mascot.flight is not None:
            self.ink.add_trail_point(*self.mascot.nib_pos())
        self.ink.step(t)
        fast = (self.mascot.is_busy() or self.ink.busy() or self.mascot.mood != "idle" or self.mascot.hover
                or self.mascot.pen)
        want = 16 if fast else 33
        if self.timer.interval() != want:
            self.timer.setInterval(want)

    def stop(self):
        self.timer.stop()
