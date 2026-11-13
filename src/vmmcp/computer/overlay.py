"""overlay.py — the on-screen AI cursor: a click-through, always-on-top layered window.

One thread owns the window AND the animation loop:

    message pump -> run queued commands -> engine.step(dt) -> engine.render() -> UpdateLayeredWindow

Other threads talk to it only through `call()` / `call_wait()` / `glide()` (a queue),
so no locks are needed.  Per-pixel alpha comes from a 32-bit premultiplied DIB.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
import threading
import time
from typing import Callable, Optional

from .cursor_engine import CursorEngine

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm")

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 2, 0, 1
WM_DESTROY, WM_NCHITTEST, WM_QUIT = 0x0002, 0x0084, 0x0012
PM_REMOVE = 1
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
HTTRANSPARENT = -1

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
CLASS_NAME = "VmMcpAiCursor"


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HANDLE),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HANDLE), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wt.ATOM
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HANDLE, wt.HINSTANCE, wt.LPVOID]
user32.CreateWindowExW.restype = wt.HWND
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.UpdateLayeredWindow.argtypes = [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                       ctypes.POINTER(wt.POINT), wt.DWORD, ctypes.POINTER(BLENDFUNCTION), wt.DWORD]
user32.UpdateLayeredWindow.restype = wt.BOOL
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wt.HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
gdi32.SelectObject.restype = wt.HANDLE
gdi32.DeleteObject.argtypes = [wt.HANDLE]
gdi32.DeleteDC.argtypes = [wt.HDC]
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE

TARGET_FPS = 90.0


class Overlay:
    def __init__(self, ui_scale: float = 1.0, on_move: Optional[Callable[[int, int], None]] = None,
                 cursor_px: float = 19.0):
        self._ui_scale = ui_scale
        self._cursor_px = cursor_px
        self._on_move = on_move
        self._q: "queue.Queue[Callable[[CursorEngine], None]]" = queue.Queue()
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="ai-cursor-overlay", daemon=True)
        self.engine: Optional[CursorEngine] = None
        self.hwnd = None

    # ── public, thread-safe API ──────────────────────────────────────────────
    def start(self, timeout: float = 6.0) -> None:
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("AI cursor overlay did not start in time")
        if self._error:
            raise RuntimeError(f"AI cursor overlay failed to start: {self._error}")

    def stop(self) -> None:
        self._stop = True

    def call(self, fn: Callable[[CursorEngine], None]) -> None:
        """Run fn(engine) on the animation thread (fire and forget)."""
        self._q.put(fn)

    def call_wait(self, fn: Callable[[CursorEngine], object], timeout: float = 2.0):
        done, box = threading.Event(), {}

        def run(e: CursorEngine) -> None:
            try:
                box["v"] = fn(e)
            finally:
                done.set()

        self._q.put(run)
        done.wait(timeout)
        return box.get("v")

    def glide(self, x: int, y: int, duration: Optional[float] = None, timeout: float = 4.0) -> None:
        """Animate the cursor (and the real mouse) to (x, y); returns when it has arrived."""
        ev = threading.Event()
        self.call(lambda e: e.glide_to(x, y, duration, ev.set))
        ev.wait(timeout)

    # ── animation thread ─────────────────────────────────────────────────────
    def _run(self) -> None:
        try:
            self._create_window()
        except BaseException as exc:      # report to start()
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        winmm.timeBeginPeriod(1)
        try:
            self._loop()
        finally:
            winmm.timeEndPeriod(1)
            self._destroy()

    def _create_window(self) -> None:
        self._wndproc = WNDPROC(self._on_msg)             # keep a reference or it gets garbage-collected
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._wndproc, hinst, CLASS_NAME
        user32.RegisterClassW(ctypes.byref(wc))           # re-registering after a crash just fails harmlessly

        self.engine = CursorEngine(self._ui_scale, self._on_move, self._cursor_px)
        n = self.engine.sp.canvas
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            CLASS_NAME, "vm-mcp AI cursor", WS_POPUP, 0, 0, n, n, None, None, hinst, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowExW failed: {ctypes.get_last_error()}")

        self._screen_dc = user32.GetDC(None)
        self._mem_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = n, -n          # negative = top-down
        bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        self._bitmap = gdi32.CreateDIBSection(self._mem_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not self._bitmap or not bits:
            raise OSError("CreateDIBSection failed")
        gdi32.SelectObject(self._mem_dc, self._bitmap)
        self._bits, self._n, self._shown = bits, n, False
        self._blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

    def _on_msg(self, hwnd, msg, wparam, lparam):
        if msg == WM_NCHITTEST:
            return HTTRANSPARENT
        if msg == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _loop(self) -> None:
        eng = self.engine
        msg = wt.MSG()
        last = time.perf_counter()
        next_top = last + 1.0
        first = True
        frame_time = 1.0 / TARGET_FPS
        while not self._stop:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            while True:
                try:
                    self._q.get_nowait()(eng)
                except queue.Empty:
                    break
                except Exception:      # a bad command must never kill the animation loop
                    pass
            now = time.perf_counter()
            need = eng.step(now - last)
            last = now
            if need or first:
                self._blit(eng.render())
                first = False
            if now >= next_top:        # other topmost windows (e.g. taskbar) can steal the top spot
                user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                next_top = now + 1.0
            elapsed = time.perf_counter() - now
            time.sleep(max(0.0, frame_time - elapsed) if need else 0.012)

    def _blit(self, f) -> None:
        if not f.visible:
            if self._shown:
                user32.ShowWindow(self.hwnd, SW_HIDE)
                self._shown = False
            return
        ctypes.memmove(self._bits, f.bgra, len(f.bgra))
        pos, size, src = wt.POINT(f.x, f.y), wt.SIZE(f.w, f.h), wt.POINT(0, 0)
        user32.UpdateLayeredWindow(self.hwnd, self._screen_dc, ctypes.byref(pos), ctypes.byref(size),
                                   self._mem_dc, ctypes.byref(src), 0, ctypes.byref(self._blend), ULW_ALPHA)
        if not self._shown:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self._shown = True

    def _destroy(self) -> None:
        try:
            user32.DestroyWindow(self.hwnd)
            gdi32.DeleteObject(self._bitmap)
            gdi32.DeleteDC(self._mem_dc)
            user32.ReleaseDC(None, self._screen_dc)
        except Exception:
            pass
