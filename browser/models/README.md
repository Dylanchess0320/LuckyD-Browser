# LuckyD Browser Models Directory

This directory contains information about all the free AI models available to LuckyD Browser.

## Files in this directory:

- FREE_MODELS_CATALOG.md - Comprehensive list of all free models
- providers_config.json - Configuration for AI providers and their free models  
- check_free_models.py - Script to verify model accessibility

## Quick Access:

All free models can be used via:
python main.py model <model-name>

Example:
python main.py model gemini-3-flash-preview  # Google Gemini (current)
python main.py model nemotron-3-ultra-free    # OpenCode Zen alternative
python main.py model llama3.2:3b              # Local Ollama (free)

## Provider Priority:
1. Google Gemini (currently active - free tier)
2. OpenCode Zen (free tier alternative) 
3. Local Ollama (always free, no API needed)
4. OpenRouter (variety of free large models)
