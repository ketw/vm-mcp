"""motion.py — pure maths for cursor animation (no Pillow, no Win32)."""

from __future__ import annotations

import math

Vec = tuple[float, float]


# ── Easing ───────────────────────────────────────────────────────────────────
def ease_in_out_cubic(t: float) -> float:
    """Glide easing: cubic/sine blend. Pure cubic peaks at 3x average speed (jumpy on long flicks)."""
    t = max(0.0, min(1.0, t))
    cubic = 4 * t * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2
    sine = -(math.cos(math.pi * t) - 1) / 2
    return 0.5 * cubic + 0.5 * sine


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


# ── Paths ────────────────────────────────────────────────────────────────────
def glide_duration(distance: float) -> float:
    """Seconds to travel `distance` px: quick for short hops, never sluggish for long ones."""
    return 0.18 + 0.70 * (1 - math.exp(-distance / 700.0))


def plan_path(p0: Vec, p1: Vec) -> tuple[Vec, Vec, Vec]:
    """Quadratic bezier from p0 to p1 with a gentle upward arc (humans don't move in straight lines)."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    d = math.hypot(dx, dy)
    if d < 40:
        mid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
        return p0, mid, p1
    nx, ny = -dy / d, dx / d                       # perpendicular
    if ny > 0:                                     # always bow upward on screen
        nx, ny = -nx, -ny
    bow = min(d * 0.10, 80.0)
    ctrl = ((p0[0] + p1[0]) / 2 + nx * bow, (p0[1] + p1[1]) / 2 + ny * bow)
    return p0, ctrl, p1


def bezier(path: tuple[Vec, Vec, Vec], t: float) -> Vec:
    a, c, b = path
    u = 1 - t
    return (u * u * a[0] + 2 * u * t * c[0] + t * t * b[0],
            u * u * a[1] + 2 * u * t * c[1] + t * t * b[1])


# ── Springs ──────────────────────────────────────────────────────────────────
class Spring:
    """Damped spring toward `target`. zeta<1 overshoots a little (that's the 'bounce')."""

    __slots__ = ("x", "v", "target", "omega", "zeta")

    def __init__(self, x: float = 0.0, omega: float = 22.0, zeta: float = 0.7):
        self.x = x
        self.v = 0.0
        self.target = x
        self.omega = omega
        self.zeta = zeta

    def step(self, dt: float) -> None:
        n = max(1, int(dt / 0.004))                # sub-step for stability
        h = dt / n
        w, z = self.omega, self.zeta
        for _ in range(n):
            a = -w * w * (self.x - self.target) - 2 * z * w * self.v
            self.v += a * h
            self.x += self.v * h

    def settled(self, eps: float = 1e-3) -> bool:
        return abs(self.x - self.target) < eps and abs(self.v) < eps * 4

    def snap(self, x: float) -> None:
        self.x = self.target = x
        self.v = 0.0


# ── Colour ───────────────────────────────────────────────────────────────────
Color = tuple[float, float, float]


def mix(a: Color, b: Color, t: float) -> Color:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def approach(cur: float, target: float, rate: float, dt: float) -> float:
    """Exponential approach (frame-rate independent)."""
    return target + (cur - target) * math.exp(-rate * dt)
