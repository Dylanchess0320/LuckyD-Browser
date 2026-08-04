"""Autonomous browsing agent — observe -> plan -> act loop driving live tabs.

Comet-style: give it a task and it navigates, clicks, types and scrolls in the
REAL tab you are watching, narrating each step. In-process via JS injection.

Threading: the agent loop runs in a worker thread (asyncio), but Qt WebEngine
only accepts calls on the GUI thread — every JS evaluation is marshaled
through JsBridge's queued signals.
"""

from __future__ import annotations

import asyncio
import json
import re
import time

from PySide6.QtCore import QObject, Qt, Signal

# Number of agent sessions currently driving tabs. While > 0, WebPage
# auto-dismisses JavaScript dialogs (alert/confirm/prompt) so they can't
# freeze the agent's JS channel, and records their text for the snapshot.
ACTIVE_SESSIONS = 0

_SYSTEM = """You are an autonomous web-browsing agent inside a desktop browser.
You control the user's current tab. Each turn you receive a snapshot (URL,
title, numbered interactive elements, visible text). Reply with EXACTLY ONE
JSON object — no markdown fences, no commentary:

{"actions": [{"action": "click|type|press|select|scroll|navigate|back|wait|done",
 "index": <element number>, "text": "<type text / Enter|Tab|Escape / option
 text / up / seconds / final answer>", "url": "<absolute url>",
 "reason": "<2-6 words>"}]}

Rules:
- click: index. type: index + text. press: Enter|Tab|Escape (submits the
  focused field — use right after typing). select: index + option text.
  scroll: "up" or empty. navigate: absolute http/https url.
  back: previous page. wait: text = seconds (max 5) for spinners/loading.
  done: final answer (always a single action).
- SPEED: batch 1-4 obvious actions in one reply (e.g. type then press Enter).
  Use ONE action when unsure or right after a navigation.
- Links show their target href — when you know the destination, navigate()
  directly; it is faster and more reliable than clicking.
- If the page is not changing after your actions, change tactics: scroll for
  new elements, press Enter/Tab, navigate directly, or finish with done.
- Prefer elements visible in the snapshot. Never invent indices.
- Follow the PLAN; if it stalls, adapt it. Keep the final answer short."""

_SNAPSHOT_JS = r"""
(() => {
  document.querySelectorAll('[data-ld-agent]').forEach(
    e => e.removeAttribute('data-ld-agent'));
  const els = [...document.querySelectorAll(
    'a, button, input, textarea, select, [role="button"], summary'
  )].filter(e => e.offsetParent !== null && !e.disabled).slice(0, __MAXELS__);
  const lines = els.map((e, i) => {
    e.setAttribute('data-ld-agent', i);
    const tag = e.tagName.toLowerCase();
    const kind = e.type ? `[${e.type}]` : '';
    const label = (e.innerText || e.value || e.placeholder ||
      e.getAttribute('aria-label') || e.name || '')
      .replace(/\s+/g, ' ').trim().slice(0, 60);
    let extra = '';
    if (tag === 'a' && e.href) extra = ' → ' + e.href.slice(0, 60);
    if (tag === 'select')
      extra = ' options: ' +
        [...e.options].slice(0, 6).map(o => o.text.trim()).join('|')
        .slice(0, 60);
    return `[${i}] <${tag}>${kind} ${label}${extra}`;
  });
  const text = (document.body ? document.body.innerText : '')
    .replace(/\s+/g, ' ').trim().slice(0, __MAXTEXT__);
  const dlg = window.__ld_dialog || ''; window.__ld_dialog = '';
  const more = document.documentElement.scrollHeight >
    window.scrollY + window.innerHeight + 100;
  return JSON.stringify({
    url: location.href, title: document.title,
    elements: lines.join('\n'), text: text, dialog: dlg, more_below: more
  });
})()
"""

_CLICK_JS = (
    "(() => {{ const e = document.querySelector('[data-ld-agent=\"{i}\"]');"
    " if (!e) return 'element not found';"
    " e.scrollIntoView({{block:'center'}}); e.click();"
    " return 'clicked <' + e.tagName.toLowerCase() + '> ' +"
    " (e.innerText || e.value || '').trim().slice(0, 60); }})()"
)
_TYPE_JS = (
    "(() => {{ const e = document.querySelector('[data-ld-agent=\"{i}\"]');"
    " if (!e) return 'element not found';"
    " e.scrollIntoView({{block:'center'}}); e.focus(); e.value = {text};"
    " e.dispatchEvent(new Event('input', {{bubbles:true}}));"
    " e.dispatchEvent(new Event('change', {{bubbles:true}}));"
    " return 'typed ' + String(e.value.length) + ' chars into <' +"
    " e.tagName.toLowerCase() + '>'; }})()"
)
_HIGHLIGHT_JS = (
    '(() => {{ const e = document.querySelector("[data-ld-agent={i}]");'
    " if (!e) return 'no target';"
    " e.scrollIntoView({{block:'center'}});"
    " const oldOutline = e.style.outline, oldShadow = e.style.boxShadow;"
    " e.style.outline = '3px solid #f9a24f';"
    " e.style.outlineOffset = '2px';"
    " e.style.boxShadow = '0 0 14px 4px rgba(249,162,79,0.75)';"
    " const badge = document.createElement('div');"
    " badge.textContent = '🤖';"
    " badge.style.cssText = 'position:absolute;z-index:2147483647;width:22px;"
    "height:22px;border-radius:50%;background:#f9a24f;color:#000;font-size:14px;"
    "display:flex;align-items:center;justify-content:center;pointer-events:none';"
    " const r = e.getBoundingClientRect();"
    " badge.style.left = (r.left + window.scrollX - 6) + 'px';"
    " badge.style.top = (r.top + window.scrollY - 6) + 'px';"
    " (document.body || document.documentElement).appendChild(badge);"
    " setTimeout(() => {{ e.style.outline = oldOutline;"
    " e.style.boxShadow = oldShadow; badge.remove(); }}, 900);"
    " return 'highlighted'; }})()"
)


_PRESS_JS = r"""
(() => {
  const key = __KEY__;
  const e = document.activeElement || document.body;
  const opts = {key: key, code: key, bubbles: true, cancelable: true};
  e.dispatchEvent(new KeyboardEvent('keydown', opts));
  e.dispatchEvent(new KeyboardEvent('keyup', opts));
  if (key === 'Enter' && e.form) e.form.requestSubmit();
  return 'pressed ' + key + ' on <' + e.tagName.toLowerCase() + '>';
})()
"""

_SELECT_JS = (
    '(() => {{ const e = document.querySelector("[data-ld-agent={i}]");'
    " if (!e || e.tagName !== 'SELECT') return 'not a select';"
    " const want = {text}.toLowerCase();"
    " const opt = [...e.options].find(o =>"
    " o.text.toLowerCase().includes(want) || o.value.toLowerCase() === want);"
    " if (!opt) return 'no matching option';"
    " e.value = opt.value;"
    " e.dispatchEvent(new Event('change', {{bubbles: true}}));"
    " return 'selected ' + opt.text; }})()"
)


def parse_action(raw: str) -> list[dict] | None:
    """Extract the action list from an LLM reply.

    Accepts the batched form {"actions": [...]} or a single legacy
    {"action": ...} object; always returns a list (or None if unusable).
    """
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("actions"), list):
        actions = [a for a in data["actions"] if isinstance(a, dict) and "action" in a]
        return actions or None
    if "action" in data:
        return [data]
    return None


class JsBridge(QObject):
    """GUI-thread executor for JavaScript — marshals worker-thread requests."""

    request = Signal(object, str)
    response = Signal(object, object)

    def __init__(self, get_view, parent=None):
        super().__init__(parent)
        self._get_view = get_view
        self.request.connect(self._on_request, Qt.ConnectionType.QueuedConnection)

    def _on_request(self, token, script: str) -> None:
        view = self._get_view()
        if view is None:
            self.response.emit(token, None)
            return
        view.page().runJavaScript(script, lambda result: self.response.emit(token, result))


class JsDriver:
    """Async driver around JsBridge (used from the agent's worker thread)."""

    def __init__(self, bridge: JsBridge):
        self._bridge = bridge
        self._pending: dict[object, asyncio.Future] = {}
        bridge.response.connect(self._on_response)

    def _on_response(self, token, result) -> None:
        future = self._pending.pop(token, None)
        if future is not None and not future.done():
            future.get_loop().call_soon_threadsafe(future.set_result, result)

    async def _js(self, script: str):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        token = object()
        self._pending[token] = future
        self._bridge.request.emit(token, script)
        return await asyncio.wait_for(future, timeout=15)

    async def snapshot(self, compact: bool = False) -> dict:
        max_els, max_text = (45, 800) if compact else (80, 1200)
        script = _SNAPSHOT_JS.replace("__MAXELS__", str(max_els)).replace(
            "__MAXTEXT__", str(max_text)
        )
        data = await self._js(script)
        if isinstance(data, str):
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                pass
        return {
            "url": "",
            "title": "",
            "elements": "",
            "text": "",
            "dialog": "",
            "more_below": False,
        }

    async def act(self, action: dict) -> str:
        kind = action.get("action")
        if kind == "click":
            index = int(action.get("index", -1))
            await self._js(_HIGHLIGHT_JS.format(i=index))
            # The ring persists ~0.9s after the click, so a long pause here
            # is wasted time — 0.15s is plenty to register visually.
            await asyncio.sleep(0.15)
            result = str(await self._js(_CLICK_JS.format(i=index)))
            # Click may navigate (adaptive wait) or just fire AJAX (returns
            # immediately) — no blind settle either way.
            await self._wait_loaded(timeout=2.5, initial=0.3)
            return result
        if kind == "type":
            index = int(action.get("index", -1))
            text = json.dumps(str(action.get("text", "")))
            await self._js(_HIGHLIGHT_JS.format(i=index))
            await asyncio.sleep(0.15)
            result = str(await self._js(_TYPE_JS.format(i=index, text=text)))
            await asyncio.sleep(0.2)
            return result
        if kind == "press":
            key = str(action.get("text", "Enter"))
            if key not in ("Enter", "Tab", "Escape"):
                key = "Enter"
            result = str(await self._js(_PRESS_JS.replace("__KEY__", json.dumps(key))))
            await self._wait_loaded(timeout=2.5, initial=0.35)
            return result
        if kind == "select":
            index = int(action.get("index", -1))
            text = json.dumps(str(action.get("text", "")))
            await self._js(_HIGHLIGHT_JS.format(i=index))
            await asyncio.sleep(0.15)
            result = str(await self._js(_SELECT_JS.format(i=index, text=text)))
            await asyncio.sleep(0.2)
            return result
        if kind == "back":
            await self._js("history.back()")
            await self._wait_loaded(timeout=4.0, initial=0.4)
            return "went back"
        if kind == "wait":
            try:
                secs = float(str(action.get("text", "1")) or 1)
            except ValueError:
                secs = 1.0
            secs = max(0.2, min(secs, 5.0))
            await asyncio.sleep(secs)
            return f"waited {secs:.1f}s"
        if kind == "scroll":
            dy = -700 if str(action.get("text", "")).lower() == "up" else 700
            await self._js(f"window.scrollBy(0, {dy})")
            await asyncio.sleep(0.2)
            return "scrolled"
        if kind == "navigate":
            url = str(action.get("url", ""))
            if url.startswith(("http://", "https://")):
                await self._js(f"window.location.href = {json.dumps(url)}")
                await self._wait_loaded(timeout=8.0, initial=0.6)
                return f"navigated to {url}"
            return "refused: invalid url"
        return "no-op"

    async def _wait_loaded(self, timeout: float = 8.0, initial: float = 0.6) -> None:
        """Return as soon as the page reports loaded — faster and more
        reliable than a fixed sleep (fast sites continue immediately)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        await asyncio.sleep(initial)  # let a potential navigation commit
        while loop.time() < deadline:
            try:
                state = await asyncio.wait_for(self._js("document.readyState"), 3)
            except Exception:
                state = None  # JS context torn down mid-navigation
            if state == "complete":
                return
            await asyncio.sleep(0.25)


class AgentSession:
    """One autonomous task run. Loop: snapshot -> LLM plan -> act -> narrate."""

    def __init__(
        self,
        ai_bridge,
        js_bridge,
        max_steps: int = 15,
        use_cdp: bool = True,
        get_url=None,
        vision: bool = False,
    ):
        self._ai = ai_bridge
        self._js_bridge = js_bridge
        self.max_steps = max_steps
        self._use_cdp = use_cdp
        self._get_url = get_url
        self._vision = vision
        self.stop_requested = False

    def stop(self) -> None:
        self.stop_requested = True

    async def run(self, task: str, provider=None, on_step=None) -> str:
        global ACTIVE_SESSIONS
        ACTIVE_SESSIONS += 1
        try:
            return await self._run(task, provider=provider, on_step=on_step)
        finally:
            ACTIVE_SESSIONS -= 1

    async def _run(self, task: str, provider=None, on_step=None) -> str:
        def say(message: str) -> None:
            if on_step:
                on_step(message)

        history: list[str] = []
        # Prefer the raw-CDP driver (trusted input events + screenshots);
        # fall back to JS injection if the debug port is unavailable.
        driver = None
        if self._use_cdp and self._get_url is not None:
            try:
                from browser_core.cdp_driver import CdpDriver

                driver = await CdpDriver.connect(self._get_url(), vision=self._vision)
                say("⚡ CDP driver: trusted input" + (" + vision" if self._vision else ""))
            except Exception as exc:
                say(f"⚠ CDP unavailable ({exc}) — using JS driver")
        if driver is None:
            driver = JsDriver(self._js_bridge)
        # Local CPU models get a smaller snapshot to keep each step snappy.
        effective = provider or self._ai.default_provider()
        compact = bool(effective) and self._ai.is_local(effective)
        # Plan-first: one upfront call sketches the route (skipped on local
        # CPU models to keep them fast).
        plan = "" if compact else await self._make_plan(task, provider)
        if plan:
            say("🧭 " + " | ".join(plan.splitlines())[:200])
        last_sig: tuple | None = None
        stuck = 0
        for step in range(1, self.max_steps + 1):
            if self.stop_requested:
                say("⏹ Stopped by user.")
                return "Stopped."
            try:
                snap = await driver.snapshot(compact=compact)
            except Exception as exc:
                say(f"⚠ Snapshot failed ({exc}); retrying…")
                await asyncio.sleep(1.5)
                continue

            # Stuck detection: same URL + same elements as last step means
            # the page didn't change — push the model to change tactics.
            sig = (snap["url"], snap["elements"][:400])
            stuck = stuck + 1 if sig == last_sig else 0
            last_sig = sig

            # Vision: attach the current frame (cloud models only — local
            # CPU vision is too slow for a per-step loop).
            shot = snap.pop("shot_b64", "")
            content = self._prompt(task, snap, history, plan, stuck)
            if shot and self._vision and not compact:
                content = [
                    {"type": "text", "text": content},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{shot}"},
                    },
                ]
            messages = [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": content},
            ]
            think_t0 = time.monotonic()
            try:
                raw, _used = await self._ai.chat(messages, provider=provider)
            except Exception as exc:
                return f"LLM error: {exc}"
            think_s = time.monotonic() - think_t0

            actions = parse_action(raw)
            if not actions:
                history.append("last reply invalid; respond with ONE JSON object")
                continue

            first = actions[0]
            if first.get("action") == "done":
                answer = str(first.get("text", "")).strip() or "Done."
                say(f"✅ {answer}")
                return answer

            actions = actions[:4]  # cap batches
            reasons = " → ".join(str(a.get("reason") or a.get("action")) for a in actions)
            say(f"▶ Step {step} [{think_s:.1f}s think]: {reasons}")

            for sub in actions:
                if self.stop_requested:
                    break
                kind = sub.get("action")
                if kind == "done":
                    answer = str(sub.get("text", "")).strip() or "Done."
                    say(f"✅ {answer}")
                    return answer
                try:
                    result = await driver.act(sub)
                except Exception as exc:
                    result = f"error: {exc}"
                history.append(f"{kind}({sub.get('index', '')}) -> {result}")
            # Settles live inside act(); just yield a beat before the next
            # snapshot so in-flight rendering can finish.
            await asyncio.sleep(0.15)

        return f"Reached {self.max_steps} steps without finishing."

    async def _make_plan(self, task: str, provider=None) -> str:
        """One upfront planning call — small models execute noticeably better
        when each step is judged against a written route. Failure is fine:
        the agent simply runs planless."""
        messages = [
            {
                "role": "system",
                "content": "You break web-browsing tasks into short plans.",
            },
            {
                "role": "user",
                "content": (
                    f"Web task: {task}\nReply with a numbered plan of 3-6 "
                    "concrete browser steps (max 12 words each), then stop."
                ),
            },
        ]
        try:
            raw, _used = await self._ai.chat(messages, provider=provider)
        except Exception:
            return ""
        lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
        return "\n".join(lines[:6])[:600]

    @staticmethod
    def _prompt(
        task: str,
        snap: dict,
        history: list[str],
        plan: str = "",
        stuck: int = 0,
    ) -> str:
        parts = [
            f"TASK: {task}",
            "",
            f"URL: {snap['url']}",
            f"TITLE: {snap['title']}",
            "",
            "INTERACTIVE ELEMENTS:",
            snap["elements"] or "(none)",
            "",
            "PAGE TEXT (excerpt):",
            snap["text"],
        ]
        if snap.get("more_below"):
            parts += [
                "",
                "(More content below the fold — scroll to reveal elements.)",
            ]
        if snap.get("dialog"):
            parts += [
                "",
                "PAGE POPUP DIALOG (auto-dismissed; react to it if relevant): " + snap["dialog"],
            ]
        if plan:
            parts += ["", "PLAN:", plan]
        if stuck >= 2:
            parts += [
                "",
                f"WARNING: the page has not changed after your last {stuck} "
                "action(s) — you are likely stuck. Change tactics: scroll, "
                "press Enter/Tab, navigate directly, or finish with done.",
            ]
        if history:
            parts += ["", "PREVIOUS STEPS:", *history[-5:]]
        return "\n".join(parts)
