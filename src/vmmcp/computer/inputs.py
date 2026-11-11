"""inputs.py — real mouse & keyboard events via SendInput (Windows only).

Coordinates here are ABSOLUTE virtual-desktop pixels (top-left of the whole desktop
may be negative on multi-monitor setups).  actor.py converts from the agent-facing
space where (0,0) = top-left of the screenshot.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

from . import keys as K

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
BUTTON_FLAGS = {  # name -> (down, up)
    "left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040),
}
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.VkKeyScanW.argtypes = [wt.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype = wt.UINT


# ── Screen geometry ──────────────────────────────────────────────────────────
def virtual_rect() -> tuple[int, int, int, int]:
    """(left, top, width, height) of the whole desktop across monitors."""
    gm = user32.GetSystemMetrics
    return gm(76), gm(77), gm(78), gm(79)


def cursor_pos() -> tuple[int, int]:
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) != len(inputs):
        raise OSError(f"SendInput blocked (error {ctypes.get_last_error()}). "
                      "Is the desktop locked, or a UAC / secure prompt showing?")


# ── Mouse ────────────────────────────────────────────────────────────────────
def move_abs(x: int, y: int) -> None:
    """Move the real cursor to absolute virtual-desktop pixel (x, y)."""
    vx, vy, vw, vh = virtual_rect()
    nx = round((x - vx) * 65535 / max(1, vw - 1))
    ny = round((y - vy) * 65535 / max(1, vh - 1))
    mi = MOUSEINPUT(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, 0)
    _send(INPUT(INPUT_MOUSE, _U(mi=mi)))


def snap(x: int, y: int) -> None:
    """Exact final placement (SendInput normalisation can be off by a pixel)."""
    user32.SetCursorPos(int(x), int(y))


def button(name: str, down: bool) -> None:
    flags = BUTTON_FLAGS.get(name)
    if flags is None:
        raise ValueError(f"Unknown mouse button {name!r} (use left, right or middle)")
    mi = MOUSEINPUT(0, 0, 0, flags[0] if down else flags[1], 0, 0)
    _send(INPUT(INPUT_MOUSE, _U(mi=mi)))


def wheel(notches: int, horizontal: bool = False) -> None:
    """Positive = up / right, negative = down / left. One notch = 120 units."""
    mi = MOUSEINPUT(0, 0, (notches * 120) & 0xFFFFFFFF, MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, 0, 0)
    _send(INPUT(INPUT_MOUSE, _U(mi=mi)))


# ── Keyboard ─────────────────────────────────────────────────────────────────
def char_to_vk(ch: str):
    r = user32.VkKeyScanW(ch)
    if r == -1:
        return None
    return r & 0xFF, bool(r & 0x100)


def _key_input(vk: int, down: bool) -> INPUT:
    flags = (KEYEVENTF_EXTENDEDKEY if vk in K.EXTENDED else 0) | (0 if down else KEYEVENTF_KEYUP)
    scan = user32.MapVirtualKeyW(vk, 0)
    return INPUT(INPUT_KEYBOARD, _U(ki=KEYBDINPUT(vk, scan, flags, 0, 0)))


def key_event(vk: int, down: bool) -> None:
    _send(_key_input(vk, down))


def press_chord(chord: list[tuple[int, bool]], hold: float = 0.04) -> None:
    """Press keys in order, hold briefly, release in reverse. Always releases, even on error."""
    vks = [vk for vk, _ in chord]
    pressed: list[int] = []
    try:
        for vk in vks:
            key_event(vk, True)
            pressed.append(vk)
            time.sleep(0.012)
        time.sleep(hold)
    finally:
        for vk in reversed(pressed):
            key_event(vk, False)
            time.sleep(0.008)


def type_char(ch: str) -> None:
    """Type one character (Unicode-aware; newline/tab become Enter/Tab)."""
    if ch in "\r\n":
        press_chord([(K.VK["enter"], False)], hold=0.01)
        return
    if ch == "\t":
        press_chord([(K.VK["tab"], False)], hold=0.01)
        return
    units = ch.encode("utf-16-le")
    for i in range(0, len(units), 2):
        code = units[i] | (units[i + 1] << 8)
        down = INPUT(INPUT_KEYBOARD, _U(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0)))
        up = INPUT(INPUT_KEYBOARD, _U(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)))
        _send(down, up)


def release_all_modifiers() -> None:
    """Safety net: make sure no modifier is left logically stuck down."""
    for vk in (0x10, 0x11, 0x12, 0x5B):
        try:
            key_event(vk, False)
        except OSError:
            pass
