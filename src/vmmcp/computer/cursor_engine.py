"""cursor_engine.py — the AI cursor's animation brain + frame renderer.

Pure Python + Pillow (no Win32), so it can be unit-tested and previewed anywhere.
overlay.py owns the window and calls:

    engine.step(dt)    -> bool   advance animation; True when a new frame is needed
    engine.render()    -> Frame  premultiplied-BGRA bytes + where to put the window

Everything an action can ask for is a method on CursorEngine:
    place / glide_to / set_mode / press / ripple / shake / spin / fade_to
Colours and poses per action live in MODES — edit that table to restyle the cursor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from PIL import Image, ImageDraw

from .cursor_art import SOFT_WHITE, Sprites
from .motion import (Spring, approach, bezier, clamp, ease_in_out_cubic, ease_out_cubic,
                     glide_duration, plan_path)


@dataclass(frozen=True)
class Mode:
    color: tuple      # fill tint
    glow: float       # 0..1 halo strength
    tilt: float       # resting rotation in degrees (+ = clockwise)
    scale: float      # resting scale


# ── The palette of actions. Soft pastels on a soft-white base. ──────────────
MODES: dict[str, Mode] = {
    "idle":         Mode(SOFT_WHITE,       0.00,   0.0, 1.00),
    "move":         Mode(SOFT_WHITE,       0.14,   0.0, 1.00),
    "click":        Mode((150, 196, 255),  0.55,   0.0, 1.00),   # soft blue
    "right_click":  Mode((200, 172, 255),  0.55,   9.0, 1.00),   # soft violet
    "double_click": Mode((138, 228, 212),  0.55,   0.0, 1.00),   # soft teal
    "drag":         Mode((255, 208, 126),  0.60, -11.0, 0.94),   # soft amber
    "hold":         Mode((255, 208, 126),  0.60,  -7.0, 0.92),
    "scroll":       Mode((146, 216, 240),  0.45,   0.0, 1.00),   # soft sky
    "type":         Mode((168, 232, 190),  0.45,   0.0, 1.00),   # soft mint
    "key":          Mode((168, 232, 190),  0.45,   0.0, 1.00),
    "wait":         Mode((216, 210, 255),  0.38,   0.0, 1.00),   # soft lavender
    "error":        Mode((255, 156, 146),  0.60,   0.0, 1.00),   # soft coral
}


@dataclass
class Frame:
    x: int            # window left, screen px
    y: int            # window top, screen px
    w: int
    h: int
    bgra: bytes       # premultiplied BGRA, top-down
    visible: bool


class CursorEngine:
    def __init__(self, ui_scale: float = 1.0, on_move: Optional[Callable[[int, int], None]] = None):
        self.sp = Sprites(ui_scale)
        self.u = ui_scale
        self.on_move = on_move                     # called every frame while gliding (moves the real cursor)

        self.x, self.y = 200.0, 200.0
        self.vx = self.vy = 0.0
        self._glide: Optional[dict] = None

        self.mode = "idle"
        self._mode_until: Optional[float] = None
        self.scroll_dir = 0
        self.color = list(map(float, SOFT_WHITE))
        self.glow = 0.0
        self.opacity = 0.0
        self.opacity_target = 1.0

        self.tilt = Spring(0.0, omega=20.0, zeta=0.65)
        self.scale = Spring(1.0, omega=28.0, zeta=0.50)
        self.pressed = False

        self._spin_t: Optional[float] = None
        self._shake_t: Optional[float] = None
        self._ripples: list[dict] = []
        self.clock = 0.0
        self._last_key = None

    # ── Commands (call from the render thread only; overlay.py queues them) ──
    def place(self, x: float, y: float) -> None:
        self.x, self.y = float(x), float(y)
        self.vx = self.vy = 0.0

    def glide_to(self, x: float, y: float, duration: Optional[float] = None,
                 on_done: Optional[Callable[[], None]] = None) -> None:
        self._finish_glide()
        dist = math.hypot(x - self.x, y - self.y)
        if dist < 0.5:
            if on_done:
                on_done()
            return
        dur = duration if duration is not None else glide_duration(dist)
        self._glide = {"path": plan_path((self.x, self.y), (float(x), float(y))),
                       "t": 0.0, "dur": max(0.05, dur), "done": on_done}

    def _finish_glide(self) -> None:
        g, self._glide = self._glide, None
        if g:
            end = g["path"][2]
            self.x, self.y = end
            if self.on_move:
                self.on_move(int(round(end[0])), int(round(end[1])))
            if g["done"]:
                g["done"]()

    def set_mode(self, mode: str, hold: Optional[float] = None, scroll_dir: int = 0) -> None:
        """Switch colour/pose. `hold` seconds later it falls back to idle (None = stay)."""
        self.mode = mode if mode in MODES else "idle"
        self._mode_until = None if hold is None else self.clock + hold
        self.scroll_dir = scroll_dir

    def press(self, down: bool) -> None:
        self.pressed = down

    def ripple(self, strength: float = 1.0) -> None:
        col = MODES[self.mode].color if self.mode != "idle" else (150, 196, 255)
        self._ripples.append({"t": 0.0, "dur": 0.55, "color": col, "k": strength})

    def shake(self) -> None:
        self._shake_t = 0.0

    def spin(self) -> None:
        self._spin_t = 0.0

    def fade_to(self, opacity: float) -> None:
        self.opacity_target = clamp(opacity, 0.0, 1.0)

    # ── Animation ────────────────────────────────────────────────────────────
    def busy(self) -> bool:
        return self._glide is not None

    def step(self, dt: float) -> bool:
        dt = clamp(dt, 0.0005, 0.05)
        self.clock += dt

        # position
        if self._glide:
            g = self._glide
            g["t"] += dt / g["dur"]
            nx, ny = bezier(g["path"], ease_in_out_cubic(g["t"]))
            self.vx, self.vy = (nx - self.x) / dt, (ny - self.y) / dt
            self.x, self.y = nx, ny
            if self.on_move:
                self.on_move(int(round(nx)), int(round(ny)))
            if g["t"] >= 1.0:
                self._finish_glide()
        else:
            self.vx = approach(self.vx, 0.0, 16.0, dt)
            self.vy = approach(self.vy, 0.0, 16.0, dt)

        if self._mode_until is not None and self.clock >= self._mode_until:
            self.set_mode("idle")
        eff = "move" if (self._glide and self.mode == "idle") else self.mode
        m = MODES[eff]

        # colour / glow / opacity
        for i in range(3):
            self.color[i] = approach(self.color[i], m.color[i], 15.0, dt)
        glow_t = m.glow
        if eff == "wait":
            glow_t += 0.14 * math.sin(self.clock * 2 * math.pi * 0.7)
        self.glow = approach(self.glow, glow_t, 12.0, dt)
        self.opacity = approach(self.opacity, self.opacity_target, 11.0, dt)

        # pose targets
        tilt_t = clamp(self.vx * 0.0055, -12.0, 12.0) + m.tilt
        if eff == "wait":
            tilt_t += 5.0 * math.sin(self.clock * 2 * math.pi * 0.55)
        if eff == "scroll":
            tilt_t += -7.0 * self.scroll_dir
        self.tilt.target = tilt_t
        scale_t = m.scale * (0.84 if self.pressed else 1.0)
        if eff in ("type", "key"):
            scale_t *= 1.0 + 0.035 * math.sin(self.clock * 2 * math.pi * 8)
        self.scale.target = scale_t
        self.tilt.step(dt)
        self.scale.step(dt)

        # one-shot effects
        if self._spin_t is not None:
            self._spin_t += dt
            if self._spin_t >= 0.5:
                self._spin_t = None
        if self._shake_t is not None:
            self._shake_t += dt
            if self._shake_t >= 0.6:
                self._shake_t = None
        for r in self._ripples:
            r["t"] += dt
        self._ripples = [r for r in self._ripples if r["t"] < r["dur"]]

        return self._frame_key() != self._last_key or bool(self._ripples)

    def _pose(self):
        tilt = self.tilt.x
        if self._shake_t is not None:
            tilt += 14.0 * math.exp(-self._shake_t * 6.0) * math.sin(self._shake_t * 2 * math.pi * 8)
        sx = sy = self.scale.x
        if self._spin_t is not None:
            sx *= math.cos(2 * math.pi * ease_in_out_cubic(self._spin_t / 0.5))
            if abs(sx) < 0.03:
                sx = 0.03 if sx >= 0 else -0.03
        offy = 0.0
        if self.mode == "scroll":
            offy = -self.scroll_dir * 2.5 * self.u * math.sin(self.clock * 2 * math.pi * 5)
        return tilt, sx, sy, offy

    def _frame_key(self):
        tilt, sx, sy, offy = self._pose()
        return (round(self.x * 2), round((self.y + offy) * 2), round(tilt, 1), round(sx, 3), round(sy, 3),
                tuple(int(c) for c in self.color), round(self.glow, 2), round(self.opacity, 2))

    # ── Rendering ────────────────────────────────────────────────────────────
    def render(self) -> Frame:
        sp, ss, W = self.sp, self.sp.ss, self.sp.W
        tilt, sx, sy, offy = self._pose()
        self._last_key = self._frame_key()

        px, py = self.x, self.y + offy
        wx, wy = math.floor(px), math.floor(py)
        fx, fy = (px - wx) * ss, (py - wy) * ss            # sub-pixel shift keeps slow moves smooth
        hot = sp.hot * ss

        th = math.radians(tilt)
        c, s = math.cos(th), math.sin(th)
        m00, m01, m10, m11 = c / sx, s / sx, -s / sy, c / sy   # inverse of R(theta) . S(sx, sy)
        ax, ay = hot + fx, hot + fy
        data = (m00, m01, hot - (m00 * ax + m01 * ay),
                m10, m11, hot - (m10 * ax + m11 * ay))

        def tf(mask):
            return mask.transform((W, W), Image.AFFINE, data, Image.BICUBIC)

        def paint(base, color, mask, k):
            if k <= 0.003:
                return base
            lut = [int(i * min(1.0, k)) for i in range(256)]
            layer = Image.new("RGBA", (W, W), tuple(int(v) for v in color) + (0,))
            layer.putalpha(mask.point(lut))
            return Image.alpha_composite(base, layer)

        op = clamp(self.opacity, 0.0, 1.0)
        img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        img = paint(img, (12, 18, 30), tf(sp.shadow), 0.42 * op)
        img = paint(img, self.color, tf(sp.glow), self.glow * 0.75 * op)
        img = paint(img, self.color, tf(sp.body), op)
        img = paint(img, (28, 38, 58), tf(sp.edge), 0.30 * op)

        if self._ripples:
            layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            cx, cy = hot + fx, hot + fy
            for r in self._ripples:
                p = r["t"] / r["dur"]
                rad = (5.0 + 22.0 * ease_out_cubic(p)) * self.u * ss * (0.8 + 0.2 * r["k"])
                a = int(255 * 0.65 * (1 - p) ** 1.6 * op)
                w = max(1, int(round((2.4 - 1.5 * p) * ss * self.u)))
                col = tuple(int(v) for v in r["color"])
                d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col + (a // 6,), outline=col + (a,), width=w)
            img = Image.alpha_composite(img, layer)

        small = img.resize((sp.canvas, sp.canvas), Image.BOX)
        r_, g_, b_, a_ = small.convert("RGBa").split()
        bgra = Image.merge("RGBA", (b_, g_, r_, a_)).tobytes()
        return Frame(wx - sp.hot, wy - sp.hot, sp.canvas, sp.canvas, bgra, op > 0.01)

    def render_image(self) -> Image.Image:
        """Straight-alpha RGBA preview of the current frame (tests / docs)."""
        f = self.render()
        b, g, r, a = Image.frombytes("RGBA", (f.w, f.h), f.bgra).split()
        premult_rgba = Image.merge("RGBA", (r, g, b, a)).tobytes()
        return Image.frombytes("RGBa", (f.w, f.h), premult_rgba).convert("RGBA")
