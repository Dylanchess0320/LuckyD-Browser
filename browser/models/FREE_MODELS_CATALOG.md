# LuckyD Browser — Free Models Catalog

Synced with the wired provider catalogs on **October 22, 2026** (10.8.3 audit).
`free_models` below means **$0 per token** on that provider — local models,
free-tier quotas, or `:free` catalog ids. Anything that bills per token or
needs a paid subscription is listed as paid, never as free.

## Always free, no key (local)

- Ollama: `luckyd:4b`, `luckyd:8b`, `luckyd:latest` — Dylan's own local
  models, i.e. exactly what `ollama list` reports on his PC (verified
  2026-10-22). The old generic list (llama3.2:3b, gemma3:4b, phi3:mini,
  mistral:7b, codellama:7b) named models that are not installed there, so
  every entry was a dead pick.

## Free with a login or free-tier key

- **Cline Usage** (logged-in Cline CLI session, rate-limited $0 models):
  `minimax/minimax-m2.5`, `deepseek/deepseek-r1`,
  `google/gemini-2.5-flash`, `z-ai/glm-5`, `z-ai/glm-5.3-flash`,
  `poolside/laguna-s-2.1:free` — every entry live-probed on the gateway
  2026-10-22 and streamed real output.
- **Google Gemini** (free-tier key): `gemini-2.5-flash`,
  `gemini-2.5-flash-lite`, `gemini-3.5-flash-lite`, `gemini-3.5-flash`,
  `gemini-3.8-flash`, `gemma-4-26b-a4b-it`, `gemma-4-31b-it`
- **OpenRouter** (`:free` ids + the meta-router): `openrouter/free`,
  `nvidia/nemotron-3-ultra-550b-a55b:free`, `z-ai/glm-5.2:free`,
  `thinkingmachines/inkling:free`, `thinkingmachines/inkling-small:free`,
  `google/gemma-4-31b-it:free`, `google/gemma-4-26b-a4b-it:free`,
  `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`,
  `nvidia/nemotron-3-super-120b-a12b:free`,
  `nvidia/nemotron-3.5-lightning:free`, `cohere/north-mini-code:free`,
  `liquid/lfm-2.5-2.6b:free`, `poolside/laguna-s-2.1:free`,
  `poolside/laguna-xs-2.1:free`
- **Groq** (free-tier key): `groq/compound-mini`, `groq/compound`, `allam-2-7b`
- **Z.ai** (free-tier key): `glm-5.3-flash`, `glm-4.7-flash`, `glm-4.5-flash`

## Paid — kept as opt-in only (your own key, never the auto default)

- OpenAI `gpt-6-sol`, Anthropic `claude-opus-5-5`, DeepSeek `deepseek-v4`
  (cheap pay-per-token), xAI `grok-4.7`, Moonshot `kimi-k3`,
  Meta `muse-wonder-1.0`, Mistral `mistral-large-4`, ClinePass
  (`cline-pass/*`, $9.99/mo flat subscription), OpenCode Zen (keyed gateway;
  its old keyless `-free` tier died in 2026-09 — every keyless call 401s, so
  no Zen model is listed as free anymore).

## Removed in 10.8.3 (live-probe audit, 2026-10-22)

- Cline Usage free ids `deepseek/deepseek-chat`,
  `meta-llama/llama-3.2-3b-instruct`, `qwen/qwen3-8b`,
  `kwaipilot/kat-coder-pro` — the gateway maps each to an upstream id that
  returns 404 "model not found" (`deepseek-v3`, `meta/llama-3.2-3b`,
  `kat-coder-pro-v1`; qwen3-8b was deprecated upstream 2026-10-09). They
  sat in the "free ✓" picker but could never answer. The free-rotation
  lead pair moved from deepseek-chat to `minimax/minimax-m2.5` for the
  same reason.

## Removed in 10.7.5 (dead or mislabeled ids)

- OpenCode Zen `-free` ids (`*-free` keyless catalog) — tier retired 2026-09.
- `deepseek-chat` / `deepseek-coder` / `deepseek-lite` as “free” DeepSeek
  models — DeepSeek bills per token; the retired chat default is gone.
- `google/gemini-2.0-flash` (Cline list) — superseded by `gemini-2.5-flash`.
- `deepseek/deepseek-chat-v3.1:free` (free rotation) — never existed; the
  rotation now uses the real `openrouter/free` meta-router.
- `stealth/ox-alpha`, `openrouter/auto` (OpenRouter list) — not guaranteed
  $0; `auto` can route to paid models.
- Credit-billed Cline ids in the free picker (`gpt-4o`, `claude-sonnet-4-6`,
  `gemini-2.5-pro`, …) — still reachable by exact-id override, no longer
  presented next to the $0 models.

## How LuckyD uses this

- Auto mode is free-first on every surface: Cline free tier → Gemini free
  tier → local Ollama → OpenRouter/Groq free catalogs. A paid provider is
  used only when you pick it explicitly *and* its key is present; a stale
  paid pick with no key falls back to the free rotation instead of failing.
- The AI sidebar pulls **live** model lists per provider; when a live fetch
  fails it falls back to the curated catalogs baked into
  `browser_core/ai_bridge.py` (`_OPENROUTER_FREE_FALLBACK`,
  `_CLINE_USAGE_CATALOG`).
- `openrouter/free` is a meta-router: it auto-picks whichever free model is
  available.

## Switching providers

```bash
python main.py model openrouter/free   # OpenRouter free meta-router
python main.py model nvidia/nemotron-3-ultra-550b-a55b:free  # OpenRouter free
provider openrouter
```

Model overrides persist in `browser/data/settings.json` under `ai_model_overrides`.
