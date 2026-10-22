"""Swap in the new LuckyD neon-clover logo (from the OneDrive Desktop webp).

The source art is an opaque poster: the clover on a near-black background
with a colored glow spilling into it. The shipped assets must be truly
transparent (Dylan 2026-10-22: "green screen the logo so it is see
through"). Keying is chroma-key style, not a corner flood fill:

* the background is every dark pixel CONNECTED to the frame edge (the
  glow ring included — it ramps well above the old max-channel<=30
  flood-fill threshold, which is why the first key-out left an opaque
  dark square-ish halo around the mark);
* background alpha is proportional to brightness, so the poster's dark
  vignette vanishes and only the neon light spill survives, as a soft
  translucent glow — never an opaque plate;
* semi-transparent pixels are defringed (un-blended from the key color),
  so no black fringe survives on light surfaces;
* dark details ENCLOSED by the neon (leaf shading) are not connected to
  the edge and stay fully opaque.

Run from a checkout that only has the shipped PNG: the PNG kept the
poster's RGB, so it can be re-keyed directly (``--from-png``).
"""

from collections import deque
from pathlib import Path

from PIL import Image

SRC = Path(r"C:\Users\dylan\OneDrive\Desktop\New LuckyD server icon.webp")
ASSETS = Path(__file__).resolve().parent / "assets"

# Key color of the poster background (sampled from its corners).
_KEY = (10, 8, 13)
# A pixel counts as background-candidate when its max channel is at or
# below this — high enough to include the whole glow ring, low enough to
# stop at the neon tubes (their anti-aliased cores are far brighter).
_BG_MAX_CHANNEL = 130
# Background alpha ramp: at or below _RAMP_LO the pixel is fully
# transparent; at _BG_MAX_CHANNEL it keeps at most _RAMP_HI alpha (the
# glow reads as light, never as a solid).
_RAMP_LO = 26
_RAMP_HI = 170


def _defringe(r: int, g: int, b: int, a: int) -> tuple[int, int, int]:
    """Un-blend a semi-transparent pixel from the key color."""
    if a <= 0:
        return (0, 0, 0)
    if a >= 255:
        return (r, g, b)
    f = a / 255.0
    out = []
    for obs, key in zip((r, g, b), _KEY, strict=True):
        v = (obs - key * (1.0 - f)) / f
        out.append(int(min(255, max(0, round(v)))))
    return (out[0], out[1], out[2])


def transparent_background(img: Image.Image) -> Image.Image:
    """Chroma-key the connected dark background to true transparency."""
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()

    def maxch(x: int, y: int) -> int:
        r, g, b, _a = px[x, y]
        return max(r, g, b)

    cand = [[maxch(x, y) <= _BG_MAX_CHANNEL for x in range(w)] for y in range(h)]
    bg = [[False] * w for _ in range(h)]
    queue: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            if cand[y][x] and not bg[y][x]:
                bg[y][x] = True
                queue.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if cand[y][x] and not bg[y][x]:
                bg[y][x] = True
                queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and cand[ny][nx] and not bg[ny][nx]:
                bg[ny][nx] = True
                queue.append((nx, ny))

    span = _BG_MAX_CHANNEL - _RAMP_LO
    out = Image.new("RGBA", (w, h))
    op = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, _a = px[x, y]
            if bg[y][x]:
                d = max(r, g, b)
                a = 0 if d <= _RAMP_LO else int(_RAMP_HI * (d - _RAMP_LO) / span)
                op[x, y] = (*_defringe(r, g, b, a), a)
            else:
                op[x, y] = (r, g, b, 255)
    return out


def _audit(img: Image.Image) -> str:
    a = img.getchannel("A")
    hist = a.histogram()
    total = img.size[0] * img.size[1]
    w, h = img.size
    pts = [
        (0, 0),
        (w - 1, 0),
        (0, h - 1),
        (w - 1, h - 1),
        (w // 2, 0),
        (w // 2, h - 1),
        (0, h // 2),
        (w - 1, h // 2),
    ]
    corners = [a.getpixel(p) for p in pts]
    return (
        f"transparent {hist[0] / total * 100:.1f}%  "
        f"opaque {hist[255] / total * 100:.1f}%  "
        f"edge/corner alphas {corners}  opaque-bbox {a.getbbox()}"
    )


def main() -> None:
    import sys

    if "--from-png" in sys.argv or not SRC.exists():
        # Re-key from the shipped PNG: it still carries the poster's RGB.
        src = Image.open(ASSETS / "luckyd-browser-icon-v2.png").convert("RGBA")
        print("source: shipped PNG (re-key)")
    else:
        src = Image.open(SRC).convert("RGBA")
        print("source:", SRC)
    small = transparent_background(src.resize((512, 512), Image.Resampling.LANCZOS))

    # Keep the full-color PNG for in-app/alert art.
    small.save(ASSETS / "luckyd-browser-icon-v2.png")

    # Windows ICO: clamp to 256px (ICO max frame) and save multi-size frames.
    ico_src = (
        small if max(small.size) <= 256 else small.resize((256, 256), Image.Resampling.LANCZOS)
    )
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    ico_src.save(ASSETS / "professional_icon.ico", sizes=sizes)
    ico_src.save(ASSETS / "luckyd-browser-icon-v2.ico", sizes=sizes)
    # Legacy filenames some surfaces still point at (source-install script):
    # they carry the same current mark, never the old veiled comet art.
    ico_src.save(ASSETS / "icon.ico", sizes=sizes)
    ico_src.save(ASSETS / "icon.png")

    logo = Image.open(ASSETS / "luckyd-browser-icon-v2.png")
    print("png   :", logo.size, logo.mode, "|", _audit(logo))
    check = Image.open(ASSETS / "professional_icon.ico")
    print("ico   :", check.size, check.format)


if __name__ == "__main__":
    main()
