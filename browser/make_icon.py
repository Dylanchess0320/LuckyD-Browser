"""Generate the LuckyD Browser icon.

Modern flat design: a dark navy rounded tile with a subtle violet glow, a
three-color orbit ring (blue -> purple -> green) around a browser globe,
and a lucky yellow spark in the ring's open gap. Clean at every size
(16 px favicon through 256 px taskbar tile).

Usage:  python make_icon.py   (writes assets/icon.png + assets/icon.ico)
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

SS = 4  # supersample factor for crisp edges
SIZE = 256
BIG = SIZE * SS
ASSETS = Path(__file__).resolve().parent / "assets"

# brand palette (matches dashboard: blue #5b9dff / purple #b46bff / green #34d399)
BG_TL = (0x07, 0x0A, 0x16)
BG_TR = (0x0C, 0x13, 0x26)
BG_BL = (0x14, 0x1B, 0x3E)
BG_BR = (0x3A, 0x22, 0x6E)
BLUE = (0x5B, 0x9D, 0xFF)
PURPLE = (0xC2, 0x7B, 0xFF)
GREEN = (0x34, 0xD3, 0x99)
GOLD = (0xFF, 0xD6, 0x33)
WHITE = (0xF6, 0xF9, 0xFF)

CX = CY = BIG // 2
GLOBE_R = int(BIG * 0.300)  # orbit ring radius
RING_W = int(BIG * 0.062)  # ring thickness (thicker -> holds up at 16px favicon size)
CORE_R = int(BIG * 0.060)  # center dot radius
CORNER = int(BIG * 0.225)  # tile corner radius
PAD = int(BIG * 0.015)  # tile inset


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def tile_path():
    return [PAD, PAD, BIG - PAD, BIG - PAD]


def gradient(flip=False):
    tiny = Image.new("RGBA", (2, 2))
    if flip:
        tiny.putpixel((0, 0), BG_BL)
        tiny.putpixel((1, 0), BG_BR)
        tiny.putpixel((0, 1), BG_TL)
        tiny.putpixel((1, 1), BG_TR)
    else:
        tiny.putpixel((0, 0), BG_TL)
        tiny.putpixel((1, 0), BG_TR)
        tiny.putpixel((0, 1), BG_BL)
        tiny.putpixel((1, 1), BG_BR)
    return tiny.resize((BIG, BIG), Image.BICUBIC)


def tile_mask():
    mask = Image.new("L", (BIG, BIG), 0)
    ImageDraw.Draw(mask).rounded_rectangle(tile_path(), radius=CORNER, fill=255)
    return mask


def ring_glow(img):
    """Multipass radial glow that fades smoothly to the tile edge."""
    for i in range(26, 0, -1):
        r = int(BIG * 0.42 * (1 + i / 26))
        a = round(42 * (i / 26) ** 2.2)
        ImageDraw.Draw(img).ellipse(
            [CX - r, CY - r, CX + r, CY + r], fill=(PURPLE[0], PURPLE[1], PURPLE[2], a)
        )


def draw_ring(img):
    """Three-color orbit ring. PIL angles: 0 deg = 3 o'clock, clockwise."""
    d = ImageDraw.Draw(img)
    segs = [(40, 150, BLUE), (185, 300, PURPLE), (330, 400, GREEN)]
    for a0, a1, col in segs:
        d.arc(
            [CX - GLOBE_R, CY - GLOBE_R, CX + GLOBE_R, CY + GLOBE_R],
            start=a0,
            end=a1,
            fill=col,
            width=RING_W,
        )
        r = RING_W // 2
        for ang in (a0, a1):
            ra = math.radians(ang)
            x = CX + GLOBE_R * math.cos(ra)
            y = CY + GLOBE_R * math.sin(ra)
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)


def draw_globe(img):
    """One crisp outline + a single meridian. The old version layered two
    translucent circles plus two meridians, which anti-aliases into a grey
    smudge once downscaled to a 16px favicon -- this reads cleanly at any size."""
    d = ImageDraw.Draw(img)
    lw = max(3, int(BIG * 0.016))
    wr = int(GLOBE_R * 0.58)
    hr = int(GLOBE_R * 0.58)
    box = [CX - wr, CY - hr, CX + wr, CY + hr]
    d.ellipse(box, outline=(WHITE[0], WHITE[1], WHITE[2], 235), width=lw)
    # single meridian for a "globe" read without adding clutter
    d.ellipse(
        [CX - hr * 0.42, CY - hr, CX + hr * 0.42, CY + hr],
        outline=(WHITE[0], WHITE[1], WHITE[2], 140),
        width=max(2, lw - 2),
    )


def star4(img, cx, cy, radius, rot=0, color=GOLD):
    d = ImageDraw.Draw(img)
    inner = radius * 0.42
    pts = []
    for i in range(8):
        rad = radius if i % 2 == 0 else inner
        a = rot + math.pi * i / 4.0
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    d.polygon(pts, fill=(color[0], color[1], color[2], 255))


def gloss(img):
    """Subtle top sheen clipped to the tile for a glass feel."""
    top = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    td = ImageDraw.Draw(top)
    for i in range(int(BIG * 0.34)):
        a = round(26 * (1 - i / (BIG * 0.34)))
        td.line([(0, i), (BIG, i)], fill=(255, 255, 255, a))
    img.paste(top, (0, 0), tile_mask())


def main():
    img = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    img.paste(gradient(False), (0, 0), tile_mask())
    ring_glow(img)
    draw_ring(img)
    draw_globe(img)
    # center dot
    ImageDraw.Draw(img).ellipse(
        [CX - CORE_R, CY - CORE_R, CX + CORE_R, CY + CORE_R],
        fill=(GREEN[0], GREEN[1], GREEN[2], 255),
    )
    # lucky spark in the ring's open gap (upper right) -- soft glow first so
    # the star reads as a glint of light, not a flat gold dot
    spark_x = int(CX + GLOBE_R * 0.74)
    spark_y = int(CY - GLOBE_R * 0.74)
    for i in range(10, 0, -1):
        r = int(BIG * 0.09 * (i / 10))
        a = round(70 * (i / 10) ** 2)
        ImageDraw.Draw(img).ellipse(
            [spark_x - r, spark_y - r, spark_x + r, spark_y + r],
            fill=(GOLD[0], GOLD[1], GOLD[2], a),
        )
    star4(img, spark_x, spark_y, int(BIG * 0.058), rot=math.radians(-15))
    # tile border
    ImageDraw.Draw(img).rounded_rectangle(
        tile_path(), radius=CORNER, outline=(0x2A, 0x35, 0x66, 235), width=int(BIG * 0.008)
    )
    gloss(img)

    small = img.resize((SIZE, SIZE), Image.LANCZOS)
    small.save(ASSETS / "icon.png")
    small.save(
        ASSETS / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("icon written", ASSETS / "icon.png")


if __name__ == "__main__":
    main()
