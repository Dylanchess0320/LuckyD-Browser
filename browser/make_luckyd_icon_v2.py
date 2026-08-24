"""Create a high-legibility LuckyD Browser v2 icon (PNG and Windows ICO)."""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S, SS = 512, 4
N = S * SS
OUT = Path(__file__).parent / "assets"


def circle(draw, cx, cy, r, fill):
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)


def main():
    # Saturated midnight tile, intentionally simple enough for small taskbar sizes.
    im = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    px = im.load()
    for y in range(N):
        for x in range(N):
            t = (x / N) * 0.40 + (y / N) * 0.60
            px[x, y] = (8 + int(17 * t), 18 + int(18 * t), 47 + int(55 * t), 255)
    mask = Image.new("L", (N, N), 0)
    ImageDraw.Draw(mask).rounded_rectangle((18, 18, N - 18, N - 18), radius=112, fill=255)
    im.putalpha(mask)

    # A restrained blue glow lends depth without muddying the mark.
    glow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    circle(gd, N * 0.50, N * 0.50, N * 0.31, (58, 105, 255, 88))
    glow = glow.filter(ImageFilter.GaussianBlur(92))
    im.alpha_composite(glow)
    d = ImageDraw.Draw(im)
    cx = cy = N // 2

    # Browser/orbit ring: cyan to violet arcs and a tiny gold "lucky" spark.
    box = (N * 0.18, N * 0.18, N * 0.82, N * 0.82)
    width = int(N * 0.057)
    d.arc(box, 202, 338, fill=(61, 221, 255, 255), width=width)
    d.arc(box, 342, 112, fill=(128, 104, 255, 255), width=width)
    d.arc(box, 116, 190, fill=(90, 151, 255, 255), width=width)
    for a, col in (
        (202, (61, 221, 255, 255)),
        (338, (61, 221, 255, 255)),
        (342, (128, 104, 255, 255)),
        (112, (128, 104, 255, 255)),
        (116, (90, 151, 255, 255)),
        (190, (90, 151, 255, 255)),
    ):
        r = N * 0.32
        rad = math.radians(a)
        circle(d, cx + r * math.cos(rad), cy + r * math.sin(rad), width / 2, col)

    # Distinctive L: a browser-tab-shaped upright joined to an arrow-like foot.
    white = (244, 248, 255, 255)
    d.rounded_rectangle((N * 0.325, N * 0.285, N * 0.445, N * 0.655), radius=N * 0.055, fill=white)
    d.rounded_rectangle((N * 0.395, N * 0.565, N * 0.690, N * 0.685), radius=N * 0.055, fill=white)
    # Cut the inner corner slightly to make it feel engineered, not font-derived.
    d.polygon(
        ((N * 0.445, N * 0.565), (N * 0.515, N * 0.565), (N * 0.445, N * 0.635)),
        fill=(31, 47, 94, 255),
    )
    # Four-point spark in the open orbit gap.
    sx, sy, r = N * 0.765, N * 0.215, N * 0.045
    d.polygon(
        (
            (sx, sy - r),
            (sx + r * 0.42, sy - r * 0.42),
            (sx + r, sy),
            (sx + r * 0.42, sy + r * 0.42),
            (sx, sy + r),
            (sx - r * 0.42, sy + r * 0.42),
            (sx - r, sy),
            (sx - r * 0.42, sy - r * 0.42),
        ),
        fill=(255, 205, 66, 255),
    )
    d.rounded_rectangle(
        (18, 18, N - 18, N - 18), radius=112, outline=(104, 137, 255, 150), width=int(N * 0.009)
    )

    png = im.resize((512, 512), Image.Resampling.LANCZOS)
    png.save(OUT / "luckyd-browser-icon-v2.png")
    png.save(
        OUT / "luckyd-browser-icon-v2.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
