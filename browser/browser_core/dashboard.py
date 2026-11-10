"""Dashboard — the live new-tab page served by the Browser Control API.

Served from the same origin as the Control API (127.0.0.1:9777 by default),
so the page can call its endpoints directly — no CORS, no auth juggling,
no external server. It is the one-window hub:

  * one-tap tiles — the AI assistant, LuckyD Chat, terminal, bookmarks,
    history, downloads, settings, shortcuts
  * web search box + user speed-dial tiles (localStorage, shared with the
    classic static new-tab page)

Everything is one self-contained HTML string (no build step, no assets).
"""

from __future__ import annotations

import json

try:
    from browser_core.page_shell import VARS_PLACEHOLDER, page_head
except ImportError:  # imported as browser.browser_core.dashboard
    from browser.browser_core.page_shell import VARS_PLACEHOLDER, page_head

try:
    from browser_core.brand import css_vars as _css_vars
except ImportError:  # imported as browser.browser_core.dashboard
    from browser.browser_core.brand import css_vars as _css_vars

# Dashboard-specific CSS — the shared head (doctype, token injection, base body
# styles, toast helper, "?" shortcut overlay) comes from page_shell.
_DASH_CSS = r"""  :root {
    color-scheme: dark;
    --card: var(--ld-card, #1a2132); --border: var(--ld-border, #232c42);
    --text: var(--ld-text, #e8ecf5); --muted: var(--ld-muted, #8b93a7);
    --accent: var(--ld-accent, #5b9dff); --accent2: var(--ld-accent2, #b46bff);
    --ok: var(--ld-ok, #34d399); --warn: #fbbf24; --err: var(--ld-danger, #ff5b6e);
    --hover: var(--ld-hover, rgba(255,255,255,.09));
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  input, select, button { font: inherit; }
  ::selection { background: rgba(91,157,255,.30); color: var(--text); }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text",
      "Segoe UI", "Helvetica Neue", system-ui, sans-serif;
    font-weight: 400;
    color: var(--text); min-height: 100vh; overflow-x: hidden;
    background: var(--ld-window, #0b0f1a);
    line-height: 1.4;
  }
  /* Faint fixed ambient wash — calm, no motion. */
  body::before {
    content: ""; position: fixed; inset: 0; z-index: 0; pointer-events: none;
    background: radial-gradient(58% 42% at 50% 0%, rgba(91,157,255,.07), transparent 70%);
  }
  header, main { position: relative; z-index: 1; }
  header { display: flex; justify-content: flex-end; align-items: flex-start;
    padding: 22px 34px; flex-wrap: wrap; gap: 12px; }
  #hello-line { font-size: 13px; font-weight: 500; letter-spacing: .01em;
    color: var(--muted); text-align: right; }
  #hello-line b { color: var(--accent); font-weight: 700; }
  #party { position: fixed; inset: 0; display: none; align-items: center; justify-content: center;
    pointer-events: none; z-index: 99; }
  #party .burst { font-size: 30px; font-weight: 800; letter-spacing: -.01em;
    padding: 22px 38px; border-radius: 18px;
    background: var(--card); border: 1px solid var(--accent); color: var(--text);
    box-shadow: 0 0 60px var(--accent); animation: pop .5s ease-out; }
  @keyframes pop { 0% { transform: scale(.6); opacity: 0; } 100% { transform: scale(1); opacity: 1; } }
  main { max-width: 780px; margin: 4vh auto 48px; padding: 0 24px; }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
  }
"""
_CSS = r"""  .search { display: flex; background: var(--card); border: 1px solid var(--border);
    border-radius: 16px; overflow: hidden;
    transition: border-color .15s, box-shadow .15s; }
  .search:focus-within { border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(91,157,255,.16); }
  .search select { background: transparent;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%238b93a7' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
    background-repeat: no-repeat; background-position: right 8px center;
    color: var(--muted); border: none; outline: none;
    padding: 0 22px 0 16px; font-size: 13.5px; font-weight: 600; cursor: pointer;
    appearance: none; -webkit-appearance: none; }
  .search select option { background: var(--ld-panel, #10151f); }
  .search input { flex: 1; background: transparent; border: none; outline: none;
    color: var(--text); font-size: 16px; font-weight: 500; letter-spacing: .005em;
    padding: 15px 8px; }
  .search input::placeholder { color: var(--muted); opacity: 1; }
  .search button { background: var(--accent); border: none; color: #fff;
    font-size: 14.5px; font-weight: 650; letter-spacing: .02em; padding: 0 26px;
    cursor: pointer; transition: filter .12s; }
  .search button:hover { filter: brightness(1.1); }
  .grid { margin-top: 24px; display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
  .tile { display: flex; flex-direction: column; align-items: center; gap: 10px;
    padding: 17px 4px 14px; background: var(--card); border: 1px solid var(--border);
    border-radius: 14px; text-decoration: none; color: var(--text); font-size: 12px;
    font-weight: 600; letter-spacing: .01em;
    position: relative; transition: transform .12s, border-color .12s, background .12s; }
  .tile:hover { transform: translateY(-2px); border-color: var(--accent); background: var(--hover); }
  .tile .ico { font-size: 22px; }
  .tile img { width: 26px; height: 26px; border-radius: 6px; }
  .tile .fav { width: 26px; height: 26px; border-radius: 7px; display: inline-flex;
    align-items: center; justify-content: center; background: var(--hover);
    border: 1px solid var(--border); color: var(--accent); font-size: 14px;
    font-weight: 700; }
  .tile .tname { color: var(--text); font-size: 13px; font-weight: 700; letter-spacing: .02em; }
  .tile .del { position: absolute; top: 3px; right: 6px; color: var(--muted); font-size: 13px; opacity: 0; cursor: pointer; padding: 2px 4px; border-radius: 6px; }
  .tile .del:hover, .tile .del:focus-visible { color: var(--err); background: var(--hover); outline: none; }
  .tile:hover .del, .tile:focus-within .del { opacity: 1; }
  .tile.add { color: var(--muted); font-size: 24px; justify-content: center; cursor: pointer; }
  .tile.add span { font-size: 11px; font-weight: 600; letter-spacing: .05em; }
  .section { margin-top: 28px; font-size: 11px; font-weight: 700; letter-spacing: .18em;
    text-transform: uppercase; color: var(--muted); }
  #addform { margin-top: 14px; display: none; gap: 8px; }
  #addform input { flex: 1; background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; color: var(--text); padding: 10px 12px; font-size: 13.5px;
    font-weight: 500; outline: none; }
  #addform input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(91,157,255,.14); }
  #addform button { background: var(--accent); border: none; color: #fff; border-radius: 10px;
    padding: 0 18px; font-size: 13.5px; font-weight: 650; letter-spacing: .02em; cursor: pointer; }
  #addform button:hover { filter: brightness(1.1); }

"""

# Shortcuts wired into the dashboard's "?" overlay (the Help surface) —
# only existing shortcuts are listed; nothing new is invented here.
_DASH_SHORTCUTS = [
    ("Ctrl+K", "Command palette — fuzzy search over tabs, bookmarks, history, actions"),
    ("Ctrl+`", "Open an agent terminal tab"),
    ("Ctrl+Shift+S", "Save a screenshot of the visible page"),
]

# _DASH_CSS holds the header/greeting chrome; _CSS holds the main content
# (search, tiles, add-form). Both must reach page_head as extra_css —
# _CSS was accidentally dropped from the assembly in 27facf8, which left
# the dashboard's search/tiles unstyled.
_HEAD = page_head(
    "New Tab",
    _DASH_CSS + _CSS,
    css_vars_text=VARS_PLACEHOLDER,
    shortcuts=_DASH_SHORTCUTS,
)
_BODY = r"""<body>
<header>
  <div id="hello-line"><b id="hello"></b> <span id="tagline"></span></div>
</header>
<div id="party"><div class="burst" id="partytext"></div></div>
<main>
  <form class="search" id="searchform">
    <select id="engine">
      <option value="bing">Bing</option>
      <option value="ddg">DuckDuckGo</option>
      <option value="brave">Brave</option>
    </select>
    <input id="q" placeholder="Search the web or type a URL" autocomplete="off" autofocus>
    <button type="submit" id="searchbtn">Go</button>
  </form>

  <div class="section">Apps</div>
  <div class="grid" id="apps"></div>

  <div class="section">Shortcuts</div>
  <div class="grid" id="grid"></div>
  <div id="addform">
    <input id="newname" placeholder="Name (e.g. Stack Overflow)" aria-label="Shortcut name">
    <input id="newurl" placeholder="URL (e.g. https://stackoverflow.com)" aria-label="Shortcut URL">
    <button id="addbtn" type="button">Add</button>
  </div>
</main>
"""
_JS = r"""<script>
const ENGINES = { bing: 'https://www.bing.com/search?q=',
  ddg: 'https://duckduckgo.com/?q=', brave: 'https://search.brave.com/search?q=' };
const APPS = [
  ['🤖', 'AI Assistant', 'luckyd://assistant'],
  ['🖥️', 'Multi Agent Terminal', '/mesh'],
  ['💬', 'LuckyD Chat', '/chat'],
  ['🖥️', 'Agent Terminal', '/terminal'],
  ['📡', 'Network', '/network'],
  ['🔖', 'Bookmarks', 'luckyd://bookmarks'],
  ['🕘', 'History', 'luckyd://history'],
  ['⬇️', 'Downloads', 'luckyd://downloads'],
  ['🧩', 'Extensions', 'luckyd://extensions'],
  ['🎨', 'Settings', 'luckyd://settings'],
  ['⌨️', 'Keyboard Shortcuts', 'luckyd://shortcuts'],
];
const DEFAULTS = [
  ['YouTube','https://www.youtube.com'],
  ['GitHub','https://github.com'], ['ChatGPT','https://chatgpt.com'],
  ['Perplexity','https://www.perplexity.ai'], ['Wikipedia','https://www.wikipedia.org'],
  ['Reddit','https://www.reddit.com']
];
let shortcuts = JSON.parse(localStorage.getItem('ld_shortcuts') || 'null') || DEFAULTS;
// One-time cleanup: drop the retired FMHY and Google tiles from saved shortcuts.
shortcuts = shortcuts.filter(([n, u]) => !/fmhy/i.test(n + u) &&
  !(n === 'Google' && u === 'https://www.google.com'));
localStorage.setItem('ld_shortcuts', JSON.stringify(shortcuts));
// A saved engine we no longer offer (e.g. Google) falls back to DDG.
const _savedEngine = localStorage.getItem('ld_engine');
document.getElementById('engine').value = ENGINES[_savedEngine] ? _savedEngine : 'ddg';

// Time-aware greeting with a rotating LuckyD wink. The browser's display
// name is injected server-side (__DISPLAY_NAME__) — Settings → Appearance.
// Flavor only — no tips/usage advice on the homepage (Dylan 2026-10-22).
// Guidance lives in Help → Keyboard Shortcuts, not on the new-tab page.
const TAGLINES = [
  'your tabs missed you', 'no accounts, no keys, no worries', 'Lucky is ready when you are',
  'surf different',
];
(function greet() {
  const h = new Date().getHours();
  const part = (h < 5 ? 'Up late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening');
  document.getElementById('hello').textContent = '🍀 ' + part + ' — __DISPLAY_NAME__\'s ready.';
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

"""
_JS2 = r"""// Platform tiles from browser_core/platform_tiles.json (TileRegistry).
const PLATFORM_TILES = __PLATFORM_TILES__;
function renderApps() {
  const g = document.getElementById('apps');
  const visible = APPS;
  // A registry tile must never duplicate a built-in app — skip platform
  // tiles whose name already renders above, case-insensitively.
  const seen = new Set(visible.map(([, name]) => name.toLowerCase()));
  visible.concat(PLATFORM_TILES.filter(([, name]) => !seen.has(name.toLowerCase()))).forEach(([ico, name, href]) => {
    const a = document.createElement('a');
    a.className = 'tile'; a.href = href;
    a.innerHTML = '<span class="ico">' + ico + '</span><span class="tname"></span>';
    a.querySelector('span:last-child').textContent = name;
    g.appendChild(a);
  });
}
// Letter-tile favicons — minted locally from the hostname. Every tile gets
// the SAME uniform theme treatment (Dylan 2026-10-22: no per-site rainbow
// colors — real apps don't color shortcut buttons per site). No favicon
// service ever sees your shortcuts.
function tileFor(url) {
  let host = '';
  try { host = new URL(url).hostname.replace(/^www\./, ''); } catch (e) {}
  const letter = host ? host[0].toUpperCase() : '•';
  const tile = document.createElement('span');
  tile.className = 'fav';
  tile.textContent = letter;
  return tile;
}
function render() {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  shortcuts.forEach(([name, url], idx) => {
    const a = document.createElement('a');
    a.className = 'tile'; a.href = url;
    a.appendChild(tileFor(url));
    a.insertAdjacentHTML('beforeend', '<span class="tname"></span>' +
      '<span class="del" role="button" tabindex="0" aria-label="Remove shortcut" title="Remove">&#10005;</span>');
    a.querySelector('span').textContent = name;
    const delBtn = a.querySelector('.del');
    const removeTile = ev => {
      ev.preventDefault(); ev.stopPropagation();
      shortcuts.splice(idx, 1); save(); render();
    };
    delBtn.addEventListener('click', removeTile);
    delBtn.addEventListener('keydown', ev => {
      if (ev.key === 'Enter' || ev.key === ' ') removeTile(ev);
    });
    grid.appendChild(a);
  });
  const add = document.createElement('div');
  add.className = 'tile add'; add.tabIndex = 0; add.setAttribute('role', 'button');
  add.setAttribute('aria-label', 'Add shortcut');
  add.innerHTML = '<div>+<span>Add</span></div>';
  const toggleAdd = () => {
    const f = document.getElementById('addform');
    f.style.display = f.style.display === 'flex' ? 'none' : 'flex';
  };
  add.addEventListener('click', toggleAdd);
  add.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggleAdd(); }
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
DASHBOARD_HTML = _HEAD + _BODY + _JS + _JS2


# ── Homepage animation (Feature 12, Dylan 2026-10-22) ─────────────────
# Cursor-interactive ambient themes for the homepage. Colors come from
# the active theme's --ld-accent/--ld-accent2 tokens at runtime (never a
# hardcoded palette), the layer is pointer-events:none and transform /
# canvas only (no layout changes), the loop pauses when the page or
# window is hidden/unfocused, prefers-reduced-motion renders one static
# frame, and "off" injects NOTHING into the page. Default: Aurora Glow —
# the calmest variant.
ANIMATION_CHOICES = (
    ("aurora", "Aurora Glow"),
    ("particles", "Particle Drift"),
    ("parallax", "Gentle Parallax"),
    ("off", "Off"),
)
HOMEPAGE_ANIMATIONS = frozenset(k for k, _ in ANIMATION_CHOICES if k != "off")
DEFAULT_HOMEPAGE_ANIMATION = "aurora"


def _homepage_animation(settings) -> str:
    try:
        mode = settings.get("homepage_animation", DEFAULT_HOMEPAGE_ANIMATION) if settings else None
    except Exception:
        mode = None
    return mode if mode in dict(ANIMATION_CHOICES) else DEFAULT_HOMEPAGE_ANIMATION


_ANIM_SNIPPET = r"""
<div id="ld-anim" aria-hidden="true"><canvas id="ld-anim-cv"></canvas><div class="ld-blob b1"></div><div class="ld-blob b2"></div><div class="ld-blob b3"></div></div>
<style>
  #ld-anim{position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden}
  #ld-anim-cv{position:absolute;inset:0;width:100%;height:100%;display:none}
  .ld-blob{position:absolute;border-radius:50%;filter:blur(70px);opacity:0;will-change:transform}
  header,main{position:relative;z-index:1}
</style>
<script>
(function(){
  var mode = "__ANIM_MODE__";
  var root = document.getElementById('ld-anim'); if (!root) return;
  var reduce = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  var cs = getComputedStyle(document.documentElement);
  var accent = (cs.getPropertyValue('--ld-accent') || '').trim() || '#5b9dff';
  var accent2 = (cs.getPropertyValue('--ld-accent2') || '').trim() || accent;
  var tx = innerWidth / 2, ty = innerHeight / 3, mx = tx, my = ty;
  addEventListener('pointermove', function(e){ tx = e.clientX; ty = e.clientY; }, {passive: true});
  var raf = 0, running = false;
  function loop(){ draw(performance.now()); if (running) raf = requestAnimationFrame(loop); }
  function start(){ if (running || reduce) return; running = true; raf = requestAnimationFrame(loop); }
  function stop(){ running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }
  document.addEventListener('visibilitychange', function(){ document.hidden ? stop() : start(); });
  addEventListener('blur', stop); addEventListener('focus', start);

  var blobs = root.querySelectorAll('.ld-blob');
  function blob(el, size, color){ el.style.width = el.style.height = size + 'px';
    el.style.background = 'radial-gradient(circle, ' + color + ' 0%, transparent 70%)'; }
  var draw = function(){};

  if (mode === 'aurora' || mode === 'parallax') {
    var s = Math.max(innerWidth, innerHeight);
    blob(blobs[0], s * 0.55, accent); blob(blobs[1], s * 0.45, accent2); blob(blobs[2], s * 0.34, accent);
    blobs.forEach(function(b){ b.style.opacity = mode === 'aurora' ? '0.34' : '0.22'; });
    if (mode === 'parallax') blobs[2].style.display = 'none';
    var hero = document.querySelector('header'), main = document.querySelector('main');
    draw = function(now){
      mx += (tx - mx) * 0.055; my += (ty - my) * 0.055;
      var t = now * 0.00006, dx = mx - innerWidth / 2, dy = my - innerHeight / 2;
      blobs[0].style.transform = 'translate(' + (-s * 0.12 + Math.cos(t) * 40 + dx * 0.03) + 'px,' + (-s * 0.18 + Math.sin(t * 1.3) * 30 + dy * 0.03) + 'px)';
      blobs[1].style.transform = 'translate(' + (innerWidth - s * 0.38 + Math.sin(t * 0.8) * 36 - dx * 0.02) + 'px,' + (innerHeight - s * 0.34 + Math.cos(t) * 26 - dy * 0.02) + 'px)';
      if (mode === 'aurora') {
        blobs[2].style.transform = 'translate(' + (mx - s * 0.17) + 'px,' + (my - s * 0.17) + 'px)';
      } else {
        if (hero) hero.style.transform = 'translate(' + dx * 0.012 + 'px,' + dy * 0.008 + 'px)';
        if (main) main.style.transform = 'translate(' + dx * 0.006 + 'px,' + dy * 0.004 + 'px)';
      }
    };
  } else if (mode === 'particles') {
    blobs.forEach(function(b){ b.style.display = 'none'; });
    var cv = document.getElementById('ld-anim-cv'); cv.style.display = 'block';
    var ctx = cv.getContext('2d'), W = 0, H = 0, ps = [];
    function size(){
      var dpr = Math.min(devicePixelRatio || 1, 1.5);
      W = innerWidth; H = innerHeight; cv.width = W * dpr; cv.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var n = Math.max(24, Math.min(70, Math.floor(W * H / 26000)));
      ps = Array.from({length: n}, function(){ return { x: Math.random() * W, y: Math.random() * H,
        vx: (Math.random() - 0.5) * 0.22, vy: (Math.random() - 0.5) * 0.22,
        r: 1 + Math.random() * 1.6, c: Math.random() < 0.5 ? accent : accent2 }; });
    }
    size(); addEventListener('resize', size);
    draw = function(){
      ctx.clearRect(0, 0, W, H);
      for (var i = 0; i < ps.length; i++) { var p = ps[i];
        var ddx = p.x - mx, ddy = p.y - my, d2 = ddx * ddx + ddy * ddy;
        if (d2 < 8100 && d2 > 0.01) { var d = Math.sqrt(d2); p.vx += ddx / d * 0.012; p.vy += ddy / d * 0.012; }
        p.vx *= 0.995; p.vy *= 0.995; p.x += p.vx; p.y += p.vy;
        if (p.x < -4) p.x = W + 4; if (p.x > W + 4) p.x = -4;
        if (p.y < -4) p.y = H + 4; if (p.y > H + 4) p.y = -4;
        ctx.globalAlpha = 0.55; ctx.fillStyle = p.c;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.2832); ctx.fill();
      }
      ctx.globalAlpha = 1;
    };
  }
  if (reduce) { draw(1200); } else { start(); }
})();
</script>
"""


def homepage_animation_html(mode: str) -> str:
    """The animation layer markup for one enabled mode ("" for off)."""
    if mode not in HOMEPAGE_ANIMATIONS:
        return ""
    return _ANIM_SNIPPET.replace("__ANIM_MODE__", mode)


def dashboard_html(settings=None) -> str:
    """The dashboard with the active theme's design tokens injected, so the
    new-tab hub matches the Qt chrome and sidebar exactly.

    Authentication uses the HttpOnly session cookie the browser sets on its
    own profile (4.0) — no token is injected into the page, and the
    dashboard's same-origin fetch() calls authenticate automatically.
    """
    block = "  " + _css_vars(settings) + "\n"
    if VARS_PLACEHOLDER in DASHBOARD_HTML:
        html = DASHBOARD_HTML.replace("  " + VARS_PLACEHOLDER + "\n", block, 1)
    else:
        html = "<style>" + _css_vars(settings) + "</style>" + DASHBOARD_HTML
    # Platform tiles: registry-driven extras appended to the built-in Apps.
    try:
        from browser_core.tile_registry import load_all_tiles

        extra = [[t.icon, t.name, t.url] for t in load_all_tiles() if t.enabled]
    except Exception:
        extra = []  # a broken config must never take the dashboard down
    # Custom display name (Settings → Appearance) for the greeting line.
    try:
        try:
            from browser_core.settings import display_name as _display_name
        except ImportError:
            from browser.browser_core.settings import display_name as _display_name

        name = _display_name(settings)
    except Exception:
        name = "LuckyD Browser"
    # The name lands inside a single-quoted JS string: escape quotes,
    # backslashes and "<" (a "</script>" in a name must never break out).
    name_js = name.replace("\\", "\\\\").replace("'", "\\'").replace("<", "\\u003c")
    out = html.replace("__PLATFORM_TILES__", json.dumps(extra)).replace("__DISPLAY_NAME__", name_js)
    anim = homepage_animation_html(_homepage_animation(settings))
    if anim:
        out = out.replace("</body>", anim + "</body>", 1)
    return out


_NETMON_CSS = r"""  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--ld-window); color: var(--ld-text);
         font: 13px/1.45 system-ui, "Segoe UI", sans-serif; }
  header { display: flex; gap: 10px; align-items: center; padding: 12px 16px;
      background: var(--ld-panel); border-bottom: 1px solid var(--ld-border); flex-wrap: wrap; }
  h1 { font-size: 16px; margin: 0; }
  .target { color: var(--ld-muted); font-size: 12px; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap; max-width: 40ch; }
  .sp { flex: 1; }
  button { border: 1px solid var(--ld-border); background: var(--ld-card); color: var(--ld-text);
      border-radius: var(--ld-r-sm); padding: 7px 12px; font: 600 12px system-ui; cursor: pointer; }
  button:hover { border-color: color-mix(in srgb, var(--ld-border) 55%, var(--ld-text)); }
  button.on { color: var(--ld-danger); border-color: var(--ld-danger); }
  input { background: var(--ld-card); border: 1px solid var(--ld-border); border-radius: var(--ld-r-sm);
      padding: 7px 10px; color: var(--ld-text); font: inherit; width: 170px; }
  input:focus { outline: none; border-color: var(--ld-accent); box-shadow: var(--ld-focus-ring); }
  table { width: 100%; border-collapse: collapse; }
  th { position: sticky; top: 0; background: var(--ld-panel); text-align: left;
      padding: 8px 10px; font-size: 11px; letter-spacing: 1px; color: var(--ld-muted);
      text-transform: uppercase; border-bottom: 1px solid var(--ld-border); }
  td { padding: 5px 10px; border-bottom: 1px solid var(--ld-border); font-size: 12px;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 0; }
  tr:hover td { background: var(--ld-panel); }
  td.u { width: 100%; }
  .m { font-weight: 700; color: var(--ld-accent); }
  .s2 { color: var(--ld-ok); } .s3 { color: var(--ld-accent); } .s4 { color: var(--ld-warn); }
  .s5, .serr { color: var(--ld-danger); } .s0 { color: var(--ld-muted); }
  .empty { text-align: center; color: var(--ld-faint); padding: 40px 0; }
"""

_NETMON_HTML = (
    page_head("Network Monitor", _NETMON_CSS)
    + r"""<body>
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
)

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
