"""Static update server for the self-hosted force-installed extension.

Serves dist/update.xml and dist/luckyd-yt-adblock.crx over 127.0.0.1:8791 so
Edge's ExtensionInstallForcelist policy can fetch and verify the extension.
Run this (auto-started by the installer) before launching Edge.
"""

import contextlib
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIST = Path(__file__).resolve().parent / "dist"
PORT = 8791

MIME = {
    ".xml": "application/xml",
    ".crx": "application/x-chrome-extension",
}


class Handler(BaseHTTPRequestHandler):
    def _serve(self, name: str) -> None:
        f = DIST / name
        if not f.is_file():
            self.send_error(404, "not found")
            return
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(f.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0].lstrip("/")
        if path in ("", "update.xml"):
            return self._serve("update.xml")
        if path == "luckyd-yt-adblock.crx":
            return self._serve("luckyd-yt-adblock.crx")
        self.send_error(404, "not found")

    def log_message(self, *args):  # quiet
        pass


def main() -> int:
    if not (DIST / "update.xml").is_file():
        print("dist/update.xml missing — run pack_crx.py first")
        return 1
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"update server on http://127.0.0.1:{PORT}/update.xml")
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
