"""Swap in the new LuckyD neon-clover logo (from the OneDrive Desktop webp)."""

from pathlib import Path

from PIL import Image

SRC = Path(r"C:\Users\dylan\OneDrive\Desktop\New LuckyD server icon.webp")
ASSETS = Path(__file__).resolve().parent / "assets"

src = Image.open(SRC).convert("RGBA")
small = src.resize((512, 512), Image.Resampling.LANCZOS)

# Keep the full-color PNG for in-app/alert art.
small.save(ASSETS / "luckyd-browser-icon-v2.png")

# Windows ICO: clamp to 256px (ICO max frame) and save multi-size frames.
ico_src = small if max(small.size) <= 256 else small.resize((256, 256), Image.Resampling.LANCZOS)
ico_src.save(
    ASSETS / "professional_icon.ico",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)

logo = Image.open(ASSETS / "luckyd-browser-icon-v2.png")
check = Image.open(ASSETS / "professional_icon.ico")
print("source:", src.size, src.mode)
print("png   :", logo.size, logo.mode)
print("ico   :", check.size, check.format)
