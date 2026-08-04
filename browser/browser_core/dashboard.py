"""Dashboard — the live new-tab page served by the Browser Control API.

Served from the same origin as the Control API (127.0.0.1:9777 by default),
so the page can call /status directly — no CORS, no auth juggling, no
external server. It is the one-window hub:

  * live status pills — coding-agent state, Control API, configured AI
  * one-tap tiles — the coding-agent workspace (the exe's web UI via /hq),
    the AI assistant, bookmarks, history, downloads, settings, shortcuts
  * web search box + user speed-dial tiles (localStorage, shared with the
    classic static new-tab page)

Everything is one self-contained HTML string (no build step, no assets).
"""

from __future__ import annotations

_HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>New Tab</title>
<style>
  /* __BRAND_VARS__ — the active theme's --ld-* tokens are injected here. */
  :root {
    --card: rgba(255,255,255,.06); --border: var(--ld-border, rgba(255,255,255,.10));
    --text: var(--ld-text, #e8eaf2); --muted: var(--ld-muted, #9aa1b5);
    --accent: var(--ld-accent, #5b9dff); --accent2: var(--ld-accent2, #b46bff);
    --ok: var(--ld-ok, #34d399); --warn: #fbbf24; --err: var(--ld-danger, #ff5b6e);
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  input, select, button { font: inherit; }
  body {
    font-family: 'Segoe UI Variable', 'Segoe UI', system-ui, sans-serif;
    font-weight: 600;
    color: var(--text); min-height: 100vh; overflow-x: hidden;
    background: var(--ld-grad, linear-gradient(135deg, #0b1020 0%, #101a30 45%, #1a1030 100%));
    background-size: 200% 200%; animation: drift 24s ease-in-out infinite;
  }
  @keyframes drift { 0%,100% { background-position: 0% 0%; } 50% { background-position: 100% 100%; } }
  header { display: flex; justify-content: space-between; align-items: center; padding: 20px 32px; flex-wrap: wrap; gap: 10px; }
  #pills { display: flex; gap: 8px; flex-wrap: wrap; }
  .pill { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px;
    padding: 5px 11px; border-radius: 999px; background: var(--card);
    border: 1px solid var(--border); color: var(--muted); white-space: nowrap; }
  .pill .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .pill.ok .dot { background: var(--ok); box-shadow: 0 0 8px var(--ok); }
  .pill.warn .dot { background: var(--warn); box-shadow: 0 0 8px var(--warn); }
  .pill.err .dot { background: var(--err); box-shadow: 0 0 8px var(--err); }
  .pill.ok { color: var(--ok); } .pill.warn { color: var(--warn); } .pill.err { color: var(--err); }
  #clock { text-align: right; }
  #clock .time { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
  #clock .date { font-size: 12px; color: var(--muted); }
  main { max-width: 780px; margin: 5vh auto 40px; padding: 0 24px; }
"""
_CSS = r"""  .search { display: flex; background: var(--card); border: 1px solid var(--border);
    border-radius: 16px; backdrop-filter: blur(14px); overflow: hidden;
    transition: border-color .15s, box-shadow .15s; }
  .search:focus-within { border-color: var(--accent); box-shadow: 0 8px 32px rgba(91,157,255,.18); }
  .search select { background: transparent; color: var(--muted); border: none; outline: none;
    padding: 0 10px 0 16px; font-size: 14px; cursor: pointer; }
  .search select option { background: #141a2e; }
  .search input { flex: 1; background: transparent; border: none; outline: none;
    color: var(--text); font-size: 16px; padding: 15px 8px; }
  .search button { background: linear-gradient(90deg, var(--accent), var(--accent2));
    border: none; color: #fff; font-size: 15px; font-weight: 600; padding: 0 24px; cursor: pointer; }
  .search button:hover { filter: brightness(1.12); }
  .grid { margin-top: 26px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
  .tile { display: flex; flex-direction: column; align-items: center; gap: 8px;
    padding: 16px 6px 13px; background: var(--card); border: 1px solid var(--border);
    border-radius: 14px; text-decoration: none; color: var(--text); font-size: 12px;
    position: relative; transition: transform .12s, border-color .12s, background .12s; }
  .tile:hover { transform: translateY(-2px); border-color: rgba(120,170,255,.5); background: rgba(255,255,255,.09); }
  .tile .ico { font-size: 22px; }
  .tile img { width: 26px; height: 26px; border-radius: 6px; }
  .tile .del { position: absolute; top: 3px; right: 6px; color: var(--muted); font-size: 13px; opacity: 0; cursor: pointer; }
  .tile:hover .del { opacity: 1; }
  .tile.add { color: var(--muted); font-size: 24px; justify-content: center; cursor: pointer; }
  .tile.add span { font-size: 11px; }
  .tile.hq { border-color: rgba(91,157,255,.45); background: rgba(91,157,255,.10); }
  .tile.hq:hover { border-color: var(--accent); background: rgba(91,157,255,.16); }
  .section { margin-top: 26px; font-size: 11px; font-weight: 700; letter-spacing: 1.2px;
    text-transform: uppercase; color: var(--muted); }
  #addform { margin-top: 14px; display: none; gap: 8px; }
  #addform input { flex: 1; background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; color: var(--text); padding: 10px 12px; font-size: 13px; outline: none; }
  #addform button { background: var(--accent); border: none; color: #fff; border-radius: 10px; padding: 0 16px; cursor: pointer; }
</style>
</head>
"""
_BODY = r"""<body>
<header>
  <div id="pills">
    <span class="pill" id="pill-harness"><span class="dot"></span><span>coding agent…</span></span>
    <span class="pill ok" id="pill-api"><span class="dot"></span><span>browser API</span></span>
    <span class="pill" id="pill-ai"><span class="dot"></span><span>AI…</span></span>
  </div>
  <div id="clock"><div class="time" id="time"></div><div class="date" id="date"></div></div>
</header>
<main>
  <form class="search" id="searchform">
    <select id="engine">
      <option value="google">Google</option>
      <option value="bing">Bing</option>
      <option value="ddg">DuckDuckGo</option>
      <option value="brave">Brave</option>
    </select>
    <input id="q" placeholder="Search the web or type a URL" autocomplete="off" autofocus>
    <button type="submit">Go</button>
  </form>

  <div class="section">Apps</div>
  <div class="grid" id="apps"></div>

  <div class="section">Shortcuts</div>
  <div class="grid" id="grid"></div>
  <div id="addform">
    <input id="newname" placeholder="Name (e.g. Stack Overflow)">
    <input id="newurl" placeholder="URL (e.g. https://stackoverflow.com)">
    <button id="addbtn" type="button">Add</button>
  </div>
</main>
"""
_JS = r"""<script>
const ENGINES = { google: 'https://www.google.com/search?q=', bing: 'https://www.bing.com/search?q=',
  ddg: 'https://duckduckgo.com/?q=', brave: 'https://search.brave.com/search?q=' };
const APPS = [
  ['⚡', 'Coding Agent', '/hq'],
  ['🤖', 'AI Assistant', 'luckyd://assistant'],
  ['🔖', 'Bookmarks', 'luckyd://bookmarks'],
  ['🕘', 'History', 'luckyd://history'],
  ['⬇️', 'Downloads', 'luckyd://downloads'],
  ['🧩', 'Extensions', 'luckyd://extensions'],
  ['🎨', 'Settings', 'luckyd://settings'],
  ['⌨️', 'Keyboard Shortcuts', 'luckyd://shortcuts'],
];
const DEFAULTS = [
  ['Google','https://www.google.com'], ['YouTube','https://www.youtube.com'],
  ['GitHub','https://github.com'], ['ChatGPT','https://chatgpt.com'],
  ['Perplexity','https://www.perplexity.ai'], ['Wikipedia','https://www.wikipedia.org'],
  ['Reddit','https://www.reddit.com']
];
let shortcuts = JSON.parse(localStorage.getItem('ld_shortcuts') || 'null') || DEFAULTS;
// One-time cleanup: drop the retired FMHY tile from saved shortcuts.
shortcuts = shortcuts.filter(([n, u]) => !/fmhy/i.test(n + u));
localStorage.setItem('ld_shortcuts', JSON.stringify(shortcuts));
document.getElementById('engine').value = localStorage.getItem('ld_engine') || 'google';

function tick() {
  const now = new Date();
  document.getElementById('time').textContent =
    now.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  document.getElementById('date').textContent =
    now.toLocaleDateString([], {weekday: 'long', month: 'long', day: 'numeric'});
}
tick(); setInterval(tick, 10000);

function setPill(id, cls, text) {
  const p = document.getElementById(id);
  p.className = 'pill ' + cls;
  p.querySelector('span:last-child').textContent = text;
}
async function refreshStatus() {
  try {
    const r = await fetch('/status'); const s = await r.json();
    if (s.harness) {
      setPill('pill-harness', 'ok', 'coding agent online');
    } else if (s.harness_starting) {
      setPill('pill-harness', 'warn', 'coding agent starting…');
    } else {
      setPill('pill-harness', 'err', 'coding agent offline');
    }
    setPill('pill-api', 'ok', 'browser API on');
    const prov = (s.ai_providers || []);
    setPill('pill-ai', prov.length ? 'ok' : 'warn',
      prov.length ? 'AI: ' + prov.slice(0, 3).join(', ') + (prov.length > 3 ? '…' : '') : 'AI not set up');
  } catch (e) {
    setPill('pill-harness', 'err', 'status unavailable');
  }
}
refreshStatus(); setInterval(refreshStatus, 5000);
"""
_JS2 = r"""function renderApps() {
  const g = document.getElementById('apps');
  APPS.forEach(([ico, name, href], i) => {
    const a = document.createElement('a');
    a.className = 'tile' + (i === 0 ? ' hq' : ''); a.href = href;
    a.innerHTML = '<span class="ico">' + ico + '</span><span></span>';
    a.querySelector('span:last-child').textContent = name;
    g.appendChild(a);
  });
}
function render() {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  shortcuts.forEach(([name, url], idx) => {
    const a = document.createElement('a');
    a.className = 'tile'; a.href = url;
    let host = ''; try { host = new URL(url).hostname; } catch (e) {}
    a.innerHTML = '<img src="https://www.google.com/s2/favicons?domain=' + host +
      '&sz=64" onerror="this.style.visibility=\'hidden\'"><span></span>' +
      '<span class="del" title="Remove">&#10005;</span>';
    a.querySelector('span').textContent = name;
    a.querySelector('.del').addEventListener('click', ev => {
      ev.preventDefault(); ev.stopPropagation();
      shortcuts.splice(idx, 1); save(); render();
    });
    grid.appendChild(a);
  });
  const add = document.createElement('div');
  add.className = 'tile add'; add.innerHTML = '<div>+<span>Add</span></div>';
  add.addEventListener('click', () => {
    const f = document.getElementById('addform');
    f.style.display = f.style.display === 'flex' ? 'none' : 'flex';
  });
  grid.appendChild(add);
}
function save() { localStorage.setItem('ld_shortcuts', JSON.stringify(shortcuts)); }

document.getElementById('searchform').addEventListener('submit', ev => {
  ev.preventDefault();
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  const engine = document.getElementById('engine').value;
  localStorage.setItem('ld_engine', engine);
  if (/^[^\s]+\.[^\s]{2,}$/.test(q)) { location.href = q.startsWith('http') ? q : 'https://' + q; }
  else { location.href = ENGINES[engine] + encodeURIComponent(q); }
});
document.getElementById('addbtn').addEventListener('click', () => {
  const name = document.getElementById('newname').value.trim();
  let url = document.getElementById('newurl').value.trim();
  if (!name || !url) return;
  if (!/^https?:\/\//.test(url)) url = 'https://' + url;
  shortcuts.push([name, url]); save(); render();
  document.getElementById('newname').value = '';
  document.getElementById('newurl').value = '';
});
renderApps(); render();
</script>
</body>
</html>
"""
DASHBOARD_HTML = _HEAD + _CSS + _BODY + _JS + _JS2


def dashboard_html(settings=None) -> str:
    """The dashboard with the active theme's design tokens injected, so the
    new-tab hub matches the Qt chrome, sidebar and HQ splash exactly."""
    from browser_core.brand import css_vars

    block = "  " + css_vars(settings) + "\n"
    if "/* __BRAND_VARS__ */" in DASHBOARD_HTML:
        return DASHBOARD_HTML.replace("  /* __BRAND_VARS__ */\n", block, 1)
    return "<style>" + css_vars(settings) + "</style>" + DASHBOARD_HTML


def hq_splash_html(harness_url: str, state: str, detail: str = "", settings=None) -> str:
    """Auto-refreshing splash for /hq while the exe boots (or its error page).

    Uses the active theme's tokens so the loading state matches the rest of
    the product instead of falling back to a hard-coded gradient."""
    from browser_core.brand import tokens

    t = tokens(settings)
    if state == "error":
        body = (
            "<div class='spin'>⚠️</div><h2>Coding agent backend unavailable</h2>"
            f"<p class='dim'>{detail}</p>"
            "<p class='dim'>Start it manually, then reload:<br>"
            "<code>luckyd-code.exe --web --port 8000</code></p>"
            "<p><a href='/hq'>↻ Retry</a> · <a href='/dashboard'>Dashboard</a></p>"
        )
        refresh = ""
    else:
        body = (
            "<div class='spin'>⚡</div><h2>Starting your coding agent…</h2>"
            "<p class='dim'>This page opens automatically when it's ready.</p>"
        )
        refresh = "<meta http-equiv='refresh' content='3'>"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Coding Agent</title>" + refresh + "<style>"
        f"body{{background:linear-gradient(135deg,{t['window']},{t['panel2']});"
        f"color:{t['text']};font:600 15px 'Segoe UI Variable','Segoe UI',"
        "system-ui,sans-serif;display:flex;"
        "height:100vh;align-items:center;justify-content:center;margin:0;"
        "text-align:center}"
        ".spin{font-size:44px;animation:pulse 1.6s ease-in-out infinite}"
        "@keyframes pulse{0%,100%{opacity:.55;transform:scale(.96)}50%{opacity:1;transform:scale(1.05)}}"
        f"h2{{font-size:20px;margin:14px 0 8px}}.dim{{color:{t['muted']};"
        "font-size:13px;max-width:420px;margin:6px auto;line-height:1.5}"
        "code{background:rgba(255,255,255,.08);border-radius:6px;padding:2px 8px}"
        f"a{{color:{t['accent']};text-decoration:none}}</style></head>"
        f"<body><div>{body}</div></body></html>"
    )


_HQ_SHELL_TMPL = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Coding Agent</title>
<style>
  __BRAND_VARS__
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    display: flex; flex-direction: column; overflow: hidden;
    font-family: 'Segoe UI Variable', 'Segoe UI', system-ui, sans-serif;
    font-weight: 600;
    background: var(--ld-grad); color: var(--ld-text);
  }
  #bar {
    display: flex; align-items: center; gap: 10px; padding: 8px 14px;
    background: var(--ld-panel); border-bottom: 1px solid var(--ld-border);
    flex-shrink: 0;
  }
  #bar .mark {
    width: 24px; height: 24px; border-radius: 7px; display: grid;
    place-items: center; font-size: 12px; font-weight: 800; color: #fff;
    background: linear-gradient(135deg, var(--ld-accent), var(--ld-accent2));
    box-shadow: 0 2px 10px rgba(0,0,0,.35); flex-shrink: 0;
  }
  #bar .txt { display: flex; flex-direction: column; min-width: 0; }
  #bar .t1 { font-size: 13px; font-weight: 650; letter-spacing: .2px; }
  #bar .spacer { flex: 1; }
  #bar a {
    font-size: 11.5px; color: var(--ld-muted); text-decoration: none;
    padding: 5px 10px; border: 1px solid var(--ld-border); border-radius: 8px;
    background: var(--ld-card); white-space: nowrap;
  }
  #bar a:hover { color: var(--ld-text); border-color: var(--ld-accent); }
  iframe { flex: 1; border: 0; width: 100%; background: var(--ld-window); }
</style>
</head>
<body>
<div id="bar">
  <div class="mark">&#9889;</div>
  <div class="txt">
    <span class="t1">Coding Agent</span>
  </div>
  <div class="spacer"></div>
  <a href="/dashboard">&#9666; Dashboard</a>
  <a href="__HARNESS_URL__" target="_blank" rel="noopener">Open full window &#8599;</a>
</div>
<iframe id="hq" src="__HARNESS_URL__" allow="clipboard-read; clipboard-write"></iframe>
<script>
  // Keep the exe's UI painted in the browser's active theme. The harness
  // page sets document.body.style.background from its own palette on load,
  // so we re-apply our tokens shortly after every load (and on an interval
  // for the first seconds while its JS settles).
  const GRAD = getComputedStyle(document.documentElement).getPropertyValue('--ld-grad');
  const TEXT = getComputedStyle(document.documentElement).getPropertyValue('--ld-text');
  function paint() {
    try {
      const doc = document.getElementById('hq').contentDocument;
      if (!doc || !doc.body) return;
      doc.body.style.background = GRAD;
      doc.body.style.backgroundAttachment = 'fixed';
      doc.body.style.color = TEXT;
    } catch (e) { /* cross-origin or not ready yet */ }
  }
  const frame = document.getElementById('hq');
  frame.addEventListener('load', () => {
    paint(); setTimeout(paint, 250); setTimeout(paint, 900); setTimeout(paint, 2200);
  });
</script>
</body>
</html>
"""


def hq_shell_html(harness_url: str, settings=None) -> str:
    """The coding-agent workspace embedded in branded chrome: theme-token
    header bar plus a same-origin iframe that re-paints the exe UI with the
    browser's active theme gradient (JS can reach in because both pages are
    127.0.0.1 loopback — treated as same-origin even across ports)."""
    from browser_core.brand import css_vars

    return _HQ_SHELL_TMPL.replace("__BRAND_VARS__", css_vars(settings), 1).replace(
        "__HARNESS_URL__", harness_url
    )
