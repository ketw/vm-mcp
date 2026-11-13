"""cursor_art.py — the AI cursor's shape. One definition, two outputs:

  * svg_document()  -> assets/cursor/cursor.svg   (design master, any SVG viewer)
  * Sprites         -> Pillow masks the overlay animates (shadow / glow / body / hairline)

Shape: a soft, fully-rounded arrow (left edge vertical, hypotenuse to the right,
small notch at the bottom), traced from the reference image: ~82 wide x 100 tall
after rounding.  Coordinates are design units where the visible shape is 100 tall.
"""

from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

# ── Geometry (design units; visible shape spans ~0..82 x 0..100) ───────────────────────────────────
VERTS = [(1.0, -12.0), (99.0, 70.0), (44.0, 70.0), (3.0, 117.0)]   # sharp-corner polygon: tip, right point, notch, bottom-left
CUTS = [30.0, 38.0, 8.0, 40.0]                                   # how far each corner is rounded off along its edges
SOFT_WHITE = (243, 245, 249)


def _unit(ax: float, ay: float, bx: float, by: float) -> tuple[float, float]:
    dx, dy = bx - ax, by - ay
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


def _corners():
    """For each vertex: (entry point, vertex, exit point)."""
    n = len(VERTS)
    out = []
    for i, (vx, vy) in enumerate(VERTS):
        px, py = VERTS[i - 1]
        qx, qy = VERTS[(i + 1) % n]
        ux, uy = _unit(vx, vy, px, py)
        wx, wy = _unit(vx, vy, qx, qy)
        c = CUTS[i]
        out.append(((vx + ux * c, vy + uy * c), (vx, vy), (vx + wx * c, vy + wy * c)))
    return out


def hotspot() -> tuple[float, float]:
    """Visible tip of the rounded corner = midpoint of its quadratic bezier."""
    a, v, b = _corners()[0]
    return ((a[0] + 2 * v[0] + b[0]) / 4, (a[1] + 2 * v[1] + b[1]) / 4)


def outline(steps: int = 10) -> list[tuple[float, float]]:
    """Flattened polygon of the rounded shape (design units)."""
    pts: list[tuple[float, float]] = []
    for a, v, b in _corners():
        for k in range(steps + 1):
            t = k / steps
            x = (1 - t) ** 2 * a[0] + 2 * (1 - t) * t * v[0] + t * t * b[0]
            y = (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * v[1] + t * t * b[1]
            pts.append((x, y))
    return pts


def svg_path_d() -> str:
    c = _corners()
    f = lambda p: f"{p[0]:.2f} {p[1]:.2f}"
    d = [f"M{f(c[0][2])}"]
    for a, v, b in c[1:] + c[:1]:
        d.append(f"L{f(a)} Q{f(v)} {f(b)}")
    d.append("Z")
    return " ".join(d)


def bounds() -> tuple[float, float, float, float]:
    pts = outline()
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def svg_document(fill: str = "#F3F5F9") -> str:
    hx, hy = hotspot()
    x0, y0, x1, y1 = bounds()
    m = 16
    vb = f"{x0 - m:.1f} {y0 - m:.1f} {x1 - x0 + 2 * m:.1f} {y1 - y0 + 2 * m + 4:.1f}"
    w, h = x1 - x0 + 2 * m, y1 - y0 + 2 * m + 4
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="{w * 1.6:.0f}" height="{h * 1.6:.0f}">
  <!-- vm-mcp AI cursor. Hotspot (the exact click point) is the tip: x={hx:.2f} y={hy:.2f} in these units. -->
  <defs>
    <filter id="soft" x="-40%" y="-30%" width="190%" height="180%">
      <feGaussianBlur in="SourceAlpha" stdDeviation="3.4"/>
      <feOffset dx="1.6" dy="3.6" result="b"/>
      <feComponentTransfer><feFuncA type="linear" slope="0.38"/></feComponentTransfer>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <path id="cursor" d="{svg_path_d()}" fill="{fill}" stroke="#1E2838" stroke-opacity="0.22" stroke-width="0.9" filter="url(#soft)"/>
  <circle id="hotspot" cx="{hx:.2f}" cy="{hy:.2f}" r="1.2" fill="none"/>
</svg>
"""


# ── Pillow sprites ───────────────────────────────────────────────────────────
class Sprites:
    """Pre-rendered 'L' masks on a canvas of size W x W (= canvas * ss pixels).

    The hotspot sits at (hot, hot) so rotating/scaling about it keeps the click
    point fixed.  Everything is rendered once; per frame the engine only applies
    an affine transform.
    """

    def __init__(self, ui_scale: float = 1.0, base_px: float = 19.0, canvas: int = 96, ss: int = 2):
        # base_px = visible arrow height at 100% scale. 19 px == the stock Windows 11 arrow.
        self.u = ui_scale
        self.ss = ss
        fx = base_px / 28.0                                  # effect sizes were designed for 28px; scale them down
        self.canvas = int(round(canvas * ui_scale))
        self.W = self.canvas * ss
        self.hot = int(round(self.canvas / 3))                # hotspot (logical px) inside the canvas
        k = base_px * ui_scale / 100.0 * ss               # design units -> sprite pixels
        hx, hy = hotspot()
        ox, oy = self.hot * ss, self.hot * ss

        # draw at 2x again for clean antialiasing, then LANCZOS down
        big = Image.new("L", (self.W * 2, self.W * 2), 0)
        pts = [((ox + (x - hx) * k) * 2, (oy + (y - hy) * k) * 2) for x, y in outline()]
        ImageDraw.Draw(big).polygon(pts, fill=255)
        self.body = big.resize((self.W, self.W), Image.LANCZOS)

        edge_px = max(3, int(round(1.2 * ss * ui_scale)) | 1)
        self.edge = ImageChops.subtract(self.body, self.body.filter(ImageFilter.MinFilter(edge_px)))

        off = (int(round(1.0 * fx * ss * ui_scale)), int(round(2.2 * fx * ss * ui_scale)))
        sh = Image.new("L", (self.W, self.W), 0)
        sh.paste(self.body, off)
        self.shadow = sh.filter(ImageFilter.GaussianBlur(max(1.0, 3.0 * fx * ss * ui_scale)))

        halo = self.body.filter(ImageFilter.MaxFilter(max(3, int(round(2 * ss * ui_scale)) | 1)))
        self.glow = halo.filter(ImageFilter.GaussianBlur(max(1.5, 5.5 * fx * ss * ui_scale)))
