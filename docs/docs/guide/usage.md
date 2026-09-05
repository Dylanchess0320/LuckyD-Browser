# Usage Guide

LuckyD Code provides multiple flexible modes of operation: an interactive terminal REPL, single-command CLI execution, background daemon service, and full integration with LuckyD Browser and VS Code.

---

## 1. Interactive Terminal REPL

Start an interactive session:

```bash
python main.py
# or if installed as package:
luckyd-code
```

### Slash Commands in REPL

| Command | Action |
|---|---|
| `/help` | Display interactive help menu and keyboard shortcuts |
| `/tools` | List all registered tools and their permission levels |
| `/model [name]` | Inspect or switch the active LLM |
| `/clear` | Clear conversation history and reset context window |
| `/history` | View recent command and prompt history |
| `/mesh` | Open or connect to the multi-agent mesh |
| `/exit` | Save session checkpoint and exit cleanly |

---

## 2. Command-Line (CLI) Modes

### One-Shot Prompts

Run a direct instruction without entering the REPL:

```bash
python main.py "Refactor database queries in server.py to use connection pooling"
```

### Non-Interactive / Autonomous YOLO Mode

Auto-approve tool actions (file modifications, command runs):

```bash
python main.py --yolo "Fix all lint errors and run unit tests"
# or
python main.py -y "Analyze repository dependencies"
```

### Specifying Models and Providers

```bash
# Use DeepSeek Reasoner / Thinking mode
python main.py --thinking "Solve algorithmic optimization"

# Specify provider explicitly
python main.py --provider openrouter --model "anthropic/claude-3.7-sonnet" "Review PR changes"

# Local offline inference with Ollama
python main.py --provider ollama --model "qwen2.5-coder:7b" "Draft unit tests"
```

### Resuming Sessions

```bash
# Resume the most recent session
python main.py --continue

# Resume a specific session checkpoint
python main.py --resume 3f9a1c
```

---

## 3. Web Headquarters (HQ) & Web GUI

LuckyD includes an integrated local web UI and REST API server:

```bash
python web_server.py
```

Visit `http://127.0.0.1:8000/` in any browser, or launch **LuckyD Browser** where HQ is integrated directly into the dashboard and top navigation.
