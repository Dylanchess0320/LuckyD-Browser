"""Check which media codecs the QtWebEngine build supports (offscreen, no window).

Run:  python tests/codec_check.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QUrl, qVersion, __version__ as PYSIDE_VERSION
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView

QGuiApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)

view = QWebEngineView()

JS = r"""
(() => {
  const v = document.createElement('video');
  const a = document.createElement('audio');
  const out = {
    "H.264 (avc1)  [needed by Twitch / YouTube LIVE]": v.canPlayType('video/mp4; codecs="avc1.42E01E"'),
    "H.264 + AAC": v.canPlayType('video/mp4; codecs="avc1.42E01E, mp4a.40.2"'),
    "AAC audio": a.canPlayType('audio/mp4; codecs="mp4a.40.2"'),
    "HLS (application/vnd.apple.mpegurl)": v.canPlayType('application/vnd.apple.mpegurl'),
    "VP9  [YouTube VOD]": v.canPlayType('video/webm; codecs="vp9"'),
    "AV1  [YouTube VOD]": v.canPlayType('video/mp4; codecs="av01.0.05M.08"'),
    "Media Source Extensions": String(!!window.MediaSource),
  };
  return JSON.stringify(out);
})()
"""


def on_load(ok):
    if not ok:
        print("WARNING: page load failed, results may be unreliable")
    view.page().runJavaScript(JS, on_js)


def on_js(result):
    import json

    print(f"PySide {PYSIDE_VERSION} / Qt runtime {qVersion()}")
    print(f"Chromium UA: {view.page().profile().httpUserAgent()}")
    print("-" * 60)
    for k, val in json.loads(result).items():
        print(f"{k:45s} -> {val or 'NO'}")
    app.quit()


view.loadFinished.connect(on_load)
view.setHtml("<html><body>codec probe</body></html>", QUrl("https://localhost/"))
sys.exit(app.exec())
