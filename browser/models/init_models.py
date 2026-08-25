#!/usr/bin/env python3
"""
LuckyD Browser - Models Directory Initializer
Sets up the models directory with all necessary files
"""

from pathlib import Path


def initialize_models_directory():
    """Initialize the models directory with all necessary files"""

    # Get the browser directory
    browser_dir = Path(__file__).parent.parent
    models_dir = browser_dir / "models"

    # Ensure models directory exists
    models_dir.mkdir(exist_ok=True)

    print("Initializing models directory:", models_dir)

    # Create README for the models directory
    readme_content = """# LuckyD Browser Models Directory

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
"""

    readme_path = models_dir / "README.md"
    with open(readme_path, "w") as f:
        f.write(readme_content)

    print("Created README.md")

    # Create a symbolic link or reference to the .model_cache.json if it exists
    cache_file = Path(__file__).parent.parent.parent / ".model_cache.json"
    if cache_file.exists():
        print("Found model cache:", cache_file)

    print("\nModels directory initialized successfully!")
    print("Location:", models_dir)
    print("\nRun 'python models/check_free_models.py' to see current model status")


if __name__ == "__main__":
    initialize_models_directory()
