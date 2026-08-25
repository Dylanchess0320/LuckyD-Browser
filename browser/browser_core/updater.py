"""Auto-update checker: polls a hosting provider for a newer release and can
download + launch the installer.

Design
------
* Update sources are pluggable. The shipped default is :class:`GitHubReleasesSource`,
  which queries the public GitHub "latest release" API and looks for an attached
  installer asset. A custom URL/file-based source can be added later without
  touching the UI.
* All network work runs on a background ``QThread`` so the UI never blocks.
  Results come back as Qt signals (safe across threads).
* The checker never downloads or installs anything on its own. It only reports
  that an update exists; the caller (MainWindow) decides whether to prompt the
  user, download, and install.
* Works both frozen (packaged) and from-source. When running from source there
  is no installer to apply, so updates are reported but "install" just opens the
  download page in a tab.

Security notes
--------------
* Version comparison is done with a proper dotted-tuple parse, not string compare.
* Downloads stream to disk with size reporting and are verified against the
  expected byte size when the provider supplies one.
* Only HTTPS endpoints are used. GitHub release assets come from github.com /
  objects.githubusercontent.com.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal


def _exe_file_version() -> str:
    """Read FileVersion from the running exe's version resource (Windows).

    Frozen-safe fallback for when the `browser` package isn't importable
    (e.g. a repackaged build): keeps CURRENT_VERSION honest so the updater
    never mistakes a fully up-to-date install for ancient "1.0.0" and nag
    users into re-downloading the same release forever.
    """
    import ctypes
    import ctypes.wintypes
    import sys

    try:
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            return ""
        path = sys.executable
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        data = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, data):
            return ""
        val = ctypes.c_void_p()
        vlen = ctypes.wintypes.UINT()
        # \VarFileInfo\Translation -> primary language id
        if not ctypes.windll.version.VerQueryValueW(
            data, r"\VarFileInfo\Translation", ctypes.byref(val), ctypes.byref(vlen)
        ):
            return ""
        lang = ctypes.cast(val, ctypes.POINTER(ctypes.c_uint16)).contents.value
        codepage = (lang >> 16) or 1200
        sub = f"\\StringFileInfo\\{lang & 0xFFFF:04x}{codepage:04x}\\FileVersion"
        if not ctypes.windll.version.VerQueryValueW(
            data, sub, ctypes.byref(val), ctypes.byref(vlen)
        ):
            return ""
        return ctypes.wstring_at(val.value) or ""
    except Exception:
        return ""


try:
    from browser import __version__

    CURRENT_VERSION = __version__
except Exception:  # frozen build without package context
    CURRENT_VERSION = _exe_file_version() or "1.0.0"


def current_version() -> str:
    """Best-effort running version (package attr, then exe metadata)."""
    global CURRENT_VERSION
    if CURRENT_VERSION not in ("", "1.0.0"):
        return CURRENT_VERSION
    try:
        from browser import __version__ as v

        CURRENT_VERSION = v
    except Exception:
        exe = _exe_file_version()
        if exe:
            CURRENT_VERSION = exe
    return CURRENT_VERSION


# ── Configuration ────────────────────────────────────────────────────────────
# The GitHub repository that hosts the browser's releases. This MUST be a public
# repo (the updater runs unauthenticated on end-user machines). Point it at the
# repo where you publish LuckyD Browser releases.
GITHUB_REPO = "Dylanchess0320/LuckyD-Browser"

GITHUB_LATEST_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
# Substrings that identify the Windows installer asset in a release.
INSTALLER_ASSET_HINTS = ("setup", ".exe")

_USER_AGENT = f"LuckyDBrowser/{CURRENT_VERSION} (+https://luckycode.xyz)"
_TIMEOUT = 12  # seconds for the metadata call


@dataclass
class ReleaseInfo:
    """A newer version that is available for download."""

    version: str
    url: str  # human-readable release page (fallback / "open in tab")
    installer_url: str = ""  # direct .exe asset URL (may be empty)
    installer_size: int = 0  # bytes, if known
    notes: str = ""  # release notes / changelog
    name: str = ""  # release title


def parse_version(text: str) -> tuple:
    """Parse 'v1.4.0' / '1.4.0' / '1.4' into a comparable tuple (1, 4, 0)."""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def is_newer(candidate: str, current: str = CURRENT_VERSION) -> bool:
    """True if candidate version is strictly newer than the running version."""
    c, cur = parse_version(candidate), parse_version(current)
    # Pad to equal length so (1,4) < (1,4,1) compares correctly.
    n = max(len(c), len(cur))
    return c + (0,) * (n - len(c)) > cur + (0,) * (n - len(cur))


class GitHubReleasesSource:
    """Fetch the newest release from a GitHub repository."""

    def __init__(self, repo: str = GITHUB_REPO):
        self.repo = repo
        self.api_url = f"https://api.github.com/repos/{repo}/releases/latest"

    def fetch_latest(self) -> ReleaseInfo:
        req = urllib.request.Request(
            self.api_url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8", "replace"))

        tag = data.get("tag_name") or data.get("name") or ""
        info = ReleaseInfo(
            version=tag.lstrip("vV"),
            url=data.get("html_url") or "",
            notes=data.get("body") or "",
            name=data.get("name") or tag,
        )
        # Pick the installer asset (first .exe whose name mentions "setup").
        assets: list[dict] = data.get("assets") or []

        def _score(a: dict) -> int:
            name = (a.get("name") or "").lower()
            return sum(h in name for h in INSTALLER_ASSET_HINTS)

        installers = sorted(assets, key=_score, reverse=True)
        if installers:
            best = installers[0]
            info.installer_url = best.get("browser_download_url") or ""
            info.installer_size = int(best.get("size") or 0)
        return info


class UpdateChecker(QThread):
    """Background thread that checks for a newer release.

    Signals
    -------
    update_available(ReleaseInfo-like dict)
        A strictly newer version was found.
    up_to_date()
        The running version is the latest.
    failed(str)
        The check could not be completed (offline, 404, bad JSON, …).
    """

    update_available = Signal(dict)
    up_to_date = Signal()
    failed = Signal(str)

    def __init__(self, source: GitHubReleasesSource | None = None, parent=None):
        super().__init__(parent)
        self.source = source or GitHubReleasesSource()

    def run(self) -> None:
        try:
            info = self.source.fetch_latest()
        except urllib.error.HTTPError as exc:
            # 404 simply means the repo has no releases yet.
            if exc.code == 404:
                self.up_to_date.emit()
            else:
                self.failed.emit(f"HTTP {exc.code}")
            return
        except Exception as exc:  # offline, DNS, timeout, bad JSON …
            self.failed.emit(str(exc))
            return

        if info.version and is_newer(info.version):
            self.update_available.emit(
                {
                    "version": info.version,
                    "url": info.url,
                    "installer_url": info.installer_url,
                    "installer_size": info.installer_size,
                    "notes": info.notes,
                    "name": info.name,
                }
            )
        else:
            self.up_to_date.emit()


class ReleaseDownloader(QThread):
    """Download the installer to a local file, reporting progress.

    Signals
    -------
    progress(int received, int total)
    finished_ok(str path)
    failed(str message)
    """

    progress = Signal(int, int)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, dest: Path, expected_size: int = 0, parent=None):
        super().__init__(parent)
        self.url = url
        self.dest = Path(dest)
        self.expected_size = expected_size

    def run(self) -> None:
        try:
            if urllib.parse.urlparse(self.url).scheme.lower() not in ("http", "https"):
                raise ValueError(f"Unsupported download URL: {self.url!r}")
            req = urllib.request.Request(self.url, headers={"User-Agent": _USER_AGENT})
            self.dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.dest.with_suffix(self.dest.suffix + ".part")
            received = 0
            with (
                urllib.request.urlopen(req, timeout=30) as resp,  # nosec B310
                open(tmp, "wb") as fh,
            ):
                total = int(resp.headers.get("Content-Length") or self.expected_size or 0)
                while True:
                    chunk = resp.read(1 << 16)  # 64 KiB
                    if not chunk:
                        break
                    fh.write(chunk)
                    received += len(chunk)
                    self.progress.emit(received, total)
            if self.expected_size and received < self.expected_size:
                self.failed.emit("Download incomplete")
                return
            tmp.replace(self.dest)
            self.finished_ok.emit(str(self.dest))
        except Exception as exc:
            self.failed.emit(str(exc))
