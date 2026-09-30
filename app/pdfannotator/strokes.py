"""Freehand stroke processing for the Pen and Marker tools.

- smooth_stroke(): turns the jittery points a mouse or pen reports into a
  smooth curve (drop near-duplicate points, average out the wobble, then
  round the corners with Chaikin subdivision). Stroke ends stay put.
- Pressure: a pen tablet (or a Windows pen/stylus) reports real pressure;
  with a mouse, SpeedPressure simulates it from how fast you draw (slow
  strokes are thicker, quick flicks thinner), like ink from a real pen.
- width_for(): the stroke width at a pressure.

No Qt here; points are (x, y) tuples in PDF points.
"""
import math

MIN_GAP_PT = 0.6          # points closer than this to the last kept one are noise
MIN_PRESSURE, MAX_PRESSURE = 0.2, 1.0


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def dedupe(points, pressures=None, min_gap=MIN_GAP_PT):
    """Drop points that barely moved (they only add wobble). Keeps the last."""
    if not points:
        return [], []
    pressures = list(pressures) if pressures else [1.0] * len(points)
    out_p, out_w = [points[0]], [pressures[0]]
    for p, w in zip(points[1:], pressures[1:]):
        if _dist(p, out_p[-1]) >= min_gap:
            out_p.append(p)
            out_w.append(w)
        else:
            out_w[-1] = max(out_w[-1], w)
    if len(out_p) > 1 and out_p[-1] != points[-1]:
        out_p.append(points[-1])
        out_w.append(pressures[-1])
    return out_p, out_w


def moving_average(values, radius):
    """Average each value with `radius` neighbours on each side (fewer near
    the ends, so the ends stay where they were drawn). Values are tuples."""
    n = len(values)
    if n < 3 or radius < 1:
        return list(values)
    out = []
    for i in range(n):
        r = min(radius, i, n - 1 - i)
        window = values[i - r:i + r + 1]
        out.append(tuple(sum(v[k] for v in window) / len(window) for k in range(len(values[0]))))
    return out


def chaikin(points, pressures, iterations=2):
    """Corner cutting: each segment is replaced by points at 1/4 and 3/4,
    which converges on a smooth curve. The first and last points are kept."""
    for _ in range(iterations):
        if len(points) < 3:
            break
        new_p, new_w = [points[0]], [pressures[0]]
        for (a, b), (wa, wb) in zip(zip(points, points[1:]), zip(pressures, pressures[1:])):
            new_p += [(0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]),
                      (0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1])]
            new_w += [0.75 * wa + 0.25 * wb, 0.25 * wa + 0.75 * wb]
        new_p.append(points[-1])
        new_w.append(pressures[-1])
        points, pressures = new_p, new_w
    return points, pressures


def smooth_stroke(points, pressures=None, strength=1.0):
    """(points, pressures) of a smoothed stroke. strength 0 only removes
    duplicate points; 1 is the Smooth handwriting setting."""
    points = [(float(p[0]), float(p[1])) for p in points]
    points, pressures = dedupe(points, pressures)
    if strength <= 0 or len(points) < 3:
        return points, pressures
    radius = max(1, round(2 * strength))
    points = moving_average(points, radius)
    pressures = [v[0] for v in moving_average([(w,) for w in pressures], radius + 1)]
    points, pressures = chaikin(points, pressures, iterations=2)
    # Chaikin doubles the points each pass; thin out what is now too dense
    return dedupe(points, pressures, min_gap=MIN_GAP_PT / 2)


def taper(pressures, count=4):
    """Ease the pressure in at the start and out at the end of a stroke, the
    way a pen touches down and lifts off."""
    n = len(pressures)
    out = list(pressures)
    for i in range(min(count, n // 2)):
        f = 0.45 + 0.55 * (i + 1) / (count + 1)
        out[i] *= f
        out[n - 1 - i] *= f
    return out


def width_for(base_width, pressure):
    """Stroke width at `pressure` (0-1): from about 40 % of the base width at
    a light touch to 125 % at full pressure."""
    p = min(MAX_PRESSURE, max(MIN_PRESSURE, pressure))
    return base_width * (0.2 + 1.05 * p)


class SpeedPressure:
    """Pressure from drawing speed, for a mouse (or a pen that reports
    none). Feed it positions in PDF points with times in milliseconds."""

    def __init__(self):
        self.last = None
        self.value = 0.75

    def feed(self, point, t_ms):
        if self.last is None:
            self.last = (point, t_ms)
            return self.value
        (prev, t0) = self.last
        dt = max(1.0, t_ms - t0)
        speed = _dist(point, prev) / dt   # points per millisecond
        target = min(MAX_PRESSURE, max(0.25, 1.05 - 0.75 * speed))
        self.value += (target - self.value) * 0.35  # ease, so the width doesn't jump
        self.last = (point, t_ms)
        return self.value
