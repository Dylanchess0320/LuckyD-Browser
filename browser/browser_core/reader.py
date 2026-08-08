"""Reader Mode — distill a page to a clean, theme-aware article view.

A readability-lite heuristic scores block elements by text density (lots of
paragraph text, few links), clones the winner, strips interactive junk, and
re-renders it in a serif, max-width layout tinted with the active theme.
Pure strings here — fully unit-testable without Qt.
"""

from __future__ import annotations

import html as _html

# Scores every candidate block; the highest text-density one wins. Returns
# JSON {title, html} or "" when the page has nothing worth distilling.
EXTRACT_JS = r"""
(() => {
  const candidates = [...document.querySelectorAll(
    'article, main, [role="main"], .post, .article, .entry-content, .post-content, section, div')];
  let best = null, bestScore = 0;
  for (const el of candidates) {
    const text = (el.innerText || '').trim();
    if (text.length < 500) continue;
    const linkLen = [...el.querySelectorAll('a')]
      .reduce((n, a) => n + (a.innerText || '').length, 0);
    const density = Math.max(0, (text.length - linkLen) / text.length);
    const paras = el.querySelectorAll('p, li').length;
    const score = text.length * density * Math.min(paras, 12);
    if (score > bestScore) { bestScore = score; best = el; }
  }
  if (!best) return '';
  const clone = best.cloneNode(true);
  clone.querySelectorAll(
    'script,style,iframe,form,button,input,nav,aside,svg,noscript,video,footer,header'
  ).forEach(e => e.remove());
  return JSON.stringify({title: document.title, html: clone.innerHTML.slice(0, 400000)});
})()
"""

_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<base href="__BASE__">
<title>__TITLE__</title>
<style>
  body { background: __WINDOW__; color: __TEXT__; margin: 0; padding: 40px 20px 80px;
         font-family: Georgia, 'Times New Roman', serif; font-size: 18px;
         line-height: 1.75; }
  article { max-width: 720px; margin: 0 auto; }
  h1.reader-title { font-family: system-ui, sans-serif; font-size: 26px;
    line-height: 1.3; border-bottom: 1px solid __BORDER__; padding-bottom: 16px; }
  .reader-badge { font: 600 11px system-ui; letter-spacing: 1.5px; color: __ACCENT__;
    text-transform: uppercase; margin-bottom: 10px; }
  a { color: __ACCENT__; }
  img { max-width: 100%; height: auto; border-radius: 10px; }
  pre, code { font-family: 'Cascadia Mono', Consolas, monospace;
    background: __CARD__; border-radius: 6px; }
  pre { padding: 14px; overflow-x: auto; border: 1px solid __BORDER__; }
  blockquote { border-left: 3px solid __ACCENT__; margin-left: 0;
    padding-left: 18px; color: __MUTED__; }
</style></head><body>
<article>
  <div class="reader-badge">◈ Reader Mode</div>
  <h1 class="reader-title">__TITLE__</h1>
  __CONTENT__
</article>
</body></html>"""


def reader_html(title: str, content_html: str, base_url: str, colors: dict) -> str:
    """The finished reader page. `colors` is a theme palette dict."""
    return (
        _TEMPLATE.replace("__TITLE__", _html.escape(title or "Untitled"))
        .replace("__BASE__", _html.escape(base_url or ""))
        .replace("__CONTENT__", content_html)
        .replace("__WINDOW__", colors.get("window", "#0b0f1a"))
        .replace("__CARD__", colors.get("card", "#1a2132"))
        .replace("__BORDER__", colors.get("border", "#232c42"))
        .replace("__TEXT__", colors.get("text", "#e8ecf5"))
        .replace("__MUTED__", colors.get("muted", "#8b93a7"))
        .replace("__ACCENT__", colors.get("accent", "#5b9dff"))
    )
