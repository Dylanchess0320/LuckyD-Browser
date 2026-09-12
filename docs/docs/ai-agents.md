# AI & Agents

LuckyD's AI is local-first and unlimited: Ollama runs on your machine for free, no account, no key. Cloud providers are there when you want bigger models — your prompts only leave the machine when *you* choose one.

## AI Sidebar — `Ctrl+Shift+A`

The sidebar is the front door to everything:

- **Markdown chat** with a **model picker** — switch between local Ollama and any configured cloud provider
- **Visual Q&A** — ask about what you're looking at; vision-capable models get automatic per-step screenshots
- **Autonomous agent on your real visible tab** — the agent reads the page, clicks, types, and scrolls through the Control API
- **Contextual skill chips** — start typing and the sidebar suggests matching skills from the bundled set (`ai-news-brief`, `chess`, `graphify`, `movie-picker`, `top-picks`); one tap attaches that skill's know-how to the chat
- **Omnibox shortcut** — type `? your question` in the address bar to send it straight to the sidebar

## Harness mode (default on)

Sidebar tasks run on the coding-agent backend — 70+ tools, memory graph, orchestration — which can drive the tabs you're looking at. It's "exe brain, browser hands": the agent reasons with the full toolset and acts on the live page via the local Control API (`127.0.0.1:9777`).

## Providers

**Free & local (no key):** Ollama — install the bundled `llama3.2:3b` at setup and chat fully offline.

**Free online fallback (no key):** with no local model or API key configured, the provider order falls through to the **OpenCode Zen** $0 gateway — it shows up in the model picker with its own honest label, so the sidebar still works out of the box.

**Cloud (your keys, Settings):** Gemini · Groq · DeepSeek · OpenAI · Anthropic · Z.ai · OpenRouter · Cline · OpenCode

The sidebar also picks up **ClinePass** — sign in with Cline and the token resolves from your live Cline CLI session, refreshing automatically.

## Deep Research — `Ctrl+Shift+R`

A real research swarm, not a single prompt:

1. **Planner** breaks your question into sub-questions
2. **Parallel grounded workers** search and read sources (Gemini native grounding, LuckyD provider stack, keyless DDG, Tavily/Brave premium search)
3. **Synthesizer** merges findings into a citation-backed draft
4. **Critic** attacks the draft; **claim-level verifier** checks each claim
5. **Finalizer** produces the report

Depth presets (`quick` → `max`), per-run budgets, SQLite result cache, and artifacts saved per run. The browser can inject the **current tab as context**.

!!! note "Hardened in 4.0"
    Research reports render markdown from untrusted sources. The renderer now escapes HTML before formatting, and links are allowlisted to `http:`, `https:`, and `mailto:` — `javascript:` and `data:` URLs are neutralized, with `rel="noopener"` on every link.

## Coding Agent HQ — `Ctrl+Shift+H`

The full `luckyd-code` workspace lives in a tab: the agent loop, 70+ tools (file ops, bash, git, web, sub-agents), session persistence, checkpoints with undo, and MCP server support. It's the same engine behind harness mode, surfaced as a workspace you can watch.

## Agent Mesh — `Ctrl+Alt+M`

Four terminal panes in one dock. Run agents side by side — the mesh ships chips for the agent CLI, PowerShell, and popular agent CLIs (availability probed via PATH). A `doctor` command validates each backend before launch.

## Cline bridge — `127.0.0.1:8317`

A ClinePass-compatible proxy (`/v1/models`, `/v1/chat/completions`) so Cline-style clients can use your configured providers. `/v1/health` stays public for diagnostics.

!!! warning "Authentication required (4.0)"
    The bridge now requires a bearer token — set `CLINE_BRIDGE_TOKEN` (or reuse `CODING_AGENT_API_KEY`). It **fails closed**: with no token configured, the endpoints refuse to serve.

Next: [Terminal →](terminal.md)
