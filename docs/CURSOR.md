# The AI cursor

Soft, fully-rounded arrow in soft white (`#F3F5F9`) with a faint shadow and hairline so it reads on any
background. It glides along a gentle arc with eased speed, banks (tilts) with its velocity, squashes when a button
is pressed, ripples on click, flips on double-click, shakes on error, and **changes colour by action**:

| Action | Colour | Extra motion |
|---|---|---|
| idle / moving | soft white | tilts with speed |
| click | soft blue `#96C4FF` | press squash + ripple |
| right-click | soft violet `#C8ACFF` | leans right |
| double-click | soft teal `#8AE4D4` | flip spin + 2 ripples |
| drag / hold | soft amber `#FFD07E` | leans left, scaled down while held |
| scroll | soft sky `#92D8F0` | bobs with wheel direction |
| type / key | soft mint `#A8E8BE` | typing pulse |
| wait | soft lavender `#D8D2FF` | slow sway + glow breathing |
| error | soft coral `#FF9C92` | shake |

Files: `assets/cursor/cursor.svg` (vector master, hotspot noted in a comment), `cursor-states.png`, `preview.gif`.
The SVG and the on-screen sprite come from the same geometry (`cursor_art.py`), so they always match.

## Size
Height matches the stock Windows 11 arrow: **19 px at 100% display scale** (≈16 px wide — your reference shape is a bit
fatter than the stock arrow). It follows Windows' own scaling: display scale (125%, 150% …) and
Settings → Accessibility → *Mouse pointer size*. Override the base with `VM_MCP_CURSOR_PX` (e.g. `16` smaller, `24` bigger).

## Restyle
* Colours / glow / pose per action: `MODES` in `src/vmmcp/computer/cursor_engine.py`.
* Shape: `VERTS` (sharp polygon) and `CUTS` (how much each corner is rounded) in `cursor_art.py`.
* Speed: `glide_duration()` in `motion.py`; bounce: `Spring(omega, zeta)` in `cursor_engine.py`.
* Then: `python scripts/make_cursor_assets.py` to refresh the SVG/PNG/GIF.
* Hide it: tool `cursor_control("hide")` (the AI still works, just invisibly).
