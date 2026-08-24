# LuckyD Browser — Free Models Catalog

Synced from **opencode's open model registry** ([models.dev](https://models.dev)) on **August 24, 2026**.
`free_models` below means **$0 per token** in that registry — verified, not guessed.
Across all 88 providers in the registry there are 961 free models; the ones wired into LuckyD are listed here.

## OpenCode Zen (opencode's own gateway) — 29 free

- `big-pickle`
- `deepseek-v4-flash-free`
- `glm-4.7-free`
- `glm-5-free`
- `grok-code`
- `hy3-free`
- `hy3-preview-free`
- `kimi-k2.5-free`
- `laguna-s-2.1-free`
- `ling-2.6-flash-free`
- `ling-3.0-flash-free`
- `ling-3.0-tiny-free`
- `longcat-2.0-free`
- `mimo-v2-flash-free`
- `mimo-v2-omni-free`
- `mimo-v2-pro-free`
- `mimo-v2.5-free`
- `minimax-m2.1-free`
- `minimax-m2.5-free`
- `minimax-m3-free`
- `muse-spark-1.2-contributor-free`
- `nemotron-3-super-free`
- `nemotron-3-ultra-free`
- `nemotron-3.5-lightning-free`
- `north-mini-code-free`
- `qwen3.6-plus-free`
- `ring-2.6-1t-free`
- `trinity-large-preview-free`
- `x-preview-f-free`

## OpenRouter — :free tier

- `openrouter/free`
- `cohere/north-mini-code:free`
- `dots-studio/dots-3-note-preview:free`
- `google/gemma-4-26b-a4b-it:free`
- `google/gemma-4-31b-it:free`
- `liquid/lfm-2.5-2.6b:free`
- `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`
- `nvidia/nemotron-3-super-120b-a12b:free`
- `nvidia/nemotron-3-ultra-550b-a55b:free`
- `nvidia/nemotron-3.5-lightning:free`
- `openrouter/auto`
- `poolside/laguna-s-2.1:free`
- `poolside/laguna-xs-2.1:free`
- `stealth/ox-alpha`
- `thinkingmachines/inkling-small:free`
- `thinkingmachines/inkling:free`
- `z-ai/glm-5.2:free`

## Google — $0 vision/chat

- `gemma-4-26b-a4b-it`
- `gemma-4-31b-it`

## Groq — $0

- `allam-2-7b`
- `groq/compound`
- `groq/compound-mini`

## Z.ai (GLM flash) — $0

- `glm-4.5-flash`
- `glm-4.7-flash`

### Ollama (local — always free, no key)

- `llama3.2:3b`, `gemma3:4b`, `phi3:mini`, `mistral:7b`, `codellama:7b`

## How LuckyD uses this

- The AI sidebar (Ctrl+J) pulls **live** model lists per provider; when a live fetch fails it falls back to the curated free catalogs baked into `browser_core/ai_bridge.py` (`_OPENCODE_FREE_CATALOG`, `_OPENROUTER_FREE_FALLBACK`).
- OpenRouter's picker sorts every `:free` model to the top automatically.
- `openrouter/free` is a meta-router: it auto-picks whichever free model is available.
- The coding agent can use OpenCode Zen directly: set `CODING_AGENT_PROVIDER=opencode` (key: `OPENCODE_API_KEY`).

## Switching providers

```bash
python main.py model nemotron-3-ultra-free   # OpenCode Zen
python main.py model nvidia/nemotron-3-ultra-550b-a55b:free  # OpenRouter free
provider openrouter
```

Model overrides persist in `browser/data/settings.json` under `ai_model_overrides`.
