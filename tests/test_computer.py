"""Platform-independent tests: key parsing, cursor engine, and the Actor driven against a fake
Windows layer (records every input event).  Run:  python -m unittest discover -s tests -v
"""

import math
import os
import queue
import sys
import threading
import time
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vmmcp.computer import keys as K  # noqa: E402
from vmmcp.computer.cursor_engine import MODES, CursorEngine  # noqa: E402
from vmmcp.computer import motion  # noqa: E402


class FakeInputs(types.ModuleType):
    """Stands in for vmmcp.computer.inputs."""

    def __init__(self):
        super().__init__("vmmcp.computer.inputs")
        self.events, self.pos = [], (0, 0)
        self.EXTENDED = K.EXTENDED

    def virtual_rect(self): return (0, 0, 1920, 1080)
    def cursor_pos(self): return self.pos
    def move_abs(self, x, y): self.pos = (x, y); self.events.append(("move", x, y))
    def snap(self, x, y): self.pos = (x, y); self.events.append(("snap", x, y))
    def button(self, name, down): self.events.append(("button", name, down))
    def wheel(self, n, h=False): self.events.append(("wheel", n, h))
    def key_event(self, vk, down): self.events.append(("key", vk, down))
    def press_chord(self, chord, hold=0.04): self.events.append(("chord", tuple(v for v, _ in chord)))
    def type_char(self, ch): self.events.append(("char", ch))
    def release_all_modifiers(self): pass
    def char_to_vk(self, ch): return None


class FakeOverlay:
    """Same threading model as the real Overlay, minus the window."""

    def __init__(self, on_move):
        self.engine = CursorEngine(1.0, on_move)
        self.engine.opacity = 1.0
        self._q = queue.Queue()
        self._stop = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        last = time.perf_counter()
        while not self._stop:
            while True:
                try: self._q.get_nowait()(self.engine)
                except queue.Empty: break
            now = time.perf_counter()
            if self.engine.step(now - last):
                self.engine.render()
            last = now
            time.sleep(1 / 120)

    def call(self, fn): self._q.put(fn)

    def call_wait(self, fn, timeout=2.0):
        done, box = threading.Event(), {}
        def run(e): box["v"] = fn(e); done.set()
        self._q.put(run); done.wait(timeout); return box.get("v")

    def glide(self, x, y, duration=None, timeout=4.0):
        ev = threading.Event()
        self.call(lambda e: e.glide_to(x, y, duration, ev.set)); ev.wait(timeout)


class KeyTests(unittest.TestCase):
    def test_chords(self):
        self.assertEqual(K.parse("ctrl+a ctrl+c"), [[(0x11, False), (0x41, False)], [(0x11, False), (0x43, False)]])
        self.assertEqual(K.parse("alt+F4")[0], [(0x12, False), (0x73, False)])
        self.assertEqual(K.parse("ctrl+shift+esc")[0][-1], (0x1B, False))

    def test_unknown(self):
        with self.assertRaises(K.KeyError_): K.parse("ctrl+banana")


class EngineTests(unittest.TestCase):
    def test_glide_hits_target_exactly_and_is_smooth(self):
        seen = []
        e = CursorEngine(1.0, lambda x, y: seen.append((x, y)))
        e.place(100, 100)
        done = []
        e.glide_to(900, 500, on_done=lambda: done.append(1))
        for _ in range(400):
            e.step(1 / 90)
            if done: break
        self.assertTrue(done)
        self.assertEqual(seen[-1], (900, 500))
        steps = [math.dist(a, b) for a, b in zip(seen, seen[1:])]
        self.assertLess(max(steps), 40)            # no teleporting between frames

    def test_every_mode_renders(self):
        for name in MODES:
            e = CursorEngine(); e.opacity = 1.0; e.set_mode(name)
            for _ in range(5): e.step(1 / 60)
            f = e.render()
            self.assertEqual(len(f.bgra), f.w * f.h * 4)
            self.assertTrue(f.visible)

    def test_mode_reverts_to_idle(self):
        e = CursorEngine(); e.set_mode("click", hold=0.2)
        for _ in range(30): e.step(1 / 60)
        self.assertEqual(e.mode, "idle")

    def test_hotspot_pixel_is_inside_shape(self):
        e = CursorEngine(); e.opacity = 1.0; e.place(500.0, 400.0)
        for _ in range(3): e.step(1 / 60)
        f = e.render(); im = e.render_image()
        hx, hy = 500 - f.x, 400 - f.y
        self.assertGreater(im.getpixel((hx + 2, hy + 4))[3], 200)   # just inside the tip is opaque
        self.assertEqual(im.getpixel((hx - 8, hy - 8))[3], 0)          # outside the tip is transparent

    def test_motion_duration_bounds(self):
        self.assertGreater(motion.glide_duration(10), 0.17)
        self.assertLess(motion.glide_duration(5000), 0.9)


class ActorTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeInputs()
        for name, mod in (("inputs", self.fake), ("windows", types.ModuleType("w")),
                          ("uia", types.ModuleType("u")), ("capture", types.ModuleType("c")),
                          ("overlay", types.ModuleType("o"))):
            sys.modules[f"vmmcp.computer.{name}"] = mod
        sys.modules["vmmcp.computer.overlay"].Overlay = FakeOverlay
        sys.modules.pop("vmmcp.computer.actor", None)
        import vmmcp.computer as pkg
        for name in ("inputs", "windows", "uia", "capture", "overlay"):
            setattr(pkg, name, sys.modules[f"vmmcp.computer.{name}"])
        from vmmcp.computer.actor import Actor
        self.ov = FakeOverlay(self.fake.move_abs)
        self.actor = Actor(self.ov)

    def buttons(self):
        return [e for e in self.fake.events if e[0] == "button"]

    def test_click_is_exact_and_ordered(self):
        r = self.actor.click(640, 360)
        self.assertEqual(self.fake.pos, (640, 360))
        self.assertEqual(self.buttons(), [("button", "left", True), ("button", "left", False)])
        self.assertEqual((r["x"], r["y"]), (640, 360))
        # the press happens AFTER arriving
        first_btn = next(i for i, e in enumerate(self.fake.events) if e[0] == "button")
        self.assertEqual(self.fake.events[first_btn - 1][:3], ("snap", 640, 360))

    def test_double_click_and_modifiers(self):
        self.actor.click(100, 100, count=2, modifiers=["ctrl"])
        self.assertEqual(len(self.buttons()), 4)
        keys = [e for e in self.fake.events if e[0] == "key"]
        self.assertEqual(keys[0], ("key", 0x11, True)); self.assertEqual(keys[-1], ("key", 0x11, False))

    def test_drag_holds_button_during_move_and_always_releases(self):
        self.actor.drag(200, 200, 800, 600)
        ev = self.fake.events
        down = ev.index(("button", "left", True)); up = ev.index(("button", "left", False))
        moves_between = [e for e in ev[down:up] if e[0] == "move"]
        self.assertGreater(len(moves_between), 10)
        self.assertEqual(self.fake.pos, (800, 600))

    def test_drag_releases_even_on_error(self):
        def boom(*a): raise OSError("blocked")
        orig = self.fake.snap
        self.fake.snap = lambda x, y: boom() if (x, y) == (50, 50) else orig(x, y)
        with self.assertRaises(OSError): self.actor.drag(10, 10, 50, 50)
        self.assertEqual(self.buttons()[-1], ("button", "left", False))

    def test_coordinates_are_clamped_to_screen(self):
        self.actor.move(99999, -50)
        self.assertEqual(self.fake.pos, (1919, 0))

    def test_type_and_key(self):
        self.actor.type_text("hi\n", delay_ms=0, submit=True)
        self.assertEqual([e[1] for e in self.fake.events if e[0] == "char"], ["h", "i", "\n"])
        self.actor.key("ctrl+a")
        self.assertIn(("chord", (0x11, 0x41)), self.fake.events)

    def test_scroll_directions(self):
        self.actor.scroll(300, 300, "down", 2); self.actor.scroll(300, 300, "right", 1)
        w = [e for e in self.fake.events if e[0] == "wheel"]
        self.assertEqual(w, [("wheel", -1, False), ("wheel", -1, False), ("wheel", 1, True)])


if __name__ == "__main__":
    unittest.main()
