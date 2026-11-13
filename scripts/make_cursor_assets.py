"""Regenerate assets/cursor/* from the real engine:  python scripts/make_cursor_assets.py
  cursor.svg         design master (vector)
  cursor-states.png  every action colour on light + dark
  preview.gif        a scripted demo: glide, click, double-click (flip), drag, scroll, type, wait, error
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from PIL import Image, ImageDraw
from vmmcp.computer import cursor_art
from vmmcp.computer.cursor_engine import CursorEngine

OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "cursor")
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, "cursor.svg"), "w", encoding="utf-8").write(cursor_art.svg_document())

# ── states sheet
names = ["idle", "move", "click", "right_click", "double_click", "drag", "scroll", "type", "wait", "error"]
cell = 104
sheet = Image.new("RGB", (cell * len(names), cell * 2))
for row, bg in enumerate([(247, 248, 251), (27, 31, 40)]):
    for i, n in enumerate(names):
        e = CursorEngine(); e.opacity = 1.0; e.place(200, 200); e.set_mode(n)
        e.press(n == "drag")
        for _ in range(60): e.step(1 / 60)
        if n in ("click", "right_click", "double_click"): e.ripple(); e.step(0.12)
        im = e.render_image().crop((14, 14, 14 + 76, 14 + 76))
        tile = Image.new("RGB", (cell, cell), bg); tile.paste(im, (14, 14), im)
        ImageDraw.Draw(tile).text((6, cell - 14), n, fill=(120, 126, 140))
        sheet.paste(tile, (i * cell, row * cell))
sheet.save(os.path.join(OUT, "cursor-states.png"))

# ── animated demo
W, H, FPS = 640, 360, 50
frames = []
e = CursorEngine(2.0); e.opacity = 1.0; e.place(80, 80)   # shown at 2x so it is visible in the README gif
e.on_move = lambda x, y: None
script = [("glide", 520, 90), ("click",), ("glide", 330, 250), ("dbl",), ("glide", 120, 280), ("drag", 520, 300),
          ("scroll",), ("type",), ("wait",), ("error",), ("glide", 90, 90)]
def bgimg():
    im = Image.new("RGB", (W, H), (244, 245, 248)); d = ImageDraw.Draw(im)
    for r in [(40, 40, 600, 70), (40, 100, 300, 320), (330, 100, 600, 320)]:
        d.rounded_rectangle(r, 10, fill=(255, 255, 255), outline=(222, 225, 232))
    return im
def snap():
    f = e.render_image(); im = bgimg(); b = e.sp.hot
    im.paste(f, (int(e.x) - b, int(e.y) - b), f); frames.append(im.convert("P", palette=Image.ADAPTIVE, colors=128))
def run(n):
    for _ in range(n): e.step(1 / FPS); snap()
for st in script:
    if st[0] == "glide":
        d = []; e.glide_to(st[1], st[2], on_done=lambda: d.append(1))
        while not d: e.step(1 / FPS); snap()
        run(4)
    elif st[0] == "click":  e.set_mode("click", hold=0.5); e.press(True); run(5); e.press(False); e.ripple(); run(28)
    elif st[0] == "dbl":    e.set_mode("double_click", hold=0.7); e.press(True); run(3); e.press(False); e.ripple(); e.spin(); run(10); e.ripple(); run(25)
    elif st[0] == "drag":
        e.set_mode("drag"); e.press(True); run(8)
        d = []; e.glide_to(st[1], st[2], on_done=lambda: d.append(1))
        while not d: e.step(1 / FPS); snap()
        e.press(False); e.ripple(); e.set_mode("drag", hold=0.3); run(20)
    elif st[0] == "scroll": e.set_mode("scroll", hold=0.9, scroll_dir=1); run(40)
    elif st[0] == "type":   e.set_mode("type", hold=0.9); run(40)
    elif st[0] == "wait":   e.set_mode("wait", hold=1.2); run(55)
    elif st[0] == "error":  e.set_mode("error", hold=0.9); e.shake(); run(40)
frames = frames[::2]
frames[0].save(os.path.join(OUT, "preview.gif"), save_all=True, append_images=frames[1:], duration=int(2000 / FPS), loop=0, disposal=2)
print("wrote", len(frames), "frames")
