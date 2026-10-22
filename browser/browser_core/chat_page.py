"""LuckyD Chat — the built-in chat + tasks surface (Feature 13).

One page, two sections, both backed by Dylan's own PC services through
the control server's proxy routes (/chat/* in control_server.py, client
in luckyd_services.py):

  * Hub chat — the conversation with the Lucky Hub's /chat route. The
    Hub is stateless, so the page keeps the thread (localStorage, this
    browser profile only) and sends it with each message.
  * Tasks (Claw) — submit a task to Claw, watch live status, read the
    finished task's result.md receipt, cancel a running task.

Failure states are ONE plain line per section with the route to the
fix — no fake content, no error spam. No tips or hint text anywhere.
"""

from __future__ import annotations

from browser_core.page_shell import page_head

_CHAT_CSS = """  :root{color-scheme:dark}
  html,body{margin:0;min-height:100%;background:var(--ld-window);color:var(--ld-text);
    font:13px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
  header{height:54px;box-sizing:border-box;display:flex;align-items:center;gap:12px;
    padding:0 18px;border-bottom:1px solid var(--ld-border);background:var(--ld-panel)}
  h1{font-size:15px;margin:0}h1 span{color:var(--ld-accent)}
  main{max-width:1180px;margin:0 auto;padding:16px;display:grid;gap:16px;
    grid-template-columns:minmax(0,7fr) minmax(0,5fr);align-items:start}
  @media(max-width:900px){main{grid-template-columns:1fr}}
  section{border:1px solid var(--ld-border);border-radius:var(--ld-r-md);
    background:var(--ld-card);box-shadow:var(--ld-sh-1);overflow:hidden}
  .sec-head{display:flex;align-items:center;gap:10px;padding:12px 16px;
    border-bottom:1px solid var(--ld-border);background:var(--ld-panel)}
  .sec-head h2{font-size:13px;margin:0;font-weight:650}
  .sec-head .sp{margin-left:auto}
  .state{padding:10px 16px;color:var(--ld-muted);font-size:12px;
    border-bottom:1px solid var(--ld-border)}
  .state:empty{display:none}
  #chat-log{height:380px;overflow:auto;padding:14px 16px;display:flex;
    flex-direction:column;gap:10px}
  .msg{max-width:88%;padding:9px 12px;border-radius:12px;white-space:pre-wrap;
    word-wrap:break-word;font-size:13px}
  .msg.user{align-self:flex-end;background:var(--ld-accent);color:#fff;border-bottom-right-radius:4px}
  .msg.assistant{align-self:flex-start;background:var(--ld-panel2);
    border:1px solid var(--ld-border);border-bottom-left-radius:4px}
  .msg .meta{display:block;margin-top:6px;font-size:11px;color:var(--ld-muted)}
  .msg.user .meta{color:rgba(255,255,255,.75)}
  form.send{display:flex;gap:8px;padding:12px 16px;border-top:1px solid var(--ld-border)}
  input[type=text],textarea{background:var(--ld-panel2);color:var(--ld-text);
    border:1px solid var(--ld-border);border-radius:9px;padding:8px 11px;
    font:inherit;box-sizing:border-box}
  input[type=text]{flex:1}
  textarea{width:100%;min-height:64px;resize:vertical}
  button{background:var(--ld-accent);color:#fff;border:0;border-radius:9px;
    padding:8px 16px;font:inherit;font-weight:600;cursor:pointer}
  button:disabled{opacity:.45;cursor:default}
  button.ghost{background:transparent;color:var(--ld-muted);
    border:1px solid var(--ld-border);font-weight:500;padding:5px 11px;font-size:12px}
  button.ghost:hover{color:var(--ld-text);border-color:var(--ld-accent)}
  .pad{padding:12px 16px;display:flex;flex-direction:column;gap:10px}
  .seg{display:flex;gap:6px;flex-wrap:wrap}
  .seg button{background:var(--ld-panel2);color:var(--ld-muted);
    border:1px solid var(--ld-border);font-weight:500;padding:6px 13px;font-size:12px}
  .seg button.on{background:var(--ld-accent);border-color:var(--ld-accent);color:#fff;font-weight:650}
  .lbl{font-size:11px;color:var(--ld-muted);text-transform:uppercase;letter-spacing:.06em}
  #task-list{display:flex;flex-direction:column}
  .task{padding:10px 16px;border-bottom:1px solid var(--ld-border)}
  .task:last-child{border-bottom:0}
  .task .t{display:flex;gap:8px;align-items:baseline}
  .task .prompt{font-weight:600;overflow:hidden;text-overflow:ellipsis;
    white-space:nowrap;flex:1;min-width:0}
  .chip{flex:none;font-size:11px;padding:2px 9px;border-radius:99px;
    border:1px solid var(--ld-border);color:var(--ld-muted)}
  .chip.running{color:var(--ld-accent);border-color:var(--ld-accent)}
  .chip.done{color:var(--ld-ok);border-color:var(--ld-ok)}
  .chip.error,.chip.cancelled{color:var(--ld-danger);border-color:var(--ld-danger)}
  .task .sub{color:var(--ld-muted);font-size:12px;margin-top:3px;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .task .row{display:flex;gap:8px;margin-top:7px}
  .empty{padding:14px 16px;color:var(--ld-muted);font-size:12px}
  #receipt{margin:0 16px 14px;border:1px solid var(--ld-border);
    border-radius:var(--ld-r-sm);background:var(--ld-panel2);display:none}
  #receipt.open{display:block}
  #receipt .rh{display:flex;align-items:center;padding:9px 12px;
    border-bottom:1px solid var(--ld-border);font-weight:650;font-size:12px}
  #receipt pre{margin:0;padding:12px;max-height:340px;overflow:auto;
    white-space:pre-wrap;word-wrap:break-word;font-size:12px;color:var(--ld-text)}
"""

_CHAT_BODY = r"""<body>
<header><h1><span>💬</span> LuckyD Chat</h1></header>
<main>
  <section id="hub-sec">
    <div class="sec-head"><h2>Hub chat</h2><span class="sp"></span>
      <button class="ghost" id="chat-clear" type="button">Clear</button></div>
    <div class="state" id="hub-state"></div>
    <div id="chat-log"></div>
    <form class="send" id="chat-form">
      <input type="text" id="chat-input" placeholder="Message the Hub" autocomplete="off">
      <button type="submit" id="chat-send">Send</button>
    </form>
  </section>

  <section id="claw-sec">
    <div class="sec-head"><h2>Tasks</h2></div>
    <div class="state" id="claw-state"></div>
    <div class="pad">
      <div><div class="lbl">Project</div><div class="seg" id="projects"></div>
        <input type="text" id="project-input" placeholder="Project name" style="display:none;width:100%"></div>
      <div><div class="lbl">Profile</div><div class="seg" id="profiles">
        <button type="button" data-profile="look">Look — read only</button>
        <button type="button" data-profile="build" class="on">Build</button></div></div>
      <textarea id="task-prompt" placeholder="What should the agent do?"></textarea>
      <div><button type="button" id="task-run">Run task</button></div>
    </div>
    <div id="receipt"><div class="rh"><span id="receipt-title">Receipt</span><span class="sp" style="margin-left:auto"></span>
      <button class="ghost" id="receipt-close" type="button">Close</button></div>
      <pre id="receipt-body"></pre></div>
    <div id="task-list"></div>
  </section>
</main>
<script>
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? '' : s);

// ── one-line service states (failures stay quiet and plain) ──────────
const STATE_LINES = {
  hub: {
    no_token: 'Hub chat is off — add your Hub token in Settings → Connections.',
    bad_token: 'Hub token was rejected — check Settings → Connections.',
    unreachable: "Hub isn't reachable right now.",
    error: "Hub isn't reachable right now."
  },
  claw: {
    no_token: 'Tasks are off — add your Claw token in Settings → Connections.',
    bad_token: 'Claw token was rejected — check Settings → Connections.',
    unreachable: "Claw isn't reachable right now.",
    error: "Claw isn't reachable right now."
  }
};
function stateLine(el, svc, state){ el.textContent = (!state || state === 'ok') ? '' : (STATE_LINES[svc][state] || ''); }

async function api(path, body){
  const opt = body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {};
  const r = await fetch(path, opt);
  return r.json();
}

// ── Hub chat ──────────────────────────────────────────────────────────
let msgs = [];
try { msgs = JSON.parse(localStorage.getItem('luckyd-chat-msgs-v1') || '[]'); } catch (_) { msgs = []; }
if (!Array.isArray(msgs)) msgs = [];
const log = $('#chat-log');
function persist(){ try { localStorage.setItem('luckyd-chat-msgs-v1', JSON.stringify(msgs.slice(-60))); } catch (_) {} }
function renderChat(){
  log.innerHTML = '';
  for (const m of msgs) {
    const d = document.createElement('div');
    d.className = 'msg ' + (m.role === 'user' ? 'user' : 'assistant');
    d.textContent = m.content;
    if (m.role === 'assistant' && (m.via || m.model)) {
      const meta = document.createElement('span');
      meta.className = 'meta';
      meta.textContent = 'answered by ' + (m.via === 'luckyd' ? 'LuckyD (local)' : m.via) + (m.model ? ' · ' + m.model : '');
      d.appendChild(meta);
    }
    log.appendChild(d);
  }
  log.scrollTop = log.scrollHeight;
}
renderChat();

let sending = false;
$('#chat-form').addEventListener('submit', async ev => {
  ev.preventDefault();
  const input = $('#chat-input');
  const text = input.value.trim();
  if (!text || sending) return;
  sending = true; $('#chat-send').disabled = true;
  msgs.push({role: 'user', content: text});
  input.value = ''; renderChat(); persist();
  try {
    const r = await api('/chat/send', {messages: msgs.map(m => ({role: m.role, content: m.content}))});
    if (r.state === 'ok') {
      msgs.push({role: 'assistant', content: r.text || '', via: r.via, model: r.model});
      renderChat(); persist();
      stateLine($('#hub-state'), 'hub', 'ok');
    } else {
      stateLine($('#hub-state'), 'hub', r.state);
    }
  } catch (_) { stateLine($('#hub-state'), 'hub', 'unreachable'); }
  sending = false; $('#chat-send').disabled = false;
});
$('#chat-clear').addEventListener('click', () => { msgs = []; renderChat(); persist(); });

// ── Tasks (Claw) ──────────────────────────────────────────────────────
let projects = [], project = '', profile = 'build', clawOk = false;
function renderProjects(){
  const box = $('#projects'); box.innerHTML = '';
  const input = $('#project-input');
  if (!projects.length) { input.style.display = 'block'; return; }
  input.style.display = 'none';
  if (!projects.includes(project)) project = projects[0];
  for (const p of projects) {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = p;
    if (p === project) b.className = 'on';
    b.addEventListener('click', () => { project = p; renderProjects(); });
    box.appendChild(b);
  }
}
$('#profiles').addEventListener('click', ev => {
  const b = ev.target.closest('button[data-profile]'); if (!b) return;
  profile = b.dataset.profile;
  document.querySelectorAll('#profiles button').forEach(x => x.classList.toggle('on', x === b));
});
function currentProject(){
  return projects.length ? project : $('#project-input').value.trim();
}

const TERMINAL = ['done', 'error', 'failed', 'cancelled', 'canceled'];
function renderTasks(tasks){
  const box = $('#task-list'); box.innerHTML = '';
  if (!tasks.length) {
    const d = document.createElement('div'); d.className = 'empty'; d.textContent = 'No tasks yet.';
    box.appendChild(d); return;
  }
  for (const t of tasks) {
    const row = document.createElement('div'); row.className = 'task';
    const head = document.createElement('div'); head.className = 't';
    const pr = document.createElement('span'); pr.className = 'prompt'; pr.textContent = t.prompt || '';
    const chip = document.createElement('span');
    chip.className = 'chip' + (t.state === 'running' || t.state === 'queued' ? ' running'
      : t.state === 'done' ? ' done' : TERMINAL.includes(t.state) ? ' error' : '');
    chip.textContent = t.state || '';
    head.appendChild(pr); head.appendChild(chip); row.appendChild(head);
    const sub = document.createElement('div'); sub.className = 'sub';
    sub.textContent = [t.project, t.profile, t.progress_line || t.summary || ''].filter(Boolean).join(' · ');
    row.appendChild(sub);
    const btns = document.createElement('div'); btns.className = 'row';
    if (TERMINAL.includes(String(t.state)) && (t.result || t.summary)) {
      const b = document.createElement('button'); b.className = 'ghost'; b.type = 'button'; b.textContent = 'Receipt';
      b.addEventListener('click', () => openReceipt(t));
      btns.appendChild(b);
    }
    if (t.state === 'queued' || t.state === 'running') {
      const b = document.createElement('button'); b.className = 'ghost'; b.type = 'button'; b.textContent = 'Cancel';
      b.addEventListener('click', async () => { b.disabled = true; await api('/chat/task/cancel', {id: t.id}); refreshTasks(); });
      btns.appendChild(b);
    }
    if (btns.children.length) row.appendChild(btns);
    box.appendChild(row);
  }
}
async function openReceipt(task){
  let t = task;
  if (!t.result) {
    try { const r = await api('/chat/task?id=' + encodeURIComponent(t.id)); if (r.state === 'ok') t = r.task; } catch (_) {}
  }
  $('#receipt-title').textContent = 'Receipt — ' + (t.project || '') + ' · ' + (t.state || '');
  $('#receipt-body').textContent = t.result || t.summary || 'No receipt recorded.';
  $('#receipt').classList.add('open');
}
$('#receipt-close').addEventListener('click', () => $('#receipt').classList.remove('open'));

$('#task-run').addEventListener('click', async () => {
  const prompt = $('#task-prompt').value.trim();
  const proj = currentProject();
  if (!prompt || !proj || !clawOk) return;
  const btn = $('#task-run'); btn.disabled = true;
  try {
    const r = await api('/chat/task', {project: proj, prompt: prompt, profile: profile});
    if (r.state === 'ok') { $('#task-prompt').value = ''; stateLine($('#claw-state'), 'claw', 'ok'); }
    else if (r.state === 'error' && r.error) $('#claw-state').textContent = 'Claw: ' + r.error;
    else stateLine($('#claw-state'), 'claw', r.state);
  } catch (_) { stateLine($('#claw-state'), 'claw', 'unreachable'); }
  btn.disabled = false; refreshTasks();
});

async function refreshState(){
  try {
    const s = await api('/chat/state');
    stateLine($('#hub-state'), 'hub', s.hub && s.hub.state);
    stateLine($('#claw-state'), 'claw', s.claw && s.claw.state);
    clawOk = !!(s.claw && s.claw.state === 'ok' && s.claw.enabled !== false);
    if (s.claw && s.claw.state === 'ok' && s.claw.enabled === false)
      $('#claw-state').textContent = 'Claw is stopped on the PC.';
    if (Array.isArray(s.projects) && s.projects.length) { projects = s.projects; renderProjects(); }
  } catch (_) {}
}
async function refreshTasks(){
  if (!clawOk) { renderTasks([]); return; }
  try { const r = await api('/chat/tasks'); if (r.state === 'ok') renderTasks(r.tasks || []); } catch (_) {}
}
renderProjects();
refreshState().then(refreshTasks);
setInterval(() => { if (!document.hidden) { refreshTasks(); } }, 4000);
setInterval(() => { if (!document.hidden) { refreshState(); } }, 20000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { refreshState().then(refreshTasks); } });
</script>
</body></html>"""


def chat_html(settings=None) -> str:
    """The LuckyD Chat page (Hub chat + Claw tasks)."""
    return page_head("LuckyD Chat — LuckyD", _CHAT_CSS, settings=settings) + _CHAT_BODY
