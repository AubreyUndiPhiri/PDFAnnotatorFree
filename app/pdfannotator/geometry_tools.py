"""Geometry tools you bring onto the document: a ruler, set squares (45° and
30°/60°), a protractor and a compass.

They lie on the page like real ones: each is pinned to a spot on a page,
so it scrolls, zooms and moves with the paper (they are children of the
scroll area's viewport, placed from that spot). They are drawn to the page's own scale: a centimetre on the ruler is a centimetre on
the page, at every zoom. With View > Zoom to Real Size, a centimetre on the
screen is a real centimetre too.

- Drag a tool to move it; drag its round knob (or turn the mouse wheel over
  it, Shift for 15° steps) to rotate it; it clicks into 0°, 45°, 90°...
  Double-click to straighten it.
- Draw with the Pen or Marker starting along a straight edge (or the
  protractor's curve) and the line follows the edge exactly; the length
  is shown as you draw.
- The protractor's two arms measure an angle: drag their round ends.
- The compass: drag the needle to place it, the pencil to set the radius,
  and the knob on top to draw an arc (double-click the knob for a circle).

Each tool is its own widget, masked to its shape, so clicks beside it go to
the page. GeometryManager (one per PDF tab) holds them, paints their soft
shadows and the compass's arc in progress on a see-through canvas beneath
them, and answers the pen's questions (which edge is near, where on it).
"""
import math

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBitmap, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import QWidget

from . import theme

PT_PER_UNIT = {"cm": 72 / 2.54, "in": 72.0}
SUBDIVISIONS = {"cm": 10, "in": 16}       # mm; sixteenths of an inch
SNAP_DISTANCE = 26                        # px: a pen stroke starting this close to an edge follows it


def _palette():
    dark = theme.mode == theme.DARK
    return {
        "top": QColor(46, 46, 56, 214) if dark else QColor(255, 255, 255, 206),
        "bottom": QColor(26, 26, 32, 200) if dark else QColor(238, 240, 250, 190),
        "edge": QColor(255, 255, 255, 60) if dark else QColor(96, 100, 140, 120),
        "rim": QColor(255, 255, 255, 34) if dark else QColor(255, 255, 255, 235),
        "ink": QColor(236, 236, 244) if dark else QColor(32, 36, 52),
        "muted": QColor(160, 160, 178) if dark else QColor(110, 116, 140),
        "accent": QColor(theme.ACCENT),
        "pill": QColor(20, 20, 26, 235) if dark else QColor(32, 34, 52, 232),
        "pill_text": QColor(255, 255, 255),
        "knob": QColor(255, 255, 255, 250) if not dark else QColor(64, 64, 76, 250),
    }


def _font(size, bold=False):
    font = QFont(theme.FONT_FAMILY)
    font.setPointSizeF(size)
    font.setBold(bold)
    font.setStyleHint(QFont.SansSerif)
    return font


def draw_pill(painter, center, text, size=8.5):
    """A dark rounded readout (angle, length...) centred on `center`."""
    colors = _palette()
    font = _font(size, True)
    metrics = QFontMetricsF(font)
    w, h = metrics.horizontalAdvance(text) + 16, metrics.height() + 6
    rect = QRectF(center.x() - w / 2, center.y() - h / 2, w, h)
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(colors["pill"])
    painter.drawRoundedRect(rect, h / 2, h / 2)
    painter.setPen(colors["pill_text"])
    painter.setFont(font)
    painter.drawText(rect, Qt.AlignCenter, text)
    painter.restore()


def _knob(painter, center, radius, glyph="rotate"):
    """A round grip: a white (dark: charcoal) button with a fine rim."""
    colors = _palette()
    painter.save()
    painter.setPen(QPen(colors["edge"], 1))
    grad = QLinearGradient(center.x(), center.y() - radius, center.x(), center.y() + radius)
    grad.setColorAt(0, colors["knob"])
    grad.setColorAt(1, colors["knob"].darker(108))
    painter.setBrush(grad)
    painter.drawEllipse(center, radius, radius)
    pen = QPen(colors["accent"], 1.6, Qt.SolidLine, Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    r = radius * 0.48
    if glyph == "rotate":   # a circling arrow
        rect = QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r)
        painter.drawArc(rect, 40 * 16, 280 * 16)
        tip = QPointF(center.x() + r * math.cos(math.radians(40)), center.y() - r * math.sin(math.radians(40)))
        painter.drawLine(tip, tip + QPointF(-3.5, -1))
        painter.drawLine(tip, tip + QPointF(0.5, 3.5))
    elif glyph == "close":
        painter.setPen(QPen(colors["muted"], 1.5, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(center + QPointF(-r * 0.7, -r * 0.7), center + QPointF(r * 0.7, r * 0.7))
        painter.drawLine(center + QPointF(-r * 0.7, r * 0.7), center + QPointF(r * 0.7, -r * 0.7))
    elif glyph == "grip":   # the compass head: three ridges
        for dx in (-3.5, 0, 3.5):
            painter.drawLine(center + QPointF(dx, -r * 0.8), center + QPointF(dx, r * 0.8))
    painter.restore()


def _seg_distance(p, a, b):
    dx, dy = b.x() - a.x(), b.y() - a.y()
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / length2))
    return math.hypot(p.x() - a.x() - t * dx, p.y() - a.y() - t * dy), t


def _angle_of(v):
    """Angle of a viewport vector in degrees, counter-clockwise from +x (as on paper)."""
    return math.degrees(math.atan2(-v.y(), v.x()))


# ---------------------------------------------------------------------------
class GeometryTool(QWidget):
    """Base: a shape at `center` (viewport px) turned by `angle` (degrees,
    clockwise on screen), drawn in its own local coordinates."""

    title = "Tool"
    KNOB = 13

    def __init__(self, manager, center):
        super().__init__(manager.viewport)
        self.manager = manager
        self.center = QPointF(center)
        self.anchor = None         # (page index, x, y in PDF points): where it lies on the paper
        self.angle = 0.0
        self._drag = None
        self._readout_until_release = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setToolTip("")
        self.show()
        self.raise_()

    # ---- scale
    @property
    def unit(self):
        return self.manager.unit

    @property
    def ppu(self):
        """Pixels per unit (cm or inch) at the current zoom: true page scale."""
        return self.manager.px_per_unit()

    # ---- geometry
    def local_path(self) -> QPainterPath:
        raise NotImplementedError

    def pivot(self):
        return self.center

    def transform(self):
        t = QTransform()
        t.translate(self.center.x(), self.center.y())
        t.rotate(self.angle)
        return t

    def world_path(self):
        return self.transform().map(self.local_path())

    def to_local(self, widget_pos):
        inverse, _ok = self.transform().inverted()
        return inverse.map(QPointF(widget_pos) + QPointF(self.pos()))

    def to_viewport(self, local):
        return self.transform().map(QPointF(local))

    def relayout(self):
        path = self.world_path()
        rect = path.boundingRect().adjusted(-3, -3, 3, 3).toAlignedRect()
        self.setGeometry(rect)
        bitmap = QBitmap(max(1, rect.width()), max(1, rect.height()))
        bitmap.fill(Qt.color0)
        p = QPainter(bitmap)
        p.setPen(Qt.NoPen)
        p.setBrush(Qt.color1)
        p.drawPath(path.translated(-rect.left(), -rect.top()))
        p.end()
        self.setMask(bitmap)
        self.update()
        self.manager.remember(self)
        self.manager.canvas_update()

    def edges(self):
        """Straight edges and curves the pen can follow: ("line", a, b) or
        ("arc", centre, radius, from_deg, to_deg), all in viewport px."""
        return []

    # ---- painting
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setTransform(self.transform() * QTransform.fromTranslate(-self.x(), -self.y()))
        self.paint_local(p)
        p.end()

    def paint_body(self, p, path):
        """The acrylic body: frosted, a bright rim on top, a fine edge."""
        colors = _palette()
        rect = path.boundingRect()
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0, colors["top"])
        grad.setColorAt(1, colors["bottom"])
        p.setPen(QPen(colors["edge"], 1))
        p.setBrush(grad)
        p.drawPath(path)
        p.setPen(QPen(colors["rim"], 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(QTransform.fromTranslate(0, 1).map(path))

    def paint_local(self, p):
        raise NotImplementedError

    # ---- interaction
    def part_at(self, local):
        """"close", "rotate", "unit", a tool's own part, or "body"."""
        return "body"

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            if event.button() == Qt.RightButton:
                self.manager.context_menu(self, event.globalPosition().toPoint())
            return
        local = self.to_local(event.position())
        part = self.part_at(local)
        vp = QPointF(event.position()) + QPointF(self.pos())
        self.raise_()
        if part == "close":
            self.manager.remove(self)
            return
        if part == "unit":
            self.manager.toggle_unit()
            return
        if part == "rotate":
            self._drag = ("rotate", self.angle, _angle_of(vp - self.pivot()))
        elif self.press_part(part, local, vp):
            return
        else:
            self._drag = ("move", vp - self.center)
            self.setCursor(Qt.ClosedHandCursor)

    def press_part(self, part, local, vp):
        """A subclass's own parts; True when it took the press."""
        return False

    def mouseMoveEvent(self, event):
        vp = QPointF(event.position()) + QPointF(self.pos())
        if self._drag is None:
            part = self.part_at(self.to_local(event.position()))
            self.setCursor({"close": Qt.PointingHandCursor, "unit": Qt.PointingHandCursor,
                            "rotate": Qt.SizeAllCursor}.get(part, self.cursor_for(part)))
            return
        kind = self._drag[0]
        if kind == "move":
            self.center = vp - self._drag[1]
            self.relayout()
        elif kind == "rotate":
            _k, start, mouse0 = self._drag
            angle = start - (_angle_of(vp - self.pivot()) - mouse0)   # screen angles run clockwise
            self.set_angle(angle, fine=bool(event.modifiers() & Qt.ShiftModifier))
        else:
            self.drag_part(kind, vp, event)

    def cursor_for(self, part):
        return Qt.OpenHandCursor

    def drag_part(self, kind, vp, event):
        pass

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            kind = self._drag[0]
            self._drag = None
            self.release_part(kind)
            self.setCursor(Qt.OpenHandCursor)
            self.update()

    def release_part(self, kind):
        pass

    def mouseDoubleClickEvent(self, event):
        if self.part_at(self.to_local(event.position())) in ("body", "rotate"):
            self.set_angle(0.0)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            event.ignore()      # Ctrl+wheel still zooms the page
            return
        steps = (event.angleDelta().y() or event.angleDelta().x()) / 120
        step = 15 if event.modifiers() & Qt.ShiftModifier else 1
        self.set_angle(self.angle - steps * step, snap=False)
        event.accept()

    def set_angle(self, angle, snap=True, fine=False):
        """Turn the tool; it clicks into multiples of 45° (15° with Shift)."""
        angle = (angle + 180) % 360 - 180
        if snap:
            grid = 15 if fine else 45
            nearest = round(angle / grid) * grid
            if abs(angle - nearest) < (7.5 if fine else 1.8):
                angle = nearest
        self.angle = angle
        self.relayout()

    def display_angle(self):
        """The angle as read on paper (counter-clockwise, 0-360)."""
        return (-self.angle) % 360

    # ---- shared painting
    def tick_scale(self, p, start, direction, normal, length_units):
        """A scale of ticks along a line: `start` (local px) is 0, `direction`
        a unit vector along the edge, `normal` the unit vector into the body."""
        colors = _palette()
        sub = SUBDIVISIONS[self.unit]
        ppu = self.ppu
        step = ppu / sub
        show_every = 1
        while step * show_every < 3.2 and show_every < sub:
            show_every *= 2 if self.unit == "in" else 5
        count = int(round(length_units * sub))
        pen = QPen(colors["ink"], 1)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        font = _font(7.4)
        p.setFont(font)
        metrics = QFontMetricsF(font)
        label_every = 1
        while ppu * label_every < metrics.horizontalAdvance("00") + 8:
            label_every *= 2 if self.unit == "in" else 5
        for i in range(0, count + 1, show_every):
            if self.unit == "cm":
                size = 15 if i % 10 == 0 else 10 if i % 5 == 0 else 6
            else:
                size = 15 if i % 16 == 0 else 11 if i % 8 == 0 else 8 if i % 4 == 0 else 6 if i % 2 == 0 else 4.5
            at = start + direction * (i * step)
            p.drawLine(at, at + normal * size)
            if i % sub == 0 and (i // sub) % label_every == 0:
                text = str(i // sub)
                centre = at + normal * (size + 8)
                rect = QRectF(centre.x() - 14, centre.y() - 7, 28, 14)
                p.drawText(rect, Qt.AlignCenter, text)


# ---------------------------------------------------------------------------
class Ruler(GeometryTool):
    title = "Ruler"
    HEIGHT = 66
    LENGTH = {"cm": 30, "in": 12}
    INSET = {"cm": 0.5, "in": 0.25}

    def _size(self):
        length = self.LENGTH[self.unit] + 2 * self.INSET[self.unit]
        return length * self.ppu, self.HEIGHT

    def local_path(self):
        w, h = self._size()
        path = QPainterPath()
        path.addRoundedRect(QRectF(-w / 2, -h / 2, w, h), 7, 7)
        return path

    def _knob_at(self):
        w, h = self._size()
        return QPointF(w / 2 - 24, h / 2 - 19)

    def _close_at(self):
        w, h = self._size()
        return QPointF(-w / 2 + 20, h / 2 - 19)

    def _unit_at(self):
        w, h = self._size()
        return QRectF(-w / 2 + 38, h / 2 - 29, 30, 20)

    def part_at(self, local):
        if math.hypot(*(local - self._knob_at()).toTuple()) <= self.KNOB + 3:
            return "rotate"
        if math.hypot(*(local - self._close_at()).toTuple()) <= 10:
            return "close"
        if self._unit_at().contains(local):
            return "unit"
        return "body"

    def edges(self):
        w, h = self._size()
        return [("line", self.to_viewport(QPointF(-w / 2, -h / 2)), self.to_viewport(QPointF(w / 2, -h / 2))),
                ("line", self.to_viewport(QPointF(-w / 2, h / 2)), self.to_viewport(QPointF(w / 2, h / 2)))]

    def zero_point(self):
        """Where 0 is on the scale edge (viewport px), for readouts."""
        w, h = self._size()
        return self.to_viewport(QPointF(-w / 2 + self.INSET[self.unit] * self.ppu, -h / 2))

    def paint_local(self, p):
        w, h = self._size()
        self.paint_body(p, self.local_path())
        colors = _palette()
        x0 = -w / 2 + self.INSET[self.unit] * self.ppu
        self.tick_scale(p, QPointF(x0, -h / 2), QPointF(1, 0), QPointF(0, 1), self.LENGTH[self.unit])
        # the lower edge: a second, finer scale, read from the other side (as on a real ruler)
        p.setPen(QPen(colors["muted"], 1))
        sub = SUBDIVISIONS[self.unit]
        step = self.ppu / sub
        if step >= 3:
            for i in range(0, int(self.LENGTH[self.unit] * sub) + 1):
                size = 7 if i % sub == 0 else 4 if (i % (sub // 2) == 0) else 2.5
                x = x0 + i * step
                p.drawLine(QPointF(x, h / 2), QPointF(x, h / 2 - size))
        _knob(p, self._knob_at(), self.KNOB, "rotate")
        _knob(p, self._close_at(), 9, "close")
        p.setFont(_font(7.6, True))
        p.setPen(colors["accent"])
        p.drawText(self._unit_at(), Qt.AlignCenter, self.unit)
        draw_pill(p, QPointF(0, h / 2 - 19), f"{self.display_angle():.0f}°")


# ---------------------------------------------------------------------------
class SetSquare(GeometryTool):
    """A triangle with a right angle: 45°-45°-90° or 30°-60°-90°."""

    LEG = {"cm": 15, "in": 6}

    def __init__(self, manager, center, kind=45):
        self.kind = kind
        self.title = "Set Square 45°" if kind == 45 else "Set Square 30°/60°"
        super().__init__(manager, center)

    def _vertices(self):
        """A (the right angle), B (along the scale), C, centred on the centroid."""
        long_leg = self.LEG[self.unit] * self.ppu * (1.0 if self.kind == 45 else 1.2)
        short_leg = long_leg if self.kind == 45 else long_leg * math.tan(math.radians(30))
        a, b, c = QPointF(0, 0), QPointF(long_leg, 0), QPointF(0, -short_leg)
        g = (a + b + c) / 3
        return a - g, b - g, c - g

    def _incircle(self):
        a, b, c = self._vertices()
        la, lb, lc = (math.hypot(*(b - c).toTuple()), math.hypot(*(a - c).toTuple()),
                      math.hypot(*(a - b).toTuple()))
        per = la + lb + lc
        centre = (a * la + b * lb + c * lc) / per
        s = per / 2
        area = abs((b.x() - a.x()) * (c.y() - a.y()) - (c.x() - a.x()) * (b.y() - a.y())) / 2
        return centre, area / s

    def _inner(self):
        """The cut-out in the middle (None when the square is too small)."""
        centre, r = self._incircle()
        frame = max(44.0, 0.36 * r)
        if r - frame < 18:
            return None
        k = (r - frame) / r
        return [centre + (v - centre) * k for v in self._vertices()]

    def local_path(self):
        a, b, c = self._vertices()
        path = QPainterPath()
        path.addPolygon(QPolygonF([a, b, c, a]))
        inner = self._inner()
        if inner:
            path.addPolygon(QPolygonF(inner + [inner[0]]))
        path.setFillRule(Qt.OddEvenFill)
        return path

    def _knob_at(self):
        a, b, c = self._vertices()
        centre, _r = self._incircle()
        d = b - centre
        return b - d / math.hypot(*d.toTuple()) * 46

    def _close_at(self):
        a, b, c = self._vertices()
        centre, _r = self._incircle()
        d = c - centre
        return c - d / math.hypot(*d.toTuple()) * 34

    def _unit_at(self):
        a, _b, _c = self._vertices()
        return QRectF(a.x() + 22, a.y() - 44, 30, 18)

    def part_at(self, local):
        if math.hypot(*(local - self._knob_at()).toTuple()) <= self.KNOB + 3:
            return "rotate"
        if math.hypot(*(local - self._close_at()).toTuple()) <= 10:
            return "close"
        if self._unit_at().contains(local):
            return "unit"
        return "body"

    def edges(self):
        a, b, c = (self.to_viewport(v) for v in self._vertices())
        return [("line", a, b), ("line", a, c), ("line", b, c)]

    def paint_local(self, p):
        a, b, c = self._vertices()
        self.paint_body(p, self.local_path())
        colors = _palette()
        legs = math.hypot(*(b - a).toTuple()) / self.ppu
        self.tick_scale(p, a, QPointF(1, 0), QPointF(0, -1), int(legs * SUBDIVISIONS[self.unit]) / SUBDIVISIONS[self.unit])
        side = math.hypot(*(c - a).toTuple()) / self.ppu
        self.tick_scale(p, a, QPointF(0, -1), QPointF(1, 0), int(side * SUBDIVISIONS[self.unit]) / SUBDIVISIONS[self.unit])
        # the angles, written in their corners
        p.setFont(_font(7.6, True))
        p.setPen(colors["muted"])
        centre, _r = self._incircle()
        for vertex, degrees, knob_room in ((b, 45 if self.kind == 45 else 30, 46 + self.KNOB),
                                           (c, 45 if self.kind == 45 else 60, 34 + 9)):
            # along the corner's bisector, past its knob, where the corner is wide enough for the label
            d = centre - vertex
            d = d / (math.hypot(*d.toTuple()) or 1)
            spot = vertex + d * max(13 / math.sin(math.radians(degrees / 2)) + 6, knob_room + 13)
            p.drawText(QRectF(spot.x() - 18, spot.y() - 8, 36, 16), Qt.AlignCenter, f"{degrees}°")
        p.setPen(QPen(colors["muted"], 1))
        p.drawRect(QRectF(a.x() + 1, a.y() - 13, 12, 12))   # the right angle mark
        _knob(p, self._knob_at(), self.KNOB, "rotate")
        _knob(p, self._close_at(), 9, "close")
        p.setFont(_font(7.6, True))
        p.setPen(colors["accent"])
        p.drawText(self._unit_at(), Qt.AlignCenter, self.unit)
        # the tilt, on the frame along the long edge (the middle is cut out)
        mid = (b + c) / 2
        towards = centre - mid
        pill_at = mid + towards / (math.hypot(*towards.toTuple()) or 1) * 20 if self._inner() else centre
        p.save()
        p.translate(pill_at)
        p.rotate(math.degrees(math.atan2(b.y() - c.y(), b.x() - c.x())))   # along the edge
        draw_pill(p, QPointF(0, 0), f"{self.display_angle():.0f}°")
        p.restore()


# ---------------------------------------------------------------------------
class Protractor(GeometryTool):
    """A 180° protractor with two arms that measure the angle between them."""

    title = "Protractor"
    RADIUS = {"cm": 8, "in": 3.25}

    def __init__(self, manager, center):
        self.arms = [0.0, 60.0]       # degrees, counter-clockwise from the right-hand baseline
        super().__init__(manager, center)

    def _r(self):
        return self.RADIUS[self.unit] * self.ppu

    def _strip(self):
        return max(28.0, 0.9 * PT_PER_UNIT["cm"] / PT_PER_UNIT[self.unit] * self.ppu)

    def _arm_end(self, i, extra=20):
        t = math.radians(self.arms[i])
        r = self._r() + extra
        return QPointF(r * math.cos(t), -r * math.sin(t))

    def local_path(self):
        r, s = self._r(), self._strip()
        path = QPainterPath()
        path.moveTo(-r, s)
        path.lineTo(-r, 0)
        path.arcTo(QRectF(-r, -r, 2 * r, 2 * r), 180, -180)
        path.lineTo(r, s)
        path.closeSubpath()
        hole = r * 0.42
        if hole > 26:
            cut = QPainterPath()
            cut.moveTo(-hole, -6)
            cut.arcTo(QRectF(-hole, -hole - 6, 2 * hole, 2 * hole), 180, -180)
            cut.closeSubpath()
            path = path.subtracted(cut)
        for i in range(2):
            knob = QPainterPath()
            knob.addEllipse(self._arm_end(i), 10, 10)
            path = path.united(knob)
        return path

    def _knob_at(self):
        return QPointF(self._r() - 24, self._strip() / 2)

    def _close_at(self):
        return QPointF(-self._r() + 20, self._strip() / 2)

    def part_at(self, local):
        for i in range(2):
            if math.hypot(*(local - self._arm_end(i)).toTuple()) <= 12:
                return f"arm{i}"
        if math.hypot(*(local - self._knob_at()).toTuple()) <= self.KNOB + 3:
            return "rotate"
        if math.hypot(*(local - self._close_at()).toTuple()) <= 10:
            return "close"
        return "body"

    def cursor_for(self, part):
        return Qt.PointingHandCursor if part.startswith("arm") else Qt.OpenHandCursor

    def press_part(self, part, local, vp):
        if part.startswith("arm"):
            self._drag = (part,)
            return True
        return False

    def drag_part(self, kind, vp, event):
        local = self.to_local(vp - QPointF(self.pos()))
        angle = math.degrees(math.atan2(-local.y(), local.x()))
        if angle < -90:
            angle = 180.0
        angle = max(0.0, min(180.0, angle))
        if not event.modifiers() & Qt.AltModifier:   # whole degrees, unless Alt is held
            angle = round(angle * 2) / 2
        self.arms[int(kind[-1])] = angle
        self.relayout()

    def edges(self):
        r, s = self._r(), self._strip()
        start = -self.angle
        return [("arc", self.to_viewport(QPointF(0, 0)), r, start, start + 180),
                ("line", self.to_viewport(QPointF(-r, s)), self.to_viewport(QPointF(r, s)))]

    def paint_local(self, p):
        r, s = self._r(), self._strip()
        colors = _palette()
        self.paint_body(p, self.local_path())
        # degree ticks round the curve, with an outer and an inner scale
        step_px = r * math.pi / 180
        every = 1 if step_px >= 3 else 5 if step_px * 5 >= 3 else 10
        for d in range(0, 181, every):
            t = math.radians(d)
            size = 16 if d % 10 == 0 else 10 if d % 5 == 0 else 6
            outer = QPointF(r * math.cos(t), -r * math.sin(t))
            inner = QPointF((r - size) * math.cos(t), -(r - size) * math.sin(t))
            p.setPen(QPen(colors["ink"], 1))
            p.drawLine(outer, inner)
        p.setFont(_font(7.2))
        label_every = 10 if step_px * 10 >= 22 else 30
        for d in range(0, 181, label_every):
            t = math.radians(d)
            for radius, value, color in ((r - 26, d, colors["ink"]), (r - 42, 180 - d, colors["muted"])):
                if radius < 30:
                    continue
                at = QPointF(radius * math.cos(t), -radius * math.sin(t))
                p.setPen(color)
                p.drawText(QRectF(at.x() - 14, at.y() - 7, 28, 14), Qt.AlignCenter, str(value))
        # baseline and centre mark
        p.setPen(QPen(colors["ink"], 1))
        p.drawLine(QPointF(-r + 8, 0), QPointF(r - 8, 0))
        p.drawLine(QPointF(0, -8), QPointF(0, 8))
        p.drawEllipse(QPointF(0, 0), 3, 3)
        # the arms, the angle between them, its value
        a0, a1 = sorted(self.arms)
        wedge = QPainterPath()
        wedge.moveTo(0, 0)
        wedge.arcTo(QRectF(-34, -34, 68, 68), a0, a1 - a0)
        wedge.closeSubpath()
        soft = QColor(colors["accent"])
        soft.setAlpha(60)
        p.setPen(Qt.NoPen)
        p.setBrush(soft)
        p.drawPath(wedge)
        for i in range(2):
            p.setPen(QPen(colors["accent"], 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(0, 0), self._arm_end(i, 10))
            knob_at = self._arm_end(i)
            p.setPen(QPen(colors["accent"], 1.4))
            p.setBrush(colors["knob"])
            p.drawEllipse(knob_at, 8, 8)
            p.setBrush(colors["accent"])
            p.drawEllipse(knob_at, 3, 3)
        _knob(p, self._knob_at(), self.KNOB, "rotate")
        _knob(p, self._close_at(), 9, "close")
        draw_pill(p, QPointF(0, -max(46.0, r * 0.22)), f"∠ {a1 - a0:.1f}°", 9)
        p.setFont(_font(7.2))
        p.setPen(colors["muted"])
        p.drawText(QRectF(-r, 0, 2 * r, s), Qt.AlignCenter, f"tilt {self.display_angle():.0f}°")


# ---------------------------------------------------------------------------
class Compass(GeometryTool):
    """Needle at `center`; the pencil `radius` units away at `angle`."""

    title = "Compass"
    LEG = {"cm": 9, "in": 3.5}

    def __init__(self, manager, center):
        self.radius = 4.0 if manager.unit == "cm" else 1.5     # units
        self._arc = None          # viewport points being drawn
        self._swept = 0.0
        super().__init__(manager, center)

    def _rp(self):
        return self.radius * self.ppu

    def _leg(self):
        return self.LEG[self.unit] * self.ppu

    def _hinge(self):
        rp, leg = self._rp(), self._leg()
        half = min(rp / 2, leg * 0.97)
        return QPointF(rp / 2, -math.sqrt(max(1.0, leg * leg - half * half)))

    def _head(self):
        return self._hinge() + QPointF(0, -28)

    def _close_at(self):
        return self._head() + QPointF(30, 0)

    def _leg_polygon(self, foot, width_top, width_foot):
        hinge = self._hinge()
        d = foot - hinge
        length = math.hypot(*d.toTuple()) or 1
        n = QPointF(-d.y() / length, d.x() / length)
        return QPolygonF([hinge + n * width_top, foot + n * width_foot, foot - n * width_foot, hinge - n * width_top])

    def local_path(self):
        path = QPainterPath()
        path.addPolygon(self._leg_polygon(QPointF(0, 0), 8, 4))
        path.addPolygon(self._leg_polygon(QPointF(self._rp(), 0), 8, 5))
        path.addEllipse(self._hinge(), 12, 12)
        path.addEllipse(self._head(), 14, 14)
        path.addEllipse(self._close_at(), 10, 10)
        path.addEllipse(QPointF(self._rp(), 0), 9, 9)
        path.addEllipse(QPointF(0, 0), 8, 8)
        path.setFillRule(Qt.WindingFill)
        return path

    def pivot(self):
        return self.center

    def part_at(self, local):
        if math.hypot(*(local - self._close_at()).toTuple()) <= 10:
            return "close"
        if math.hypot(*(local - self._head()).toTuple()) <= 15:
            return "draw"
        pencil = QPointF(self._rp(), 0)
        dist, t = _seg_distance(local, self._hinge(), pencil)
        if math.hypot(*(local - pencil).toTuple()) <= 14 or (dist <= 10 and t > 0.55):
            return "pencil"
        return "body"

    def cursor_for(self, part):
        return {"draw": Qt.CrossCursor, "pencil": Qt.SizeHorCursor}.get(part, Qt.OpenHandCursor)

    def press_part(self, part, local, vp):
        if part == "draw":
            self._drag = ("draw", _angle_of(vp - self.center))
            self._arc = [self.pencil_point()]
            self._swept = 0.0
            return True
        if part == "pencil":
            self._drag = ("pencil",)
            return True
        return False

    def pencil_point(self):
        return self.to_viewport(QPointF(self._rp(), 0))

    def drag_part(self, kind, vp, event):
        v = vp - self.center
        if kind == "pencil":   # set the radius (and direction)
            sub = 1 / SUBDIVISIONS[self.unit]
            r = max(sub * 2, min(1.9 * self.LEG[self.unit], math.hypot(*v.toTuple()) / self.ppu))
            self.radius = round(r / sub) * sub if not event.modifiers() & Qt.AltModifier else r
            self.angle = -_angle_of(v)
            self.relayout()
        elif kind == "draw":   # turn round the needle, drawing with the pencil
            target = -_angle_of(v)
            delta = (target - self.angle + 180) % 360 - 180
            steps = max(1, int(abs(delta) / 1.0))
            for k in range(1, steps + 1):
                self.angle += delta / steps
                self._arc.append(self.pencil_point())
            self._swept += delta
            self.relayout()

    def release_part(self, kind):
        if kind == "draw" and self._arc and len(self._arc) > 2 and abs(self._swept) >= 1:
            self.manager.commit_arc(self, self._arc)
        self._arc = None
        self.manager.canvas_update()

    def mouseDoubleClickEvent(self, event):
        if self.part_at(self.to_local(event.position())) == "draw":   # a whole circle
            start = self.angle
            points = []
            for k in range(0, 361, 1):
                self.angle = start + k
                points.append(self.pencil_point())
            self.angle = start
            self._drag = None
            self.manager.commit_arc(self, points)
            self.relayout()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            event.ignore()
            return
        steps = (event.angleDelta().y() or event.angleDelta().x()) / 120
        sub = 1 / SUBDIVISIONS[self.unit]
        self.radius = max(sub * 2, min(1.9 * self.LEG[self.unit], self.radius + steps * sub))
        self.relayout()
        event.accept()

    def paint_local(self, p):
        colors = _palette()
        rp = self._rp()
        hinge = self._hinge()
        steel = QLinearGradient(hinge + QPointF(-8, 0), hinge + QPointF(8, 0))
        dark = theme.mode == theme.DARK
        steel.setColorAt(0, QColor(150, 156, 170) if not dark else QColor(110, 112, 124))
        steel.setColorAt(0.5, QColor(246, 248, 252) if not dark else QColor(200, 202, 214))
        steel.setColorAt(1, QColor(132, 138, 152) if not dark else QColor(86, 88, 98))
        p.setPen(QPen(QColor(70, 74, 90, 180), 0.8))
        p.setBrush(steel)
        p.drawPolygon(self._leg_polygon(QPointF(0, 0) + (hinge - QPointF(0, 0)) * 0.12, 7, 3))
        p.setPen(QPen(QColor(40, 42, 50), 1.4, Qt.SolidLine, Qt.RoundCap))   # the needle
        p.drawLine(QPointF(0, 0), (hinge - QPointF(0, 0)) * 0.12)
        # the pencil leg: steel, then the wood, then the lead in the pen's colour
        foot = QPointF(rp, 0)
        wood_start = foot + (hinge - foot) * 0.2
        p.setPen(QPen(QColor(70, 74, 90, 180), 0.8))
        p.setBrush(steel)
        p.drawPolygon(QPolygonF([hinge + QPointF(-7, 0), wood_start + QPointF(-4.5, 0),
                                 wood_start + QPointF(4.5, 0), hinge + QPointF(7, 0)]))
        d = foot - wood_start
        n = QPointF(-d.y(), d.x()) / (math.hypot(*d.toTuple()) or 1)
        lead_from = foot - d * 0.3
        p.setBrush(QColor(232, 196, 140))   # the sharpened wood
        p.drawPolygon(QPolygonF([wood_start + n * 4.5, lead_from + n * 1.6, lead_from - n * 1.6,
                                 wood_start - n * 4.5]))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(*self.manager.pen_color()))   # the lead draws in the pen's colour
        p.drawPolygon(QPolygonF([lead_from + n * 1.6, foot, lead_from - n * 1.6]))
        # hinge and head
        p.setPen(QPen(QColor(60, 64, 80, 200), 1))
        p.setBrush(steel)
        p.drawEllipse(hinge, 9, 9)
        p.setPen(QPen(QColor(60, 64, 80, 200), 1))
        p.drawLine(hinge, self._head())
        _knob(p, self._head(), 13, "grip")
        _knob(p, self._close_at(), 9, "close")
        p.setPen(QPen(colors["accent"], 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), 3.5, 3.5)
        text = f"r {self.radius:.2f} {self.unit}"
        if self._drag and self._drag[0] == "draw":
            text += f" · {abs(self._swept):.0f}°"
        p.save()
        p.translate(hinge + QPointF(0, 26))
        p.rotate(-self.angle)            # the readout stays level
        draw_pill(p, QPointF(0, 0), text)
        p.restore()


TOOL_KINDS = {
    "ruler": (Ruler, {}),
    "square45": (SetSquare, {"kind": 45}),
    "square30": (SetSquare, {"kind": 30}),
    "protractor": (Protractor, {}),
    "compass": (Compass, {}),
}


# ---------------------------------------------------------------------------
class _Canvas(QWidget):
    """Under the tools: their soft shadows, the arc being drawn, and the
    pen's length readout. Never takes a click."""

    def __init__(self, manager):
        super().__init__(manager.viewport)
        self.manager = manager
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.readout = None       # (text, viewport point)
        self.resize(manager.viewport.size())
        self.show()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = theme.mode == theme.DARK
        for tool in self.manager.tools:
            path = tool.world_path()
            outside = QPainterPath()
            outside.addRect(QRectF(self.rect()))
            p.save()
            p.setClipPath(outside.subtracted(path))   # only round the tool: its body is see-through
            for spread, alpha in ((7, 10), (4, 16), (2, 24)):
                p.setPen(QPen(QColor(0, 0, 0, int(alpha * (2.2 if dark else 1))), spread * 2,
                              Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.setBrush(Qt.NoBrush)
                p.drawPath(QTransform.fromTranslate(0, spread * 0.6).map(path))
            p.restore()
            arc = getattr(tool, "_arc", None)
            if arc and len(arc) > 1:
                color = QColor(*self.manager.pen_color())
                p.setPen(QPen(color, max(1.0, self.manager.pen_width() * self.manager.tab.zoom),
                              Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.drawPolyline(QPolygonF(arc))
        if self.readout:
            text, at = self.readout
            draw_pill(p, at + QPointF(0, -26), text, 9)
        p.end()


class GeometryManager:
    """The geometry tools of one PDF tab."""

    def __init__(self, tab):
        self.tab = tab
        self.viewport = tab.scroll_area.viewport()
        self.tools = []
        self._canvas = None
        self.unit = theme._settings().value("geometry/unit", "cm")
        if self.unit not in PT_PER_UNIT:
            self.unit = "cm"
        self._syncing = False
        self._watcher = _ResizeWatcher(self)
        self.viewport.installEventFilter(self._watcher)
        tab.pages_container.installEventFilter(self._watcher)
        tab.scroll_area.verticalScrollBar().valueChanged.connect(self.schedule_sync)
        tab.scroll_area.horizontalScrollBar().valueChanged.connect(self.schedule_sync)
        self._sync_timer = QTimer(self.viewport)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(0)
        self._sync_timer.timeout.connect(self.sync)

    # ---- scale and the pen's style
    def px_per_unit(self):
        return self.tab.zoom * PT_PER_UNIT[self.unit]

    def pen_color(self):
        from .tools import Tool

        return self.tab.window.tool_styles[Tool.INK]["color"]

    def pen_width(self):
        from .tools import Tool

        return self.tab.window.tool_styles[Tool.INK]["width"]

    # ---- the tools
    def add(self, kind):
        cls, kwargs = TOOL_KINDS[kind]
        if self._canvas is None:
            self._canvas = _Canvas(self)
        self._canvas.raise_()
        for other in self.tools:
            other.raise_()
        offset = 28 * sum(1 for t in self.tools)
        centre = QPointF(self.viewport.width() / 2 + offset, self.viewport.height() / 2 + offset)
        tool = cls(self, centre, **kwargs)
        self.tools.append(tool)
        tool.relayout()
        tool.raise_()
        return tool

    def remove(self, tool):
        if tool in self.tools:
            self.tools.remove(tool)
        tool.hide()
        tool.deleteLater()
        self.canvas_update()

    def clear(self):
        for tool in list(self.tools):
            self.remove(tool)

    def toggle_unit(self):
        self.set_unit("in" if self.unit == "cm" else "cm")

    def set_unit(self, unit):
        self.unit = unit
        theme._settings().setValue("geometry/unit", unit)
        for tool in self.tools:
            if isinstance(tool, Compass):
                tool.radius = tool.radius * PT_PER_UNIT["in" if unit == "cm" else "cm"] / PT_PER_UNIT[unit]
        self.refresh()

    def refresh(self):
        """The zoom (or the look) changed: redraw every tool to scale, then put
        each back on its spot once the pages are laid out again."""
        self._syncing = True
        try:
            for tool in self.tools:
                tool.relayout()
        finally:
            self._syncing = False
        self.canvas_update()
        self.schedule_sync()
        QTimer.singleShot(60, self.sync)

    # ---- lying on the paper
    def remember(self, tool):
        """Note where on the page the tool now is (unless it is being put back there)."""
        if self._syncing:
            return
        pw = self.page_widget_at(tool.center)
        if pw is None:
            return
        pdf = pw.to_pdf_point(pw.mapFrom(self.viewport, QPointF(tool.center)))
        tool.anchor = (pw.page_index, pdf.x, pdf.y)

    def schedule_sync(self, *_):
        if self.tools:
            self._sync_timer.start()

    def sync(self):
        """Put every tool back on its spot of the page (after scrolling, zooming,
        or the view changing size)."""
        import pymupdf as fitz

        from . import pdf_ops

        self._syncing = True
        try:
            for tool in self.tools:
                if tool.anchor is None or tool._drag is not None:
                    continue
                index, x, y = tool.anchor
                if index >= len(self.tab.page_widgets):
                    continue
                pw = self.tab.page_widgets[index]
                px = fitz.Point(x, y) * pdf_ops.coord_matrix(pw.page(), self.tab.zoom)
                at = pw.mapTo(self.viewport, QPointF(px.x, px.y))
                if abs(at.x() - tool.center.x()) > 0.25 or abs(at.y() - tool.center.y()) > 0.25:
                    tool.center = at
                    tool.relayout()
        finally:
            self._syncing = False
        self.canvas_update()

    def canvas_update(self):
        if self._canvas is not None:
            self._canvas.update()

    def context_menu(self, tool, global_pos):
        from PySide6.QtWidgets import QMenu

        menu = QMenu(tool)
        menu.addAction("Straighten (0°)", lambda: tool.set_angle(0))
        menu.addAction("Turn 90°", lambda: tool.set_angle(tool.angle - 90))
        menu.addSeparator()
        menu.addAction("Centimetres" if self.unit == "in" else "Inches", self.toggle_unit)
        menu.addSeparator()
        menu.addAction(f"Remove {tool.title}", lambda: self.remove(tool))
        menu.exec(global_pos)

    # ---- the pen following an edge
    def edge_near(self, point, reach=SNAP_DISTANCE):
        """The tool edge nearest a viewport point, if within `reach` px."""
        best, best_d = None, reach
        for tool in reversed(self.tools):
            for edge in tool.edges():
                if edge[0] == "line":
                    d, _t = _seg_distance(point, edge[1], edge[2])
                else:
                    _k, c, r, a0, a1 = edge
                    v = point - c
                    ang = _angle_of(v) % 360
                    lo, hi = a0 % 360, (a0 % 360) + (a1 - a0)
                    inside = lo <= ang <= hi or lo <= ang + 360 <= hi
                    d = abs(math.hypot(*v.toTuple()) - r) if inside else reach + 1
                if d < best_d:
                    best, best_d = edge + (tool,), d
        return best

    @staticmethod
    def project(edge, point):
        """`point` moved onto the edge."""
        if edge[0] == "line":
            a, b = edge[1], edge[2]
            d = b - a
            length2 = d.x() ** 2 + d.y() ** 2 or 1
            t = ((point.x() - a.x()) * d.x() + (point.y() - a.y()) * d.y()) / length2
            t = max(-0.02, min(1.02, t))
            return a + d * t
        c, r = edge[1], edge[2]
        v = point - c
        length = math.hypot(*v.toTuple()) or 1
        return c + v * (r / length)

    def measure_text(self, edge, start, end):
        """The length drawn along an edge, in page units."""
        ppu = self.px_per_unit()
        if edge[0] == "line":
            return f"{math.hypot(*(end - start).toTuple()) / ppu:.2f} {self.unit}"
        c, r = edge[1], edge[2]
        sweep = abs((_angle_of(end - c) - _angle_of(start - c) + 180) % 360 - 180)
        return f"{sweep:.1f}° · arc {math.radians(sweep) * r / ppu:.2f} {self.unit}"

    def show_readout(self, text, point):
        if self._canvas is not None:
            self._canvas.readout = (text, QPointF(point))
            self._canvas.update()

    def hide_readout(self):
        if self._canvas is not None and self._canvas.readout:
            self._canvas.readout = None
            self._canvas.update()

    # ---- the compass draws on the page
    def page_widget_at(self, point):
        best, best_d = None, None
        for pw in self.tab.page_widgets:
            if not pw.isVisible():
                continue
            top_left = pw.mapTo(self.viewport, QPointF(0, 0))
            rect = QRectF(top_left, QPointF(top_left.x() + pw.width(), top_left.y() + pw.height()))
            if rect.contains(point):
                return pw
            d = min(abs(point.y() - rect.top()), abs(point.y() - rect.bottom()))
            if best_d is None or d < best_d:
                best, best_d = pw, d
        return best

    def commit_arc(self, tool, viewport_points):
        pw = self.page_widget_at(tool.center)
        if pw is None:
            return
        pdf = [pw.to_pdf_point(pw.mapFrom(self.viewport, QPointF(pt))) for pt in viewport_points]
        self.tab.commit_geometry_ink(pw, pdf)


class _ResizeWatcher(QObject):
    """Keeps the canvas as big as the view."""

    def __init__(self, manager):
        super().__init__(manager.viewport)
        self.manager = manager

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.LayoutRequest, QEvent.Move):
            if obj is self.manager.viewport and self.manager._canvas is not None:
                self.manager._canvas.resize(obj.size())
            self.manager.schedule_sync()   # the pages may have moved under the tools
        return False
