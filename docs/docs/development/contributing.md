# Contributing to LuckyD Code

First off, thank you for considering contributing! 🎉

## Code of Conduct

This project and everyone participating in it is governed by our [Code of Conduct](code-of-conduct.md).
By participating, you are expected to uphold this code.

## How Can I Contribute?

### Reporting Bugs

1. **Check existing issues** first to see if the bug has already been reported
2. **Use the bug report template** when creating a new issue
3. **Include detailed steps** to reproduce the bug
4. **Include environment details** (OS, Python version, model used)
5. **Include relevant logs** (redact any API keys)

### Suggesting Enhancements

1. **Check existing issues** for similar suggestions
2. **Describe the feature** and why it would be valuable
3. **Consider how it fits** with the existing architecture

### Pull Requests

1. **Fork the repo** and create your branch from `main`
2. **Run quality checks** before submitting:
   ```bash
   make quality    # lint + format + types + security
   make test       # run tests
   ```
3. **Write tests** for new functionality
4. **Update documentation** as needed
5. **Keep PRs focused** — one feature/fix per PR

## Development Setup

### Prerequisites

- Python 3.10+
- Git
- (Optional) Docker for containerized development

### Setup

```bash
# Clone your fork
git clone https://github.com/your-username/coding-agent.git
cd coding-agent

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
make setup

# Verify setup
make all-checks
```

### Environment Variables

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
# Edit .env with your API keys
```

## Coding Standards

### Style Guide

- **Python**: Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/)
- **Line length**: 100 characters
- **Formatting**: Black (runs via `make format`)
- **Linting**: Ruff (runs via `make lint`)
- **Type hints**: Required for all function signatures
- **Docstrings**: Google-style docstrings preferred

### Naming Conventions

- **Classes**: `PascalCase`
- **Functions/Methods**: `snake_case`
- **Variables**: `snake_case`
- **Constants**: `UPPER_CASE`
- **Private members**: Prefix with `_`

### Testing

- Write tests using `pytest` and `pytest-asyncio`
- Place tests in the `tests/` directory
- Name test files: `test_<module_name>.py`
- Name test functions: `test_<function_name>`
- Mark integration/slow tests appropriately:
  ```python
  @pytest.mark.slow
  @pytest.mark.integration
  async def test_agent_run():
      ...
  ```

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add new feature
fix: correct bug in module
docs: update documentation
refactor: restructure code
test: add tests
chore: maintenance tasks
```

## Project Structure

```
coding-agent/
├── main.py              # Entry point
├── agent.py             # Core agent logic
├── config.py            # Configuration
├── ui.py                # Terminal UI
├── logging_setup.py     # Structured logging
├── model_resolver.py    # Model auto-detection
├── sandbox.py           # Safe command execution
├── bridge.py            # VS Code bridge
├── tools/               # Plugin tool system
│   ├── registry.py      # Tool registry
│   ├── base.py          # Base tool class
│   ├── file_tools.py    # File operations
│   ├── bash_tool.py     # Command execution
│   └── ...              # 20+ tools
├── llm/                 # LLM provider clients
│   ├── deepseek_client.py
│   ├── openai_client.py
│   ├── anthropic_client.py
│   ├── google_client.py
│   └── ollama_client.py
├── memory/              # Memory graph system
│   ├── graph.py         # Knowledge graph
│   ├── store.py         # Persistent store
│   └── embeddings.py    # ONNX embeddings
├── project/             # Project detection
├── tests/               # Test suite
├── docs/                # Documentation site
└── vscode-extension/    # VS Code extension
```

## Questions?

Open a [Discussion](https://github.com/luckyd/coding-agent/discussions) or join our community chat.
