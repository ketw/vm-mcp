"""windows.py — list and focus top-level windows (Windows only). Rects are absolute desktop px."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from typing import Optional

from .overlay import CLASS_NAME

user32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    dwmapi = ctypes.WinDLL("dwmapi")
except OSError:
    dwmapi = None

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetForegroundWindow.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]


def _text(fn, hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    fn(hwnd, buf, 256)
    return buf.value


def _rect(hwnd) -> tuple[int, int, int, int]:
    r = wt.RECT()
    if dwmapi and dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) == 0:   # visible frame bounds
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _cloaked(hwnd) -> bool:
    if not dwmapi:
        return False
    c = wt.BOOL(0)
    dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(c), ctypes.sizeof(c))
    return bool(c.value)


def list_windows(include_minimized: bool = True) -> list[dict]:
    """Visible titled windows, front-most first."""
    fg = user32.GetForegroundWindow()
    out: list[dict] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or _cloaked(hwnd):
            return True
        title = _text(user32.GetWindowTextW, hwnd)
        cls = _text(user32.GetClassNameW, hwnd)
        if not title or cls in (CLASS_NAME, "Progman", "Shell_TrayWnd"):
            return True
        x, y, w, h = _rect(hwnd)
        mini = bool(user32.IsIconic(hwnd))
        if (w <= 1 or h <= 1) and not mini:
            return True
        if mini and not include_minimized:
            return True
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append({"hwnd": int(hwnd or 0), "title": title, "x": x, "y": y, "w": w, "h": h,
                    "pid": pid.value, "minimized": mini, "foreground": hwnd == fg})
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def foreground() -> Optional[dict]:
    for w in list_windows():
        if w["foreground"]:
            return w
    return None


def focus(title: str = "", hwnd: int = 0) -> dict:
    """Bring a window to the front by (case-insensitive) title substring or handle."""
    target = None
    for w in list_windows():
        if hwnd and w["hwnd"] == hwnd:
            target = w
            break
        if title and title.lower() in w["title"].lower():
            target = w
            break
    if not target:
        return {"focused": False, "error": f"No visible window matches {title or hwnd!r}"}
    h = wt.HWND(target["hwnd"])
    if target["minimized"]:
        user32.ShowWindow(h, 9)                       # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)                 # tap Alt: lifts Windows' foreground-lock restriction
    user32.keybd_event(0x12, 0, 2, 0)
    user32.BringWindowToTop(h)
    ok = bool(user32.SetForegroundWindow(h))
    time.sleep(0.15)
    return {"focused": ok, "window": target}
