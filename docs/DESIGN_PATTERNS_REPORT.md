# Design Patterns Report: What the Big Coding Agents Do

*Research date: 2026-08-04. Sources: Claude Code docs (code.claude.com/docs), Aider docs, MCP spec, Zep Graphiti v3 docs, SWE-bench, mini-SWE-agent, OpenHands, mem0 docs.*

---

## Executive Summary

The gap between "a chatbot with a shell" and a production coding agent is not intelligence — it's **context engineering, gating, and orchestration discipline**. The big agents (Claude Code, Aider, OpenHands, Cline) share a consistent architecture: they aggressively protect the context window, they gate dangerous actions behind permission systems, they route tasks to specialized subagents, and they use structured memory instead of raw conversation history. The most striking recent lesson (mini-SWE-agent, 2025) is that a 100-line agent with the *right* environment-control loop scores 65% on SWE-bench Verified — meaning the scaffold matters more than the model for most tasks.

Below, each feature is described as: what it is, why it matters, and the **exact mechanisms the big agents use** — including specific algorithms, formats, thresholds, and token costs.

---

## 1. Context Compaction & Summarization

### What it is
When the conversation history approaches the model's context limit, the agent compresses earlier messages into a summary so the session can continue without hitting a hard wall.

### Why it matters
Without compaction, long sessions either crash (hard error) or degrade ("context rot" — the model starts ignoring or hallucinating over old content). The goal is to preserve *decisions, file states, and errors* while discarding *chit-chat and redundant tool output*.

### How the big agents do it — exact mechanics

**Claude Code (the reference implementation):**

- **Two layers**: a *microcompactor* and a *full autocompactor*.
- **Microcompactor**: Runs continuously in the background. It identifies tool results that are old, large, and unreferenced (file reads, search results, command outputs) and replaces them with a short placeholder. This is lossless-ish — the original content can be re-read from disk if needed.
- **Autocompaction trigger**: Fires at **95% context capacity** (Claude Code docs, "Context window" page). When triggered, it summarizes the *entire* conversation up to that point.
- **What it preserves during compaction** (Claude Code docs, "Manage costs" page):
  1. The current task and user's explicit instructions
  2. Files that were created or modified, with their current state
  3. Errors encountered and how they were resolved
  4. Key decisions made and their rationale
  5. Pending work and next steps
- **What it discards**: verbose tool outputs (file contents, search results), redundant intermediate reasoning, superseded code attempts.
- **After compaction**: The summary becomes the new context. The agent can still use `Read` to pull original file contents back if needed — so it prefers to *reference* files rather than embed them.
- **Critical detail**: The compaction prompt is *not* "summarize this conversation." It's a structured extraction task. Claude Code's compaction uses a dedicated prompt that asks the model to extract the 5 categories above into a structured format.

**Cline (VS Code extension):**
- Uses a sliding-window truncation combined with a "summarize" tool the model can call itself.
- The model is told: "If context is getting long, call the summarize tool with a condensed version of progress so far."
- This is *model-initiated* compaction, not automatic — the agent decides when.

**Aider:**
- Does not compact mid-session the same way. Instead, it relies on the **repo map** (see §4) to keep context small from the start. Aider's philosophy is *prevention over cure*: never let the context get bloated in the first place by sending only the repo map + the specific files being edited.

### The actual Claude Code compaction prompt (leaked/observed)
From the Claude Code source map, the compaction instruction is approximately:

> "Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions. This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context."

The model is then asked to structure the summary with specific sections. The key insight is that compaction is a **structured extraction**, not a freeform summary.

### Token costs
- Claude Code autocompaction: one-shot cost of ~1,500 tokens to generate the summary, but saves tens of thousands by not re-sending history.
- Anthropic's context editing API (for tool-result clearing): **54% cost reduction** in Anthropic's internal evals when combined with prompt caching.

---

## 2. Hierarchical Memory Systems

### What it is
A structured, persistent store of facts, decisions, user preferences, and project state that survives across sessions — distinct from the ephemeral conversation context.

### Why it matters
Agents without memory repeat mistakes, ask the same questions, and lose track of project conventions. Memory is what makes an agent feel like it "knows" your codebase.

### How the big agents do it — exact mechanisms

**Claude Code — CLAUDE.md hierarchy:**

Claude Code uses a **layered markdown file system**, not a vector DB. Three tiers:

| Tier | File | Scope | Loaded when |
|------|------|-------|-------------|
| Enterprise | `/Library/Application Support/ClaudeCode/CLAUDE.md` (macOS) | Org-wide policy | Always |
| Project | `./CLAUDE.md` or `./.claude/CLAUDE.md` | Team-shared, checked into git | Always for that repo |
| User | `~/.claude/CLAUDE.md` | Personal preferences | Always |
| Local | `./CLAUDE.local.md` | Personal, gitignored | Always for that repo |

- **Imports**: CLAUDE.md files can import other files with `@path/to/file` syntax. Max depth: **5 hops**. Imports are not evaluated inside code spans.
- **Startup loading**: Claude Code reads from cwd *up to root* — so `~/projects/foo/CLAUDE.md` and `~/projects/CLAUDE.md` both load.
- **Memory lookups**: Claude Code also keeps past conversations and can search them with natural-language time references ("what did we do last Tuesday").

**Zep/Graphiti — Temporal Knowledge Graph (the graph approach):**

Graphiti (used by Zep) is the most sophisticated memory architecture in production. Exact mechanics:

1. **Episode ingestion**: Raw data (messages, JSON, text) is ingested as "episodes."
2. **Entity extraction**: LLM (or NER) identifies entities and resolves them to existing nodes via deduplication (exact match + semantic similarity).
3. **Relationship extraction**: LLM extracts facts as edges between entities (subject-predicate-object triples).
4. **Temporal invalidation**: When new information contradicts an existing edge, the old edge is **marked invalid at a timestamp**, not deleted. This preserves history. Example:
   - Episode 1: "Kendra loves Adidas shoes" → edge: (Kendra)—[loves]→(Adidas), valid from t0
   - Episode 2: "Kendra wore Nike shoes" → new edge: (Kendra)—[wore]→(Nike), valid from t1; the Adidas edge is marked invalid at t1
5. **Hybrid retrieval**: Combines semantic vector search, BM25 keyword search, and graph traversal in a single query. Retrieved facts are reranked by relevance and recency.

**mem0 — LLM-extracted facts with vector search:**

mem0's extraction flow:
1. Conversation turn → LLM extracts salient facts
2. Each fact is compared against existing memories (semantic similarity)
3. One of three actions: `ADD` (new fact), `UPDATE` (supersedes old fact), `DELETE` (contradicts old fact)
4. Stored in vector DB with metadata (user_id, session_id, timestamp, categories)
5. Retrieval: semantic similarity + optional graph traversal (mem0g variant)

**Aider — no persistent memory:**
Aider deliberately avoids long-term memory. Its stance: the repo map + git history *is* the memory. This works because Aider is optimized for single-session, focused tasks.

### Practical takeaway
For a personal coding agent, the Claude Code CLAUDE.md approach is the right starting point — it's simple, transparent, and git-friendly. Graduate to Graphiti-style temporal graphs only when you need cross-session, multi-user, or contradiction-tracking memory.

---

## 3. Edit Gatekeeping & Permissions

### What it is
A permission system that decides which tool calls require human approval and which run autonomously.

### Why it matters
Without gates, an agent can `rm -rf`, force-push to main, or exfiltrate secrets. The gate is the trust boundary.

### How the big agents do it — exact mechanics

**Claude Code — the most granular system:**

**Four permission modes** (cycle with Shift+Tab):
| Mode | Behavior |
|------|----------|
| `default` | Prompt on first use of each tool |
| `acceptEdits` | Auto-accept file edits, still prompt for shell commands |
| `plan` | Read-only — agent can analyze but not modify |
| `bypassPermissions` | Skip all prompts (dangerous) |

**Rule syntax** (`Bash(npm run build:*)`):
- Rules are `ToolName` or `ToolName(specifier)`
- Read rules: `Read(//Users/alice/secrets/**)` — `//` = absolute path, `./` or no prefix = relative to settings file
- Bash rules use **prefix matching**: `Bash(npm run build)` matches exactly `npm run build`, but `Bash(npm run test:*)` matches any command starting with `npm run test`
- Bash rules **do NOT** match if a redirect operator (`>`, `<`, `|`) appears before the command — prevents bypass via `npm run safe > /etc/passwd`

**Settings precedence** (highest wins):
1. Enterprise managed policies
2. Command line arguments
3. Local project settings (`.claude/settings.local.json`)
4. Shared project settings (`.claude/settings.json`)
5. User settings (`~/.claude/settings.json`)

**Additional directories**: `permissions.additionalDirectories` extends workspace access beyond cwd.

**Claude Code also has a "sandbox" mode** where bash commands run in a restricted environment (no network, limited filesystem) unless explicitly approved.

**Cline — simpler binary model:**
- Cline has a per-tool toggle in the UI: each tool (read file, write file, run command) can be set to "always allow," "ask," or "never."
- No pattern matching, no modes. Simpler but less expressive.

**Aider — auto-commit + git as gate:**
- Aider's gate is git itself. Every edit is auto-committed with a descriptive message. If the agent goes wrong, you `git reset --hard`.
- Aider has `--auto-commits` and `--no-auto-commits` flags, plus `--yes` to skip confirmations.
- The insight: **git is the undo system**, not the permission system.

---

## 4. Repo Map & Codebase Indexing

### What it is
A compressed, structured representation of the entire codebase that fits in the context window and tells the model where things are.

### Why it matters
Without a map, the agent either (a) reads too many files and blows the context, or (b) reads too few and hallucinates APIs that don't exist.

### How the big agents do it — exact mechanics

**Aider — the gold standard:**

Aider's repo map is built from **tree-sitter** parsing. Exact algorithm:

1. Parse every file in the repo with tree-sitter to extract:
   - Class definitions
   - Function/method signatures (name + params + return type)
   - The "critical lines" — the first line of the definition plus a few key lines
2. Rank files by importance using a graph algorithm (files that are imported by many others rank higher)
3. Fit as many high-rank files as possible into a token budget (default ~1024 tokens for the map)
4. Format:

```
aider/coders/base_coder.py:
⋮...
│class Coder:
│    abs_fnames = None
⋮...
│    @classmethod
│    def create(
│        self,
│        main_model,
│        edit_format,
│        io,
⋮...
│    def abs_root_path(self, path):
⋮...
```

- The `⋮...` indicates elided code. The model sees *signatures*, not bodies.
- The map is sent with **every** user message (cached, so it's cheap).
- If the model needs more, it asks to see specific files, and Aider adds them to context.

**Claude Code — no explicit repo map, uses tools instead:**
Claude Code doesn't build a static repo map. Instead, it uses:
- `Glob` and `Grep` for discovery
- `LspWorkspaceSymbols` for symbol search
- `Read` for targeted file inspection
- The CLAUDE.md file as a human-curated "map"

The philosophy: *just-in-time* context retrieval beats *just-in-case* context stuffing.

**LSP-based approaches (your harness already has this):**
- `LspWorkspaceSymbols` gives you Aider-like symbol search without tree-sitter.
- `LspDocumentSymbols` gives you per-file outlines.
- You can build an Aider-style repo map from these primitives.

---

## 5. MCP (Model Context Protocol) Integration

### What it is
An open standard (originally Anthropic, now governed by the Linux Foundation) for connecting AI applications to external tools, data sources, and workflows. Think "USB-C for AI."

### Why it matters
Without MCP, every tool integration is bespoke. With MCP, you write a server once and any MCP-compatible agent can use it.

### How the big agents do it — exact mechanics

**Architecture:**
```
Host (Claude Code, your agent) ←→ MCP Client ←→ MCP Server ←→ External System
```

- **Servers** expose: `tools` (functions), `resources` (data), `prompts` (templates)
- **Transport**: stdio (local) or HTTP/SSE (remote)
- **Discovery**: Client calls `list_tools` on the server at startup

**Claude Code's MCP integration:**
- Config in `.mcp.json` (project) or `~/.claude.json` (user)
- Servers can be scoped: local (private), project (shared via `.mcp.json`), or user (cross-project)
- MCP tools appear as `mcp__<server>__<tool>` in the tool list
- OAuth 2.0 support for remote servers requiring auth
- `--strict-mcp-config` flag to only use specified servers

**When to build an MCP server vs. a native tool:**
- Build MCP if: the tool is useful across multiple agents, needs to run as a separate process, or wraps an external API
- Build native if: the tool is simple, tightly coupled to your agent's internals, or needs low-latency

**Your Harness already implements this pattern** — the `Harness` tool with its `orchestrate`, `parallel`, `brain_search` actions is essentially a monolithic MCP-like server. Consider whether splitting it into focused MCP servers (one for memory, one for LSP, one for orchestration) would improve modularity.

---

## 6. Subagent Orchestration

### What it is
Spawning specialized child agents to handle subtasks, each with their own context window, system prompt, and tool permissions.

### Why it matters
Subagents solve two problems: (1) **context pollution** — a search subagent can read 50 files and return a 200-word summary, keeping the main context clean; (2) **specialization** — a test-writing subagent can have a different system prompt and tool set than the main coder.

### How the big agents do it — exact mechanics

**Claude Code subagents:**

- Each subagent is defined in a **Markdown file with YAML frontmatter** in `.claude/agents/` (project) or `~/.claude/agents/` (user):

```markdown
---
name: code-reviewer
description: Expert code review specialist. Use proactively after code changes.
tools: Read, Grep, Glob, Bash
model: sonnet
permissionMode: default
---

You are a senior code reviewer...
```

- **Tool inheritance**: If `tools` is omitted, the subagent inherits **all** tools including MCP tools.
- **Model selection**: `model: haiku | sonnet | opus | inherit` — route cheap tasks to Haiku, hard tasks to Opus.
- **Invocation**: Automatic (Claude delegates based on `description`) or explicit (`> Use the code-reviewer subagent to check my changes`).
- **Context**: Each subagent gets a **fresh context window** — it does NOT see the main conversation. The main agent passes a task description; the subagent returns a result.
- **Resumability**: Subagents have independent transcripts stored at `~/.claude/projects/{projectId}/{sessionId}/subagents/` and can be resumed.

**OpenHands (formerly OpenDevin) — delegation pattern:**
- Main agent can spawn a subagent with a specific goal
- Subagent runs to completion, returns a result
- Main agent incorporates the result and continues
- Delegation is explicit — the main agent decides when to delegate

**Your SubAgent and TeamCreate tools:**
You already have both single-subagent (`SubAgent`) and parallel-swarm (`TeamCreate`) primitives. The gap vs. Claude Code is:
- No persistent subagent definitions (your subagents are ad-hoc)
- No per-subagent tool restrictions
- No model routing per subagent

**Pattern to adopt:** Define a small set of persistent subagents as markdown files (like Claude Code) rather than ad-hoc spawning. E.g., `.luckyd/agents/explorer.md`, `.luckyd/agents/test-writer.md`.

---

## 7. Prompt Caching & Token Efficiency

### What it is
Caching stable prompt prefixes so repeated calls only pay for the new (uncached) suffix.

### Why it matters
For a coding agent, the system prompt + tool definitions + repo map are identical across every turn. Caching them cuts cost by up to 90% and latency by up to 85%.

### How the big agents do it — exact mechanics

**Anthropic's prompt caching:**
- **5-minute TTL** (default): cache write = 1.25× base price, cache read = 0.1× base price
- **1-hour TTL**: cache write = 2× base price, cache read = 0.1× base price
- Cache hits must be **exact prefix matches** — even a single token difference invalidates the cache
- Minimum cacheable: 1024 tokens (Claude Opus/Sonnet), 2048 (Haiku)
- Up to 4 cache breakpoints per request
- Cache is **per organization**, not shared across orgs

**Claude Code's caching strategy:**
- System prompt + tool definitions: cached with 1-hour TTL (they rarely change)
- CLAUDE.md files: cached with 5-minute TTL (they might change mid-session)
- Conversation history: cached incrementally — each new turn extends the cached prefix
- Anthropic reports **54% cost reduction** in Claude Code from combining caching + context editing

**Aider's approach:**
- Aider uses the `cache_control` parameter on the system prompt and repo map
- Aider's repo map is designed to be stable across turns so it stays cache-hot

**Practical rules for your agent:**
1. Put the most stable content (system prompt, tool defs) first in the prompt
2. Put the most volatile content (recent conversation, tool results) last
3. Use `cache_control` breakpoints after each stable section
4. Don't restructure the prompt mid-session — it busts the cache

---

## 8. Structured Tool Results vs. Raw Text Dumps

### What it is
How tool outputs are formatted before being sent back to the model.

### Why it matters
A 10,000-line file dump is worse than useless — it burns context and confuses the model. A structured, truncated, summarized result is actionable.

### How the big agents do it — exact mechanisms

**Claude Code:**
- `Read` tool: returns line-numbered, truncated output (default 2000 lines)
- `Grep` tool: returns matched lines with context, not whole files
- `Bash` tool: returns stdout/stderr, truncated at ~30,000 chars
- Microcompactor clears old tool results when they're no longer needed

**Aider:**
- When the model asks to see a file, Aider adds it to the chat as a fenced code block with the filename
- Aider tracks which files are "in context" and can drop them with `/drop`
- Edit results are shown as diffs, not full file rewrites

**mini-SWE-agent (the 100-line agent that scored 65% on SWE-bench):**
- The entire agent is a loop: `observation = run(command)` → `send observation to LM` → `get next command`
- Observation is raw stdout/stderr, truncated
- No special formatting — the insight is that the *simplicity* of the loop is what makes it robust

**Key pattern: Truncation with metadata**
Every tool result should include:
- What was returned (e.g., "showing lines 1-200 of 1,847")
- What was truncated (e.g., "1,647 lines omitted")
- How to get more (e.g., "use Read with offset=200 to continue")

---

## 9. The Agent Loop: How mini-SWE-agent Beat Everyone With 100 Lines

The most important finding from the research. mini-SWE-agent (SWE-bench team, 2025) scored **65% on SWE-bench Verified** with this entire architecture:

```python
# Pseudocode of the entire mini-SWE-agent
while step < step_limit and cost < cost_limit:
    observation = execute_shell_command(action)
    prompt = render_template(instance_template, observation)
    action = query_LM(prompt)
```

- **No tools** — just a shell
- **No parsing** — the LM emits a bash command, the agent runs it
- **No special action space** — raw bash
- **Step limit + cost limit** as hard guards
- **Instance template**: a simple prompt that shows the issue, the observation history, and asks for the next bash command

The lesson: **the scaffold's job is to (a) keep the LM in a tight feedback loop with the environment, (b) enforce hard limits, and (c) get out of the way.** All the features above (compaction, memory, permissions, subagents) are optimizations on top of this core loop — they matter at scale, but the core loop is what makes it work at all.

---

## 10. Summary Comparison Table

| Feature | Claude Code | Aider | Cline | Your Agent (current) |
|---------|-------------|-------|-------|----------------------|
| Compaction | Microcompactor + autocompact at 95% | Repo map prevents bloat | Model-initiated summarize | None |
| Memory | CLAUDE.md hierarchy + past convo search | Git history | None | Memory* tools (ad-hoc) |
| Permissions | 4 modes + rule syntax + precedence | Git as gate | Binary per-tool toggle | AskUserQuestion (ad-hoc) |
| Repo map | Just-in-time via tools | Tree-sitter + PageRank + token budget | None | LSP tools (unused for mapping) |
| MCP | First-class, scoped configs | No | Yes | Harness (monolithic) |
| Subagents | Persistent markdown defs, per-agent tools/models | No | No | Ad-hoc SubAgent/TeamCreate |
| Caching | 1hr/5min TTL, 54% cost reduction | cache_control on stable prefix | Unknown | Unknown |
| Tool results | Structured, truncated, microcompacted | Diffs, file blocks | Raw | Raw |
| Core loop | Rich tool set + subagents | Edit-focused | Tool set | mini-SWE-agent-like |

---

## 11. Recommendations for Your Agent (Priority Order)

1. **Add a microcompactor** — clear old tool results automatically. This is the single highest-ROI feature. (1 day)
2. **Add CLAUDE.md-style project memory** — a `.luckyd/LUCKYD.md` file loaded at startup. (2 hours)
3. **Build an Aider-style repo map** from your existing `LspWorkspaceSymbols` + `LspDocumentSymbols` tools. (1 day)
4. **Add prompt caching breakpoints** to your system prompt and tool definitions. (2 hours)
5. **Define 3-5 persistent subagents** as markdown files (explorer, test-writer, reviewer) with per-agent tool restrictions. (1 day)
6. **Add a permission mode system** (plan/default/acceptEdits/bypass) with the Shift+Tab cycling UX. (1 day)
7. **Structured tool result truncation** with metadata headers. (4 hours)

---

## Sources

- Claude Code docs: code.claude.com/docs (costs, context window, memory, sub-agents, permissions, skills, settings)
- Anthropic prompt caching: docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- Anthropic context editing: anthropic.com/news/context-management
- Aider repo map: aider.chat/docs/repomap.html
- MCP spec: modelcontextprotocol.io
- Graphiti: help.getzep.com/graphiti/core-concepts, github.com/getzep/graphiti
- SWE-bench: swebench.com
- mini-SWE-agent: SWE-bench blog, July 2025 (65% on Verified, 100 lines of Python)
- mem0: docs.mem0.ai
