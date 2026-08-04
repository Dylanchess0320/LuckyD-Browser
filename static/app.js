/* LuckyD Code — Web GUI client
 * Talks to web_gui.py over WebSocket:
 *   → {type:"message", text} | {type:"approval", id, decision} | {type:"auto_approve", value}
 *   ← token / thinking / tool_start / tool_result / status / markdown /
 *     approval_request / session / models / help / tools / done / goodbye / auto_approve
 */
(() => {
"use strict";

const $ = (id) => document.getElementById(id);
const chat = $("chat");
const input = $("input");
const sendBtn = $("send");
const wsDot = $("ws-dot");
const costPill = $("cost-pill");
const sessionInfo = $("session-info");
const slashPopup = $("slash-popup");
const panel = $("panel");
const autoApproveBox = $("auto-approve");
const modal = $("approval-modal");

let ws = null;
let busy = false;
let commands = [];
let currentModel = "";
let pendingApprovalId = null;

// ── streaming state ───────────────────────────────────────────────
let agentEl = null;      // current agent message container
let answerEl = null;     // answer div inside agentEl
let thinkEl = null;      // thinking <details>
let thinkBodyEl = null;
let streamBuf = "";
let thinkBuf = "";
let renderScheduled = false;
let openTools = [];      // running tool cards awaiting results

// ── helpers ───────────────────────────────────────────────────────
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

if (window.marked) marked.setOptions({ breaks: true, gfm: true });

function renderMd(el, text) {
  let html;
  if (window.marked && window.DOMPurify) {
    html = DOMPurify.sanitize(marked.parse(text || ""));
  } else if (window.marked) {
    html = marked.parse(text || "");
  } else {
    // minimal offline fallback (no CDN)
    html = escapeHtml(text || "")
      .replace(/```(\w*)\n([\s\S]*?)```/g, (m, l, c) => `<pre><code>${c}</code></pre>`)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/\n/g, "<br>");
  }
  el.innerHTML = html;
  if (window.hljs) {
    el.querySelectorAll("pre code").forEach((b) => {
      try { hljs.highlightElement(b); } catch (e) { /* no-op */ }
    });
  }
}

function scrollBottom() { chat.scrollTop = chat.scrollHeight; }

// While streaming, render as cheap plain text (rAF-batched);
// full markdown render happens once at stream_end.
function scheduleStreamRender() {
  if (renderScheduled) return;
  renderScheduled = true;
  requestAnimationFrame(() => {
    renderScheduled = false;
    if (answerEl) answerEl.textContent = streamBuf;
    if (thinkBodyEl) thinkBodyEl.textContent = thinkBuf;
    scrollBottom();
  });
}

function setBusy() {
  sendBtn.classList.toggle("working", busy);
  sendBtn.title = busy ? "Working… (messages are queued)" : "Send (Enter)";
}

// ── message builders ──────────────────────────────────────────────
function addUser(text) {
  const d = document.createElement("div");
  d.className = "msg user";
  d.textContent = text;
  chat.appendChild(d);
  scrollBottom();
}

function addStatus(level, text) {
  const d = document.createElement("div");
  d.className = "status-line " + (level || "info");
  d.textContent = text;
  chat.appendChild(d);
  scrollBottom();
}

function addMarkdownMsg(text) {
  const d = document.createElement("div");
  d.className = "msg agent";
  renderMd(d, text);
  chat.appendChild(d);
  scrollBottom();
}

function ensureAgent() {
  if (!agentEl) {
    agentEl = document.createElement("div");
    agentEl.className = "msg agent";
    chat.appendChild(agentEl);
  }
}

function ensureThink() {
  ensureAgent();
  if (!thinkEl) {
    thinkEl = document.createElement("details");
    thinkEl.className = "think";
    thinkEl.open = true;
    const sum = document.createElement("summary");
    sum.textContent = "thinking…";
    thinkBodyEl = document.createElement("div");
    thinkBodyEl.className = "think-body";
    thinkEl.appendChild(sum);
    thinkEl.appendChild(thinkBodyEl);
    agentEl.appendChild(thinkEl);
  }
}

function ensureAnswer() {
  ensureAgent();
  if (!answerEl) {
    answerEl = document.createElement("div");
    answerEl.className = "answer streaming";
    agentEl.appendChild(answerEl);
  }
}

function finalizeStream() {
  if (thinkEl) {
    thinkEl.open = false;
    const sum = thinkEl.querySelector("summary");
    if (sum) sum.textContent = "thinking";
    if (thinkBodyEl && thinkBuf.trim()) renderMd(thinkBodyEl, thinkBuf);
  }
  if (answerEl) {
    answerEl.classList.remove("streaming");
    if (streamBuf.trim()) renderMd(answerEl, streamBuf);
    else answerEl.remove();
  }
  agentEl = null; answerEl = null; thinkEl = null; thinkBodyEl = null;
  streamBuf = ""; thinkBuf = "";
}

// ── tool cards ────────────────────────────────────────────────────
function argPreview(args) {
  if (!args) return "";
  for (const k of ["file_path", "path", "command", "url", "query"]) {
    if (args[k]) return String(args[k]).slice(0, 80);
  }
  const ks = Object.keys(args);
  return ks.length ? ks.slice(0, 3).join(", ") : "";
}

function addToolCard(name, args) {
  ensureAgent();
  const d = document.createElement("details");
  d.className = "tool-card running";
  const sum = document.createElement("summary");
  const nameSpan = document.createElement("span");
  nameSpan.className = "tool-name";
  nameSpan.textContent = "› " + name;
  const argSpan = document.createElement("span");
  argSpan.className = "tool-args";
  argSpan.textContent = argPreview(args);
  const statusSpan = document.createElement("span");
  statusSpan.className = "tool-status";
  statusSpan.textContent = "running…";
  sum.appendChild(nameSpan); sum.appendChild(argSpan); sum.appendChild(statusSpan);
  const body = document.createElement("div");
  body.className = "tool-body";
  if (args && Object.keys(args).length) {
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(args, null, 2);
    body.appendChild(pre);
  }
  const resultPre = document.createElement("pre");
  resultPre.className = "tool-result-preview hidden";
  body.appendChild(resultPre);
  d.appendChild(sum); d.appendChild(body);
  agentEl.appendChild(d);
  openTools.push({ name, el: d, statusEl: statusSpan, resultEl: resultPre });
  scrollBottom();
}

function updateToolCard(name, elapsed, ok, preview) {
  let card = null;
  for (let i = openTools.length - 1; i >= 0; i--) {
    if (openTools[i].name === name) { card = openTools.splice(i, 1)[0]; break; }
  }
  if (!card) return;
  card.el.classList.remove("running");
  card.el.classList.add(ok ? "ok" : "fail");
  card.statusEl.textContent = (ok ? "ok " : "fail ") + Number(elapsed || 0).toFixed(1) + "s";
  if (preview) {
    card.resultEl.textContent = preview;
    card.resultEl.classList.remove("hidden");
  }
  scrollBottom();
}

// ── chat renderers for command output ─────────────────────────────
function addCommandsMessage(cmds) {
  const rows = cmds.map((c) =>
    `<tr><td><code>${escapeHtml(c[0])}</code></td><td>${escapeHtml(c[1])}</td></tr>`
  ).join("");
  const d = document.createElement("div");
  d.className = "msg agent";
  d.innerHTML = `<p><b>Commands</b></p><table>${rows}</table>`;
  chat.appendChild(d);
  scrollBottom();
}

function addToolsMessage(tools) {
  const d = document.createElement("div");
  d.className = "msg agent";
  const items = tools.map((t) => `<li><code>${escapeHtml(t)}</code></li>`).join("");
  d.innerHTML = `<p><b>Tools</b> <span style="color:var(--muted)">(${tools.length})</span></p><ul>${items}</ul>`;
  chat.appendChild(d);
  scrollBottom();
}

// ── event dispatch ────────────────────────────────────────────────
function handleEvent(ev) {
  switch (ev.type) {
    case "session": {
      const parts = [];
      if (ev.project) parts.push(ev.project);
      if (ev.provider) parts.push(ev.model ? ev.provider + " / " + ev.model : ev.provider);
      else if (ev.model) parts.push(ev.model);
      sessionInfo.textContent = parts.join("  ·  ");
      if (ev.cost) costPill.textContent = ev.cost;
      if (ev.model && ev.model !== currentModel) loadModels();
      break;
    }
    case "stream_start":
      busy = true; setBusy();
      agentEl = null; answerEl = null; thinkEl = null; thinkBodyEl = null;
      streamBuf = ""; thinkBuf = "";
      break;
    case "token":
      ensureAnswer();
      streamBuf += ev.text;
      scheduleStreamRender();
      break;
    case "thinking":
      ensureThink();
      thinkBuf += ev.text;
      scheduleStreamRender();
      break;
    case "think_end":
      if (thinkEl) {
        thinkEl.open = false;
        const sum = thinkEl.querySelector("summary");
        if (sum) sum.textContent = "thinking";
      }
      break;
    case "stream_end":
      finalizeStream();
      scrollBottom();
      break;
    case "markdown":
      addMarkdownMsg(ev.text || "");
      break;
    case "tool_start":
      addToolCard(ev.name, ev.args);
      break;
    case "tool_result":
      updateToolCard(ev.name, ev.elapsed, !!ev.ok, ev.preview || "");
      break;
    case "status":
      addStatus(ev.level, ev.text || "");
      break;
    case "help":
      addCommandsMessage(ev.commands || []);
      break;
    case "tools":
      addToolsMessage(ev.tools || []);
      break;
    case "models":
      renderModels(ev.sections || []);
      openPanelTab("models");
      break;
    case "approval_request":
      showApproval(ev);
      break;
    case "auto_approve":
      autoApproveBox.checked = !!ev.value;
      break;
    case "goodbye":
      addStatus("info", "Session ended — " + (ev.text || "goodbye."));
      busy = false; setBusy();
      break;
    case "done":
      busy = false; setBusy();
      if (agentEl || answerEl) finalizeStream();
      scrollBottom();
      break;
  }
}

// ── websocket ─────────────────────────────────────────────────────
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    wsDot.className = "dot connected";
    loadCommands(); loadModels(); loadTools();
  };
  ws.onclose = () => {
    wsDot.className = "dot disconnected";
    setTimeout(connect, 2000);
  };
  ws.onerror = () => { try { ws.close(); } catch (e) { /* no-op */ } };
  ws.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch (e) { return; }
    handleEvent(ev);
  };
}

function wsSend(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

// ── approval modal ────────────────────────────────────────────────
function showApproval(ev) {
  pendingApprovalId = ev.id;
  $("approval-tool").textContent = ev.tool || "";
  $("approval-args").textContent =
    ev.args && Object.keys(ev.args).length ? JSON.stringify(ev.args, null, 2) : "(no arguments)";
  modal.classList.remove("hidden");
}

function resolveApproval(decision) {
  if (pendingApprovalId) wsSend({ type: "approval", id: pendingApprovalId, decision });
  pendingApprovalId = null;
  modal.classList.add("hidden");
}

$("approve-y").onclick = () => resolveApproval("y");
$("approve-n").onclick = () => resolveApproval("n");
$("approve-a").onclick = () => resolveApproval("a");

// ── side panel ────────────────────────────────────────────────────
function openPanelTab(name) {
  panel.classList.remove("hidden");
  document.querySelectorAll("#panel-tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === name)
  );
  document.querySelectorAll("#panel .tab").forEach((t) =>
    t.classList.toggle("hidden", t.id !== "tab-" + name)
  );
}

async function loadCommands() {
  try {
    const r = await fetch("/api/commands");
    const data = await r.json();
    commands = (data.commands || []).map((c) => [c.cmd, c.desc]);
    const tab = $("tab-commands");
    tab.innerHTML = "";
    for (const [cmd, desc] of commands) {
      const row = document.createElement("button");
      row.className = "cmd-row";
      row.innerHTML = `<span class="cmd-name">${escapeHtml(cmd)}</span>` +
        `<span class="cmd-desc">${escapeHtml(desc)}</span>`;
      row.onclick = () => { input.value = cmd + " "; input.focus(); onInputChange(); };
      tab.appendChild(row);
    }
  } catch (e) { /* offline */ }
}

async function loadModels() {
  try {
    const r = await fetch("/api/models");
    const data = await r.json();
    currentModel = data.current || "";
    renderModels(data.sections || []);
  } catch (e) { /* offline */ }
}

function renderModels(sections) {
  const tab = $("tab-models");
  tab.innerHTML = "";
  for (const sec of sections) {
    const h = document.createElement("div");
    h.className = "tier " + (sec.tier === "free" ? "free" : "paid");
    h.textContent = sec.tier === "free" ? "FREE — $0" : "PAID — costs money";
    h.title = sec.label || "";
    tab.appendChild(h);
    for (const g of sec.groups || []) {
      const gh = document.createElement("div");
      gh.className = "group-label";
      gh.textContent = g.provider;
      tab.appendChild(gh);
      for (const m of g.models || []) {
        const row = document.createElement("button");
        row.className = "model-row" + (m === currentModel ? " current" : "");
        row.textContent = m;
        row.title = "Switch to " + m;
        row.onclick = () => { input.value = "/model " + m; sendCurrent(); };
        tab.appendChild(row);
      }
    }
  }
}

async function loadTools() {
  try {
    const r = await fetch("/api/tools");
    const data = await r.json();
    const tab = $("tab-tools");
    tab.innerHTML = "";
    for (const t of data.tools || []) {
      const row = document.createElement("div");
      row.className = "tool-row";
      const name = typeof t === "string" ? t : (t.name || "");
      const desc = typeof t === "string" ? "" : (t.description || "");
      row.innerHTML = `<div class="t-name">${escapeHtml(name)}</div>` +
        (desc ? `<div class="t-desc">${escapeHtml(desc)}</div>` : "");
      tab.appendChild(row);
    }
  } catch (e) { /* offline */ }
}

// ── slash command autocomplete ────────────────────────────────────
let slashIndex = -1;

function showSlash(matches) {
  slashPopup.innerHTML = "";
  slashIndex = matches.length ? 0 : -1;
  matches.forEach(([cmd, desc], i) => {
    const row = document.createElement("div");
    row.className = "slash-row" + (i === 0 ? " active" : "");
    row.innerHTML = `<span class="slash-cmd">${escapeHtml(cmd)}</span>` +
      `<span class="slash-desc">${escapeHtml(desc)}</span>`;
    row.onclick = () => { input.value = cmd + " "; input.focus(); hideSlash(); };
    slashPopup.appendChild(row);
  });
  slashPopup.classList.remove("hidden");
}

function hideSlash() {
  slashPopup.classList.add("hidden");
  slashIndex = -1;
}

function slashMatches() {
  const v = input.value;
  if (!v.startsWith("/") || v.includes(" ")) return [];
  const q = v.slice(1).toLowerCase();
  return commands.filter(([cmd]) => cmd.slice(1).startsWith(q));
}

function onInputChange() {
  autoGrow();
  const matches = slashMatches();
  if (matches.length) showSlash(matches); else hideSlash();
}

function autoGrow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 180) + "px";
}

// ── sending ───────────────────────────────────────────────────────
function sendCurrent() {
  const text = input.value.trim();
  if (!text) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    addStatus("error", "Not connected — retrying connection…");
    return;
  }
  addUser(text);
  if (text === "/clear") chat.innerHTML = ""; // server resets state; we clear the view
  wsSend({ type: "message", text });
  input.value = "";
  autoGrow();
  hideSlash();
  busy = true; setBusy();
}

input.addEventListener("input", onInputChange);
input.addEventListener("keydown", (e) => {
  const popupOpen = !slashPopup.classList.contains("hidden");
  if (popupOpen) {
    const rows = slashPopup.querySelectorAll(".slash-row");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      slashIndex = (slashIndex + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length;
      rows.forEach((r, i) => r.classList.toggle("active", i === slashIndex));
      return;
    }
    if (e.key === "Tab" || (e.key === "Enter" && slashIndex >= 0)) {
      e.preventDefault();
      const row = rows[slashIndex];
      if (row) {
        input.value = row.querySelector(".slash-cmd").textContent + " ";
        onInputChange();
      }
      return;
    }
    if (e.key === "Escape") { hideSlash(); return; }
  }
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendCurrent();
  }
});

sendBtn.onclick = sendCurrent;
$("panel-toggle").onclick = () => panel.classList.toggle("hidden");
document.querySelectorAll("#panel-tabs button").forEach((b) => {
  b.onclick = () => openPanelTab(b.dataset.tab);
});
autoApproveBox.addEventListener("change", () => {
  wsSend({ type: "auto_approve", value: autoApproveBox.checked });
});

// ── init ──────────────────────────────────────────────────────────
connect();
input.focus();
})();

