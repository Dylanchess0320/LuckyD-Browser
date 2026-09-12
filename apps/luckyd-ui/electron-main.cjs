// LuckyD Electron shell — cross-platform desktop wrapper.
// Spawns the existing Python backend (web_server.py / luckyd-code.exe)
// as a sidecar, reads .luckyd-code/hq_token, injects it into the UI.
// Zero backend changes. Your memory/schedules/trust store untouched.

const { app, BrowserWindow, shell, dialog } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const BACKEND_PORT = Number(process.env.LUCKYD_PORT || 8000);
const BACKEND_HOST = '127.0.0.1';
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

// ── Logging / crash capture ───────────────────────────────────────────
// Every lifecycle event and any unhandled main-process exception is written
// to a readable log instead of leaving the scary "A JavaScript error occurred
// in the main process" dialog with no details.
const LUCKY_LOG = path.join(app.getPath('userData'), 'luckyd.log');

function logMain(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  console.error('[luckyd]', msg);
  try {
    fs.mkdirSync(path.dirname(LUCKY_LOG), { recursive: true });
    fs.appendFileSync(LUCKY_LOG, line);
  } catch { /* no disk */ }
}

process.on('uncaughtException', (err) => {
  logMain(`UNCAUGHT: ${err && err.stack ? err.stack : String(err)}`);
});
process.on('unhandledRejection', (reason) => {
  logMain(`REJECTION: ${reason && reason.stack ? reason.stack : String(reason)}`);
});

// ── Single instance ───────────────────────────────────────────────────
// The entire app lifecycle lives in the `else` branch. A second instance
// must quit immediately — if app.whenReady() stays registered it can fire
// after app.quit() and crash with "cannot create window after quit".
const SINGLE_LOCK = app.requestSingleInstanceLock();

if (!SINGLE_LOCK) {
  logMain('second instance — quitting to honor the single-instance lock');
  app.exit(0);
} else {
  app.on('second-instance', () => {
    const w = BrowserWindow.getAllWindows()[0];
    if (w) {
      if (w.isMinimized()) w.restore();
      w.focus();
    }
  });
}

// Repo root = two levels above apps/luckyd-ui (dev) or resources (packaged).
function repoRoot() {
  if (app.isPackaged) return path.join(process.resourcesPath, 'backend');
  return path.join(__dirname, '..', '..', '..');
}

function readToken() {
  const p = path.join(repoRoot(), '.luckyd-code', 'hq_token');
  try {
    const t = fs.readFileSync(p, 'utf-8').trim();
    if (t) return t;
  } catch { /* backend creates it on first boot */ }
  return '';
}

let backendProc = null;
let bridgeProc = null;
let splash = null;
let windowCreated = false;

// ── Agents bridge (terminal + mesh + model switching) ─────────────────
// A small Python sidecar (scripts/luckyd_agents_bridge.py) that reuses the
// browser's own terminal_server/terminal_page modules to serve the Agent
// Mesh xterm pages on 127.0.0.1:9885 and the model-switch API. Dev: run
// from source with the system Python. Packaged: a bundled
// luckyd-agents-bridge.exe. Never fatal — the Agents tab just shows
// "bridge offline" if it can't start.
const BRIDGE_PORT = 9885;

function bridgeCmd() {
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, 'luckyd-agents-bridge.exe');
    if (fs.existsSync(exe)) {
      return {
        cmd: exe,
        args: ['--root', path.join(process.resourcesPath, 'backend')],
      };
    }
    return null;
  }
  const script = path.join(__dirname, 'scripts', 'luckyd_agents_bridge.py');
  if (fs.existsSync(script)) {
    return {
      cmd: process.platform === 'win32' ? 'python' : 'python3',
      args: [script, '--root', repoRoot()],
    };
  }
  return null;
}

function startBridge() {
  const found = bridgeCmd();
  if (!found) {
    logMain('[bridge] not found — Agents tab will show offline');
    return;
  }
  try {
    bridgeProc = spawn(found.cmd, found.args, {
      cwd: path.dirname(found.cmd === 'python' ? __dirname : found.args[0] || __dirname),
      windowsHide: true,
      stdio: 'ignore',
    });
    bridgeProc.on('error', (e) => logMain(`[bridge] spawn failed: ${e.message}`));
    bridgeProc.on('exit', (code) => {
      if (code && code !== 0) logMain(`[bridge] exited code ${code}`);
    });
    logMain('[bridge] spawned');
  } catch (e) {
    logMain(`[bridge] spawn exception: ${e.message}`);
  }
}

function backendCmd() {
  const root = repoRoot();
  const exe = path.join(root, 'luckyd-code.exe');
  const py = path.join(root, 'web_server.py');
  if (!app.isPackaged && fs.existsSync(py)) {
    return {
      cmd: process.platform === 'win32' ? 'python' : 'python3',
      args: [py, '--web', '--port', String(BACKEND_PORT), '--host', BACKEND_HOST],
      cwd: root,
    };
  }
  if (fs.existsSync(exe)) {
    return { cmd: exe, args: ['--web', '--port', String(BACKEND_PORT), '--host', BACKEND_HOST], cwd: root };
  }
  return null;
}

function startBackend() {
  // Never double-spawn: the packaged app + a dev server can collide on :8000.
  const found = backendCmd();
  if (!found) {
    console.log('[luckyd] no backend found (expected web_server.py or luckyd-code.exe)');
    return;
  }
  console.log(`[luckyd] starting backend: ${found.cmd} ${found.args.join(' ')}`);
  backendProc = spawn(found.cmd, found.args, { cwd: found.cwd, windowsHide: true, stdio: 'ignore' });
  backendProc.on('error', (e) => console.error('[luckyd] backend spawn failed:', e.message));
}

async function waitForBackend(tries = 60) {
  for (let i = 0; i < tries; i++) {
    try {
      const res = await fetch(`${BACKEND_URL}/health`);
      if (res.ok) return true;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

function showSplash() {
  splash = new BrowserWindow({
    width: 420, height: 300, frame: false, resizable: false,
    backgroundColor: '#0b0f1a', alwaysOnTop: true, center: true,
  });
  splash.loadURL(
    'data:text/html;charset=utf-8,' +
      encodeURIComponent(`<body style="margin:0;background:#0b0f1a;color:#e8ecf5;font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh">
      <div style="font-size:48px">🍀</div><h2>LuckyD</h2><p style="color:#8b93a7">Starting local brain…</p></body>`),
  );
}

async function createWindow() {
  // Guard: only one real window family per process.
  if (windowCreated || !SINGLE_LOCK) return;
  windowCreated = true;

  showSplash();
  // Only spawn when nothing answers — respects an already-running backend.
  let ok = await waitForBackend(4);
  if (!ok) {
    startBackend();
    ok = await waitForBackend();
  }
  logMain('backend ' + (ok ? 'ready' : 'NOT reachable — UI will show reconnect help'));

  // Agents bridge: start (or reuse one already listening on 9885).
  let bridgeOk = false;
  try {
    const res = await fetch('http://127.0.0.1:9885/api/ping');
    bridgeOk = res.ok;
  } catch { /* not running */ }
  if (!bridgeOk) startBridge();

  try { splash?.close(); } catch { /* gone */ }
  splash = null;

  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: '#0b0f1a',
    autoHideMenuBar: true,
    icon: app.isPackaged
      ? path.join(process.resourcesPath, 'icon.ico')
      : path.join(__dirname, '..', '..', 'browser', 'assets', 'professional_icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      sandbox: true,
      // Token handshake: main reads .luckyd-code/hq_token from disk, the
      // renderer only sees the value (never the filesystem).
      additionalArguments: [
        `--luckyd-url=${BACKEND_URL}`,
        `--luckyd-token=${readToken()}`,
      ],
    },
  });

  // Open external links in the real browser, never in the app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith('http://127.0.0.1') && !url.startsWith('http://localhost')) {
      shell.openExternal(url);
      return { action: 'deny' };
    }
    return { action: 'allow' };
  });

  try {
    if (!app.isPackaged && process.env.LUCKYD_DEV_URL) {
      await win.loadURL(process.env.LUCKYD_DEV_URL);
    } else {
      await win.loadFile(path.join(__dirname, 'dist', 'index.html'));
    }
  } catch (e) {
    logMain(`load failed: ${e && e.message ? e.message : String(e)}`);
    dialog.showMessageBox({
      type: 'error', title: 'LuckyD', message: 'Could not load the UI.',
      detail: String(e && e.message ? e.message : e), buttons: ['OK'],
    });
  }
}

// ── App lifecycle (first instance only) ───────────────────────────────
if (SINGLE_LOCK) {
  app.whenReady().then(createWindow);
  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) void createWindow();
  });
  app.on('before-quit', () => {
    try { backendProc?.kill(); } catch { /* already dead */ }
    try { bridgeProc?.kill(); } catch { /* already dead */ }
  });
}

