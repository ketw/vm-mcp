"""tools/computer.py — the computer-use toolset (14 tools). Thin: all real work is in vmmcp/computer/.

Look:    screenshot  screen_info  ui_tree
Act:     mouse_move  mouse_click  mouse_drag  mouse_hold  mouse_button  mouse_scroll
         keyboard_type  keyboard_key  window_focus  wait
Cursor:  cursor_control

Every ACT tool takes `screenshot_after=True` to return the resulting screen in the same call
(waits until the screen stops changing). That replaces a separate screenshot() round-trip.
Every coordinate an agent passes in or receives is in SCREENSHOT pixel space (see computer/client.py).
"""

from __future__ import annotations

import base64
from typing import Optional

from fastmcp.exceptions import ToolError
from fastmcp.utilities.types import Image

from ..computer import client
from ..computer.client import ComputerError
from ..config import AUTO_SCREENSHOT, SCREENSHOT_JPEG_QUALITY, SCREENSHOT_MAX_WIDTH
from ..mcp_instance import mcp

def _call(action: str, **params) -> dict:
    try:
        return client.call(action, **params)
    except ComputerError as exc:
        raise ToolError(str(exc)) from None


def _pt(r: dict, *keys: str) -> dict:
    """Convert x/y fields in an agent reply to screenshot space."""
    out = dict(r)
    out.pop("ok", None)
    for k in keys:
        if k in out:
            out[k] = client.to_shot(out[k])
    return out


def _result(r: dict, screenshot_after: bool = False):
    """Action result, optionally followed by the settled screen (per call, or always via VM_MCP_AUTO_SCREENSHOT=1)."""
    res = _pt(r, "x", "y")
    if screenshot_after or AUTO_SCREENSHOT:
        return [res, *_shot(settle=True)]
    return res


def _shot(grid: bool = False, region=None, hide_cursor: bool = False, max_width: Optional[int] = None,
          settle: bool = False):
    w, h = client.refresh_screen()
    if region is None and max_width:
        client.update_screen(w, h, max_width)
    sc = client.scale()
    real_region = [int(round(v * sc)) for v in region] if region else None
    r = _call("screenshot", region=real_region, out_width=min(max_width or SCREENSHOT_MAX_WIDTH, w), label_scale=sc,
              grid=grid, hide_cursor=hide_cursor, settle=settle, quality=SCREENSHOT_JPEG_QUALITY)
    full_w, full_h = r["screen_size"]
    cur = r["cursor"]
    note = (f"Screen {round(full_w / sc)}x{round(full_h / sc)} (all coordinates use this space, origin top-left). "
            f"AI cursor at ({client.to_shot(cur['x'])}, {client.to_shot(cur['y'])}).")
    if region:
        note = (f"Zoomed view of region x={region[0]} y={region[1]} w={region[2]} h={region[3]}. " +
                ("Grid labels = full-screen coordinates. " if grid else "") + note)
    return [Image(data=base64.b64decode(r["image_b64"]), format=r["format"]), note]


# ═════════════════════════════ LOOK ═════════════════════════════════════════

@mcp.tool()
def screenshot(
    grid: bool = False,
    region: Optional[list[int]] = None,
    hide_cursor: bool = False,
    max_width: Optional[int] = None,
):
    """
    Look at the screen (returns an image). Do this once at the start of a GUI task; afterwards prefer
    `screenshot_after=True` on your action tools instead of calling this again.

    All tool coordinates are pixels of the latest FULL screenshot (origin top-left).

    Args:
        grid: Draw labelled coordinate lines (in screenshot coordinates) for precise targeting.
        region: [x, y, width, height] in screenshot coordinates — zoomed crop of that area. Coordinates stay in
                full-screenshot space, so a grid label read here is exactly what you pass to mouse_click.
        hide_cursor: Hide the AI cursor overlay for this capture.
        max_width: Downscale to at most this width (default 1920). Smaller = fewer tokens.
    """
    return _shot(grid, region, hide_cursor, max_width)


@mcp.tool()
def screen_info() -> dict:
    """
    Text-only snapshot (no image, very cheap): screen size, AI cursor position, front window title, and visible
    windows with position/size, front-most first. Use it to orient or to check which app is focused.
    """
    r = _call("info")
    sc = client.scale()
    s = r["screen"]
    wins = [{"title": w["title"], "x": client.to_shot(w["x"]), "y": client.to_shot(w["y"]),
             "w": client.to_shot(w["w"]), "h": client.to_shot(w["h"]),
             "minimized": w["minimized"], "front": w["foreground"]} for w in r["windows"]]
    return {"screenshot_size": [round(s["width"] / sc), round(s["height"] / sc)],
            "cursor": {"x": client.to_shot(r["cursor"]["x"]), "y": client.to_shot(r["cursor"]["y"])},
            "front_window": r["foreground"], "windows": wins}


@mcp.tool()
def ui_tree(max_items: int = 250) -> dict:
    """
    Accessibility tree of the front window: buttons, fields, links, menu items with names and exact click
    centres (cx, cy). Text-only and precise — best when you have no vision or a target is tiny.
    Slow on huge apps (browsers); lower max_items if it times out. Games/canvas UIs expose nothing.

    Returns: {window, elements: [{name, type, x, y, w, h, cx, cy, enabled, focused, id}]}
    """
    r = _call("ui_tree", max_items=max(10, min(max_items, 800)), timeout=40)
    for e in r["elements"]:
        for k in ("x", "y", "w", "h", "cx", "cy"):
            e[k] = client.to_shot(e[k])
    return {"window": r["window"], "elements": r["elements"]}


# ═════════════════════════════ ACT: mouse ═══════════════════════════════════

@mcp.tool()
def mouse_move(x: int, y: int, duration: Optional[float] = None, screenshot_after: bool = False):
    """
    Glide the AI cursor (and the real mouse) to (x, y). For hover effects / tooltips.

    Args:
        duration: Seconds for the glide (default automatic, ~0.2–0.9 s).
        screenshot_after: True = also return a screenshot of the RESULT in this same call.
    """
    return _result(_call("move", x=client.to_screen(x), y=client.to_screen(y), duration=duration), screenshot_after)


@mcp.tool()
def mouse_click(x: int, y: int, button: str = "left", count: int = 1, modifiers: Optional[list[str]] = None,
                screenshot_after: bool = False):
    """
    Move to (x, y) and click.

    Args:
        button: left | right | middle.
        count: 1 = click, 2 = double-click, 3 = triple-click (select line/paragraph).
        modifiers: Keys held during the click, e.g. ["ctrl"], ["shift"].
        screenshot_after: True = also return a screenshot of the RESULT in this same call (waits for the screen
            to settle). Use it whenever you would otherwise call screenshot() next — one call instead of two.
    """
    return _result(_call("click", x=client.to_screen(x), y=client.to_screen(y), button=button, count=count,
                         modifiers=modifiers), screenshot_after)


@mcp.tool()
def mouse_drag(x1: int, y1: int, x2: int, y2: int, button: str = "left",
               path: Optional[list[list[int]]] = None, duration: Optional[float] = None,
               screenshot_after: bool = False):
    """
    Press at (x1, y1), move to (x2, y2) with the button held, release. Moving files/windows, sliders,
    selecting text/regions, resizing.

    Args:
        path: Optional waypoints [[x, y], ...] passed through before the end (curved/constrained drags).
        duration: Seconds per segment (default automatic).
        screenshot_after: True = also return a screenshot of the RESULT in this same call.
    """
    return _result(_call("drag", x1=client.to_screen(x1), y1=client.to_screen(y1), x2=client.to_screen(x2),
                         y2=client.to_screen(y2), button=button, duration=duration,
                         path=[[client.to_screen(px), client.to_screen(py)] for px, py in (path or [])]),
                   screenshot_after)


@mcp.tool()
def mouse_hold(x: int, y: int, seconds: float = 1.0, button: str = "left", screenshot_after: bool = False):
    """
    Press and hold a mouse button at (x, y) for `seconds` (max 30), then release (long-press / hold-to-confirm).

    Args:
        screenshot_after: True = also return a screenshot of the RESULT in this same call.
    """
    return _result(_call("hold", x=client.to_screen(x), y=client.to_screen(y), seconds=seconds, button=button,
                         timeout=seconds + 15), screenshot_after)


@mcp.tool()
def mouse_button(state: str, x: Optional[int] = None, y: Optional[int] = None, button: str = "left",
                 screenshot_after: bool = False):
    """
    Low-level: press ("down") or release ("up") a mouse button, optionally gliding to (x, y) first.
    Combine with mouse_move for custom gestures. ALWAYS release what you press.

    Args:
        screenshot_after: True = also return a screenshot of the RESULT in this same call.
    """
    if state not in ("down", "up"):
        raise ToolError("state must be 'down' or 'up'")
    kw = {} if x is None or y is None else {"x": client.to_screen(x), "y": client.to_screen(y)}
    return _result(_call("button", state=state, button=button, **kw), screenshot_after)


@mcp.tool()
def mouse_scroll(x: int, y: int, direction: str = "down", amount: int = 3, screenshot_after: bool = False):
    """
    Scroll the wheel with the pointer at (x, y). direction: up | down | left | right; amount = wheel notches
    (≈3 lines each). The element under the pointer scrolls, so aim at the content.

    Args:
        screenshot_after: True = also return a screenshot of the RESULT in this same call.
    """
    return _result(_call("scroll", x=client.to_screen(x), y=client.to_screen(y), direction=direction,
                         amount=amount), screenshot_after)


# ═════════════════════════════ ACT: keyboard & windows ══════════════════════

@mcp.tool()
def keyboard_type(text: str, submit: bool = False, delay_ms: int = 8, screenshot_after: bool = False):
    """
    Type text into the focused control (Unicode-safe, newlines become Enter). Click the field first.

    Args:
        submit: Press Enter afterwards.
        delay_ms: Pause between characters (raise to ~30 for fussy apps).
        screenshot_after: True = also return a screenshot of the RESULT in this same call.
    """
    return _result(_call("type", text=text, submit=submit, delay_ms=delay_ms, timeout=30 + len(text) * 0.05),
                   screenshot_after)


@mcp.tool()
def keyboard_key(keys: str, repeat: int = 1, screenshot_after: bool = False):
    """
    Press a key or shortcut: 'enter', 'esc', 'tab', 'ctrl+a', 'ctrl+shift+t', 'alt+f4', 'win+r', 'f5',
    'pagedown', 'ctrl+plus'. Separate chords with spaces to run in order: 'ctrl+a ctrl+c'.

    Args:
        repeat: Run the whole sequence this many times (max 100) — e.g. arrow-key navigation.
        screenshot_after: True = also return a screenshot of the RESULT in this same call.
    """
    return _result(_call("key", keys=keys, repeat=repeat), screenshot_after)


@mcp.tool()
def window_focus(title: str, screenshot_after: bool = False):
    """
    Bring the window whose title contains `title` (case-insensitive) to the front, restoring it if minimized.

    Args:
        screenshot_after: True = also return a screenshot of the RESULT in this same call.
    """
    r = _call("focus", title=title)
    if not r.get("focused"):
        raise ToolError(r.get("error", "Could not focus that window (Windows may be blocking focus changes)."))
    return _result({"focused": True, "title": r["window"]["title"]}, screenshot_after)


@mcp.tool()
def wait(seconds: float = 1.0, screenshot_after: bool = False):
    """
    Pause (max 60 s) with a 'thinking' cursor pulse — after launching apps or while pages load.

    Args:
        screenshot_after: True = return a screenshot when the wait ends (replaces wait + screenshot).
    """
    return _result(_call("wait", seconds=seconds, timeout=seconds + 15), screenshot_after)


# ═════════════════════════════ CURSOR ═══════════════════════════════════════

@mcp.tool()
def cursor_control(action: str = "status") -> dict:
    """
    Manage the visible AI cursor overlay. action: status | show | hide | test.
    'test' plays the error shake so you can confirm the overlay is alive. The overlay starts automatically
    with the first computer-use call; the cursor still works (invisibly) when hidden.
    """
    if action not in ("status", "show", "hide", "test"):
        raise ToolError("action must be status, show, hide or test")
    r = _call("cursor", action=action)
    return {"visible": r["visible"], "x": client.to_shot(r["x"]), "y": client.to_shot(r["y"]), "ui_scale": r["ui_scale"]}
