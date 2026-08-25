# Getting Started

Welcome to LuckyD Code! This guide will help you get up and running quickly.

## What is LuckyD Code?

LuckyD Code is an AI-powered coding agent that runs in your terminal and integrates with VS Code. It uses large language models (LLMs) to help you write, debug, and understand code.

### Key Capabilities

| Capability | Description |
|------------|-------------|
| **Code Generation** | Generate code from natural language descriptions |
| **Code Explanation** | Explain complex code in plain language |
| **Bug Fixing** | Identify and fix bugs in your code |
| **Test Generation** | Create unit tests for your code |
| **Refactoring** | Suggest and apply code improvements |
| **Search & Analysis** | Search across your codebase with semantic understanding |

## First Run

### 1. Set Up Your API Key

Choose a provider and set your API key in the `.env` file:

```bash
# For DeepSeek (default):
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 2. Start the Agent

```bash
python main.py
```

### 3. Try Some Commands

```
> explain this project structure
> find any bugs in the code
> generate tests for the sandbox module
> help me refactor this function
```

## Next Steps

- Check the [Installation Guide](installation.md) for detailed setup instructions
- Learn about [Configuration](configuration.md) options
- Tools and detailed usage guides are coming soon
