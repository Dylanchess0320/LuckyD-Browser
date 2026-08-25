"""HTML for the in-browser LuckyD Code terminal (xterm.js + PTY bridge)."""

from __future__ import annotations

import contextlib
from pathlib import Path

# Vendored xterm assets: browser/browser_core/terminal_page.py → ../assets/terminal
STATIC_DIR = Path(__file__).resolve().parent.parent / "assets" / "terminal"

_WS_HOST = "127.0.0.1"
_WS_PORT = 9881  # must match browser_app's "terminal_port" default

# Keep in sync with terminal_server.SHELLS (allowlist lives there).
_SHELL_LABELS = {"agent": "Agent", "agent2": "Agent 2", "powershell": "PowerShell", "cmd": "CMD"}

# Agent Mesh dock — the CLIs wired up in ~/agent-mesh, shown as an agent
# picker in the terminal tab. (label, emoji, accent color, one-line blurb)
_MESH_AGENTS = {
    "mesh-claude": ("Claude", "🟠", "#d97706", "Anthropic · architect"),
    "mesh-codex": ("Codex", "🟢", "#10b981", "OpenAI · builder"),
    "mesh-copilot": ("Copilot", "⚫", "#8b9bb4", "GitHub · reviewer"),
    "mesh-qwen": ("Qwen", "🟣", "#a855f7", "Qwen · test writer"),
    "mesh-opencode": ("OpenCode", "🔵", "#3b82f6", "Anomaly · implementer"),
    "mesh-cline": ("Cline", "🟡", "#eab308", "autonomous builder"),
    "mesh-openclaw": ("OpenClaw", "🦞", "#ef4444", "100+ skills"),
    "mesh-dsh": ("DeepSeek", "🐋", "#06b6d4", "DeepSeek harness"),
    "mesh-pi": ("Pi", "⚪", "#94a3b8", "minimal toolkit"),
}


def _mesh_available() -> dict:
    """Which mesh agent CLIs are installed (probed via terminal_server)."""
    try:
        from . import terminal_server

        return terminal_server.mesh_shells_available()
    except Exception:
        return {}


def _mesh_dock_html() -> str:
    """Render the Agent Mesh dock: one chip per agent, dimmed when the CLI
    isn't installed (clicking it explains how to install via `mesh install`)."""
    avail = _mesh_available()
    chips = []
    for shell, (label, emoji, color, blurb) in _MESH_AGENTS.items():
        ok = avail.get(shell, False)
        cls = "chip" + (" on" if ok else " off")
        state = "ready" if ok else "not installed"
        chips.append(
            f'<button class="{cls}" data-sh="{shell}" data-avail="{str(ok).lower()}" '
            f'style="--ac:{color}" title="{label} — {blurb} ({state})">'
            f'<span class="ce">{emoji}</span>{label}</button>'
        )
    return (
        '<div id="meshdock"><div id="meshhead"><span class="mh-t">◈ AGENT MESH</span>'
        '<span class="mh-s">pick an agent — each gets its own live PTY session</span></div>'
        '<div id="meshchips">' + "".join(chips) + "</div></div>"
    )


def _mesh_dock_css() -> str:
    return """
  #meshdock{background:linear-gradient(180deg,#0d1320,#0b0f16);border-bottom:1px solid #1e293b;
       padding:10px 14px 12px;font:600 13px/1 system-ui,Segoe UI,Arial}
  #meshhead{display:flex;align-items:baseline;gap:10px;margin-bottom:9px}
  #meshhead .mh-t{color:#e2e8f0;letter-spacing:.14em;font-size:12px;font-weight:800}
  #meshhead .mh-s{color:#475569;font-size:11px;font-weight:500}
  #meshchips{display:flex;flex-wrap:wrap;gap:8px}
  .chip{display:flex;align-items:center;gap:7px;padding:6px 12px;border-radius:999px;
       border:1px solid #1e293b;background:#0f1622;color:#cbd5e1;cursor:pointer;
       font:600 12px/1 system-ui,Segoe UI,Arial;transition:all .15s ease}
  .chip .ce{font-size:13px}
  .chip:hover{border-color:var(--ac);transform:translateY(-1px);
       box-shadow:0 4px 14px rgba(0,0,0,.4)}
  .chip.on{border-color:color-mix(in srgb,var(--ac) 55%,transparent)}
  .chip.on:active,.chip.sel{border-color:var(--ac);color:#fff;
       background:color-mix(in srgb,var(--ac) 18%,#0f1622);
       box-shadow:0 0 12px color-mix(in srgb,var(--ac) 35%,transparent)}
  .chip.off{opacity:.42}
  .chip.off:hover{opacity:.75}
"""


def terminal_html(settings=None, shell: str = "agent") -> str:
    """The terminal tab page. Connects to the PTY bridge over WebSocket.

    Honors the browser's ``terminal_port`` setting so the page always dials
    the same port the WS->PTY bridge actually bound (default 9881). ``shell``
    picks the spawned process — the agent CLI, PowerShell, or CMD; every tab
    gets its own independent ConPTY session, so terminals multiply freely.
    """
    port = _WS_PORT
    if settings is not None:
        with contextlib.suppress(TypeError, ValueError, AttributeError):
            port = int(settings.get("terminal_port", _WS_PORT) or _WS_PORT)
    shell = (shell or "agent").strip().lower()
    if shell not in _SHELL_LABELS and shell not in _MESH_AGENTS:
        shell = "agent"
    return (
        _HTML.replace("__WS_URL__", f"ws://{_WS_HOST}:{port}")
        .replace("__SHELL__", shell)
        .replace("__MESH_DOCK__", _mesh_dock_html())
        .replace("__MESH_CSS__", _mesh_dock_css())
    )


_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Terminal</title>
<link rel="stylesheet" href="/static/terminal/xterm.css">
<style>
  html,body{margin:0;height:100%;background:#0b0f16;overflow:hidden}
  #bar{display:flex;align-items:center;gap:10px;padding:8px 14px;
       background:#0f1622;border-bottom:1px solid #1e293b;
       font:600 13px/1 system-ui,Segoe UI,Arial;color:#cbd5e1}
  #bar .dot{width:9px;height:9px;border-radius:50%;background:#64748b;flex:0 0 auto}
  #bar .dot.on{background:#34d399}
  #bar .dot.off{background:#f87171}
  #status{color:#64748b;font-weight:500;font-size:12px}
  #hint{margin-left:auto;color:#475569;font-weight:500;font-size:11px}
  #wrap{position:absolute;top:37px;left:0;right:0;bottom:0;padding:6px 4px}
  body.has-mesh #wrap{top:calc(37px + var(--dockh,104px))}
  #meshdock{position:fixed;top:37px;left:0;right:0;z-index:50}
  __MESH_CSS__
  #term{height:100%}
  #menu{position:fixed;z-index:99;min-width:200px;background:#0f1622;
        border:1px solid #1e293b;border-radius:8px;padding:4px;display:none;
        box-shadow:0 8px 24px rgba(0,0,0,.55);
        font:500 13px/1.4 system-ui,Segoe UI,Arial;color:#cbd5e1}
  #menu .mi{display:flex;justify-content:space-between;gap:18px;padding:6px 10px;
        border-radius:5px;cursor:default;white-space:nowrap}
  #menu .mi:hover{background:#1e293b}
  #menu .mi.off{opacity:.38;pointer-events:none}
  #menu .mi span{color:#64748b;font-size:11px}
  #menu .sep{height:1px;background:#1e293b;margin:4px 6px}
  .sh{border:1px solid #1e293b;background:transparent;color:#64748b;
      font:600 11px/1 system-ui;padding:4px 10px;border-radius:6px;cursor:pointer}
  .sh:hover{color:#cbd5e1;border-color:#334155}
  .sh.on{color:#34d399;border-color:#34d399;background:rgba(52,211,153,.08)}
</style></head><body>
<div id="bar"><span id="dot" class="dot"></span><b id="title">&#9000; Terminal</b>
  <button class="sh" data-sh="agent" title="LuckyD Code agent CLI">Agent</button>
  <button class="sh" data-sh="agent2" title="2nd agent — standalone coding-agent CLI (Desktop shortcut)">Agent 2</button>
  <button class="sh" data-sh="powershell" title="Plain PowerShell console">PowerShell</button>
  <button class="sh" data-sh="cmd" title="Plain cmd.exe console">CMD</button>
__MESH_DOCK__
  <span id="status">connecting&hellip;</span>
  <span id="hint">Ctrl+Shift+C copy &middot; Ctrl+Shift+V paste &middot; right-click for menu</span></div>
<div id="wrap"><div id="term"></div></div>
<div id="menu">
  <div class="mi" data-a="copy">Copy <span>Ctrl+Shift+C</span></div>
  <div class="mi" data-a="paste">Paste <span>Ctrl+Shift+V</span></div>
  <div class="sep"></div>
  <div class="mi" data-a="selall">Select All</div>
  <div class="mi" data-a="clear">Clear</div>
</div>
<script src="/static/terminal/xterm.js"></script>
<script src="/static/terminal/xterm-addon-fit.js"></script>
<script>
const WS_URL = "__WS_URL__";
let SHELL = "__SHELL__";
const SHELL_LABELS = {agent: 'Agent', agent2: 'Agent 2', powershell: 'PowerShell', cmd: 'CMD',
  'mesh-claude': 'Claude', 'mesh-codex': 'Codex', 'mesh-copilot': 'Copilot',
  'mesh-qwen': 'Qwen', 'mesh-opencode': 'OpenCode', 'mesh-cline': 'Cline',
  'mesh-openclaw': 'OpenClaw', 'mesh-dsh': 'DeepSeek', 'mesh-pi': 'Pi'};
const dot = document.getElementById('dot');
const statusEl = document.getElementById('status');
const titleEl = document.getElementById('title');
function paintShell(){
  document.querySelectorAll('.sh').forEach(b =>
    b.classList.toggle('on', b.dataset.sh === SHELL));
  document.querySelectorAll('.chip').forEach(c =>
    c.classList.toggle('sel', c.dataset.sh === SHELL));
  const label = SHELL_LABELS[SHELL] || 'Agent';
  titleEl.innerHTML = '&#9000; Terminal — ' + label;
  document.title = 'Terminal — ' + label;
}
function switchShell(name){
  if (!SHELL_LABELS[name] || name === SHELL) return;
  SHELL = name;  // reconnect spawns a fresh, independent PTY for the shell
  paintShell();
  try { if (ws) ws.onclose = null, ws.close(); } catch(e) {}
  retry = 0;
  term.reset();
  connect();
}
document.querySelectorAll('.sh').forEach(b =>
  b.addEventListener('click', () => switchShell(b.dataset.sh)));
// Agent Mesh dock: available agents switch shells; missing ones explain
// how to install instead of spawning a dead PTY.
document.querySelectorAll('.chip').forEach(c =>
  c.addEventListener('click', () => {
    if (c.dataset.avail === 'true') { switchShell(c.dataset.sh); }
    else flash(c.textContent.trim() + ' is not installed — run: mesh install ' + c.dataset.sh.replace('mesh-',''));
  }));
// Size the terminal below the dock (dock height varies with chip wrapping).
const dock = document.getElementById('meshdock');
if (dock) {
  document.body.classList.add('has-mesh');
  const setDockH = () => document.body.style.setProperty('--dockh', dock.offsetHeight + 'px');
  setDockH();
  new ResizeObserver(() => { setDockH(); setTimeout(refit, 30); }).observe(dock);
}
paintShell();
const term = new Terminal({
  cursorBlink: true, convertEol: false, fontSize: 14,
  fontFamily: 'Cascadia Mono, Consolas, "Courier New", monospace',
  scrollback: 5000, allowProposedApi: true,
  theme: { background:'#0b0f16', foreground:'#e2e8f0', cursor:'#00e5ff',
           selectionBackground:'#264f78' }
});
const fit = new FitAddon.FitAddon();
term.loadAddon(fit);
term.open(document.getElementById('term'));
function refit(){
  try { fit.fit(); } catch(e) {}
  if (ws && ws.readyState === 1)
    ws.send(JSON.stringify({type:'resize', cols:term.cols, rows:term.rows}));
}
window.addEventListener('resize', refit);
let ws = null, retry = 0;
function setState(cls, msg){ dot.className = 'dot ' + cls; statusEl.textContent = msg; }
function connect(){
  setState('', 'connecting…');
  // Advertise our real dimensions so the bridge spawns the PTY at the right
  // size — a birth-size mismatch makes fullscreen CLIs wrap off-screen.
  ws = new WebSocket(WS_URL + '?cols=' + term.cols + '&rows=' + term.rows + '&shell=' + SHELL);
  ws.onopen = () => { retry = 0; setState('on', 'connected'); refit(); term.focus(); };
  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') term.write(ev.data);
    else ev.data.arrayBuffer().then(b => term.write(new Uint8Array(b)));
  };
  ws.onclose = () => {
    setState('off', 'disconnected — retrying…');
    retry = Math.min(retry + 1, 8);
    setTimeout(connect, 400 * retry);
  };
  ws.onerror = () => { try { ws.close(); } catch(e) {} };
}
term.onData(d => { if (ws && ws.readyState === 1) ws.send(d); });
term.onBinary(d => {
  if (!ws || ws.readyState !== 1) return;
  const buf = new Uint8Array(d.length);
  for (let i = 0; i < d.length; i++) buf[i] = d.charCodeAt(i) & 255;
  ws.send(buf);
});
// ── copy & paste ──────────────────────────────────────────────────────
// Plain Ctrl+C stays SIGINT and Ctrl+V stays readline quoted-insert — like
// every real terminal, the clipboard lives on the Shift variants (plus
// Ctrl/Shift+Insert and a right-click menu). Paste goes through term.paste()
// so bracketed-paste-aware CLIs receive it properly wrapped.
const menu = document.getElementById('menu');
function flash(msg){
  const prev = statusEl.textContent;
  statusEl.textContent = msg;
  clearTimeout(flash._t);
  flash._t = setTimeout(() => { statusEl.textContent = prev; }, 1400);
}
// Clipboard promises can HANG forever in engines that block the API (the
// permission request never settles) — race every call against a timeout so
// the user always gets feedback instead of a silently dead menu.
function clipTimeout(promise){
  return Promise.race([
    promise,
    new Promise((_, rej) => setTimeout(() => rej(new Error('clipboard timeout')), 1500)),
  ]);
}
async function clipWrite(text){
  if (!text) return false;
  try { await clipTimeout(navigator.clipboard.writeText(text)); return true; } catch(e) {}
  try {  // execCommand fallback for locked-down engines
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch(e) { return false; }
}
async function clipRead(){
  try { return await clipTimeout(navigator.clipboard.readText()); }
  catch(e) { return null; }
}
function copySelection(){
  const sel = term.getSelection();
  if (!sel) return false;
  clipWrite(sel).then(ok => flash(ok ? 'copied ' + sel.length + ' chars' : 'copy failed'));
  return true;
}
async function pasteClipboard(){
  const text = await clipRead();
  if (text) { term.paste(text); flash('pasted ' + text.length + ' chars'); }
  else { flash('clipboard unavailable'); }
  term.focus();
}
term.attachCustomKeyEventHandler(ev => {
  if (ev.type !== 'keydown') return true;
  const code = ev.code || '';
  if (ev.ctrlKey && ev.shiftKey && code === 'KeyC') { copySelection(); return false; }
  if (ev.ctrlKey && ev.shiftKey && code === 'KeyV') { pasteClipboard(); return false; }
  if (ev.ctrlKey && !ev.shiftKey && code === 'Insert') { copySelection(); return false; }
  if (!ev.ctrlKey && ev.shiftKey && code === 'Insert') { pasteClipboard(); return false; }
  return true;
});
function hideMenu(){ menu.style.display = 'none'; }
document.getElementById('wrap').addEventListener('contextmenu', ev => {
  ev.preventDefault();
  menu.querySelector('[data-a=copy]').classList.toggle('off', !term.hasSelection());
  menu.style.display = 'block';
  menu.style.left = Math.min(ev.clientX, innerWidth - menu.offsetWidth - 6) + 'px';
  menu.style.top = Math.min(ev.clientY, innerHeight - menu.offsetHeight - 6) + 'px';
});
menu.addEventListener('click', ev => {
  const item = ev.target.closest('.mi');
  hideMenu();
  const a = item ? item.dataset.a : '';
  if (a === 'copy') copySelection();
  else if (a === 'paste') pasteClipboard();
  else if (a === 'selall') { term.selectAll(); term.focus(); }
  else if (a === 'clear') { term.clear(); term.focus(); }
});
document.addEventListener('click', ev => { if (!menu.contains(ev.target)) hideMenu(); });
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') hideMenu(); });
// Fit BEFORE the first connect so the URL carries the true cols/rows.
try { fit.fit(); } catch(e) {}
connect();
setTimeout(refit, 60);
</script></body></html>"""
