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

import json

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
  #hello-line { font-size: 12.5px; color: var(--muted); margin-top: 3px; }
  #hello-line b { color: var(--text); }
  #party { position: fixed; inset: 0; display: none; align-items: center; justify-content: center;
    pointer-events: none; z-index: 99; }
  #party .burst { font-size: 30px; font-weight: 800; padding: 22px 38px; border-radius: 18px;
    background: var(--card); border: 1px solid var(--accent); color: var(--text);
    box-shadow: 0 0 60px var(--accent); animation: pop .5s ease-out; }
  @keyframes pop { 0% { transform: scale(.6); opacity: 0; } 100% { transform: scale(1); opacity: 1; } }
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
  .tile .fav { width: 26px; height: 26px; border-radius: 7px; display: inline-flex;
    align-items: center; justify-content: center; color: #fff; font-size: 14px;
    font-weight: 700; text-shadow: 0 1px 2px rgba(0,0,0,.35); }
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
    <span class="pill" id="pill-ads"><span class="dot"></span><span>shield…</span></span>
  </div>
  <div id="clock"><div class="time" id="time"></div><div class="date" id="date"></div>
    <div id="hello-line"><b id="hello"></b> <span id="tagline"></span></div></div>
</header>
<div id="party"><div class="burst" id="partytext"></div></div>
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
  ['⌘', 'Coding Agent', '/hq'],
  ['🤖', 'AI Assistant', 'luckyd://assistant'],
  ['🕸️', 'Agent Mesh', '/mesh'],
  ['🖥️', 'Agent Terminal', '/terminal'],
  ['🛸', 'Antigravity CLI', '/terminal?shell=mesh-agy'],
  ['🎬', 'Workflows', '/workflows'],
  ['📡', 'Network', '/network'],
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

// Time-aware greeting with a rotating LuckyD wink.
const TAGLINES = [
  'your tabs missed you', 'no accounts, no keys, no worries', 'the agent is ready when you are',
  'press Ctrl+K for everything', 'tip: Ctrl+` opens a terminal', 'surf different',
  'tip: Ctrl+Shift+S screenshots the page', 'workflows replay your best moves',
];
(function greet() {
  const h = new Date().getHours();
  document.getElementById('hello').textContent =
    (h < 5 ? 'Up late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening') + ' —';
  document.getElementById('tagline').textContent = TAGLINES[Math.floor(Math.random() * TAGLINES.length)];
})();

// Konami code (↑↑↓↓←→←→BA) unlocks the secret Synthwave Sunset theme.
const KONAMI = ['ArrowUp','ArrowUp','ArrowDown','ArrowDown','ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'];
let kpos = 0;
addEventListener('keydown', e => {
  if (e.key === KONAMI[kpos]) {
    kpos++;
    if (kpos === KONAMI.length) { kpos = 0; unlockSynthwave(); }
  } else {
    kpos = (e.key === KONAMI[0]) ? 1 : 0;
  }
});
function party(text) {
  const p = document.getElementById('party');
  document.getElementById('partytext').textContent = text;
  p.style.display = 'flex';
  setTimeout(() => { p.style.display = 'none'; }, 1600);
}
async function unlockSynthwave() {
  try {
    await fetch('/theme', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: 'synthwave'})});
    party('🌆 Synthwave Sunset unlocked!');
    setTimeout(() => location.reload(), 1400);  // re-render with the new theme vars
  } catch (e) { party('🌆 nice combo!'); }
}

function setPill(id, cls, text) {
  const p = document.getElementById(id);
  p.className = 'pill ' + cls;
  p.querySelector('span:last-child').textContent = text;
}
async function refreshStatus() {
  try {
    const r = await fetch('/status', { headers: DASH_TOKEN ? { 'Authorization': 'Bearer ' + DASH_TOKEN } : {} });
    const s = await r.json();
    if (s.harness) {
      const tools = Number(s.harness_tools || 0);
      setPill('pill-harness', 'ok', tools ? 'coding agent online · ' + tools + ' tools' : 'coding agent online');
    } else if (s.harness_starting) {
      setPill('pill-harness', 'warn', 'coding agent starting…');
    } else {
      setPill('pill-harness', 'err', 'coding agent offline');
    }
    setPill('pill-api', 'ok', 'browser API on');
    const prov = (s.ai_providers || []);
    setPill('pill-ai', prov.length ? 'ok' : 'warn',
      prov.length ? 'AI: ' + prov.slice(0, 3).join(', ') + (prov.length > 3 ? '…' : '') : 'AI not set up');
    const blocked = Number(s.ads_blocked || 0);
    setPill('pill-ads', blocked ? 'ok' : '',
      '🛡 ' + blocked.toLocaleString() + ' blocked');
  } catch (e) {
    setPill('pill-harness', 'err', 'status unavailable');
  }
}
refreshStatus(); setInterval(refreshStatus, 5000);
"""
_JS2 = r"""// Platform tiles from browser_core/platform_tiles.json (TileRegistry).
const PLATFORM_TILES = __PLATFORM_TILES__;
function renderApps() {
  const g = document.getElementById('apps');
  APPS.concat(PLATFORM_TILES).forEach(([ico, name, href], i) => {
    const a = document.createElement('a');
    a.className = 'tile' + (i === 0 ? ' hq' : ''); a.href = href;
    a.innerHTML = '<span class="ico">' + ico + '</span><span></span>';
    a.querySelector('span:last-child').textContent = name;
    g.appendChild(a);
  });
}
// Letter-tile favicons — minted locally from the hostname (same hue hash as
// the app's icons.py). No favicon service ever sees your shortcuts.
function tileFor(url) {
  let host = '';
  try { host = new URL(url).hostname.replace(/^www\./, ''); } catch (e) {}
  const letter = host ? host[0].toUpperCase() : '•';
  let hue = 0;
  for (let i = 0; i < host.length; i++) hue = (hue + (i + 1) * host.charCodeAt(i)) % 360;
  const tile = document.createElement('span');
  tile.className = 'fav';
  tile.textContent = letter;
  tile.style.background = 'linear-gradient(135deg, hsl(' + hue + ',53%,59%), hsl(' +
    ((hue + 48) % 360) + ',58%,47%))';
  return tile;
}
function render() {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  shortcuts.forEach(([name, url], idx) => {
    const a = document.createElement('a');
    a.className = 'tile'; a.href = url;
    a.appendChild(tileFor(url));
    a.insertAdjacentHTML('beforeend', '<span></span>' +
      '<span class="del" title="Remove">&#10005;</span>');
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


def dashboard_html(settings=None, token: str = "") -> str:
    """The dashboard with the active theme's design tokens injected, so the
    new-tab hub matches the Qt chrome, sidebar and HQ splash exactly.

    ``token`` (the Control API's auth token) is injected as a JS constant so
    the dashboard's own same-origin fetch('/status') call can authenticate —
    /status is otherwise gated behind the same token as every other route.
    """
    from browser_core.brand import css_vars

    block = "  " + css_vars(settings) + "\n"
    if "/* __BRAND_VARS__ */" in DASHBOARD_HTML:
        html = DASHBOARD_HTML.replace("  /* __BRAND_VARS__ */\n", block, 1)
    else:
        html = "<style>" + css_vars(settings) + "</style>" + DASHBOARD_HTML
    # Platform tiles: registry-driven extras appended to the built-in Apps.
    try:
        from browser_core.tile_registry import load_tiles

        extra = [[t.icon, t.name, t.url] for t in load_tiles()]
    except Exception:
        extra = []  # a broken config must never take the dashboard down
    html = html.replace("__PLATFORM_TILES__", json.dumps(extra))
    return html.replace(
        "<script>\nconst ENGINES",
        f"<script>\nconst DASH_TOKEN = {json.dumps(token)};\nconst ENGINES",
    )


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
            "<div class='spin'>⌘</div><h2>Starting your coding agent…</h2>"
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


_WORKFLOWS_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Workflows</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; background: #0b0f16; color: #e2e8f0;
         font: 14px/1.5 system-ui, "Segoe UI", sans-serif; padding: 28px 20px; }
  main { max-width: 760px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: #64748b; font-size: 12.5px; margin-bottom: 20px; }
  .card { background: #0f1622; border: 1px solid #1e293b; border-radius: 12px;
          padding: 16px; margin-bottom: 14px; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .row .sp { flex: 1; }
  input[type=text] { flex: 1; min-width: 180px; background: #1a2132;
      border: 1px solid #232c42; border-radius: 8px; padding: 9px 12px;
      color: #e2e8f0; font: inherit; }
  button { border: 1px solid #1e293b; background: #1a2132; color: #cbd5e1;
      border-radius: 8px; padding: 9px 14px; font: 600 13px system-ui;
      cursor: pointer; }
  button:hover { border-color: #334155; }
  button.rec { background: #7f1d1d; border-color: #991b1b; color: #fecaca; }
  button.play { color: #34d399; }
  button.del { color: #f87171; }
  button:disabled { opacity: .45; cursor: default; }
  .pill { font: 600 11.5px system-ui; padding: 4px 10px; border-radius: 999px;
      background: #1a2132; border: 1px solid #232c42; color: #64748b; }
  .pill.on { color: #f87171; border-color: #7f1d1d; }
  .wf { display: flex; align-items: center; gap: 10px; padding: 10px 4px;
        border-top: 1px solid #1e293b; }
  .wf:first-of-type { border-top: none; }
  .wf .name { font-weight: 600; }
  .wf .meta { color: #64748b; font-size: 12px; }
  .wf .sp { flex: 1; }
  .wf .last { color: #475569; font-size: 11px; width: 100%; padding-left: 2px; }
  select.sched { background: #1a2132; border: 1px solid #232c42; border-radius: 8px;
    color: #cbd5e1; padding: 6px 8px; font: 600 12px system-ui; }
  select.sched.on { color: #fbbf24; border-color: #92400e; }
  #log { font: 12px/1.6 ui-monospace, "Cascadia Mono", Consolas, monospace;
      white-space: pre-wrap; color: #94a3b8; max-height: 260px; overflow: auto; }
  #log .ok { color: #34d399; } #log .bad { color: #f87171; }
  #log .heal { color: #fbbf24; }
  .empty { color: #475569; text-align: center; padding: 18px 0; }
</style></head><body>
<main>
  <h1>🎬 Workflows</h1>
  <div class="sub">Record Control-API actions (agent runs, scripts) into named
    automations, then replay them — element targets re-resolve by fingerprint
    when the page changes.</div>

  <div class="card">
    <div class="row">
      <span id="recpill" class="pill">idle</span>
      <input id="wfname" type="text" placeholder="workflow name (e.g. daily-report)" maxlength="60">
      <button id="recbtn" class="rec">⏺ Start Recording</button>
    </div>
  </div>

  <div class="card" id="list-card">
    <div class="row"><b>Saved workflows</b><span class="sp"></span>
      <button id="refresh">↻ Refresh</button></div>
    <div id="list"><div class="empty">No workflows yet — record one above.</div></div>
  </div>

  <div class="card" id="log-card" style="display:none">
    <b>Replay log</b>
    <div id="log"></div>
  </div>
</main>
<script>
const $ = id => document.getElementById(id);
async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  return await r.json();
}
let recording = false;

function paintRecorder(st) {
  recording = !!(st && st.recording);
  $('recpill').textContent = recording
    ? `⏺ recording "${st.name}" — ${st.steps} steps` : 'idle';
  $('recpill').className = 'pill' + (recording ? ' on' : '');
  $('recbtn').textContent = recording ? '⏹ Stop & Save' : '⏺ Start Recording';
  $('wfname').disabled = recording;
}

async function refresh() {
  try {
    const data = await api('/workflows/list');
    let schedData = {schedules: [], intervals: {0: 'Off'}};
    try { schedData = await api('/schedules'); } catch (e) {}
    const byName = {};
    for (const s of schedData.schedules || []) byName[s.name] = s;
    paintRecorder(data.recording);
    const rows = data.workflows || [];
    $('list').innerHTML = rows.length ? '' :
      '<div class="empty">No workflows yet — record one above.</div>';
    for (const wf of rows) {
      const when = wf.created ? new Date(wf.created * 1000).toLocaleString() : '';
      const div = document.createElement('div');
      div.className = 'wf';
      div.innerHTML = `<span class="name"></span>
        <span class="meta">${wf.steps} steps · ${when}</span>
        <span class="sp"></span>`;
      div.querySelector('.name').textContent = wf.name;
      const play = document.createElement('button');
      play.className = 'play'; play.textContent = '▶ Replay';
      play.onclick = () => replay(wf.name, play);
      const sel = document.createElement('select');
      sel.className = 'sched';
      sel.title = 'Auto-replay this workflow on a schedule';
      const cur = byName[wf.name];
      for (const [mins, label] of Object.entries(schedData.intervals || {0: 'Off'})) {
        const opt = document.createElement('option');
        opt.value = mins; opt.textContent = label;
        if (cur && Number(mins) === cur.every_min) opt.selected = true;
        sel.appendChild(opt);
      }
      if (cur && cur.every_min > 0) sel.classList.add('on');
      sel.onchange = async () => {
        await api('/schedule', {name: wf.name, every_min: Number(sel.value)});
        refresh();
      };
      const del = document.createElement('button');
      del.className = 'del'; del.textContent = '🗑';
      del.title = 'Delete workflow';
      del.onclick = async () => {
        if (confirm(`Delete workflow "${wf.name}"?`)) {
          await api('/workflow/delete', {name: wf.name}); refresh();
        }
      };
      div.append(play, sel, del);
      if (cur && cur.last_result) {
        const last = document.createElement('span');
        last.className = 'last';
        const ago = cur.last_run ? new Date(cur.last_run * 1000).toLocaleTimeString() : '';
        last.textContent = `⏰ last: ${cur.last_result}${ago ? ' · ' + ago : ''}`;
        div.appendChild(last);
      }
      $('list').appendChild(div);
    }
  } catch (e) { /* control API hiccup — retry on next tick */ }
}

$('recbtn').onclick = async () => {
  if (recording) { await api('/workflow/stop', {}); refresh(); return; }
  const name = $('wfname').value.trim() || 'workflow-' + Date.now();
  await api('/workflow/record', {name});
  $('wfname').value = '';
  refresh();
};
$('refresh').onclick = refresh;

async function replay(name, btn) {
  btn.disabled = true;
  $('log-card').style.display = '';
  $('log').innerHTML = `▶ replaying "${name}"…\n`;
  try {
    const r = await api('/workflow/replay', {name});
    const lines = (r.results || []).map(s => {
      const cls = s.ok ? 'ok' : 'bad';
      const heal = s.healed ? ' <span class="heal">⟲ healed</span>' : '';
      return `<span class="${cls}">${s.ok ? '✓' : '✗'} step ${s.step} ${s.action}</span>${heal} — ${s.detail}`;
    });
    $('log').innerHTML = lines.join('\n') +
      `\n\n<b>${r.succeeded}/${r.total}</b> steps succeeded`;
    if (r.total > 0 && r.succeeded === r.total) confetti();  // flawless run
  } catch (e) {
    $('log').innerHTML += 'replay failed: ' + e;
  }
  btn.disabled = false;
}

function confetti() {
  const cv = document.createElement('canvas');
  cv.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:99';
  document.body.appendChild(cv);
  cv.width = innerWidth; cv.height = innerHeight;
  const ctx = cv.getContext('2d');
  const P = [];
  for (let i = 0; i < 130; i++) P.push({
    x: innerWidth / 2 + (Math.random() - 0.5) * 120, y: innerHeight * 0.3,
    vx: (Math.random() - 0.5) * 10, vy: -Math.random() * 9 - 2, g: 0.24,
    c: `hsl(${Math.random() * 360},92%,62%)`, s: 3 + Math.random() * 4,
    r: Math.random() * Math.PI,
  });
  let frames = 0;
  (function anim() {
    ctx.clearRect(0, 0, cv.width, cv.height);
    for (const p of P) {
      p.x += p.vx; p.y += p.vy; p.vy += p.g; p.r += 0.12;
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.r);
      ctx.fillStyle = p.c; ctx.fillRect(-p.s / 2, -p.s / 2, p.s, p.s); ctx.restore();
    }
    if (++frames < 150) requestAnimationFrame(anim); else cv.remove();
  })();
}

refresh();
setInterval(refresh, 2500);
</script>
</body></html>
"""


def workflows_html() -> str:
    """The workflow manager page (record / replay / delete saved automations).

    Same-origin with the Control API, so it drives the JSON routes directly —
    no auth juggling, no external assets.
    """
    return _WORKFLOWS_HTML


_NETMON_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Network Monitor</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #0b0f16; color: #e2e8f0;
         font: 13px/1.45 system-ui, "Segoe UI", sans-serif; }
  header { display: flex; gap: 10px; align-items: center; padding: 12px 16px;
      background: #0f1622; border-bottom: 1px solid #1e293b; flex-wrap: wrap; }
  h1 { font-size: 16px; margin: 0; }
  .target { color: #64748b; font-size: 12px; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap; max-width: 40ch; }
  .sp { flex: 1; }
  button { border: 1px solid #1e293b; background: #1a2132; color: #cbd5e1;
      border-radius: 8px; padding: 7px 12px; font: 600 12px system-ui; cursor: pointer; }
  button:hover { border-color: #334155; }
  button.on { color: #f87171; border-color: #7f1d1d; }
  input { background: #1a2132; border: 1px solid #232c42; border-radius: 8px;
      padding: 7px 10px; color: #e2e8f0; font: inherit; width: 170px; }
  table { width: 100%; border-collapse: collapse; }
  th { position: sticky; top: 0; background: #0f1622; text-align: left;
      padding: 8px 10px; font-size: 11px; letter-spacing: 1px; color: #64748b;
      text-transform: uppercase; border-bottom: 1px solid #1e293b; }
  td { padding: 5px 10px; border-bottom: 1px solid #131a26; font-size: 12px;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 0; }
  tr:hover td { background: #101827; }
  td.u { width: 100%; }
  .m { font-weight: 700; color: #7dd3fc; }
  .s2 { color: #34d399; } .s3 { color: #7dd3fc; } .s4 { color: #fbbf24; }
  .s5, .serr { color: #f87171; } .s0 { color: #64748b; }
  .empty { text-align: center; color: #475569; padding: 40px 0; }
</style></head><body>
<header>
  <h1>📡 Network</h1>
  <span class="target" id="target">not capturing</span>
  <span class="sp"></span>
  <input id="filter" placeholder="filter (host, .js, 404…)">
  <button id="toggle">⏺ Start</button>
  <button id="clear">Clear</button>
  <button id="har">⬇ Export HAR</button>
</header>
<table><thead><tr>
  <th style="width:60px">Method</th><th style="width:56px">Status</th>
  <th>URL</th><th style="width:76px">Type</th>
  <th style="width:76px">Size</th><th style="width:64px">Time</th>
</tr></thead><tbody id="rows"></tbody></table>
<div class="empty" id="empty">Start capture, then browse — requests appear live.</div>
<script>
const $ = id => document.getElementById(id);
let since = 0, capturing = false;
const seen = new Map();  // seq -> <tr> for in-place updates

function fmtSize(b) {
  if (!b) return '—';
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
  return (b / 1048576).toFixed(1) + ' MB';
}
function statusClass(s) {
  if (s < 0) return 'serr';
  if (s < 200) return 's0';
  if (s < 300) return 's2';
  if (s < 400) return 's3';
  if (s < 500) return 's4';
  return 's5';
}
function hostOf(u) { try { return new URL(u).hostname; } catch (e) { return u; } }
function matches(row, f) {
  if (!f) return true;
  return row.url.toLowerCase().includes(f) || String(row.status).includes(f) ||
    (row.type || '').toLowerCase().includes(f) || row.method.toLowerCase().includes(f);
}
"""

_NETMON_HTML2 = r"""function paint(row) {
  const f = $('filter').value.trim().toLowerCase();
  let tr = seen.get(row.seq);
  if (!tr) {
    tr = document.createElement('tr');
    tr.innerHTML = '<td class="m"></td><td class="st"></td><td class="u"></td>' +
      '<td class="ty"></td><td class="sz"></td><td class="ms"></td>';
    tr.title = row.url;
    seen.set(row.seq, tr);
    $('rows').prepend(tr);
  }
  tr.children[0].textContent = row.method;
  tr.children[1].textContent = row.status < 0 ? '✗' : (row.status || '…');
  tr.children[1].className = statusClass(row.status);
  tr.children[2].textContent = row.url;
  tr.children[3].textContent = row.type || '—';
  tr.children[4].textContent = fmtSize(row.size);
  tr.children[5].textContent = row.ms ? row.ms + ' ms' : '—';
  tr.style.display = matches(row, f) ? '' : 'none';
  $('empty').style.display = seen.size ? 'none' : '';
}
async function poll() {
  try {
    const r = await fetch('/network/events?since=' + since);
    const data = await r.json();
    since = data.seq;
    capturing = !!data.running;
    $('target').textContent = data.target ? 'watching: ' + hostOf(data.target) : 'not capturing';
    $('toggle').textContent = capturing ? '⏹ Stop' : '⏺ Start';
    $('toggle').className = capturing ? 'on' : '';
    for (const row of data.rows || []) paint(row);
  } catch (e) { /* retry next tick */ }
}
$('toggle').onclick = async () => {
  await fetch('/network/' + (capturing ? 'stop' : 'start'),
    {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
  poll();
};
$('clear').onclick = async () => {
  await fetch('/network/clear', {method: 'POST', body: '{}'});
  seen.forEach(tr => tr.remove()); seen.clear();
  $('empty').style.display = '';
  poll();
};
$('har').onclick = () => { location.href = '/network/har'; };
$('filter').addEventListener('input', () => { since = since; poll(); });
fetch('/network/start', {method: 'POST', body: '{}'}).then(poll);  // auto-start
setInterval(poll, 1000);
</script>
</body></html>
"""


def netmon_html() -> str:
    """The network monitor page — live request table for the active tab.

    Polls /network/events with a since-cursor (new rows only), repaints rows
    in place as status/size land, and exports HAR straight from the server.
    """
    return _NETMON_HTML + _NETMON_HTML2
