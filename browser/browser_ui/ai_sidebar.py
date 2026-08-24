"""AI sidebar: chat with page context, quick actions, autonomous agent."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import html
import json
import re

from browser_core.agent import AgentSession, JsBridge
from browser_core.ai_bridge import AIBridge
from browser_core.brand import tokens as _brand_tokens
from PySide6.QtCore import QBuffer, QIODevice, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

_SYSTEM = (
    "You are the browser's built-in AI assistant — concise, helpful, factual. "
    "When page context is provided, ground answers in it and say when the "
    "answer is not on the page. Use short paragraphs or bullets. "
    "Format answers in GitHub-flavored Markdown: fenced code blocks for "
    "code/commands, **bold** for key terms, short bullet lists for steps."
)

# ── markdown-lite → HTML (chat bubbles) ──────────────────────────────────
# Deliberately small: fenced code, inline code, bold, headings, bullet and
# numbered lists, paragraphs. Everything is HTML-escaped FIRST so model
# output can never inject markup into the chat view.

# The active theme's design tokens, set by AiSidebar.__init__ so chat bubbles,
# code blocks and status lines match the rest of the product (not hard-coded
# neon). Re-applied when the theme changes via AiSidebar._apply_theme().
_P: dict = {}


def _code_style() -> str:
    return (
        f"background:{_P.get('window', '#0d1322')};"
        f"border:1px solid {_P.get('border', '#232c42')};border-radius:10px;"
        "padding:9px 11px;margin:6px 0;font-family:'Cascadia Code',Consolas,"
        "monospace;font-size:12px;white-space:pre-wrap;"
    )


def _inline_code_style() -> str:
    return (
        f"background:{_P.get('window', '#0d1322')};border-radius:5px;"
        "padding:1px 6px;font-family:'Cascadia Code',Consolas,monospace;"
        "font-size:12px;"
    )


def _md_inline(text: str) -> str:
    """Inline markdown for one non-code segment (already plain text)."""
    h = html.escape(text)
    h = re.sub(r"`([^`\n]+)`", rf"<code style='{_inline_code_style()}'>\1</code>", h)
    h = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", h)
    h = re.sub(r"^#{1,6}\s+(.+?)\s*$", r"<b>\1</b>", h, flags=re.M)
    paragraphs = re.split(r"\n{2,}", h)
    out = []
    for para in paragraphs:
        lines = [ln for ln in para.split("\n") if ln.strip()]
        if len(lines) > 1 and all(re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", ln) for ln in lines):
            items = "".join(
                "<li>" + re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln) + "</li>" for ln in lines
            )
            out.append(f"<ul style='margin:4px 0 4px 16px'>{items}</ul>")
        else:
            out.append("<p style='margin:4px 0'>" + "<br>".join(lines) + "</p>")
    return "".join(out)


def _md_lite(text: str) -> str:
    """Render a small, safe Markdown subset to HTML for the chat bubbles."""
    out = []
    for i, part in enumerate(str(text).split("```")):
        if i % 2 == 1:
            code = part
            newline = code.find("\n")
            if newline != -1 and re.fullmatch(r"[A-Za-z0-9_+.#-]{1,20}", code[:newline].strip()):
                code = code[newline + 1 :]
            out.append(f"<pre style='{_code_style()}'>{html.escape(code.strip())}</pre>")
        else:
            out.append(_md_inline(part))
    return "".join(out)


def _bubble(role: str, content_html: str) -> str:
    """One chat bubble with a colored edge per role."""
    edges = {
        "user": _P.get("accent", "#4f9cf9"),
        "assistant": _P.get("accent2", "#9d4ff9"),
        "error": _P.get("danger", "#e06c75"),
    }
    labels = {"user": "You", "assistant": "Assistant", "error": "Error"}
    color = edges.get(role, _P.get("muted", "#8b93a7"))
    label = labels.get(role, role.title())
    return (
        f"<div style='margin:8px 0;padding:7px 11px;background:rgba(255,255,255,.04);"
        f"border-left:3px solid {color};border-radius:8px'>"
        f"<div style='color:{color};font-weight:600;font-size:11px;margin-bottom:2px'>"
        f"{label}</div>{content_html}</div>"
    )


# Appended to harness-mode tasks when the Browser Control API is live, so the
# exe's agent (Bash + WebFetch tools) can drive the REAL open tabs - the
# "exe brain, browser hands" loop. Keep it copy-paste runnable on Windows.
_CONTROL_API_HINT = """

---
ENVIRONMENT: The user's LIVE desktop browser (the tabs they are looking at
right now) is controllable through the local Browser Control API at {base}
(localhost only). Use WebFetch for GET routes and Bash with curl
for POST routes:
  GET  {base}/status       browser state
  GET  {base}/tabs         open tabs
  POST {base}/navigate     {{"url": "https://example.com", "new_tab": false}}
  POST {base}/snapshot     URL, title, numbered interactive elements, text
  POST {base}/act          {{"action": "click|type|press|select|scroll|navigate|back|wait", "index": N, "text": "...", "url": "..."}}
  GET  {base}/screenshot   base64 JPEG of the visible tab
  POST {base}/eval         {{"js": "..."}} - run JS in the active tab
  POST {base}/ask          {{"question": "..."}} - AI answer grounded in the page
Windows curl example:
  curl -s -X POST {base}/snapshot -H "Content-Type: application/json" -d "{{}}"
Workflow: /snapshot to see the page, /act by element index, /snapshot again
to verify. When this task involves an open web page, drive it with these
calls instead of asking the user to do it.{auth}"""


class _ChatWorker(QThread):
    token = Signal(str)
    finished = Signal(str, str)
    failed = Signal(str)

    def __init__(self, bridge, messages, provider, parent=None):
        super().__init__(parent)
        self._bridge = bridge
        self._messages = messages
        self._provider = provider

    def run(self):
        try:
            text, used = asyncio.run(
                self._bridge.chat(
                    self._messages,
                    provider=self._provider,
                    on_token=lambda t: self.token.emit(t),
                )
            )
            self.finished.emit(text, used)
        except Exception as exc:
            self.failed.emit(str(exc))


class _AgentWorker(QThread):
    step = Signal(str)
    finished = Signal(str)

    def __init__(self, session, task, provider, parent=None):
        super().__init__(parent)
        self._session = session
        self._task = task
        self._provider = provider

    def run(self):
        try:
            result = asyncio.run(
                self._session.run(
                    self._task,
                    provider=self._provider,
                    on_step=lambda m: self.step.emit(m),
                )
            )
            self.finished.emit(result)
        except Exception as exc:
            self.finished.emit(f"Agent error: {exc}")


class _ModelWorker(QThread):
    ready = Signal(list)

    def __init__(self, bridge, provider, parent=None):
        super().__init__(parent)
        self._bridge = bridge
        self._provider = provider

    def run(self):
        self.ready.emit(self._bridge.fetch_models(self._provider))


class _ShotWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        try:
            from browser_core.screenshot import capture_b64

            self.done.emit(asyncio.run(capture_b64(self._url)))
        except Exception as exc:
            self.failed.emit(str(exc))


class AiSidebar(QDockWidget):
    def __init__(self, main_window):
        super().__init__("AI Assistant", main_window)
        self.setObjectName("ai_sidebar")
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self._mw = main_window
        self.bridge = AIBridge()
        self._model_worker = None
        # Adopt the active theme's tokens so bubbles/code match the product.
        self._apply_theme()
        # Restore per-provider model picks from previous sessions before any
        # labels are built with model_for().
        saved = self._mw.settings.get("ai_model_overrides", {}) or {}
        if isinstance(saved, dict):
            for pname, pmodel in saved.items():
                self.bridge.set_model_override(str(pname), str(pmodel))
        self.js_bridge = JsBridge(lambda: self._mw.tabs.current_view(), self)
        self.harness_bridge = None
        self._chat_worker = None
        self._agent_worker = None
        self._agent_session = None
        self._harness_worker = None
        self._shot_worker = None
        self._shot_prompt = ""
        self._history: list[dict] = []
        self._stream_count = 0
        # Chat is a list of blocks (role + payload) re-rendered as bubbles;
        # streaming updates the last assistant block with throttled renders.
        self._blocks: list[dict] = []
        self._render_pending = False

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        self.provider_box = QComboBox(body)
        self.provider_box.addItem("auto (fallback chain)", None)
        # Show providers with cline-usage first when available
        ordered = list(self.bridge.providers())
        default = self.bridge.default_provider()
        if default in ordered:
            ordered.remove(default)
            ordered.insert(0, default)
        for name in ordered:
            display = self._provider_label(name)
            label = f"{display} — {self.bridge.model_for(name)}"
            self.provider_box.addItem(label, name)
        self.context_box = QCheckBox("Page context", body)
        self.context_box.setChecked(True)
        self.context_box.setToolTip("Include the current page text with your message")
        top.addWidget(self.provider_box, 1)
        top.addWidget(self.context_box)
        layout.addLayout(top)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:", body))
        self.model_box = QComboBox(body)
        self.model_box.setEnabled(False)
        self.model_box.currentTextChanged.connect(self._model_changed)
        model_row.addWidget(self.model_box, 1)
        layout.addLayout(model_row)
        self.provider_box.currentIndexChanged.connect(self._provider_changed)

        # Live coding-agent backend status.
        self.harness_status = QLabel("coding agent: …", body)
        self.harness_status.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.harness_status)

        if not self.bridge.providers():
            warn = QLabel(
                "No AI backend found. Free + no API key: install Ollama "
                "(https://ollama.com) and run `ollama pull qwen3:4b` "
                "(CPU-friendly), then restart — or add cloud keys to the "
                "repo .env.",
                body,
            )
            warn.setWordWrap(True)
            layout.addWidget(warn)

        actions = QHBoxLayout()
        for label, slot in (
            ("Summarize", self._summarize),
            ("📷 Look at page", self._visual_qa),
            ("Clear", self._clear_chat),
        ):
            btn = QPushButton(label, body)
            btn.clicked.connect(slot)
            actions.addWidget(btn)
        layout.addLayout(actions)

        self.harness_box = QCheckBox(body)
        self.harness_box.setText("\U0001f50c Full coding agent (recommended)")
        self.harness_box.setToolTip(
            "Agent tasks run on the full coding-agent backend, which "
            "starts automatically and can drive your open tabs. "
            "Uncheck to use the lighter built-in agent instead."
        )
        self.harness_box.setChecked(bool(self._mw.settings.get("harness_mode", True)))
        self.harness_box.toggled.connect(lambda on: self._mw.settings.set("harness_mode", bool(on)))
        layout.addWidget(self.harness_box)

        self.chat = QTextBrowser(body)
        # Links in chat open as browser tabs — never in an external
        # browser. The user never leaves this one window.
        self.chat.setOpenExternalLinks(False)
        self.chat.setOpenLinks(False)
        self.chat.anchorClicked.connect(self._open_link)
        layout.addWidget(self.chat)

        row = QHBoxLayout()
        self.input = QLineEdit(body)
        self.input.setPlaceholderText("Ask about this page or anything…")
        self.input.returnPressed.connect(self._send)
        self.send_btn = QPushButton("Send", body)
        self.send_btn.clicked.connect(self._send)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        layout.addLayout(row)

        layout.addWidget(QLabel("Autonomous agent (drives the current tab):", body))
        agent_row = QHBoxLayout()
        self.agent_input = QLineEdit(body)
        self.agent_input.setPlaceholderText("e.g. Search this site for X, report back")
        self.agent_input.returnPressed.connect(self._start_agent)
        self.agent_btn = QPushButton("Start", body)
        self.agent_btn.clicked.connect(self._start_agent)
        self.stop_btn = QPushButton("Stop", body)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_agent)
        agent_row.addWidget(self.agent_input, 1)
        agent_row.addWidget(self.agent_btn)
        agent_row.addWidget(self.stop_btn)
        layout.addLayout(agent_row)
        self.vision_box = QCheckBox("Vision steps (auto — screenshots each step)", body)
        self.vision_box.setToolTip(
            "The agent sees a screenshot every step (uses image tokens). "
            "Auto-ENABLED when the selected model accepts images "
            "(gpt-4o, gemini, claude-sonnet, gemma3…), auto-DISABLED "
            "for text-only models so the agent never sends a bad payload."
        )
        layout.addWidget(self.vision_box)

        self.status = QLabel("", body)
        self.status.setStyleSheet(f"color: {_P.get('muted', '#8b93a7')}; font-size: 11px;")
        layout.addWidget(self.status)

        self.setWidget(body)
        self._sync_vision_default()
        self._restore_provider()
        self._greet()
        self.refresh_harness_status()

    def _apply_theme(self) -> None:
        """Adopt the active theme's tokens and re-render the chat with them."""
        global _P
        _P = dict(_brand_tokens(getattr(self._mw, "settings", None)))
        muted = _P.get("muted", "#8b93a7")
        if hasattr(self, "status"):
            self.status.setStyleSheet(f"color: {muted}; font-size: 11px;")
        if hasattr(self, "_blocks") and self._blocks:
            self._render()

    def _restore_provider(self) -> None:
        """Re-select the provider the user picked last session."""
        saved = self._mw.settings.get("ai_provider")
        if not saved:
            return
        for i in range(self.provider_box.count()):
            if self.provider_box.itemData(i) == saved:
                self.provider_box.blockSignals(True)
                self.provider_box.setCurrentIndex(i)
                self.provider_box.blockSignals(False)
                self._provider_changed(i)
                break

    # ── rendering ────────────────────────────────────────────────────

    def _greet(self) -> None:
        providers = ", ".join(self.bridge.providers()) or "none set up"
        muted = _P.get("muted", "#9aa1b5")
        text_c = _P.get("text", "#e8ecf5")
        self._blocks = [
            {
                "role": "raw",
                "text": (
                    f"<div style='color:{muted};padding:4px 2px'>"
                    f"<b style='color:{text_c}'>🤖 Assistant</b><br>"
                    "Ask about this page, use a quick action, or give the "
                    "agent a task — it drives the current tab while you "
                    "watch.<br>"
                    f"<span style='font-size:11px'>AI: "
                    f"{html.escape(providers)}</span></div>"
                ),
            }
        ]
        self._render()

    def _render_block(self, block: dict) -> str:
        role = block.get("role", "raw")
        text = block.get("text", "")
        if role == "raw":
            return text
        if role == "user":
            return _bubble("user", html.escape(text))
        if role == "assistant":
            return _bubble("assistant", _md_lite(text))
        if role == "error":
            return _bubble("error", html.escape(text))
        return html.escape(text)

    def _render(self) -> None:
        parts = ["<div style='font-size:13px'>"]
        parts.extend(self._render_block(b) for b in self._blocks[-300:])
        parts.append("</div>")
        self.chat.setHtml("".join(parts))
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _schedule_render(self) -> None:
        """Throttle re-renders during token streaming (~12 fps)."""
        if self._render_pending:
            return
        self._render_pending = True
        QTimer.singleShot(80, self._flush_render)

    def _flush_render(self) -> None:
        self._render_pending = False
        self._render()

    def _append(self, html_chunk: str) -> None:
        """Append a raw-HTML block (steps, notes, results)."""
        self._blocks.append({"role": "raw", "text": html_chunk})
        self._render()

    def _user(self, text: str) -> None:
        self._blocks.append({"role": "user", "text": text})
        self._render()

    def _begin_assistant(self) -> None:
        self._blocks.append({"role": "assistant", "text": ""})
        self._stream_count = 0
        self._render()

    def _on_token(self, token: str) -> None:
        self._stream_count += 1
        if self._blocks and self._blocks[-1].get("role") == "assistant":
            self._blocks[-1]["text"] += token
        else:
            self._blocks.append({"role": "assistant", "text": token})
        self._schedule_render()

    def _open_link(self, url) -> None:
        """Chat links open as browser tabs — the user never leaves the app."""
        self._mw.open_in_new_tab(url)

    def refresh_harness_status(self) -> None:
        """Update the coding-agent status line from the supervisor cache."""
        muted = _P.get("muted", "#8b93a7")
        ok = _P.get("ok", "#34d399")
        err = _P.get("danger", "#ff5b6e")
        warn = "#fbbf24"
        sup = getattr(self._mw._app, "harness", None)
        if sup is None:
            self.harness_status.setText(
                f"<span style='color:{muted}'>coding agent: unavailable</span>"
            )
            return
        st = sup.status()
        if st.get("up"):
            self.harness_status.setText(f"<span style='color:{ok}'>● coding agent online</span>")
        elif st.get("starting"):
            self.harness_status.setText(
                f"<span style='color:{warn}'>● coding agent starting…</span>"
            )
        elif st.get("error"):
            self.harness_status.setText(
                f"<span style='color:{err}'>● coding agent offline</span> "
                f"<span style='color:{muted};font-size:10px'>retries automatically</span>"
            )
        else:
            self.harness_status.setText(
                f"<span style='color:{muted}'>● coding agent off — starts when needed</span>"
            )

    def _sync_vision_default(self) -> None:
        """Vision steps default ON for vision-capable models, OFF otherwise."""
        provider = self._selected_provider() or self.bridge.default_provider()
        capable = bool(provider) and self.bridge.supports_vision(provider)
        self.vision_box.setChecked(capable)

    # ── chat ─────────────────────────────────────────────────────────

    def _selected_provider(self):
        return self.provider_box.currentData()

    # ── model picker ─────────────────────────────────────────────────

    def _provider_changed(self, _index: int) -> None:
        provider = self._selected_provider()
        # Remember the pick so it survives restarts (set to "" for auto).
        self._mw.settings.set("ai_provider", provider or "")
        self.model_box.clear()
        self.model_box.setEnabled(False)
        if provider is None:
            return
        self.model_box.addItem("loading models…")
        self._model_worker = _ModelWorker(self.bridge, provider, self)
        self._model_worker.ready.connect(lambda models, p=provider: self._models_ready(p, models))
        self._model_worker.start()

    def _models_ready(self, provider: str, models: list) -> None:
        if provider != self._selected_provider():
            return
        current = self.bridge.model_for(provider)
        self.model_box.blockSignals(True)
        self.model_box.clear()
        for model in models:
            self.model_box.addItem(self._model_label(provider, model), model)
        index = self.model_box.findData(current)
        if index >= 0:
            self.model_box.setCurrentIndex(index)
        self.model_box.blockSignals(False)
        self.model_box.setEnabled(True)
        self._sync_vision_default()

    def _provider_label(self, provider: str) -> str:
        """Human-friendly provider name shown in the dropdown."""
        endpoint_label = self.bridge.provider_label(provider)
        if endpoint_label:
            return endpoint_label
        labels = {
            "clinepass": "ClinePass",
            "cline-usage": "Cline Usage",
            "ollama": "Ollama",
            "lmstudio": "LM Studio",
            "google": "Google Gemini",
            "groq": "Groq",
            "zai": "Z.ai",
            "openrouter": "OpenRouter",
            "deepseek": "DeepSeek",
            "openai": "OpenAI",
            "anthropic": "Anthropic",
        }
        return labels.get(provider, provider)

    @staticmethod
    def _model_label(provider: str, model: str) -> str:
        """Show the billing group right in the picker."""
        if provider == "clinepass":
            if model.startswith("cline-pass/"):
                return f"{model}   · flat subscription"
            return f"{model}   · credit-billed ⚠"
        if provider == "cline-usage":
            # Free-tier models are billed at $0.00; credit models deduct balance.
            free_tier = (
                "minimax/minimax-m2.5",
                "meta-llama/llama-3.2-3b-instruct",
                "google/gemini-2.0-flash",
                "qwen/qwen3-8b",
            )
            if model in free_tier:
                return f"{model}   · free tier"
            if model in ("deepseek/deepseek-chat", "deepseek/deepseek-r1"):
                return f"{model}   · free tier"
            return f"{model}   · credit-billed"
        return model

    def _model_changed(self, _text: str) -> None:
        provider = self._selected_provider()
        model = self.model_box.currentData()
        if provider is None or not model:
            return
        if model == self.bridge.model_for(provider):
            return
        self.bridge.set_model_override(provider, model)
        saved = self._mw.settings.get("ai_model_overrides", {}) or {}
        if not isinstance(saved, dict):
            saved = {}
        saved[provider] = model
        self._mw.settings.set("ai_model_overrides", saved)
        self.status.setText(f"{provider} model → {model}")
        self._sync_vision_default()

    def _send(self) -> None:
        text = self.input.text().strip()
        if not text or self._chat_worker is not None:
            return
        self.input.clear()
        if text.lower() in ("/clear", "/new", "/reset"):
            self._clear_chat()
            return
        self._user(text)
        self._history.append({"role": "user", "content": text})
        if self.context_box.isChecked():
            view = self._mw.tabs.current_view()
            if view is not None and view.url().scheme() in ("http", "https"):
                view.page().toPlainText(lambda t: self._start_chat(t))
                return
        self._start_chat("")

    def _start_chat(self, page_text: str) -> None:
        messages = [{"role": "system", "content": _SYSTEM}]
        if page_text:
            # Local CPU models ingest prompts slowly — send a smaller excerpt.
            provider = self._selected_provider() or self.bridge.default_provider()
            local = provider is not None and self.bridge.is_local(provider)
            budget = 4000 if local else 12000
            messages.append(
                {
                    "role": "system",
                    "content": "Current page content for context:\n" + page_text[:budget],
                }
            )
        messages.extend(self._history[-8:])
        self._begin_assistant()
        self.status.setText("thinking…")
        self._chat_worker = _ChatWorker(self.bridge, messages, self._selected_provider(), self)
        self._chat_worker.token.connect(self._on_token)
        self._chat_worker.finished.connect(self._chat_done)
        self._chat_worker.failed.connect(self._chat_failed)
        self._chat_worker.start()

    def _chat_done(self, text: str, provider: str) -> None:
        if self._stream_count == 0 and text:
            self._on_token(text)
        if self._blocks and self._blocks[-1].get("role") == "assistant":
            self._blocks[-1]["text"] = text or self._blocks[-1]["text"]
        self._render()  # final, unthrottled render
        self._history.append({"role": "assistant", "content": text})
        self.status.setText(f"answered by {provider}")
        self._chat_worker = None

    def _chat_failed(self, error: str) -> None:
        # Drop the empty assistant placeholder left by _begin_assistant.
        if (
            self._blocks
            and self._blocks[-1].get("role") == "assistant"
            and not self._blocks[-1].get("text")
        ):
            self._blocks.pop()
        self._blocks.append({"role": "error", "text": error})
        self._render()
        self.status.setText("failed")
        self._chat_worker = None

    # ── quick actions ────────────────────────────────────────────────

    def _quick(self, prompt: str) -> None:
        """Send a preset prompt about the current page."""
        if self._chat_worker is not None:
            self.status.setText("busy — wait for the current response")
            return
        self._user(prompt)
        self._history.append({"role": "user", "content": prompt})
        view = self._mw.tabs.current_view()
        if view is None or view.url().scheme() not in ("http", "https"):
            # No page context — send the prompt as-is
            self._start_chat("")
            return
        view.page().toPlainText(lambda t: self._start_chat(t))

    def _summarize(self) -> None:
        self._quick("Summarize this page in 5 concise bullet points.")

    def _clear_chat(self) -> None:
        self._history = []
        self.status.setText("Conversation cleared")
        self._greet()

    def ask(self, question: str) -> None:
        """Programmatic question (e.g. omnibox "?…" prefix) — into the chat."""
        self.show()
        self.input.setText(question)
        self._send()

    def ask_about(self, instruction: str, selected_text: str) -> None:
        """Context-menu entry: run an instruction over the selected text."""
        if self._chat_worker is not None:
            self.status.setText("busy — try again in a moment")
            return
        self.show()
        snippet = selected_text[:80] + ("…" if len(selected_text) > 80 else "")
        label = f"{instruction} “{snippet}”"
        self._user(label)
        self._history.append({"role": "user", "content": label})
        messages = [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "system",
                "content": "Selected text from the page:\n" + selected_text[:6000],
            },
        ]
        messages.extend(self._history[-8:])
        self._begin_assistant()
        self.status.setText("thinking…")
        self._chat_worker = _ChatWorker(self.bridge, messages, self._selected_provider(), self)
        self._chat_worker.token.connect(self._on_token)
        self._chat_worker.finished.connect(self._chat_done)
        self._chat_worker.failed.connect(self._chat_failed)
        self._chat_worker.start()

    def _visual_qa(self) -> None:
        """Screenshot the current tab and ask a vision model about it."""
        if self._chat_worker is not None or self._shot_worker is not None:
            return
        view = self._mw.tabs.current_view()
        if view is None or view.url().scheme() not in ("http", "https"):
            self._append("<i>Open a web page first.</i>")
            return
        self._shot_prompt = self.input.text().strip() or (
            "Describe this page: what is it, what stands out, " "anything actionable?"
        )
        self.input.clear()
        self.status.setText("capturing screenshot…")
        self._shot_worker = _ShotWorker(view.url().toString(), self)
        self._shot_worker.done.connect(self._shot_ready)
        self._shot_worker.failed.connect(self._shot_failed)
        self._shot_worker.start()

    def _shot_ready(self, b64: str, mime: str = "image/jpeg") -> None:
        self._shot_worker = None
        prompt = self._shot_prompt
        self._user("📷 " + prompt)
        self.status.setText("asking vision model…")
        content = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            },
        ]
        messages = [{"role": "system", "content": _SYSTEM}]
        messages.extend(self._history[-8:])
        messages.append({"role": "user", "content": content})
        self._begin_assistant()
        self._chat_worker = _ChatWorker(self.bridge, messages, self._selected_provider(), self)
        self._chat_worker.token.connect(self._on_token)
        self._chat_worker.finished.connect(self._chat_done)
        self._chat_worker.failed.connect(self._chat_failed)
        self._chat_worker.start()

    def _shot_failed(self, error: str) -> None:
        self._shot_worker = None
        # Fallback: QWidget.grab() works when WebEngine renders in software.
        view = self._mw.tabs.current_view()
        if view is not None:
            pixmap = view.grab()
            if not pixmap.isNull():
                image = pixmap.toImage()
                if image.width() > 1024:
                    image = image.scaledToWidth(1024, Qt.TransformationMode.SmoothTransformation)
                buffer = QBuffer(self)
                buffer.open(QIODevice.OpenModeFlag.ReadWrite)
                image.save(buffer, "PNG")
                b64 = base64.b64encode(bytes(buffer.data())).decode()
                self._shot_ready(b64, mime="image/png")
                return
        self._append(f"<i>Screenshot failed: {html.escape(error)}</i>")

    # ── autonomous agent ─────────────────────────────────────────────

    def _start_agent(self) -> None:
        task = self.agent_input.text().strip()
        if not task or self._agent_worker is not None or self._harness_worker is not None:
            return
        if self.harness_box.isChecked():
            self.agent_input.clear()
            self._start_harness_agent(task)
            return
        view = self._mw.tabs.current_view()
        url = view.url().toString() if view else ""
        self.agent_input.clear()
        self._append(
            f"<b style='color:{_P.get('accent', '#f9a24f')}'>Agent task:</b> "
            f"{html.escape(task)}"
        )
        use_cdp = bool(url) and url.startswith(
            ("http://", "https://", "file://localhost", "file://", "about:")
        )
        session = AgentSession(
            self.bridge,
            self.js_bridge,
            use_cdp=use_cdp,
            get_url=lambda u=url: (
                self._mw.tabs.current_view().url().toString() if self._mw.tabs.current_view() else u
            ),
            vision=(
                self.vision_box.isChecked()
                and self.bridge.supports_vision(self._selected_provider())
            ),
        )
        self._agent_session = session
        self._agent_worker = _AgentWorker(
            self._agent_session, task, self._selected_provider(), self
        )
        self._agent_worker.step.connect(
            lambda m: self._append(
                f"<span style='color:{_P.get('muted', '#888')}'>{html.escape(m)}</span>"
            )
        )
        self._agent_worker.finished.connect(self._agent_done)
        self.agent_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status.setText("agent running…")
        self._agent_worker.start()

    def _stop_agent(self) -> None:
        if self._harness_worker is not None:
            self._harness_worker.stop()
            self.status.setText("stopping coding agent task…")
            return
        if self._agent_session is not None:
            self._agent_session.stop()
            self.status.setText("stopping after current step…")

    def _agent_done(self, result: str) -> None:
        self._append(
            f"<b style='color:{_P.get('accent', '#f9a24f')}'>Agent finished:</b> "
            f"{html.escape(result)}"
        )
        self.agent_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status.setText("agent idle")
        self._agent_worker = None
        self._agent_session = None

    # ── harness-mode agent ──────────────────────────────────────────────

    def _ensure_harness_bridge(self):
        """The app-shared harness bridge (one connection for the whole app)."""
        sup = getattr(self._mw._app, "harness", None)
        if sup is not None:
            return sup.bridge
        if self.harness_bridge is None:
            from browser_core.harness_bridge import HarnessBridge

            self.harness_bridge = HarnessBridge()
        return self.harness_bridge

    def _control_api_hint(self) -> str:
        """Instructions letting the harness agent drive THIS live browser."""
        server = getattr(self._mw._app, "control_server", None)
        if server is None or not server.running:
            return ""
        auth = ""
        token = str(self._mw.settings.get("browser_api_token", "") or "")
        if token:
            auth = (
                f"\nAUTH: every request needs header "
                f"`Authorization: Bearer {token}` "
                f'(curl: -H "Authorization: Bearer {token}").'
            )
        return _CONTROL_API_HINT.format(base=server.base_url, auth=auth)

    def _start_harness_agent(self, task: str) -> None:
        self._append(
            f"<b style='color:{_P.get('accent2', '#9d4ff9')}'>Coding agent:</b> "
            f"{html.escape(task)}"
        )
        full_task = task + self._control_api_hint()
        worker = _HarnessWorker(
            self._ensure_harness_bridge(),
            full_task,
            self,
            supervisor=getattr(self._mw._app, "harness", None),
        )
        worker.progress.connect(lambda m: self.status.setText(m))
        worker.note.connect(
            lambda m: self._append(
                f"<span style='color:{_P.get('muted', '#888')}'>{html.escape(m)}</span>"
            )
        )
        worker.finished.connect(self._harness_done)
        worker.failed.connect(self._harness_failed)
        self._harness_worker = worker
        self.agent_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status.setText("coding agent: connecting…")
        worker.start()

    def _harness_done(self, result: str) -> None:
        self._append(
            f"<b style='color:{_P.get('accent2', '#9d4ff9')}'>Coding agent finished:</b> "
            "<pre style='white-space:pre-wrap;margin:4px 0'>"
            f"{html.escape(result)}</pre>"
        )
        # Surface the harness sub-agent's turn cap as an actionable hint so a
        # "no response after 25 turns" result doesn't look like a dead end.
        if "turn limit" in result.lower() or "no response after" in result.lower():
            self._append(
                "<i style='color:#fbbf24'>The coding agent hit its turn limit "
                "before finishing. Re-run with a narrower task, or type "
                "“continue” to let it keep going.</i>"
            )
        self.agent_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status.setText("coding agent idle")
        self._harness_worker = None
        self.refresh_harness_status()

    def _harness_failed(self, message: str) -> None:
        self._append(f"<i>Coding agent error: {html.escape(message)}</i>")
        self.agent_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status.setText("coding agent failed")
        self._harness_worker = None
        self.refresh_harness_status()


class _HarnessWorker(QThread):
    """Runs a harness task (exe backend) with live progress + cooperative stop.

    The old implementation emitted None on a str signal, dropped the result,
    and stopped via terminate() — this one cancels the asyncio task cleanly.
    """

    progress = Signal(str)  # one-liner for the status bar
    note = Signal(str)  # chat-worthy progress note
    finished = Signal(str)  # final rendered result
    failed = Signal(str)

    def __init__(self, bridge, task: str, parent=None, supervisor=None):
        super().__init__(parent)
        self._bridge = bridge
        self._task_text = task
        self._supervisor = supervisor
        self._loop = None
        self._task = None

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._task = self._loop.create_task(self._flow())
        try:
            result = self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            self.finished.emit("Stopped by user.")
            return
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        finally:
            self._loop.close()
        self.finished.emit(result or "(harness returned an empty result)")

    def stop(self):
        if self._loop is not None and self._task is not None and not self._task.done():
            self._loop.call_soon_threadsafe(self._task.cancel)

    async def _flow(self) -> str:
        bridge = self._bridge
        self.progress.emit("harness: connecting…")
        ok = False
        try:
            ok = await bridge.connect(timeout=4.0)
        except Exception:
            ok = False
        if not ok:
            self.progress.emit("harness: starting luckyd-code.exe backend…")
            try:
                ok = await bridge.start(timeout=25.0)
            except FileNotFoundError as exc:
                return f"Harness backend not found: {exc}"
            except Exception:
                ok = False
        if not ok:
            return (
                f"Harness server unreachable at {bridge.base} — start it with "
                "`luckyd-code.exe --web` or `python start_platform.py`, then retry."
            )
        tools = []
        with contextlib.suppress(Exception):
            tools = await bridge.list_tools()
        # Keep the app-wide supervisor cache truthful for the status line
        # and the dashboard (this worker used the shared bridge directly).
        if self._supervisor is not None:
            self._supervisor.last["up"] = True
            self._supervisor.last["error"] = None
            self._supervisor.last["tools"] = len(tools) or None
        self.note.emit(f"Harness connected — {len(tools) or '98'} tools available.")
        self.progress.emit("harness: task queued…")
        try:
            task_id = await bridge.start_background(self._task_text)
        except Exception:
            task_id = None
        if task_id:
            return await self._poll(task_id)
        # Fallback: blocking orchestrate pipeline
        self.note.emit("Background tasks unavailable — using orchestrate pipeline…")
        data = await bridge.orchestrate(self._task_text)
        return _render_harness_result(data)

    async def _poll(self, task_id: str) -> str:
        bridge = self._bridge
        polls = 0
        missed = 0  # consecutive status lookups that came back empty/404

        async def _fetch_result() -> str | None:
            """Pull the finished payload straight from /result — used both when
            status says done AND as a last resort when status lookups go quiet."""
            try:
                data = await bridge.background_result(task_id)
            except Exception:
                return None
            if isinstance(data, dict):
                text = str(data.get("result") or data.get("output") or "").strip()
                return text or None
            text = str(data).strip()
            return text or None

        while True:
            await asyncio.sleep(2.0)
            polls += 1
            task: dict = {}
            try:
                info = await bridge.background_status(task_id)
                task = info.get("task", info) if isinstance(info, dict) else {}
            except Exception:
                # The direct /status route can transiently 404 (server busy,
                # mid-write as a task flips running→done, or a registry reload).
                # Recover from the registry list instead of giving up.
                task = {}
            if not isinstance(task, dict):
                task = {}
            if not task.get("status"):
                found = None
                with contextlib.suppress(Exception):
                    found = await bridge.find_background_task(task_id)
                if found:
                    task = found
                else:
                    missed += 1
                    # The registry doesn't have it either — before assuming the
                    # task vanished, ask the result endpoint directly. If the
                    # task already finished, that succeeds and we never warn.
                    direct = await _fetch_result()
                    if direct is not None:
                        return direct
                    # Stay patient: a healthy fast task can sit in this window
                    # for a while. Only declare it lost after ~60s of nothing.
                    if missed >= 30:
                        return (
                            f"Lost track of harness task {task_id} — the backend "
                            "isn't reporting it in status, registry, or result. "
                            "It may still have finished; please retry."
                        )
            else:
                missed = 0
            status = str(task.get("status", "")).lower()
            if status in ("done", "completed", "success"):
                result = await _fetch_result()
                if result is not None:
                    return result
                return str(task.get("result_preview") or "(finished — no result payload)")
            if status in ("failed", "error"):
                return f"Harness task failed: {task.get('error') or 'unknown error'}"
            preview = str(task.get("result_preview") or "").strip()
            elapsed = polls * 2
            self.progress.emit(
                f"harness: {status or 'running'} {elapsed}s"
                + (f" — {preview[:100]}" if preview else "")
            )
            if polls >= 150:  # 5 minutes
                return (
                    f"Timed out waiting for harness task {task_id} (5 min) — "
                    "it may still finish in the background."
                )


def _render_harness_result(data) -> str:
    """Readable text from an orchestrate/parallel response payload."""
    if isinstance(data, dict):
        for key in ("result", "output", "answer", "summary", "final"):
            if data.get(key):
                return str(data[key])
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)[:6000]
    return str(data)
