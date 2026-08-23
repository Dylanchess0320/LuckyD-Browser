#!/usr/bin/env node
// studio-server.js
// ---------------------------------------------------------------
// Deck Studio — the local HTTP service that lets LuckyD Browser use
// the Marp pipeline as a platform tile (Phase 3 of the platform plan).
//
//   GET    /health                 -> {"ok":true}  (dashboard probe)
//   GET    /api/decks              -> JSON list of decks/*.md
//   GET    /api/deck/<name>        -> raw markdown of one deck
//   POST   /api/decks              -> create/save a deck {"name","content"}
//   DELETE /api/decks/<name>       -> delete a deck
//   POST   /api/build  {deck:..}   -> runs build.js (paginate -> images ->
//                                     filter -> video -> marp -> verify-fit
//                                     repair loop), returns the export URL
//   GET    /export/<file>          -> serves exported decks + images
//                                     (?download=1 forces a download)
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
const zlib = require("zlib");
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
  // NOTE: the package *bin* is marp-cli.js at the root — lib/index.js is a
  // library entry that no-ops when executed directly.
  for (const cand of ["@marp-team/marp-cli/marp-cli.js", "@marp-team/marp-cli"]) {
    try { marpCliPath = require.resolve(cand); break; } catch (e) { /* next */ }
  }
}

function send(res, code, body, type = "application/json", extra = {}) {
  res.writeHead(code, Object.assign({ "Content-Type": type, "Cache-Control": "no-store" }, extra));
  res.end(typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body));
}

function readBody(req) {
  return new Promise((resolve) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => resolve(body));
  });
}

async function readJsonBody(req) {
  const body = await readBody(req);
  try { return JSON.parse(body || "{}"); } catch (e) { return null; }
}

// Only plain filenames inside decks/. Rejects traversal and _-prefixed temps.
function safeDeckName(name) {
  const base = path.basename(String(name || "").trim());
  if (!base || base.startsWith("_") || /[\\/:*?"<>|]/.test(base)) return null;
  return base.toLowerCase().endsWith(".md") ? base : base + ".md";
}

function listDecks() {
  try {
    return fs.readdirSync(DECKS_DIR)
      .filter((f) => f.toLowerCase().endsWith(".md") && !f.startsWith("_"))
      .map((f) => {
        const st = fs.statSync(path.join(DECKS_DIR, f));
        const html = path.join(EXPORT_DIR, f.replace(/\.md$/i, ".html"));
        return {
          deck: f,
          modified: st.mtime.toISOString(),
          built: fs.existsSync(html),
        };
      })
      .sort((a, b) => b.modified.localeCompare(a.modified));
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

// ── self-contained download: ZIP the wrapped HTML + its images ────────
// A bare .html download loses the relative images/ references when opened
// locally. A zip that bundles the html and every image it uses travels
// anywhere and renders exactly like the live export.
function makeZip(entries) {
  const parts = [];
  const central = [];
  let offset = 0;
  let count = 0;
  for (const { name, data } of entries) {
    const nameBuf = Buffer.from(name, "utf8");
    const crc = zlib.crc32(data) >>> 0;
    const size = data.length;
    const lfh = Buffer.alloc(30);
    lfh.writeUInt32LE(0x04034b50, 0);
    lfh.writeUInt16LE(20, 4);        // version needed
    lfh.writeUInt16LE(0x0800, 6);    // UTF-8 name flag
    lfh.writeUInt16LE(0, 8);         // stored (no compression)
    lfh.writeUInt16LE(0, 10);
    lfh.writeUInt16LE(0, 12);
    lfh.writeUInt32LE(crc, 14);
    lfh.writeUInt32LE(size, 18);
    lfh.writeUInt32LE(size, 22);
    lfh.writeUInt16LE(nameBuf.length, 26);
    lfh.writeUInt16LE(0, 28);
    parts.push(Buffer.concat([lfh, nameBuf, data]));
    const cf = Buffer.alloc(46);
    cf.writeUInt32LE(0x02014b50, 0);
    cf.writeUInt16LE(20, 4);         // version made by
    cf.writeUInt16LE(20, 6);         // version needed
    cf.writeUInt16LE(0x0800, 8);
    cf.writeUInt16LE(0, 10);
    cf.writeUInt16LE(0, 12);
    cf.writeUInt16LE(0, 14);
    cf.writeUInt32LE(crc, 16);
    cf.writeUInt32LE(size, 20);
    cf.writeUInt32LE(size, 24);
    cf.writeUInt16LE(nameBuf.length, 28);
    cf.writeUInt16LE(0, 30);
    cf.writeUInt16LE(0, 32);
    cf.writeUInt16LE(0, 34);
    cf.writeUInt16LE(0, 36);
    cf.writeUInt32LE(0, 38);
    cf.writeUInt32LE(offset, 42);
    central.push(Buffer.concat([cf, nameBuf]));
    offset += lfh.length + nameBuf.length + size;
    count++;
  }
  const cdBuf = Buffer.concat(central);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(0, 4);
  eocd.writeUInt16LE(0, 6);
  eocd.writeUInt16LE(count, 8);
  eocd.writeUInt16LE(count, 10);
  eocd.writeUInt32LE(cdBuf.length, 12);
  eocd.writeUInt32LE(offset, 16);
  eocd.writeUInt16LE(0, 20);
  return Buffer.concat([...parts, cdBuf, eocd]);
}

function buildDeckZip(deckName) {
  const safe = path.basename(deckName);
  fs.mkdirSync(EXPORT_DIR, { recursive: true });
  const base = path.basename(safe, ".md");
  const htmlPath = path.join(EXPORT_DIR, `${base}.html`);
  if (!fs.existsSync(htmlPath)) throw new Error(`deck not built yet: ${safe}`);
  const html = fs.readFileSync(htmlPath);
  const entries = [{ name: `${base}.html`, data: html }];
  // Bundle exactly the images this deck references, kept in images/.
  const seen = new Set();
  for (const m of html.toString("utf8").matchAll(/images\/[^"')\]>\s]+/g)) {
    const rel = m[0];
    if (seen.has(rel)) continue;
    seen.add(rel);
    const p = path.normalize(path.join(EXPORT_DIR, rel));
    if (p.startsWith(EXPORT_DIR) && fs.existsSync(p) && fs.statSync(p).isFile()) {
      entries.push({ name: rel, data: fs.readFileSync(p) });
    }
  }
  return { dir: EXPORT_DIR, buffer: makeZip(entries), name: `${base}.zip` };
}

const NEW_DECK_TEMPLATE = [
  "---",
  "marp: true",
  "theme: black-green",
  "paginate: true",
  "---",
  "",
  "# My new deck",
  "",
  "---",
  "",
  "## Slide 2",
  "",
  "- point one",
  "- point two",
  "",
].join("\n");

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, `http://127.0.0.1:${PORT}`);
  try {
    if (u.pathname === "/health") return send(res, 200, { ok: true, service: "deck-studio",
      building, lastResult });

    // ── deck CRUD ────────────────────────────────────────────────────
    if (u.pathname === "/api/decks" && req.method === "GET") {
      return send(res, 200, { decks: listDecks() });
    }

    if (u.pathname === "/api/decks" && req.method === "POST") {
      const data = await readJsonBody(req);
      if (!data) return send(res, 400, { error: "invalid JSON body" });
      const name = safeDeckName(data.name);
      if (!name) return send(res, 400, { error: "invalid deck name" });
      if (typeof data.content !== "string" || !data.content.trim())
        return send(res, 400, { error: "content must be a non-empty string" });
      fs.mkdirSync(DECKS_DIR, { recursive: true });
      fs.writeFileSync(path.join(DECKS_DIR, name), data.content, "utf8");
      return send(res, 200, { ok: true, deck: name, saved: true });
    }

    let m;
    if ((m = u.pathname.match(/^\/api\/deck\/(.+)$/)) && req.method === "GET") {
      const name = safeDeckName(decodeURIComponent(m[1]));
      const p = name && path.join(DECKS_DIR, name);
      if (!p || !fs.existsSync(p)) return send(res, 404, { error: "deck not found" });
      return send(res, 200, { name, content: fs.readFileSync(p, "utf8") });
    }

    if ((m = u.pathname.match(/^\/api\/decks\/(.+)$/)) && req.method === "DELETE") {
      const name = safeDeckName(decodeURIComponent(m[1]));
      const p = name && path.join(DECKS_DIR, name);
      if (!p || !fs.existsSync(p)) return send(res, 404, { error: "deck not found" });
      fs.unlinkSync(p);
      return send(res, 200, { ok: true, deleted: name });
    }

    // ── build ────────────────────────────────────────────────────────
    if (u.pathname === "/api/build") {
      if (req.method !== "POST") return send(res, 405, { error: "use POST" });
      const data = await readJsonBody(req);
      if (building) return send(res, 409, { error: "a build is already running" });
      const deck = data && data.deck;
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
      return;
    }

    // ── exports ──────────────────────────────────────────────────────
    if (u.pathname.startsWith("/export/")) {
      const rel = decodeURIComponent(u.pathname.slice("/export/".length));
      const file = path.normalize(path.join(EXPORT_DIR, rel));
      if (!file.startsWith(EXPORT_DIR)) return send(res, 403, { error: "forbidden" });
      if (!fs.existsSync(file) || !fs.statSync(file).isFile())
        return send(res, 404, { error: "not found" });
      const extra = {};
      if (u.searchParams.get("download")) {
        const asciiName = path.basename(file).replace(/[^\x20-\x7e]/g, "_");
        extra["Content-Disposition"] =
          `attachment; filename="${asciiName}"; filename*=UTF-8''${encodeURIComponent(path.basename(file))}`;
      }
      return send(res, 200, fs.readFileSync(file),
        MIME[path.extname(file).toLowerCase()] || "application/octet-stream", extra);
    }

    // ── self-contained download (HTML + images) ───────────────────────
    if ((m = u.pathname.match(/^\/api\/download\/(.+)$/))) {
      const name = safeDeckName(decodeURIComponent(m[1]));
      try {
        const { buffer, name: zipName } = buildDeckZip(name);
        return send(res, 200, buffer, "application/zip",
          { "Content-Disposition": `attachment; filename="${zipName}"` });
      } catch (e) {
        return send(res, 404, { error: String(e.message || e).slice(0, 200) });
      }
    }

    if (u.pathname === "/" || u.pathname === "/index.html") {
      const decks = listDecks();
      const rows = decks.map((d) => {
        const htmlName = d.deck.replace(/\.md$/i, ".html");
        const enc = encodeURIComponent(d.deck);
        const built = d.built
          ? '<span class="badge ok">built</span>'
          : '<span class="badge">not built</span>';
        return `<div class="row"><b>${d.deck}</b>${built}<span class="sp"></span>` +
          `<button onclick="build('${enc}',this)">Build</button>` +
          `<button class="ghost" onclick="editDeck('${enc}')">Edit</button>` +
          (d.built
            ? `<a class="view" href="/export/${encodeURIComponent(htmlName)}" target="_blank">Open ↗</a>` +
              `<a class="btn ghost" href="/api/download/${enc}">⬇ Download (with images)</a>`
            : `<span class="sub" style="margin:0">build first to download</span>`) +
          `<button class="ghost danger" onclick="delDeck('${enc}')">🗑</button></div>`;
      }).join("") ||
        '<div class="empty">No decks yet — click "＋ New deck" or upload a .md file.</div>';
      return send(res, 200, `<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Deck Studio</title><link rel="stylesheet" href="/app.css"></head><body>
<h1>🎬 Deck Studio</h1>
<div class="sub">Add Markdown → Build → View &amp; download the finished presentation. Pipeline: paginate → AI images → word filter → video embed → Marp render → verified-fit repair loop.</div>
<div class="bar">
  <button onclick="newDeck()">＋ New deck</button>
  <button class="ghost" onclick="document.getElementById('up').click()">⬆ Upload .md</button>
  <input type="file" id="up" multiple accept=".md,text/markdown" style="display:none">
  <span class="sp"></span>
  <span class="sub" style="margin:0">${decks.length} deck(s)</span>
</div>
${rows}<div id="log"></div>
<dialog id="ed">
  <h2 style="margin:8px 0">✏️ Deck editor</h2>
  <input type="text" id="edname" placeholder="my-deck.md">
  <textarea id="edtext" spellcheck="false"></textarea>
  <div class="dlgbar">
    <button onclick="saveDeck(this)">💾 Save</button>
    <button class="ghost" onclick="document.getElementById('ed').close()">Close</button>
    <span class="sp"></span><span class="sub" style="margin:0">Markdown (Marp) — separate slides with ---</span>
  </div>
</dialog>
<script src="/app.js"></script></body></html>`, "text/html");
    }

    if (u.pathname === "/app.css") {
      const css = `body{font:600 15px 'Segoe UI Variable','Segoe UI',system-ui,sans-serif;color:#e8eaf2;
background:linear-gradient(135deg,#0b1020,#101a30 45%,#1a1030);min-height:100vh;margin:0;padding:40px}
h1{margin:0 0 4px}.sub{color:#9aa1b5;font-size:13px;margin-bottom:20px}
.bar{display:flex;gap:10px;margin-bottom:22px;align-items:center}
.row{display:flex;gap:10px;align-items:center;background:rgba(255,255,255,.06);
border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:12px 16px;margin-bottom:10px;flex-wrap:wrap}
button,.btn{background:linear-gradient(90deg,#5b9dff,#b46bff);border:none;color:#fff;
font-weight:600;padding:8px 18px;border-radius:9px;cursor:pointer;text-decoration:none;display:inline-block;font-size:13px}
button.ghost,.btn.ghost{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.2)}
button.danger{background:rgba(255,80,80,.25)}
button:disabled{opacity:.5}.empty{color:#9aa1b5}.sp{flex:1}
.badge{font-size:11px;color:#9aa1b5;border:1px solid rgba(255,255,255,.15);border-radius:99px;padding:2px 10px;margin-left:10px}
.badge.ok{color:#34d399;border-color:rgba(52,211,153,.4)}
a.view{color:#5b9dff;text-decoration:none}a.view:hover{text-decoration:underline}
#log{white-space:pre-wrap;font:12px Consolas,monospace;color:#94a3b8;margin-top:18px;max-height:300px;overflow:auto}
dialog{background:#141c30;color:#e8eaf2;border:1px solid rgba(255,255,255,.15);border-radius:14px;width:min(860px,92vw)}
dialog::backdrop{background:rgba(0,0,0,.55)}
textarea{width:100%;box-sizing:border-box;height:52vh;background:#0d1424;color:#dfe6f5;
border:1px solid rgba(255,255,255,.15);border-radius:10px;padding:12px;font:13px Consolas,monospace;resize:vertical}
.dlgbar{display:flex;gap:10px;margin-top:12px;align-items:center}
input[type=text]{flex:1;background:#0d1424;color:#e8eaf2;border:1px solid rgba(255,255,255,.15);border-radius:9px;padding:9px 12px;font-weight:600}`;
      return send(res, 200, css, "text/css");
    }

    if (u.pathname === "/app.js") {
      const js = `const NEW = ['---','marp: true','theme: black-green','paginate: true','---','','# Title','','Your first slide.',''].join('\\n');
function log(t){ document.getElementById('log').textContent += t + '\\n'; }
async function build(name, btn) {
  btn.disabled = true; log('building ' + decodeURIComponent(name) + ' …');
  try {
    const r = await fetch('/api/build', {method:'POST', body: JSON.stringify({deck: decodeURIComponent(name)})});
    const j = await r.json();
    if (j.ok) { log('done in ' + j.seconds + 's'); window.open(j.url, '_blank'); setTimeout(()=>location.reload(), 800); }
    else log('FAILED: ' + (j.error||''));
  } catch (e) { log('error: ' + e); }
  btn.disabled = false;
}
async function editDeck(name) {
  const n = decodeURIComponent(name);
  const r = await fetch('/api/deck/' + encodeURIComponent(n)); const j = await r.json();
  document.getElementById('edname').value = j.name || n;
  document.getElementById('edname').disabled = true;
  document.getElementById('edtext').value = j.content || '';
  document.getElementById('ed').showModal();
}
function newDeck() {
  document.getElementById('edname').value = '';
  document.getElementById('edname').disabled = false;
  document.getElementById('edtext').value = NEW;
  document.getElementById('ed').showModal();
  document.getElementById('edname').focus();
}
async function saveDeck(btn) {
  btn.disabled = true;
  const r = await fetch('/api/decks', {method:'POST',
    body: JSON.stringify({name: document.getElementById('edname').value,
                          content: document.getElementById('edtext').value})});
  const j = await r.json(); btn.disabled = false;
  if (r.ok) { document.getElementById('ed').close(); log('saved ' + j.deck); setTimeout(()=>location.reload(), 500); }
  else log('save FAILED: ' + (j.error||''));
}
async function delDeck(name) {
  const n = decodeURIComponent(name);
  if (!confirm('Delete deck ' + n + '?')) return;
  const r = await fetch('/api/decks/' + encodeURIComponent(n), {method:'DELETE'});
  if (r.ok) { log('deleted ' + n); setTimeout(()=>location.reload(), 400); } else log('delete FAILED');
}
document.getElementById('up').addEventListener('change', async (ev) => {
  let uploaded = 0;
  for (const file of ev.target.files) {
    const text = await file.text();
    const r = await fetch('/api/decks', {method:'POST',
      body: JSON.stringify({name: file.name, content: text})});
    if (r.ok) { uploaded++; log('uploaded ' + file.name); }
    else log('upload FAILED ' + file.name);
  }
  ev.target.value = '';
  if (uploaded) setTimeout(()=>location.reload(), 500);
});`;
      return send(res, 200, js, "application/javascript");
    }
  } catch (e) {
    send(res, 500, { error: String(e.message || e).slice(0, 300) });
  }
});

server.listen(PORT, "127.0.0.1", () =>
  console.log(`[deck-studio] listening on http://127.0.0.1:${PORT} (marp: ${marpCliPath || "npx fallback"})`));

