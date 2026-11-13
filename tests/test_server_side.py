"""Server-side tests (no Windows, no fastmcp needed except the toolwrap test):
activity store, dashboard backend, Gate (auth + SSE connection tracking), settle-capture, tool wrapper.
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp()
os.environ.update(VM_MCP_API_KEY="k123", VM_MCP_ACTIVITY_DIR=os.path.join(_TMP, "act"), PROGRAMDATA=_TMP)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vmmcp import activity  # noqa: E402
from vmmcp.activity import Store  # noqa: E402
from vmmcp.security import Gate  # noqa: E402
from vmmcp.webui import app as webui  # noqa: E402


async def call(app, method="GET", path="/", headers=None, body=b"", query=b""):
    msgs = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(m):
        msgs.append(m)

    scope = {"type": "http", "method": method, "path": path, "query_string": query, "client": ("9.9.9.9", 1),
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]}
    await app(scope, receive, send)
    hdrs = {k.decode(): v.decode() for k, v in msgs[0]["headers"]}
    return msgs[0]["status"], hdrs, b"".join(m.get("body", b"") for m in msgs[1:])


def run(coro):
    return asyncio.run(coro)


class FakeImage:
    def __init__(self, data, fmt): self.data, self.format = data, fmt


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(os.path.join(_TMP, "store_" + str(time.time_ns())))

    def test_tool_lifecycle_with_image_and_redaction(self):
        i = self.s.begin("mouse_click", {"x": 1, "y": 2, "password": "hunter2", "text": "a" * 1000})
        ev = self.s.by_id[i]
        self.assertEqual(ev["status"], "running")
        self.assertEqual(ev["args"]["password"], "***")
        self.assertIn("+600 chars", ev["args"]["text"])
        from PIL import Image
        import io
        buf = io.BytesIO(); Image.new("RGB", (64, 32)).save(buf, "JPEG")
        self.s.end(i, True, [{"clicked": True}, FakeImage(buf.getvalue(), "jpeg"), "Screen 64x32"])
        ev = self.s.by_id[i]
        self.assertEqual((ev["status"], ev["images"][0]["w"], ev["images"][0]["h"]), ("ok", 64, 32))
        self.assertTrue(self.s.image(ev["images"][0]["id"])[0].startswith(b"\xff\xd8"))
        self.assertIn("clicked", ev["summary"])

    def test_persistence_and_running_calls_are_marked_failed_after_restart(self):
        i = self.s.begin("wait", {}); self.s.end(i, True, {"ok": 1})
        j = self.s.begin("shell_run", {"command": "x"})          # never finished (server died)
        self.s._persist(self.s.by_id[j])
        again = Store(self.s.dir)
        self.assertEqual(again.by_id[i]["status"], "ok")
        self.assertEqual(again.by_id[j]["status"], "error")
        self.assertGreater(again.begin("x", {}), j)                     # ids keep counting up

    def test_connection_counter(self):
        self.s.connection_opened("1.1.1.1"); self.assertEqual(self.s.active_connections, 1)
        self.s.connection_closed("1.1.1.1", 3.2, "stream cancelled (client dropped)")
        self.assertEqual(self.s.active_connections, 0)
        last = self.s.after(0)[-1]
        self.assertEqual((last["kind"], last["status"]), ("connection", "error"))


class GateAndDashboardTests(unittest.TestCase):
    def setUp(self):
        activity.store.__init__(os.path.join(_TMP, "gate_" + str(time.time_ns())))

        async def mcp_app(scope, receive, send):
            if scope["type"] != "http":
                return
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"mcp"})
        self.mcp_app = mcp_app
        self.gate = Gate(mcp_app, webui.ui_app)

    def test_mcp_needs_api_key_but_health_does_not(self):
        self.assertEqual(run(call(self.gate, path="/health"))[0], 200)
        self.assertEqual(run(call(self.gate, path="/sse"))[0], 401)
        self.assertEqual(run(call(self.gate, path="/messages/", headers={"X-Api-Key": "nope"}))[0], 401)
        st, _, body = run(call(self.gate, path="/messages/", headers={"X-Api-Key": "k123"}))
        self.assertEqual((st, body), (200, b"mcp"))
        self.assertTrue(any(e.get("event") == "reject" for e in activity.store.after(0)))

    def test_dashboard_login_cookie_and_api(self):
        st, _, html = run(call(self.gate, path="/ui/"))
        self.assertIn(b"API key", html)                                         # login form
        self.assertEqual(run(call(self.gate, path="/ui/api/events"))[0], 401)
        st, h, _ = run(call(self.gate, "POST", "/ui/login", body=b"key=wrong"))
        self.assertEqual((st, h["location"]), (303, "/ui/?bad=1"))
        st, h, _ = run(call(self.gate, "POST", "/ui/login", body=b"key=k123"))
        self.assertEqual(st, 303); cookie = h["set-cookie"].split(";")[0]
        self.assertIn("HttpOnly", h["set-cookie"])
        auth = {"cookie": cookie}
        st, _, html = run(call(self.gate, path="/ui/", headers=auth))
        self.assertEqual(st, 200); self.assertIn(b"What the AI sees", html); self.assertNotIn(b"{{VERSION}}", html)

        i = activity.store.begin("mouse_click", {"x": 10, "y": 20}); activity.store.end(i, True, {"clicked": True})
        st, _, body = run(call(self.gate, path="/ui/api/events", headers=auth, query=b"after=0"))
        ev = json.loads(body)["events"]
        self.assertEqual([e["tool"] for e in ev if e["kind"] == "tool"], ["mouse_click"])
        # polling again after the last id: finished calls are re-sent (refreshed) so 'running' rows complete
        last = json.loads(body)["last_id"]
        st, _, body = run(call(self.gate, path="/ui/api/events", headers=auth, query=f"after={last}".encode()))
        self.assertEqual(json.loads(body)["events"], [])
        self.assertEqual(run(call(self.gate, path="/ui/api/image/999999", headers=auth))[0], 404)

    def test_sse_stream_open_close_is_recorded_with_reason(self):
        async def stream(scope, receive, send):
            await asyncio.sleep(5)
        gate = Gate(stream, webui.ui_app)

        async def go():
            task = asyncio.create_task(call(gate, path="/sse", headers={"X-Api-Key": "k123"}))
            await asyncio.sleep(0.05)
            self.assertEqual(activity.store.active_connections, 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        run(go())
        self.assertEqual(activity.store.active_connections, 0)
        close = [e for e in activity.store.after(0) if e.get("event") == "close"][-1]
        self.assertEqual(close["status"], "error"); self.assertIn("cancelled", close["reason"])


class CaptureSettleTests(unittest.TestCase):
    def test_waits_for_screen_to_stop_changing(self):
        from PIL import Image
        from vmmcp.computer import capture
        frames = [Image.new("RGB", (400, 300), (i * 40, 0, 0)) for i in range(3)]   # changing...
        steady = Image.new("RGB", (400, 300), (10, 200, 10))
        seq = iter(frames + [steady] * 10)
        with mock.patch.object(capture, "grab", lambda: next(seq)):
            t0 = time.perf_counter()
            img = capture.grab_settled(min_wait=0.0, max_wait=3.0, interval=0.01)
        self.assertEqual(img.getpixel((5, 5)), (10, 200, 10))
        self.assertLess(time.perf_counter() - t0, 1.0)

    def test_gives_up_after_max_wait(self):
        from PIL import Image
        from vmmcp.computer import capture
        n = [0]
        def grab():
            n[0] += 40; return Image.new("RGB", (400, 300), (n[0] % 250, 0, 0))
        with mock.patch.object(capture, "grab", grab):
            t0 = time.perf_counter(); capture.grab_settled(min_wait=0.0, max_wait=0.3, interval=0.02)
        self.assertLess(time.perf_counter() - t0, 1.0)


try:
    import fastmcp  # noqa: F401
    HAVE_FASTMCP = True
except ImportError:
    HAVE_FASTMCP = False


@unittest.skipUnless(HAVE_FASTMCP, "needs fastmcp")
class ToolWrapTests(unittest.TestCase):
    def test_blocking_tool_does_not_freeze_event_loop(self):
        from vmmcp.toolwrap import wrap

        def slow(seconds: float = 0.5) -> dict:
            time.sleep(seconds); return {"thread": threading.current_thread().name}

        async def go():
            ticks = []
            async def beat():
                for _ in range(12):
                    ticks.append(time.perf_counter()); await asyncio.sleep(0.04)
            hb = asyncio.create_task(beat())
            r = await wrap(slow)(seconds=0.5)
            await hb
            return r, max(b - a for a, b in zip(ticks, ticks[1:]))
        r, gap = run(go())
        self.assertNotEqual(r["thread"], "MainThread")
        self.assertLess(gap, 0.2)                       # blocked-on-loop would be >= 0.5

    def test_timeout_returns_error_instead_of_hanging(self):
        from vmmcp import toolwrap
        from fastmcp.exceptions import ToolError
        with mock.patch.object(toolwrap, "TOOL_TIMEOUT", 0.2):
            async def go():
                with self.assertRaises(ToolError):
                    await toolwrap.wrap(lambda: time.sleep(1))()
            run(go())
        self.assertEqual(activity.store.after(0)[-1]["status"], "error")


class SkillTests(unittest.TestCase):
    def test_skill_copies_identical_and_has_frontmatter(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        a = open(os.path.join(root, "src", "vmmcp", "skill", "SKILL.md"), encoding="utf-8").read()
        b = open(os.path.join(root, "skills", "vm-mcp", "SKILL.md"), encoding="utf-8").read()
        self.assertEqual(a, b, "run: cp src/vmmcp/skill/SKILL.md skills/vm-mcp/SKILL.md")
        self.assertTrue(a.startswith("---\nname: vm-mcp\ndescription:"))
        self.assertIn("screenshot_after", a)


try:
    import httpx  # noqa: F401
    HAVE_ALL = HAVE_FASTMCP
except ImportError:
    HAVE_ALL = False


@unittest.skipUnless(HAVE_ALL, "needs fastmcp + httpx")
class ToolSurfaceTests(unittest.TestCase):
    ACT = ["mouse_move", "mouse_click", "mouse_drag", "mouse_hold", "mouse_button", "mouse_scroll",
           "keyboard_type", "keyboard_key", "window_focus", "wait"]

    def test_every_action_tool_can_return_a_screenshot(self):
        import inspect
        from vmmcp import tools  # noqa: F401
        from vmmcp.mcp_instance import mcp
        registry = getattr(mcp, "tools", None)
        if registry is None:
            self.skipTest("real FastMCP: tool registry is async-only")
        for name in self.ACT:
            sig = inspect.signature(registry[name])
            self.assertIn("screenshot_after", sig.parameters, name)
            self.assertIs(sig.parameters["screenshot_after"].default, False, name)
            self.assertTrue(inspect.iscoroutinefunction(registry[name]), f"{name} must not block the event loop")
        self.assertEqual(len(registry), 67)
        self.assertEqual(len(set(registry)), len(registry))


if __name__ == "__main__":
    unittest.main()
