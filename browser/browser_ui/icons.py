"""Site letter-tile icons — offline favicon stand-ins with per-site identity.

Qt WebEngine has no public favicon database, and calling out to a favicon
service would leak every bookmark to a third party. Instead we mint a
rounded gradient tile from the site's initial, with the gradient hues derived
from the hostname hash — stable per site, instantly recognizable, 100% local.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap

_cache: dict[tuple[str, int], QIcon] = {}


def site_letter(url: str) -> str:
    """Display initial for a URL ('G' for google.com, '•' when unknown)."""
    try:
        host = urlsplit(url or "").hostname or ""
    except Exception:
        host = ""
    host = host.removeprefix("www.")
    return host[:1].upper() if host else "•"


def site_hues(url: str) -> tuple[int, int]:
    """Stable (hue, hue2) 0-359 pair derived from the hostname."""
    try:
        host = urlsplit(url or "").hostname or ""
    except Exception:
        host = ""
    hue = sum((i + 1) * ord(c) for i, c in enumerate(host)) % 360
    return hue, (hue + 48) % 360


def letter_tile(url: str, size: int = 16) -> QIcon:
    """Rounded gradient tile with the site's initial (cached per host+size)."""
    key = (urlsplit(url or "").hostname or "", size)
    icon = _cache.get(key)
    if icon is not None:
        return icon

    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    hue, hue2 = site_hues(url)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor.fromHsv(hue, 190, 235))
    grad.setColorAt(1, QColor.fromHsv(hue2, 210, 200))

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(grad)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(pix.rect().adjusted(0, 0, -1, -1), size * 0.3, size * 0.3)
    painter.setPen(QColor("#ffffff"))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(max(8, int(size * 0.6)))
    painter.setFont(font)
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, site_letter(url))
    painter.end()

    icon = QIcon(pix)
    _cache[key] = icon
    return icon
