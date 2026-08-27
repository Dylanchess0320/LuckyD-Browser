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

import contextlib
import hashlib
import html as _html
import json
import re
import time
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
        # \VarFileInfo\Translation is two 16-bit values: language and
        # codepage.  Reading a single uint16 and shifting it (as older builds
        # did) always produced codepage 1200, so valid version resources such
        # as 0409/04B0 could not be read in frozen installs.
        if not ctypes.windll.version.VerQueryValueW(
            data, r"\VarFileInfo\Translation", ctypes.byref(val), ctypes.byref(vlen)
        ):
            return ""
        if vlen.value < 4:
            return ""
        translation = ctypes.cast(val, ctypes.POINTER(ctypes.c_uint16 * 2)).contents
        lang, codepage = translation[0], translation[1]
        sub = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileVersion"
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
_USER_AGENT = f"LuckyDBrowser/{CURRENT_VERSION} (+https://luckycode.xyz)"
_TIMEOUT = 12  # seconds for the metadata call


@dataclass
class ReleaseInfo:
    """A newer version that is available for download."""

    version: str
    url: str  # human-readable release page (fallback / "open in tab")
    installer_url: str = ""  # direct .exe asset URL (may be empty)
    installer_size: int = 0  # bytes, if known
    installer_sha256: str = ""  # GitHub asset digest, when published
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


def is_installer_asset(asset: dict, version: str = "") -> bool:
    """Whether a GitHub release asset is a LuckyD Browser installer.

    Matches LuckyDBrowserSetup*.exe or any LuckyD Browser setup executable.
    """
    name = str(asset.get("name") or "").casefold()
    if not (name.endswith(".exe") and "luckyd" in name and ("browser" in name or "setup" in name)):
        return False
    if version:
        normalized = re.escape(version.lstrip("vV").casefold())
        return bool(
            re.search(rf"luckyd.*browser.*setup.*v?{normalized}\.exe", name)
            or re.search(rf"luckydbrowsersetup-v?{normalized}\.exe", name)
        )
    return True


def asset_sha256(asset: dict) -> str:
    """Return GitHub's SHA-256 asset digest, or an empty string if absent."""
    digest = str(asset.get("digest") or "")
    algorithm, separator, value = digest.partition(":")
    return (
        value.lower()
        if separator
        and algorithm.lower() == "sha256"
        and re.fullmatch(r"[0-9a-f]{64}", value.lower())
        else ""
    )


def _asset_reachable(url: str, timeout: float = 10.0) -> bool:
    """Cheap HEAD probe: does this download URL actually resolve?

    Used by the Atom-feed fallback to validate an installer URL we derived
    from the release-script's fixed naming instead of from the JSON API.
    Returns False (never raises) for 404, TLS trouble, or a dead host.
    """
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return resp.status == 200
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False


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
        # Pick only the browser installer asset, never an arbitrary release
        # executable or source archive.
        assets: list[dict] = data.get("assets") or []
        installers = [asset for asset in assets if is_installer_asset(asset, info.version)]
        if not installers:
            installers = [asset for asset in assets if is_installer_asset(asset, "")]
        if installers:
            best = installers[0]
            info.installer_url = best.get("browser_download_url") or ""
            info.installer_size = int(best.get("size") or 0)
            info.installer_sha256 = asset_sha256(best)
        return info

    def fetch_latest_atom(self) -> ReleaseInfo:
        """Fetch the newest release via GitHub's Atom feed (rate-limit safe).

        The JSON ``/releases/latest`` endpoint allows only 60 unauthenticated
        requests/hour per IP, and a browser that auto-checks shortly after
        every launch can trip that — at which point the updater surfaces
        bare "HTTP 403" error codes. The public Atom feed serves fully
        rendered HTML on the same host end-users already reach, so it is a
        robust fallback that never counts against that API budget.

        The feed reports the newest tag, release page, and notes but not the
        attached asset binaries, so the installer URL is derived from the
        release script's fixed naming (``LuckyDBrowserSetup-<version>.exe``)
        and confirmed live with a cheap HEAD probe rather than assumed.
        """
        feed_url = f"https://github.com/{self.repo}/releases.atom"
        req = urllib.request.Request(feed_url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # nosec B310
            body = resp.read().decode("utf-8", "replace")

        # The newest entry is the first <entry> block; others are historical.
        m = re.search(r"<entry>(.*?)</entry>", body, re.S)
        block = m.group(1) if m else body

        def _field(tag: str) -> str:
            mm = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S)
            return mm.group(1).strip() if mm else ""

        tag_id = _field("id")  # e.g. tag:github.com,2025:Repository/123/v2.5.8
        version = ""
        mm = re.search(r"/v?(\d+(?:\.\d+){1,3})\s*$", tag_id)
        if mm:
            version = mm.group(1)
        title = _field("title")
        if not version:
            mm = re.search(r"v?(\d+(?:\.\d+){1,3})", title)
            version = mm.group(1) if mm else ""
        href = re.search(r'<link[^>]*\shref="([^"]+)"', block)
        release_url = href.group(1) if href else f"https://github.com/{self.repo}/releases/latest"
        notes = _html.unescape(re.sub(r"<[^>]+>", "", _field("content"))).strip()
        if not notes:
            notes = _html.unescape(title)
        name = title or (f"v{version}" if version else version or "latest")

        info = ReleaseInfo(version=version, url=release_url, notes=notes, name=name)
        if version:
            repo_verified = version.lstrip("vV")
            candidates = [
                (
                    f"https://github.com/{self.repo}/releases/download/"
                    f"v{repo_verified}/LuckyDBrowserSetup-{repo_verified}.exe"
                ),
                (
                    f"https://github.com/{self.repo}/releases/download/"
                    f"{repo_verified}/LuckyDBrowserSetup-{repo_verified}.exe"
                ),
                (
                    f"https://github.com/{self.repo}/releases/download/"
                    f"v{repo_verified}/LuckyDBrowserSetup.exe"
                ),
            ]
            for cand in candidates:
                if _asset_reachable(cand):
                    info.installer_url = cand
                    break
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

    def _atom_fallback(self) -> ReleaseInfo | None:
        """Best-effort Atom feed check — not subject to the per-IP API limit."""
        try:
            return self.source.fetch_latest_atom()
        except Exception:
            return None

    def run(self) -> None:
        reason = ""
        info: ReleaseInfo | None = None
        try:
            info = self.source.fetch_latest()
        except urllib.error.HTTPError as exc:
            # 404 simply means the repo has no releases yet.
            if exc.code == 404:
                self.up_to_date.emit()
                return
            # 403 / 429 = GitHub API rate limit. The Atom feed is not API
            # rate-limited, so fall back to it instead of exposing a bare
            # "HTTP 403" error code to the user.
            if exc.code in (403, 429):
                info = self._atom_fallback()
                reason = f"HTTP {exc.code} (GitHub rate limit)"
            else:
                reason = f"HTTP {exc.code}"
        except Exception as exc:  # offline, DNS, timeout, bad JSON …
            # The JSON API may be blocked while github.com is reachable;
            # give the Atom feed a chance regardless of failure type.
            info = self._atom_fallback()
            reason = str(exc)

        if info is None:
            self.failed.emit(reason or "network error, no fallback available")
            return

        if info.version and is_newer(info.version):
            self.update_available.emit(
                {
                    "version": info.version,
                    "url": info.url,
                    "installer_url": info.installer_url,
                    "installer_size": info.installer_size,
                    "installer_sha256": info.installer_sha256,
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
    cancelled = Signal()

    def __init__(
        self,
        url: str,
        dest: Path,
        expected_size: int = 0,
        expected_sha256: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.url = url
        self.dest = Path(dest)
        self.expected_size = expected_size
        self.expected_sha256 = expected_sha256.lower()

    def cancel(self) -> None:
        """Request cooperative cancellation rather than killing a live I/O thread."""
        self.requestInterruption()

    def run(self) -> None:
        try:
            if urllib.parse.urlparse(self.url).scheme.lower() != "https":
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
                    if self.isInterruptionRequested():
                        with contextlib.suppress(OSError):
                            tmp.unlink()
                        self.cancelled.emit()
                        return
                    chunk = resp.read(1 << 16)  # 64 KiB
                    if not chunk:
                        break
                    fh.write(chunk)
                    received += len(chunk)
                    self.progress.emit(received, total)
            if self.expected_size and received != self.expected_size:
                with contextlib.suppress(OSError):
                    tmp.unlink()
                raise RuntimeError("Downloaded update size did not match the release asset")
            if self.expected_sha256:
                digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
                if digest != self.expected_sha256:
                    with contextlib.suppress(OSError):
                        tmp.unlink()
                    raise RuntimeError("Downloaded update SHA-256 did not match the release asset")
            # Windows Defender (or another AV) often grabs a lock on a
            # freshly-written .exe to scan it right as we try to rename it
            # into place -- the rename loses that race with a transient
            # WinError 5 (Access is denied). Retry with backoff instead of
            # failing outright; build_installer.ps1 hits the identical class
            # of locked-file problem and handles it the same way.
            last_exc: Exception | None = None
            for attempt in range(6):
                if self.isInterruptionRequested():
                    with contextlib.suppress(OSError):
                        tmp.unlink()
                    self.cancelled.emit()
                    return
                try:
                    tmp.replace(self.dest)
                    last_exc = None
                    break
                except OSError as exc:
                    last_exc = exc
                    time.sleep(0.5 * (attempt + 1))
            if last_exc is not None:
                with contextlib.suppress(Exception):
                    tmp.unlink()
                raise last_exc
            self.finished_ok.emit(str(self.dest))
        except Exception as exc:
            self.failed.emit(str(exc))
