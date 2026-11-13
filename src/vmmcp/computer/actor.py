"""actor.py — high-level actions: animate the cursor AND perform the real input.

Rule of thumb: every public method here = one thing an agent can ask for.  Each one
(1) glides the visible cursor (the real mouse follows it frame by frame),
(2) sets the cursor's colour/pose for the action, (3) injects real input, (4) returns a dict.

Coordinates in/out of this class are *real screen pixels relative to the desktop's
top-left corner* (i.e. the same origin as a screenshot).  Scaling to the agent's
screenshot space happens on the server side (client.py).
"""

from __future__ import annotations

import math
import random
import threading
import time
from typing import Optional

from . import capture, inputs, keys, uia, windows
from .overlay import Overlay

MAX_TYPE_CHARS = 8000


class Actor:
    def __init__(self, overlay: Overlay):
        self.ov = overlay
        self.lock = threading.RLock()        # one physical mouse/keyboard: serialise actions

    # ── coordinate helpers ───────────────────────────────────────────────────
    def _origin(self) -> tuple[int, int, int, int]:
        return inputs.virtual_rect()

    def _abs(self, x: float, y: float) -> tuple[int, int]:
        vx, vy, vw, vh = self._origin()
        return (int(max(0, min(vw - 1, round(x)))) + vx, int(max(0, min(vh - 1, round(y)))) + vy)

    def _rel(self, ax: int, ay: int) -> tuple[int, int]:
        vx, vy, _, _ = self._origin()
        return ax - vx, ay - vy

    def _sync_start(self) -> None:
        """If the real mouse was moved by someone else, start the animation from where it actually is."""
        rx, ry = inputs.cursor_pos()

        def fix(e):
            if math.hypot(e.x - rx, e.y - ry) > 3 and not e.busy():
                e.place(rx, ry)
        self.ov.call_wait(fix)

    def _glide(self, x: float, y: float, duration: Optional[float] = None) -> tuple[int, int]:
        ax, ay = self._abs(x, y)
        self._sync_start()
        self.ov.call(lambda e: e.fade_to(1.0))
        self.ov.glide(ax, ay, duration)
        inputs.snap(ax, ay)
        return ax, ay

    def _mode(self, name: str, hold: Optional[float] = 0.5, scroll_dir: int = 0) -> None:
        self.ov.call(lambda e: e.set_mode(name, hold=hold, scroll_dir=scroll_dir))

    def _press(self, down: bool) -> None:
        self.ov.call(lambda e: e.press(down))

    def _ripple(self) -> None:
        self.ov.call(lambda e: e.ripple())

    def pos(self) -> dict:
        x, y = self._rel(*inputs.cursor_pos())
        return {"x": x, "y": y}

    # ── actions ──────────────────────────────────────────────────────────────
    def move(self, x, y, duration=None) -> dict:
        with self.lock:
            self._glide(x, y, duration)
            return {"moved": True, **self.pos()}

    def click(self, x, y, button="left", count=1, modifiers=None) -> dict:
        with self.lock:
            count = max(1, min(int(count), 3))
            mods = [keys.resolve_key(m)[0] for m in (modifiers or [])]
            self._glide(x, y)
            self._mode({"right": "right_click", "middle": "click"}.get(button, "double_click" if count > 1 else "click"), hold=0.55)
            try:
                for vk in mods:
                    inputs.key_event(vk, True)
                for i in range(count):
                    self._press(True)
                    inputs.button(button, True)
                    time.sleep(0.055)
                    inputs.button(button, False)
                    self._press(False)
                    self._ripple()
                    if i < count - 1:
                        time.sleep(0.07)
                if count > 1:
                    self.ov.call(lambda e: e.spin())
            finally:
                for vk in reversed(mods):
                    inputs.key_event(vk, False)
            time.sleep(0.10)
            return {"clicked": True, "button": button, "count": count, **self.pos()}

    def drag(self, x1, y1, x2, y2, button="left", path=None, duration=None) -> dict:
        with self.lock:
            self._glide(x1, y1)
            self._mode("drag", hold=None)
            down = False
            try:
                self._press(True)
                time.sleep(0.09)                      # the visible "grab"
                inputs.button(button, True)
                down = True
                time.sleep(0.07)
                for (px, py) in (path or []) + [[x2, y2]]:
                    ax, ay = self._abs(px, py)
                    self.ov.glide(ax, ay, duration)
                inputs.snap(*self._abs(x2, y2))
                time.sleep(0.10)
            finally:
                if down:
                    inputs.button(button, False)
                self._press(False)
            self._ripple()
            self._mode("drag", hold=0.35)
            time.sleep(0.12)
            return {"dragged": True, **self.pos()}

    def hold(self, x, y, seconds=1.0, button="left") -> dict:
        with self.lock:
            seconds = max(0.05, min(float(seconds), 30.0))
            self._glide(x, y)
            self._mode("hold", hold=None)
            self._press(True)
            inputs.button(button, True)
            try:
                time.sleep(seconds)
            finally:
                inputs.button(button, False)
                self._press(False)
            self._ripple()
            self._mode("hold", hold=0.3)
            return {"held": True, "seconds": seconds, **self.pos()}

    def button(self, state, x=None, y=None, button="left") -> dict:
        with self.lock:
            if x is not None and y is not None:
                self._glide(x, y)
            if state == "down":
                self._mode("hold", hold=None)
                self._press(True)
                inputs.button(button, True)
            else:
                inputs.button(button, False)
                self._press(False)
                self._ripple()
                self._mode("hold", hold=0.3)
            return {"button": button, "state": state, **self.pos()}

    def scroll(self, x, y, direction="down", amount=3) -> dict:
        with self.lock:
            amount = max(1, min(int(amount), 50))
            horizontal = direction in ("left", "right")
            sign = 1 if direction in ("up", "right") else -1
            self._glide(x, y)
            self._mode("scroll", hold=0.5 + 0.05 * amount, scroll_dir=-sign if not horizontal else 0)
            for _ in range(amount):
                inputs.wheel(sign, horizontal)
                time.sleep(0.045)
            return {"scrolled": True, "direction": direction, "amount": amount, **self.pos()}

    def type_text(self, text, delay_ms=8, submit=False) -> dict:
        with self.lock:
            text = text[:MAX_TYPE_CHARS]
            self._mode("type", hold=None)
            try:
                for ch in text:
                    inputs.type_char(ch)
                    time.sleep(max(0.0, delay_ms / 1000.0) * random.uniform(0.6, 1.4))
                if submit:
                    inputs.press_chord([(keys.VK["enter"], False)])
            finally:
                self._mode("type", hold=0.35)
            return {"typed": len(text), "submitted": bool(submit)}

    def key(self, spec, repeat=1) -> dict:
        with self.lock:
            chords = keys.parse(spec, inputs.char_to_vk)
            self._mode("key", hold=None)
            try:
                for _ in range(max(1, min(int(repeat), 100))):
                    for chord in chords:
                        inputs.press_chord(chord)
                        time.sleep(0.03)
            finally:
                self._mode("key", hold=0.35)
            return {"pressed": spec, "repeat": repeat}

    def wait(self, seconds) -> dict:
        seconds = max(0.0, min(float(seconds), 60.0))
        self._mode("wait", hold=seconds + 0.2)
        time.sleep(seconds)
        return {"waited": seconds}

    def fail(self) -> None:
        """Visual 'that went wrong' cue."""
        self.ov.call(lambda e: (e.set_mode("error", hold=0.9), e.shake()))

    # ── perception ───────────────────────────────────────────────────────────
    def screenshot(self, region=None, out_width=None, label_scale=1.0, grid=False, hide_cursor=False,
                   fmt="auto", quality=78, settle=False) -> tuple[bytes, dict]:
        if hide_cursor:
            self.ov.call_wait(lambda e: (setattr(e, "opacity", 0.0), setattr(e, "opacity_target", 0.0)))
            time.sleep(0.06)
        try:
            img = capture.grab_settled() if settle else capture.grab()
        finally:
            if hide_cursor:
                self.ov.call(lambda e: e.fade_to(1.0))
        data, meta = capture.render(img, region, out_width, label_scale, grid, fmt, quality)
        meta["cursor"] = self.pos()
        return data, meta

    def info(self) -> dict:
        vx, vy, vw, vh = self._origin()
        fg = windows.foreground()
        wins = []
        for w in windows.list_windows()[:15]:
            wins.append({**w, "x": w["x"] - vx, "y": w["y"] - vy})
        return {"screen": {"width": vw, "height": vh, "origin": [vx, vy]}, "cursor": self.pos(),
                "foreground": fg["title"] if fg else None, "windows": wins}

    def focus(self, title="") -> dict:
        with self.lock:
            return windows.focus(title=title)

    def ui_tree(self, max_items=250) -> dict:
        vx, vy, _, _ = self._origin()
        fg = windows.foreground()
        items = uia.tree(hwnd=fg["hwnd"] if fg else 0, max_items=max_items)
        for it in items:
            it["x"] -= vx
            it["y"] -= vy
            it["cx"], it["cy"] = it["x"] + it["w"] // 2, it["y"] + it["h"] // 2
        return {"window": fg["title"] if fg else None, "elements": items}
