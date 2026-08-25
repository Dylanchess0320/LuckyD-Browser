#!/usr/bin/env python3
"""
LuckyD Browser - Free Models Status Checker
"""

import os
from pathlib import Path

def main():
    print("LuckyD Browser - Free Models Status Checker")
    print("=" * 50)
    
    # Show available free models
    print("\nAVAILABLE FREE MODELS:")
    print("-" * 30)
    
    models = {
        "Google Gemini": ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-flash-latest"],
        "OpenCode Zen": ["nemotron-3-ultra-free", "big-pickle", "mimo-v2.5-free"],
        "Ollama (Local)": ["llama3.2:3b", "gemma3:4b", "phi3:mini", "mistral:7b", "codellama:7b"],
        "OpenRouter": ["z-ai/glm-5.2:free", "google/gemma-4-31b-it:free", "nvidia/nemotron-3-super-120b-a12b:free", "nvidia/nemotron-3-ultra-550b-a55b:free"]
    }
    
    for provider, model_list in models.items():
        print("\n" + provider + ":")
        for model in model_list:
            print("  • " + model)
    
    print("\n" + "=" * 50)
    print("To switch models:")
    print("  python main.py model <model-name>")
    print("  Example: python main.py model gemini-3-flash-preview")
    print("")
    print("Current settings in browser/data/settings.json")
    print("See models/FREE_MODELS_CATALOG.md for details")

if __name__ == "__main__":
    main()