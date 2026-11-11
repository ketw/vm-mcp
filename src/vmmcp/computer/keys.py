"""keys.py — turn "ctrl+shift+t" / "enter" / "ctrl+a ctrl+c" into virtual-key chords. Pure Python.

A *chord* is a list of keys pressed together (modifiers first) then released in reverse.
Several chords separated by spaces run one after another:  "ctrl+a ctrl+c".
Single printable characters are resolved by `char_to_vk` (user32.VkKeyScanW on Windows).
"""

from __future__ import annotations

from typing import Callable, Optional

MODIFIERS = {"ctrl", "shift", "alt", "win"}

VK: dict[str, int] = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "space": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E,
    "win": 0x5B, "menu": 0x5D, "numlock": 0x90, "scrolllock": 0x91,
    "volumemute": 0xAD, "volumedown": 0xAE, "volumeup": 0xAF,
    "medianext": 0xB0, "mediaprev": 0xB1, "mediastop": 0xB2, "mediaplay": 0xB3,
}
for _i in range(1, 25):
    VK[f"f{_i}"] = 0x6F + _i
for _i in range(10):
    VK[f"num{_i}"] = 0x60 + _i

ALIASES = {
    "return": "enter", "control": "ctrl", "ctl": "ctrl", "option": "alt", "opt": "alt", "meta": "win",
    "windows": "win", "super": "win", "cmd": "win", "command": "win", "escape": "esc", "del": "delete",
    "ins": "insert", "bksp": "backspace", "pgup": "pageup", "pgdn": "pagedown", "pgdown": "pagedown",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "caps": "capslock", "prtsc": "printscreen", "apps": "menu", "spacebar": "space",
    "plus": "+", "minus": "-", "comma": ",", "period": ".", "dot": ".", "slash": "/", "backslash": "\\",
    "semicolon": ";", "quote": "'", "equals": "=", "tilde": "`", "backtick": "`",
}

# keys that need KEYEVENTF_EXTENDEDKEY or they act as their numpad twin
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B, 0x5D, 0x6F, 0x90}

CharResolver = Callable[[str], Optional[tuple[int, bool]]]   # char -> (vk, needs_shift) or None


class KeyError_(ValueError):
    pass


def split_chords(spec: str) -> list[str]:
    return [c for c in spec.strip().split() if c]


def split_keys(chord: str) -> list[str]:
    """'ctrl+shift+t' -> ['ctrl','shift','t'];  'ctrl++' -> ['ctrl','+'];  '+' -> ['+']."""
    if chord == "+":
        return ["+"]
    parts = chord.split("+")
    out: list[str] = []
    i = 0
    while i < len(parts):
        if parts[i] == "" and i + 1 < len(parts) and parts[i + 1] == "":
            out.append("+")
            i += 2
        elif parts[i] == "" and i == len(parts) - 1 and out:
            out.append("+")
            i += 1
        elif parts[i] != "":
            out.append(parts[i])
            i += 1
        else:
            i += 1
    return out


def resolve_key(name: str, char_to_vk: Optional[CharResolver] = None) -> tuple[int, bool]:
    """-> (virtual key code, needs_shift_for_this_char). Raises KeyError_ if unknown."""
    n = name.strip().lower()
    n = ALIASES.get(n, n)
    if n in VK:
        return VK[n], False
    if len(n) == 1:
        ch = name.strip() if len(name.strip()) == 1 else n      # keep case for plain letters ('A' -> shift+a)
        if char_to_vk is not None:
            hit = char_to_vk(ch)
            if hit:
                return hit
        if ch.isalpha() and ch.isascii():
            return ord(ch.upper()), ch.isupper()
        if ch.isdigit():
            return ord(ch), False
    raise KeyError_(f"Unknown key {name!r}. Use names like enter, tab, esc, ctrl, shift, alt, win, f5, left, a, 7 …")


def parse(spec: str, char_to_vk: Optional[CharResolver] = None) -> list[list[tuple[int, bool]]]:
    """'ctrl+a ctrl+c' -> [[(0x11,False),(0x41,False)], [(0x11,False),(0x43,False)]]  (modifiers first)."""
    chords = []
    for chord in split_chords(spec):
        keys = [resolve_key(k, char_to_vk) for k in split_keys(chord)]
        if not keys:
            raise KeyError_(f"Empty key chord in {spec!r}")
        mods = [k for k in keys if k[0] in (0x10, 0x11, 0x12, 0x5B)]
        rest = [k for k in keys if k not in mods]
        if any(shift for _, shift in rest) and not any(vk == 0x10 for vk, _ in mods):
            mods.append((0x10, False))                      # e.g. "ctrl++" needs shift for '+'
        chords.append(mods + [(vk, False) for vk, _ in rest])
    if not chords:
        raise KeyError_("No keys given")
    return chords
