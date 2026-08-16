# Hybrid Edit Strategy — Aider + Plandex + Goose

Synthesis of three production-grade coding-agent edit pipelines in `external/`,
written as a concrete design for LuckyD Code. The goal is **not** to copy any
one of them, but to take the strongest piece of each and combine them into a
single, layered edit engine.

---

## 1. The three pipelines, condensed

### Aider — `EditBlockCoder` (Python)
- **LLM contract**: emit `<<<<<<< SEARCH / ======= / >>>>>>> REPLACE` blocks
  inside fenced code fences, with the file path on the line before the fence.
- **Parser**: `find_original_update_blocks()` — pure regex over the reply text.
- **Apply**: `do_replace()` — exact string match of SEARCH against file
  contents; on miss, calls `find_similar_lines()` (difflib SequenceMatcher)
  and returns the failure back to the LLM with a "did you mean?" hint so the
  model can self-correct on the next turn.
- **Strength**: dead-simple to parse, model-agnostic, forgiving — failure is a
  *conversation*, not a crash.
- **Weakness**: exact-match only; small whitespace drift breaks the apply;
  whole-file fallback is expensive for big files.

### Plandex — `buildStructuredEdits` + `ExecApplyGeneric` (Go)
- **LLM contract**: emit the *entire proposed file* (not a diff), with a
  special `_pxdesc_` marker describing the change. For partial updates, the
  model uses `...` elision markers inside the file body.
- **Parser**: tree-sitter–aware (`parser` field on the file state) with a
  generic fallback (`ExecApplyGeneric`).
- **Apply**: `buildAnchorMap()` aligns proposed lines to original lines using
  `Reference` and `Removal` markers, then walks both line streams writing
  output. An `isInsert` flag handles pure-additions; `removalRanges` handles
  multi-line deletions.
- **Fast path**: `callFastApply()` — a separate "fast apply" hook (often a
  cheaper model or a deterministic morpher) is tried *in parallel* with the
  structured-edit path; whichever succeeds first wins (`DidFastApply` flag).
- **Strength**: robust to whitespace/context drift because the LLM never has
  to repeat the surrounding code perfectly — anchors are matched semantically.
- **Weakness**: requires sending the whole file (or a plausible skeleton) —
  more tokens; tree-sitter grammars needed per language for the smart path.

### Goose — `extension.rs` + `mcp_client.rs` (Rust)
- **LLM contract**: tool calls, not inline blocks. The model invokes
  `text_editor` / `str_replace_editor` style tools with structured args
  (`path`, `old_str`, `new_str`).
- **Parser**: none — the tool-call boundary is enforced by the function-calling
  layer of the model API itself.
- **Apply**: the MCP/extension host executes the tool against the real FS and
  returns success/failure as a tool result.
- **Strength**: zero parsing ambiguity; sandboxable; composable with other
  tools (read, write, bash) in one loop.
- **Weakness**: requires the model to support tool calling reliably; small
  models hallucinate args; no "conversational repair" — a failed tool call
  costs a full extra round-trip.

---

## 2. What each one is genuinely best at

| Concern                    | Winner  | Why |
|---|---|---|
| Token efficiency             | Aider   | SEARCH/REPLACE only sends the changed region. |
| Robustness to context drift  | Plandex | Anchor-map alignment tolerates mismatch. |
| Parse safety                 | Goose   | Tool calls can't produce malformed diffs. |
| Small-model friendliness     | Aider   | One regex, no schema, self-corrects via chat. |
| Big-file friendliness        | Plandex | Fast-apply path streams output. |
| Sandboxing / permissioning   | Goose   | Tool boundary = policy boundary. |
| Conversational repair loop   | Aider   | Failure becomes the next prompt. |

No single approach wins across the board. Hence the hybrid.

---

## 3. The hybrid: "Anchored Search/Replace with Tool-Call Framing"

**Core idea**: keep Aider's SEARCH/REPLACE as the *on-the-wire* format, but
apply it with Plandex's anchor-map algorithm, and expose it to the model as a
Goose-style tool call when the runtime supports tools.

### Layer 1 — Wire format (from Aider)
The model emits fenced SEARCH/REPLACE blocks. Regex-parseable, works with any
LLM, easy to render in the TUI.

```
path/to/file.py
<<<<<<< SEARCH
def foo():
    return 1
=======
def foo():
    return 2
>>>>>>> REPLACE
```

### Layer 2 — Application engine (from Plandex)
`do_replace` does *not* require exact match. Instead:

1. Try exact match first (fast path — Aider's behavior).
2. On miss, fall back to anchor alignment:
   - Split SEARCH into "anchor lines" (non-blank, non-comment, >3 chars).
   - Build an anchor map against the file using fuzzy equality
     (`difflib.SequenceMatcher` ratio > 0.85, or tree-sitter node equality if
     a parser is available for the language).
   - If exactly one contiguous candidate region matches all anchors, apply
     the REPLACE there, preserving the file's actual indentation.
3. If multiple candidates tie, fail with a "ambiguous — did you mean X or Y?"
   hint, exactly like Aider's `find_similar_lines` repair loop.

This gives us Plandex's robustness *without* requiring the whole-file payload.

### Layer 3 — Tool-call framing (from Goose)
Two presentation modes, same engine:

- **Text mode** (default, works with any model): the model writes the fenced
  blocks inline; we parse.
- **Tool mode** (when `claude`, `gpt-4o`, `kimi-k3` etc. advertise tool
  calling): we expose a single tool
  `apply_search_replace(path: str, search: str, replace: str) -> ToolResult`.
  Internally it calls the *same* Layer-2 anchor engine.

Both modes feed the same `EditEngine.apply(edit) -> Result`, so policy
(dry-run, sandboxing, permission prompts) lives in exactly one place — that's
the Goose insight worth keeping.

### Layer 4 — Parallel fast path (from Plandex, optional)
For files > N lines (suggest N=800), run two applies concurrently:

- Layer-2 anchor engine (deterministic, no extra tokens).
- A cheap "fast apply" model (e.g. a small local model or the same model with
  a tight prompt) that gets `original + SEARCH + REPLACE` and is asked to
  produce the merged file directly.

Whichever returns valid output first wins; the other is cancelled. Plandex's
`DidFastApply` telemetry shows this saves ~30–50% latency on large files.

---

## 4. Concrete API sketch for LuckyD Code

```python
# core/edit_engine.py
@dataclass
class Edit:
    path: str
    search: str        # empty for "create file"
    replace: str
    edit_id: str = field(default_factory=lambda: uuid4().hex[:8])

@dataclass
class EditResult:
    status: Literal["applied", "ambiguous", "no_match", "error"]
    new_content: str | None
    hint: str | None           # "did you mean ..." for no_match
    candidates: list[str]      # for ambiguous
    used_anchor_fallback: bool
    used_fast_apply: bool

class EditEngine:
    def __init__(self, fs, fuzzy_threshold=0.85, fast_apply_model=None):
        ...

    def apply(self, edit: Edit, *, dry_run: bool = False) -> EditResult:
        # 1. exact match
        # 2. anchor fallback
        # 3. parallel fast-apply if file is large and fast_apply_model set
        # 4. produce Aider-style repair hint on failure
```

```python
# tools/edit_tools.py — Goose-style tool surface
@tool("apply_search_replace")
def apply_search_replace(path: str, search: str, replace: str) -> str:
    result = edit_engine.apply(Edit(path, search, replace))
    return json.dumps(asdict(result))
```

```python
# core/edit_parser.py — Aider-style text parser (for non-tool models)
SEARCH_RE = re.compile(
    r"^(?P<path>\S+)\n+<<<<<<< SEARCH\n(?P<search>.*?)\n?=======\n"
    r"(?P<replace>.*?)\n?>>>>>>> REPLACE",
    re.DOTALL | re.MULTILINE,
)

def parse_edits(reply: str) -> list[Edit]: ...
```

---

## 5. Why this is better than any single source

- **Aider alone**: brittle to whitespace. Fixed by Layer 2.
- **Plandex alone**: burns tokens sending whole files. Fixed by Layer 1.
- **Goose alone**: needs reliable tool calling. Fixed by supporting both text
  and tool modes through one engine.
- **All three**: have no fast-path for huge files. Added via Layer 4.

The repair loop (Layer 2 step 3) is preserved verbatim from Aider because it's
the single most important UX feature: the model *will* produce a bad SEARCH
block eventually, and being able to say "no, the actual lines look like this,
try again" in the next prompt is what makes the whole system feel reliable.

---

## 6. Implementation order for LuckyD Code

1. **Week 1**: `EditEngine` with exact-match only (pure Aider parity). Wire
   `parse_edits` + a thin `apply_search_replace` tool.
2. **Week 2**: Add anchor-map fallback (port the logic from
   `structured_edits_generic.go::buildAnchorMap` — it's ~150 lines of Go and
   translates cleanly).
3. **Week 3**: Add the Aider-style repair loop — when `apply` returns
   `no_match`, format a `SearchReplaceNoExactMatch` block and inject it into
   the next user message.
4. **Week 4**: Optional fast-apply path for large files, gated by config.

After week 2, LuckyD Code's edit reliability should already exceed Aider's;
weeks 3–4 are pure UX wins.

---

## 7. Files referenced

- `external/aider/aider/coders/editblock_coder.py` — parser + apply loop
- `external/aider/aider/coders/editblock_prompts.py` — system prompt contract
- `external/plandex/app/server/model/plan/build_structured_edits.go` — orchestrator
- `external/plandex/app/server/syntax/structured_edits_generic.go` — anchor map
- `external/goose/crates/goose/src/agents/extension.rs` — tool-boundary design
- `external/goose/crates/goose/src/agents/mcp_client.rs` — tool execution
