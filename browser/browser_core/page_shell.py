"""Shared HTML head shell for browser_core pages.

Every HTML page the Control API serves (dashboard, workflows, network
monitor, terminal, agent mesh, HQ splash, deep research) used to embed its
own near-duplicate dark color vars and base styles. This module emits one
shared ``<head>`` block instead:

  * charset / viewport meta
  * the ``:root`` token injection from ``brand.css_vars()`` — or the
    ``VARS_PLACEHOLDER`` marker, which a page builder replaces per-request
    with the active theme's tokens
  * base body styles (background, text color, font) plus scrollbar styling
  * a tiny dependency-free ``toast(msg, kind)`` JS helper every page can call
  * an optional ``?``-triggered keyboard-shortcut overlay (pass ``shortcuts``)

Pages keep their own page-specific CSS/JS in ``extra_css`` — this module
only carries what is genuinely shared. No Qt, no third-party dependencies.
"""

from __future__ import annotations

import html as _html
import json as _json

try:
    from browser_core.brand import css_vars
except ImportError:  # imported as browser.browser_core.page_shell
    from browser.browser_core.brand import css_vars

# Marker a page builder replaces per-request with the active theme's
# css_vars() output (same pattern dashboard_html already used).
VARS_PLACEHOLDER = "/* __BRAND_VARS__ */"

_BASE_CSS = """* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--ld-window, #0b0f16);
  color: var(--ld-text, #e8ecf5);
  font-family: 'Segoe UI Variable', 'Segoe UI', system-ui, sans-serif;
}
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--ld-border, #232c42); border-radius: 6px; }
::-webkit-scrollbar-thumb:hover { background: var(--ld-muted, #8b93a7); }
/* shared toast notifications — see toast_js() */
#ld-toasts { position: fixed; right: 18px; bottom: 18px; z-index: 9999;
  display: flex; flex-direction: column; gap: 8px; align-items: flex-end; }
.ld-toast { max-width: 340px; padding: 10px 14px; border-radius: var(--ld-r-md, 10px);
  background: var(--ld-card, #1a2132); border: 1px solid var(--ld-border, #232c42);
  color: var(--ld-text, #e8ecf5); font-size: 13px;
  box-shadow: var(--ld-sh-2, 0 4px 16px rgba(0,0,0,.35));
  opacity: 0; transform: translateY(6px); transition: opacity .25s, transform .25s; }
.ld-toast.show { opacity: 1; transform: none; }
.ld-toast-ok { border-color: var(--ld-ok, #34d399); }
.ld-toast-err { border-color: var(--ld-danger, #ff5b6e); }
.ld-toast-info { border-color: var(--ld-accent, #5b9dff); }"""


def toast_js() -> str:
    """A tiny dependency-free ``toast(msg, kind)`` helper.

    ``kind`` is one of ``info``/``ok``/``err`` (anything else falls back to
    ``info``). The message is set via ``textContent``, so it is safe to pass
    untrusted strings.
    """
    return """<script>
function toast(msg, kind) {
  kind = (kind === 'ok' || kind === 'err') ? kind : 'info';
  var wrap = document.getElementById('ld-toasts');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.id = 'ld-toasts';
    document.body.appendChild(wrap);
  }
  var el = document.createElement('div');
  el.className = 'ld-toast ld-toast-' + kind;
  el.textContent = String(msg);
  wrap.appendChild(el);
  requestAnimationFrame(function () { el.classList.add('show'); });
  setTimeout(function () {
    el.classList.remove('show');
    setTimeout(function () { el.remove(); }, 300);
  }, 2600);
}
</script>"""


def shortcuts_overlay_js(entries: list[tuple[str, str]]) -> str:
    """A ``?``-triggered overlay listing a page's existing keyboard shortcuts.

    ``entries`` is ``[(keys, description), ...]`` — only document shortcuts
    that already exist; nothing here invents new ones. The overlay ignores
    ``?`` typed inside inputs/textareas/selects and while Ctrl/Alt/Meta is
    held, so it never steals keystrokes from the page.
    """
    rows = "".join(
        '<div class="ld-sc-row"><span class="ld-sc-keys">'
        + _html.escape(str(keys))
        + '</span><span class="ld-sc-desc">'
        + _html.escape(str(desc))
        + "</span></div>"
        for keys, desc in entries
    )
    return (
        "<style>\n"
        "#ld-shortcuts { position: fixed; inset: 0; z-index: 9998;\n"
        "  background: rgba(4,7,12,.6); display: flex; align-items: center;\n"
        "  justify-content: center; backdrop-filter: blur(2px); }\n"
        ".ld-sc-card { background: var(--ld-card, #1a2132);\n"
        "  border: 1px solid var(--ld-border, #232c42); border-radius: var(--ld-r-lg, 14px);\n"
        "  padding: 20px 24px; min-width: 320px; max-width: 90vw;\n"
        "  box-shadow: var(--ld-sh-3, 0 12px 40px rgba(0,0,0,.5)); }\n"
        ".ld-sc-title { font-size: 14px; font-weight: 700; color: var(--ld-text, #e8ecf5);\n"
        "  margin-bottom: 12px; display: flex; justify-content: space-between;\n"
        "  gap: 16px; align-items: baseline; }\n"
        ".ld-sc-hint { font-size: 11px; color: var(--ld-muted, #8b93a7); font-weight: 500; }\n"
        ".ld-sc-row { display: flex; gap: 12px; align-items: baseline; padding: 6px 0;\n"
        "  border-top: 1px solid var(--ld-border, #232c42); font-size: 13px; }\n"
        ".ld-sc-row:first-of-type { border-top: none; }\n"
        ".ld-sc-keys { font-family: Consolas, monospace; font-size: 12px;\n"
        "  color: var(--ld-accent, #5b9dff); background: var(--ld-panel2, #141a28);\n"
        "  border: 1px solid var(--ld-border, #232c42); border-radius: 6px;\n"
        "  padding: 2px 8px; white-space: nowrap; }\n"
        ".ld-sc-desc { color: var(--ld-text, #e8ecf5); }\n"
        "</style>\n"
        "<script>\n"
        "(function () {\n"
        "  var rowsHtml = " + _json.dumps(rows) + ";\n"
        "  var overlay = null;\n"
        "  function toggle() {\n"
        "    if (overlay) { overlay.remove(); overlay = null; return; }\n"
        "    overlay = document.createElement('div');\n"
        "    overlay.id = 'ld-shortcuts';\n"
        "    overlay.innerHTML = '<div class=\"ld-sc-card\">' +\n"
        "      '<div class=\"ld-sc-title\"><span>Keyboard shortcuts</span>' +\n"
        "      '<span class=\"ld-sc-hint\">press ? to close</span></div>' +\n"
        "      rowsHtml + '</div>';\n"
        "    overlay.addEventListener('click', function (e) {\n"
        "      if (e.target === overlay) toggle();\n"
        "    });\n"
        "    document.body.appendChild(overlay);\n"
        "  }\n"
        "  document.addEventListener('keydown', function (e) {\n"
        "    if (e.key !== '?' || e.ctrlKey || e.metaKey || e.altKey) return;\n"
        "    var t = e.target;\n"
        "    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||\n"
        "             t.tagName === 'SELECT' || t.isContentEditable)) return;\n"
        "    e.preventDefault();\n"
        "    toggle();\n"
        "  });\n"
        "})();\n"
        "</script>"
    )


def page_head(
    title: str,
    extra_css: str = "",
    *,
    settings=None,
    css_vars_text: str | None = None,
    extra_head: str = "",
    shortcuts: list[tuple[str, str]] | None = None,
) -> str:
    """The shared ``<head>`` block: doctype through ``</head>``.

    ``extra_css`` is page-specific CSS, emitted after the token injection
    and base styles so page rules win. Pass
    ``css_vars_text=VARS_PLACEHOLDER`` when the page builder injects the
    active theme per-request (dashboard); otherwise the current/persisted
    theme is baked in. ``extra_head`` carries extra head tags (e.g. the
    xterm stylesheet link). ``shortcuts`` wires the ``?`` overlay.
    """
    safe_title = _html.escape(str(title))
    vars_block = css_vars_text if css_vars_text is not None else css_vars(settings)
    overlay = shortcuts_overlay_js(shortcuts) if shortcuts else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{safe_title}</title>\n"
        + extra_head
        + "<style>\n"
        + "  "
        + vars_block
        + "\n"
        + _BASE_CSS
        + "\n"
        + extra_css
        + "\n</style>\n"
        + toast_js()
        + "\n"
        + overlay
        + "</head>\n"
    )
