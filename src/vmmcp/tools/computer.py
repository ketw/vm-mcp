"""tools/computer.py — the computer-use toolset (14 tools). Thin: all real work is in vmmcp/computer/.

Look:    screenshot  screen_info  ui_tree
Act:     mouse_move  mouse_click  mouse_drag  mouse_hold  mouse_button  mouse_scroll
         keyboard_type  keyboard_key  window_focus  wait
Cursor:  cursor_control

Every coordinate an agent passes in or receives is in SCREENSHOT pixel space (see computer/client.py).
"""

from __future__ import annotations

import base64
from typing import Optional

from fastmcp.exceptions import ToolError
from fastmcp.utilities.types import Image

from ..computer import client
from ..computer.client import ComputerError
from ..config import AUTO_SCREENSHOT, SCREENSHOT_MAX_WIDTH
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


def _result(r: dict):
    """Action result; optionally with a fresh screenshot attached (VM_MCP_AUTO_SCREENSHOT=1)."""
    res = _pt(r, "x", "y")
    if not AUTO_SCREENSHOT:
        return res
    import time
    time.sleep(0.35)
    return [res, *_shot()]


def _shot(grid: bool = False, region=None, hide_cursor: bool = False, max_width: Optional[int] = None):
    w, _ = client.refresh_screen()
    if region is None and max_width:
        client.update_screen(w, client._screen[1], max_width)
    sc = client.scale()
    real_region = [int(round(v * sc)) for v in region] if region else None
    r = _call("screenshot", region=real_region, out_width=min(max_width or SCREENSHOT_MAX_WIDTH, w),
              label_scale=sc, grid=grid, hide_cursor=hide_cursor)
    full_w, full_h = r["screen_size"]
    shot_w, shot_h = round(full_w / sc), round(full_h / sc)
    cur = r["cursor"]
    note = (f"Screenshot space: {shot_w}x{shot_h}, origin top-left; AI cursor at "
            f"({client.to_shot(cur['x'])}, {client.to_shot(cur['y'])}). All tool coordinates use this space.")
    if region:
        note = (f"Zoomed view of region x={region[0]} y={region[1]} w={region[2]} h={region[3]}. " +
                ("Grid labels show full-screenshot coordinates. " if grid else "") + note)
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
    Look at the screen. Returns an image. Always do this before clicking and after acting.

    All tool coordinates are pixels of the latest FULL screenshot (origin top-left).

    Args:
        grid: Draw labelled coordinate lines (labels are in screenshot coordinates) for precise targeting.
        region: [x, y, width, height] in screenshot coordinates — returns a zoomed crop of that area.
                Coordinates stay in full-screenshot space, so a grid label read here is what you pass to mouse_click.
        hide_cursor: Hide the AI cursor overlay for this capture.
        max_width: Downscale full screenshots to at most this width (default 1920).
    """
    return _shot(grid, region, hide_cursor, max_width)


@mcp.tool()
def screen_info() -> dict:
    """
    Text-only snapshot: screen size, AI cursor position, front window title, and visible windows
    (title + position/size, front-most first). Cheap — use it to orient before a screenshot.
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
    Accessibility tree of the front window: buttons, fields, links, menu items with names and exact
    click centres (cx, cy). Works without vision and is more precise than eyeballing pixels.
    Slow on huge apps (browsers); lower max_items if it times out. Some apps (games, canvas UIs) expose nothing.

    Returns: {window, elements: [{name, type, x, y, w, h, cx, cy, enabled, focused, id}]}
    """
    r = _call("ui_tree", max_items=max(10, min(max_items, 800)), timeout=40)
    for e in r["elements"]:
        for k in ("x", "y", "w", "h", "cx", "cy"):
            e[k] = client.to_shot(e[k])
    return {"window": r["window"], "elements": r["elements"]}


# ═════════════════════════════ ACT: mouse ═══════════════════════════════════

@mcp.tool()
def mouse_move(x: int, y: int, duration: Optional[float] = None):
    """
    Glide the AI cursor (and the real mouse) to (x, y) in screenshot coordinates. Use for hover effects/tooltips.

    Args:
        duration: Seconds for the glide (default: automatic, ~0.2–0.9 s depending on distance).
    """
    return _result(_call("move", x=client.to_screen(x), y=client.to_screen(y), duration=duration))


@mcp.tool()
def mouse_click(x: int, y: int, button: str = "left", count: int = 1, modifiers: Optional[list[str]] = None):
    """
    Move to (x, y) and click.

    Args:
        button: left | right | middle.
        count: 1 = click, 2 = double-click, 3 = triple-click (select line/paragraph).
        modifiers: Keys held during the click, e.g. ["ctrl"], ["shift"], ["ctrl", "shift"].
    """
    return _result(_call("click", x=client.to_screen(x), y=client.to_screen(y), button=button, count=count,
                         modifiers=modifiers))


@mcp.tool()
def mouse_drag(x1: int, y1: int, x2: int, y2: int, button: str = "left",
               path: Optional[list[list[int]]] = None, duration: Optional[float] = None):
    """
    Press at (x1, y1), move to (x2, y2) with the button held, release. For moving files/windows, sliders,
    selecting text/regions, resizing.

    Args:
        path: Optional waypoints [[x, y], ...] to pass through before the end (for curved or constrained drags).
        duration: Seconds per segment (default automatic).
    """
    return _result(_call("drag", x1=client.to_screen(x1), y1=client.to_screen(y1), x2=client.to_screen(x2),
                         y2=client.to_screen(y2), button=button, duration=duration,
                         path=[[client.to_screen(px), client.to_screen(py)] for px, py in (path or [])]))


@mcp.tool()
def mouse_hold(x: int, y: int, seconds: float = 1.0, button: str = "left"):
    """Press and hold a mouse button at (x, y) for `seconds` (max 30), then release. For long-press / hold-to-confirm UIs."""
    return _result(_call("hold", x=client.to_screen(x), y=client.to_screen(y), seconds=seconds, button=button,
                         timeout=seconds + 15))


@mcp.tool()
def mouse_button(state: str, x: Optional[int] = None, y: Optional[int] = None, button: str = "left"):
    """
    Low-level: press ("down") or release ("up") a mouse button. Optionally glide to (x, y) first.
    Combine with mouse_move for custom gestures. ALWAYS release what you press.
    """
    if state not in ("down", "up"):
        raise ToolError("state must be 'down' or 'up'")
    kw = {} if x is None or y is None else {"x": client.to_screen(x), "y": client.to_screen(y)}
    return _result(_call("button", state=state, button=button, **kw))


@mcp.tool()
def mouse_scroll(x: int, y: int, direction: str = "down", amount: int = 3):
    """
    Scroll the wheel with the pointer at (x, y). direction: up | down | left | right. amount = wheel notches
    (≈3 lines each). Scroll the element under the pointer, so aim at the content you want to scroll.
    """
    return _result(_call("scroll", x=client.to_screen(x), y=client.to_screen(y), direction=direction, amount=amount))


# ═════════════════════════════ ACT: keyboard & windows ══════════════════════

@mcp.tool()
def keyboard_type(text: str, submit: bool = False, delay_ms: int = 8):
    """
    Type text into the focused control (Unicode-safe, newlines become Enter). Click the field first.

    Args:
        submit: Press Enter afterwards.
        delay_ms: Pause between characters (raise to ~30 for fussy apps).
    """
    return _result(_call("type", text=text, submit=submit, delay_ms=delay_ms, timeout=30 + len(text) * 0.05))


@mcp.tool()
def keyboard_key(keys: str, repeat: int = 1):
    """
    Press a key or shortcut. Examples: 'enter', 'esc', 'tab', 'ctrl+a', 'ctrl+shift+t', 'alt+f4', 'win+r',
    'f5', 'pagedown', 'ctrl+plus'. Separate chords with spaces to run in order: 'ctrl+a ctrl+c'.

    Args:
        repeat: Run the whole sequence this many times (max 100) — handy for arrow-key navigation.
    """
    return _result(_call("key", keys=keys, repeat=repeat))


@mcp.tool()
def window_focus(title: str):
    """Bring the window whose title contains `title` (case-insensitive) to the front, restoring it if minimized."""
    r = _call("focus", title=title)
    if not r.get("focused"):
        raise ToolError(r.get("error", "Could not focus that window (Windows may be blocking focus changes)."))
    return _result({"focused": True, "title": r["window"]["title"]})


@mcp.tool()
def wait(seconds: float = 1.0):
    """Pause (max 60 s) while the cursor shows a 'thinking' pulse. Use after launching apps or while pages load."""
    return _result(_call("wait", seconds=seconds, timeout=seconds + 15))


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
