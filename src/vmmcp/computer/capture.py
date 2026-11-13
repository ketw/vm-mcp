"""capture.py — see the screen: grab, crop/zoom, coordinate grid, encode.

Coordinate contract (important, see docs/ARCHITECTURE.md):
  * The agent-facing coordinate space is the pixel space of a FULL screenshot returned by
    the `screenshot` tool, with (0,0) at its top-left.
  * Internally this module works in real screen pixels and receives `label_scale`
    (= screen px per screenshot px) so grid labels are printed in the agent's space.
"""

from __future__ import annotations

import io
import time
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

NICE_STEPS = [5, 10, 20, 25, 50, 100, 200, 250, 500, 1000]


def grab() -> Image.Image:
    """Whole virtual desktop as RGB, including layered windows (our cursor overlay)."""
    from PIL import ImageGrab
    return ImageGrab.grab(all_screens=True, include_layered_windows=True).convert("RGB")


def grab_settled(min_wait: float = 0.18, max_wait: float = 1.6, interval: float = 0.12) -> Image.Image:
    """Grab once the screen stops changing (menus/pages finished animating) — or after max_wait.

    Used for 'screenshot after an action': the picture then shows the RESULT, not a half-drawn transition.
    """
    from PIL import ImageChops, ImageStat
    t0 = time.perf_counter()
    time.sleep(min_wait)
    prev = None
    while True:
        img = grab()
        small = img.resize((192, 108), Image.BILINEAR)
        if prev is not None and sum(ImageStat.Stat(ImageChops.difference(prev, small)).mean) < 0.8:
            return img
        if time.perf_counter() - t0 > max_wait:
            return img
        prev = small
        time.sleep(interval)


def _font(size: int):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def pick_step(span_label_units: float, target_lines: int = 10) -> int:
    raw = span_label_units / target_lines
    for s in NICE_STEPS:
        if s >= raw:
            return s
    return NICE_STEPS[-1]


def draw_grid(img: Image.Image, origin: tuple[float, float], factor: float, label_scale: float) -> Image.Image:
    """Overlay labelled grid lines.

    origin       top-left of `img` in real screen px
    factor       output px per real screen px
    label_scale  real screen px per label unit (labels are real_px / label_scale)
    """
    w, h = img.size
    ox, oy = origin
    span_x = w / factor / label_scale
    span_y = h / factor / label_scale
    step = pick_step(max(span_x, span_y))

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    font = _font(max(11, min(15, w // 90)))
    line_col, pill = (255, 64, 96, 95), (20, 24, 36, 205)

    def labels_between(lo_screen: float, hi_screen: float):
        lo_l, hi_l = lo_screen / label_scale, hi_screen / label_scale
        v = (int(lo_l // step) + 1) * step
        while v < hi_l:
            yield v
            v += step

    for v in labels_between(ox, ox + w / factor):
        x = (v * label_scale - ox) * factor
        d.line([(x, 0), (x, h)], fill=line_col, width=1)
    for v in labels_between(oy, oy + h / factor):
        y = (v * label_scale - oy) * factor
        d.line([(0, y), (w, y)], fill=line_col, width=1)

    def tag(text: str, x: float, y: float):
        l, t, r, b = d.textbbox((x, y), text, font=font)
        d.rounded_rectangle((l - 3, t - 1, r + 3, b + 2), radius=3, fill=pill)
        d.text((x, y), text, font=font, fill=(255, 255, 255, 255))

    for v in labels_between(ox, ox + w / factor):
        tag(str(int(v)), (v * label_scale - ox) * factor + 3, 2)
    for v in labels_between(oy, oy + h / factor):
        tag(str(int(v)), 2, (v * label_scale - oy) * factor + 2)
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def render(
    img: Image.Image,
    region: Optional[list[int]] = None,     # [x, y, w, h] in real screen px
    out_width: Optional[int] = None,        # full-screen output width (px)
    label_scale: float = 1.0,
    grid: bool = False,
    fmt: str = "auto",                      # auto | png | jpeg
    quality: int = 78,
    min_zoom_width: int = 900,
) -> tuple[bytes, dict]:
    full_w, full_h = img.size
    ox = oy = 0
    if region:
        x, y, w, h = [int(v) for v in region]
        x, y = max(0, min(x, full_w - 1)), max(0, min(y, full_h - 1))
        w, h = max(1, min(w, full_w - x)), max(1, min(h, full_h - y))
        img = img.crop((x, y, x + w, y + h))
        ox, oy = x, y
        zoom = max(1.0, min(4.0, min_zoom_width / w))
        factor = zoom
    else:
        out_w = min(out_width or full_w, full_w)
        factor = out_w / full_w
    if abs(factor - 1.0) > 1e-3:
        img = img.resize((max(1, round(img.width * factor)), max(1, round(img.height * factor))), Image.LANCZOS)
    if grid:
        img = draw_grid(img, (ox, oy), factor, label_scale)

    if fmt == "auto":
        fmt = "png" if img.width * img.height <= 400_000 else "jpeg"      # big PNGs bloat the SSE stream
    buf = io.BytesIO()
    if fmt == "jpeg":
        img.save(buf, format="JPEG", quality=quality)
    else:
        fmt = "png"
        img.save(buf, format="PNG")
    meta = {
        "format": fmt, "width": img.width, "height": img.height, "bytes": buf.tell(),
        "screen_size": [full_w, full_h],
        "region_real_px": [ox, oy, round(img.width / factor), round(img.height / factor)] if region else None,
    }
    return buf.getvalue(), meta
