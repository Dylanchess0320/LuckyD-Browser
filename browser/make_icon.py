import math
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
ASSETS = Path(__file__).resolve().parent / "assets"


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

top = (0x0F, 0x5A, 0x28)
bot = (0x06, 0x30, 0x14)
for y in range(SIZE):
    c = lerp(top, bot, y / SIZE)
    d.line([(0, y), (SIZE, y)], fill=(c[0], c[1], c[2], 255))

mask = Image.new("L", (SIZE, SIZE), 0)
ImageDraw.Draw(mask).rounded_rectangle([4, 4, SIZE - 5, SIZE - 5], radius=58, fill=255)
img.putalpha(mask)
d = ImageDraw.Draw(img)
d.rounded_rectangle([4, 4, SIZE - 5, SIZE - 5], radius=58, outline=(0x9C, 0xEF, 0x6E, 255), width=5)

CX, CY = 128, 118


def clover_leaf(d, ang, length, width, c0, c1, steps=40):
    ux, uy = math.cos(ang), math.sin(ang)
    px, py = -uy, ux
    left, right = [], []
    for i in range(steps + 1):
        t = i / steps
        dist = length * t
        prof = math.sin(math.pi * (t**0.85))
        w = width * prof
        bx, by = CX + ux * dist, CY + uy * dist
        left.append((bx + px * w, by + py * w))
        right.append((bx - px * w, by - py * w))
    poly = left + right[::-1]
    for i in range(steps):
        t = (i + 0.5) / steps
        col = lerp(c0, c1, t)
        quad = [poly[i], poly[i + 1], poly[2 * steps + 1 - (i + 1)], poly[2 * steps + 1 - i]]
        d.polygon(quad, fill=(col[0], col[1], col[2], 255))
    tx, ty = CX + ux * length, CY + uy * length
    r = max(2, int(width * 0.28))
    d.ellipse([tx - r, ty - r, tx + r, ty + r], fill=(c1[0], c1[1], c1[2], 255))


for ang in (225, 315, 45, 135):
    a = math.radians(ang)
    clover_leaf(d, a, 72, 26, (0x2E, 0x8B, 0x2E), (0x93, 0xE8, 0x4E))
    d.ellipse([CX - 5, CY - 5, CX + 5, CY + 5], fill=(0x2F, 0x7D, 0x2B, 255))

d.line([(CX, CY), (96, 214)], fill=(0x2E, 0x8B, 0x2E, 255), width=9)
d.line([(CX, CY), (96, 214)], fill=(0x6F, 0xD0, 0x3A, 255), width=4)


def horseshoe(
    d, cx, cy, radius, thick, gapdeg, rot, gold=(0xF5, 0xC1, 0x18), dark=(0xB8, 0x86, 0x0B)
):
    gap = math.radians(gapdeg)
    half = gap / 2.0
    steps = 40
    for i in range(steps):
        a0 = rot + half + (2 * math.pi - gap) * (i / steps)
        a1 = rot + half + (2 * math.pi - gap) * ((i + 1) / steps)
        t = (i + 0.5) / steps
        col = lerp(gold, dark, 0.5 + 0.5 * math.cos((t - 0.5) * math.pi))
        p0 = (cx + radius * math.cos(a0), cy + radius * math.sin(a0))
        p1 = (cx + radius * math.cos(a1), cy + radius * math.sin(a1))
        d.line([p0, p1], fill=(col[0], col[1], col[2], 255), width=thick)
    for a in (rot + half, rot - half):
        tx, ty = cx + radius * math.cos(a), cy + radius * math.sin(a)
        rr = thick // 2 + 1
        d.ellipse([tx - rr, ty - rr, tx + rr, ty + rr], fill=(dark[0], dark[1], dark[2], 255))


horseshoe(d, 66, 62, 26, 9, 95, math.radians(-90))


def star4(d, cx, cy, radius, rot, color=(0xFF, 0xD6, 0x33)):
    r = radius * 0.30
    pts = []
    for i in range(8):
        rad = radius if i % 2 == 0 else r
        a = rot + math.pi * i / 4.0
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    d.polygon(pts, fill=(color[0], color[1], color[2], 255))


star4(d, 200, 60, 17, 0)
star4(d, 213, 100, 9, math.radians(20))
star4(d, 185, 40, 7, math.radians(45))

d.ellipse([66, 172, 88, 192], fill=(0x20, 0x20, 0x20, 255))
d.ellipse([58, 184, 106, 216], fill=(0xD9, 0x30, 0x25, 255))
d.line([(82, 184), (82, 216)], fill=(0x1A, 0x1A, 0x1A, 255), width=3)
d.ellipse([66, 191, 73, 198], fill=(0x1A, 0x1A, 0x1A, 255))
d.ellipse([91, 191, 98, 198], fill=(0x1A, 0x1A, 0x1A, 255))
d.ellipse([70, 205, 76, 211], fill=(0x1A, 0x1A, 0x1A, 255))
d.ellipse([88, 205, 94, 211], fill=(0x1A, 0x1A, 0x1A, 255))

png_path = ASSETS / "icon.png"
img.save(png_path)
img.save(
    ASSETS / "icon.ico",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
print("icon written", png_path)
