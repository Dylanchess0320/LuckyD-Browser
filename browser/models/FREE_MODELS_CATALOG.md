# LuckyD Browser - Free Models Catalog

This catalog lists all the free AI models available to LuckyD Browser through various providers.

## Available Free Models by Provider

### 🔥 Google Gemini (Currently Active - Free Tier)
- **gemini-3-flash-preview** - Latest Gemini 3 flash model (free tier)
- **gemini-2.5-flash** - Previous generation (fallback option)
- **gemini-flash-latest** - Auto-newest alias

### 🤖 OpenAI Compatible (Free Tier via OpenCode Zen)
- **nemotron-3-ultra-free** - NVIDIA's ultra-large free model
- **big-pickle** - Alternative free model
- **mimo-v2.5-free** - Another free option

### 🔓 OpenRouter (Free Tier Models)
- **z-ai/glm-5.2:free** - ZAI GLM 5.2B free model
- **google/gemma-4-31b-it:free** - Google Gemma 4 31B instruct (free)
- **nvidia/nemotron-3-super-120b-a12b:free** - NVIDIA Nemotron 3 Super 120B (free)
- **nvidia/nemotron-3-ultra-550b-a55b:free** - NVIDIA Nemotron 3 Ultra 550B (free)

### 🦙 Ollama (Local - Completely Free)
- **llama3.2:3b** - Fast local model (currently configured)
- **gemma3:4b** - Good quality local model (used for sidebar)
- **phi3:mini** - Microsoft's compact free model
- **mistral:7b** - Popular free Mistral model
- **codellama:7b** - Code-specialized Llama model

### 💰 Cline Usage (Free Tier Models)
- **deepseek/deepseek-chat** - DeepSeek chat model (free tier)
- **deepseek/deepseek-r1** - DeepSeek reasoning model (free tier)
- **meta-llama/llama-3.2-3b-instruct** - Llama 3.2 3B instruct (free)
- **google/gemini-2.0-flash** - Gemini 2.0 flash (free tier)
- **qwen/qwen3-8b** - Qwen 3 8B (free tier)
- **minimax/minimax-m2.5** - Minimax M2.5 (free tier)

### 🚀 ClinePass (When Credits Available)
- **cline-pass/kimi-k3** - Kimi K3 model
- (Other models available based on subscription)

## Recommended Configuration for Maximum Free Access

To ensure LuckyD Browser has access to all free models, configure it to:

1. **Primary Provider**: Google Gemini (gemini-3-flash-preview) - Currently active and reliable
2. **Fallback Providers**: 
   - OpenCode Zen (nemotron-3-ultra-free) - Excellent free alternative
   - Local Ollama (llama3.2:3b) - Zero cost, always available
   - OpenRouter free tiers - Variety of large free models

## Usage Instructions

### Switching Providers via Command Line
```bash
# Use Google Gemini (free)
python main.py model gemini-3-flash-preview

# Use OpenCode Zen free model
python main.py model nemotron-3-ultra-free
provider openai  # switches to OpenCode Zen

# Use local Ollama
python main.py model llama3.2:3b
provider ollama

# Use OpenRouter free model
python main.py model nvidia/nemotron-3-ultra-550b-a55b:free
provider openrouter
```

### Model Overrides in settings.json
The browser supports per-provider model overrides in `browser/data/settings.json`:
```json
{
  "ai_model_overrides": {
    "google": "gemini-3-flash-preview",
    "openai": "nemotron-3-ultra-free", 
    "ollama": "llama3.2:3b",
    "openrouter": "nvidia/nemotron-3-ultra-550b-a55b:free"
  }
}
```

## Accessing Models in the Browser

All free models are accessible through:
- **AI Chat Sidebar** (Ctrl+J or Cmd+J)
- **Agent Commands** in the terminal
- **Harvest Mode** for context-aware assistance
- **Web Search Enhancement** with AI summaries

## Last Updated
August 23, 2026 - All models verified as free and accessible