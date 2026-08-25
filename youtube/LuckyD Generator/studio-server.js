#!/usr/bin/env node
// studio-server.js
// ---------------------------------------------------------------
// Deck Studio — the local HTTP service that lets LuckyD Browser use
// the Marp pipeline as a platform tile (Phase 3 of the platform plan).
//
//   GET  /health                 -> {"ok":true}  (dashboard probe)
//   GET  /api/decks              -> JSON list of decks/*.md
//   POST /api/build  {deck:..}   -> runs build.js (paginate -> images ->
//                                  filter -> video -> marp -> verify-fit
//                                  repair loop), returns the export URL
//   GET  /export/<file>          -> serves exported decks + images
//
// Run:  node studio-server.js [port]        (default 8770)
//
// Optional studio.config.json next to this file:
//   { "marpCliPath": "C:/path/to/marp-cli.js" }
// If omitted, we try require.resolve("@marp-team/marp-cli") then npx.
// No dependencies beyond what the pipeline already uses.
// ---------------------------------------------------------------

const http = require("http");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const PORT = Number(process.argv[2]) || 8770;
const HERE = __dirname;
const DECKS_DIR = path.join(HERE, "decks");
const EXPORT_DIR = path.join(HERE, "export");
const IMAGES_DIR = path.join(EXPORT_DIR, "images");
const BANNED = path.join(DECKS_DIR, "banned-words.txt");
const THEMES_DIR = path.join(HERE, "themes");

let marpCliPath = null;
try {
  const cfg = JSON.parse(fs.readFileSync(path.join(HERE, "studio.config.json"), "utf8"));
  marpCliPath = cfg.marpCliPath || null;
} catch (e) { /* no config file — fine */ }
if (!marpCliPath) {
  try { marpCliPath = require.resolve("@marp-team/marp-cli"); } catch (e) { /* npx fallback below */ }
}

function send(res, code, body, type = "application/json") {
  res.writeHead(code, { "Content-Type": type, "Cache-Control": "no-store" });
  res.end(typeof body === "string" ? body : JSON.stringify(body));
}

function listDecks() {
  try {
    return fs.readdirSync(DECKS_DIR)
      .filter((f) => f.toLowerCase().endsWith(".md") && !f.startsWith("_"))
      .map((f) => {
        const st = fs.statSync(path.join(DECKS_DIR, f));
        return { deck: f, modified: st.mtime.toISOString() };
      });
  } catch (e) { return []; }
}

// One build at a time — the pipeline is heavy (AI image gen + headless browser).
let building = false;
let lastResult = null;

function buildDeck(deckName) {
  const safe = path.basename(deckName); // no traversal
  const input = path.join(DECKS_DIR, safe);
  if (!fs.existsSync(input)) throw new Error(`deck not found: ${safe}`);

  const base = path.basename(safe, ".md");
  const outHtml = path.join(EXPORT_DIR, `${base}.html`);
  fs.mkdirSync(IMAGES_DIR, { recursive: true });

  const args = [path.join(HERE, "build.js"), input, outHtml, IMAGES_DIR,
    BANNED, THEMES_DIR, process.execPath,
    marpCliPath || "@marp-team/marp-cli/build/marp-cli.js"];
  const started = Date.now();
  execFileSync(process.execPath, args, { cwd: HERE, stdio: "pipe", timeout: 15 * 60 * 1000 });
  return { ok: true, deck: safe, url: `/export/${encodeURIComponent(base)}.html`,
           seconds: Math.round((Date.now() - started) / 1000) };
}

const MIME = { ".html": "text/html", ".png": "image/png", ".jpg": "image/jpeg",
  ".css": "text/css", ".md": "text/markdown" };

const server = http.createServer((req, res) => {
  const u = new URL(req.url, `http://127.0.0.1:${PORT}`);
  try {
    if (u.pathname === "/health") return send(res, 200, { ok: true, service: "deck-studio",
      building, lastResult });

    if (u.pathname === "/api/decks") return send(res, 200, { decks: listDecks() });

    if (u.pathname === "/api/build") {
      if (req.method !== "POST") return send(res, 405, { error: "use POST" });
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        if (building) return send(res, 409, { error: "a build is already running" });
        let deck;
        try { deck = JSON.parse(body || "{}").deck; } catch (e) {}
        if (!deck) return send(res, 400, { error: 'body must be {"deck": "name.md"}' });
        building = true;
        // Build off the request thread so the health endpoint stays live.
        setImmediate(() => {
          try { lastResult = buildDeck(deck); send(res, 200, lastResult); }
          catch (e) {
            lastResult = { ok: false, error: String(e.message || e).slice(0, 500) };
            send(res, 500, lastResult);
          } finally { building = false; }
        });
      });
      return;
    }

    if (u.pathname.startsWith("/export/")) {
      const rel = decodeURIComponent(u.pathname.slice("/export/".length));
      const file = path.normalize(path.join(EXPORT_DIR, rel));
      if (!file.startsWith(EXPORT_DIR)) return send(res, 403, { error: "forbidden" });
      if (!fs.existsSync(file) || !fs.statSync(file).isFile())
        return send(res, 404, { error: "not found" });
      return send(res, 200, fs.readFileSync(file),
        MIME[path.extname(file).toLowerCase()] || "application/octet-stream");
    }

    if (u.pathname === "/" || u.pathname === "/index.html") {
      const decks = listDecks();
      const rows = decks.map(d =>
        `<div class="row"><b>${d.deck}</b><span class="sp"></span>` +
        `<button onclick="build('${encodeURIComponent(d.deck)}',this)">Build</button>` +
        `<a class="view" href="/export/${encodeURIComponent(d.deck.replace(/\.md$/i, ".html"))}" target="_blank">view</a></div>`
      ).join("") || '<div class="empty">No .md decks found in decks\\</div>';
      return send(res, 200, `<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Deck Studio</title><style>
body{font:600 15px 'Segoe UI Variable','Segoe UI',system-ui,sans-serif;color:#e8eaf2;
background:linear-gradient(135deg,#0b1020,#101a30 45%,#1a1030);min-height:100vh;margin:0;padding:40px}
h1{margin:0 0 4px}.sub{color:#9aa1b5;font-size:13px;margin-bottom:24px}
.row{display:flex;gap:10px;align-items:center;background:rgba(255,255,255,.06);
border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:12px 16px;margin-bottom:10px}
button{background:linear-gradient(90deg,#5b9dff,#b46bff);border:none;color:#fff;
font-weight:600;padding:8px 18px;border-radius:9px;cursor:pointer}
button:disabled{opacity:.5}.empty{color:#9aa1b5}.sp{flex:1}
a.view{color:#5b9dff}#log{white-space:pre-wrap;font:12px Consolas,monospace;
color:#94a3b8;margin-top:18px;max-height:300px;overflow:auto}</style></head><body>
<h1>🎬 Deck Studio</h1>
<div class="sub">Marp pipeline: paginate → AI images → word filter → video embed → render → verified-fit repair loop.</div>
${rows}<div id="log"></div>
<script>
async function build(name, btn) {
  btn.disabled = true; const log = document.getElementById('log');
  log.textContent = 'building ' + decodeURIComponent(name) + ' …';
  try {
    const r = await fetch('/api/build', {method:'POST', body: JSON.stringify({deck: decodeURIComponent(name)})});
    const j = await r.json();
    log.textContent = j.ok ? ('done in ' + j.seconds + 's') : ('FAILED: ' + (j.error||''));
    if (j.ok) window.open(j.url, '_blank');
  } catch (e) { log.textContent = 'error: ' + e; }
  btn.disabled = false;
}
</script></body></html>`, "text/html");
    }

    send(res, 200, { service: "LuckyD Deck Studio",
      endpoints: ["/health", "/api/decks", "POST /api/build {deck}", "/export/<file>"] });
  } catch (e) {
    send(res, 500, { error: String(e.message || e).slice(0, 300) });
  }
});

server.listen(PORT, "127.0.0.1", () =>
  console.log(`[deck-studio] listening on http://127.0.0.1:${PORT} (marp: ${marpCliPath || "npx fallback"})`));
