# Switch Model Utility

This utility provides an easy way to switch the LLM provider and model for LuckyD Code.

## Files

- `switch_model.py` - Python script with interactive menu
- `switch_model.bat` - Batch file to run the script (Windows)

## Usage

Double-click `switch_model.bat` or run it from a terminal:

```
switch_model.bat
```

Or run the Python script directly:

```
python switch_model.py
```

## Features

- View current provider and model
- Change provider (openrouter, ollama, openai, anthropic, google, cohere, etc.)
- Change model for the current provider
- Saves changes to `.env` file in the coding-agent directory
- Automatic detection of valid providers and their default models

## How it works

The script reads the existing `.env` file, presents an interactive menu to change settings, and writes back to the same file.

When changing providers, it automatically sets the model to the provider's default if not already set.

## Providers Supported

The script supports all providers defined in `core/providers.py`:
- openrouter
- ollama
- openai
- anthropic
- google
- cohere
- deepseek
- clinepass
- And any others added to the provider configuration

## Notes

- Run this script from the `coding-agent` directory where the `.env` file is located.
- The batch file pauses at the end so you can see the results.
- You must restart LuckyD Code or the agent for changes to take effect.