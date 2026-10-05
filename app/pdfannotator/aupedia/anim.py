"""Timing for AUPedia: one clock for everything that moves, easing curves,
and the smooth "hand" noise that makes lines wobble like real ink.

SPEED scales time (tests run the animations many times faster)."""
import math
import random
import time

SPEED = 1.0
BOIL_FPS = 10          # the lines are redrawn this often, like hand-drawn animation (motion itself is 60 fps)


def now():
    return time.monotonic() * SPEED


def ms(seconds):
    """Real milliseconds for an animation time, for QTimer.singleShot."""
    return max(0, int(seconds * 1000 / SPEED))


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def lerp(a, b, t):
    return a + (b - a) * t


def ease_in_out_cubic(t):
    t = clamp(t)
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out_cubic(t):
    return 1 - (1 - clamp(t)) ** 3


def ease_out_back(t, s=1.4):
    t = clamp(t) - 1
    return 1 + t * t * ((s + 1) * t + s)


def spring(t, amount=1.0, freq=18.0, damping=7.0):
    """A settling wobble: starts at `amount`, rings, and dies away to 0."""
    if t < 0:
        return 0.0
    return amount * math.exp(-damping * t) * math.cos(freq * t)


def cubic(p0, p1, p2, p3, t):
    u = 1 - t
    return (u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
            u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1])


class Wobble:
    """Smooth noise from a few sines with random phases: a hand that isn't quite steady."""

    def __init__(self, seed, terms=3):
        rnd = random.Random(seed)
        self.terms = [(rnd.uniform(0.6, 1.0) / (k + 1), rnd.uniform(0.7, 1.3) * (k + 1) * 1.7, rnd.uniform(0, math.tau))
                      for k in range(terms)]

    def __call__(self, x):
        return sum(a * math.sin(f * x + p) for a, f, p in self.terms)


def boil_frame(t, frames=3):
    """Which of the hand-drawn variants to show now."""
    return int(t * BOIL_FPS) % frames
