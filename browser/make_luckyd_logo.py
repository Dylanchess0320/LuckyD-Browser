"""Swap in the new LuckyD neon-clover logo (from the OneDrive Desktop webp).

The source webp has an OPAQUE near-black background. The shipped assets
must be transparent (Dylan 2026-10-22: "my luckyd logo can have a see
through background so it doesnt look ugly"), so the background is keyed
out here: near-black pixels connected to the frame edge become alpha 0,
with a feathered edge; the neon artwork itself is untouched. The same
step produced the currently shipped assets from the repo PNG when the
webp wasn't available.
"""

from collections import deque
from pathlib import Path

from PIL import Image

SRC = Path(r"C:\Users\dylan\OneDrive\Desktop\New LuckyD server icon.webp")
ASSETS = Path(__file__).resolve().parent / "assets"

# Background = pixels at or below this max-channel value, flood-filled
# from the frame edge so dark details INSIDE the artwork stay opaque.
_BG_MAX_CHANNEL = 30


def transparent_background(img: Image.Image) -> Image.Image:
    """Key out the connected near-black background to alpha 0."""
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()

    def maxch(x: int, y: int) -> int:
        r, g, b, _ = px[x, y]
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

    out = Image.new("RGBA", (w, h))
    op = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, _ = px[x, y]
            if bg[y][x]:
                # Feather: pixels near the threshold keep a whisper of alpha.
                a = min(int(max(0, maxch(x, y) - 18) * 6), 90)
                op[x, y] = (r, g, b, a)
            else:
                op[x, y] = (r, g, b, 255)
    return out


src = Image.open(SRC).convert("RGBA")
small = transparent_background(src.resize((512, 512), Image.Resampling.LANCZOS))

# Keep the full-color PNG for in-app/alert art.
small.save(ASSETS / "luckyd-browser-icon-v2.png")

# Windows ICO: clamp to 256px (ICO max frame) and save multi-size frames.
ico_src = small if max(small.size) <= 256 else small.resize((256, 256), Image.Resampling.LANCZOS)
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
ico_src.save(ASSETS / "professional_icon.ico", sizes=sizes)
ico_src.save(ASSETS / "luckyd-browser-icon-v2.ico", sizes=sizes)

logo = Image.open(ASSETS / "luckyd-browser-icon-v2.png")
check = Image.open(ASSETS / "professional_icon.ico")
print("source:", src.size, src.mode)
print("png   :", logo.size, logo.mode, "corner alpha:", logo.getpixel((0, 0))[3])
print("ico   :", check.size, check.format)
