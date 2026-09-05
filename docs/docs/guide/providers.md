# Supported LLM Providers

LuckyD Code is provider-agnostic and connects seamlessly to local and cloud LLMs.

---

## Provider Overview

| Provider | Default Model | Config Var | Features |
|---|---|---|---|
| **OpenCode Zen** | Free live catalog | `OPENCODE_API_KEY` | Fast coding models, free tier |
| **DeepSeek** | `deepseek-chat` / `deepseek-reasoner` | `DEEPSEEK_API_KEY` | Native reasoning / thinking support |
| **OpenRouter** | Multi-vendor aggregator | `OPENROUTER_API_KEY` | Free models + flagship models |
| **Google Gemini** | `gemini-2.5-flash` | `GEMINI_API_KEY` | Large context window, multimodal vision |
| **OpenAI** | `gpt-4o` / `o3-mini` | `OPENAI_API_KEY` | Industry standard tool calling |
| **Anthropic** | `claude-3.7-sonnet` | `ANTHROPIC_API_KEY` | Extended thinking & code analysis |
| **Ollama** | Local models (`qwen2.5-coder`, `llama3`) | Local daemon | 100% offline & private |
| **Groq** | `llama-3.3-70b-versatile` | `GROQ_API_KEY` | Ultra-low latency streaming |
