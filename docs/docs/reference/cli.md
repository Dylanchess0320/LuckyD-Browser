# CLI Reference

Detailed command-line flags and parameters for `main.py` / `luckyd-code`.

---

## Synopsis

```bash
luckyd-code [OPTIONS] [PROMPT]
```

## Options

| Option | Type | Description |
|---|---|---|
| `--model NAME` | String | Override model name (e.g. `deepseek-chat`, `gpt-4o`) |
| `--provider NAME` | String | Set provider (`opencode`, `openrouter`, `deepseek`, `google`, `ollama`, etc.) |
| `--thinking` | Flag | Enable thinking / reasoning model mode |
| `--temp FLOAT` | Float | Model temperature (default: `0.0`) |
| `-y, --yes, --yolo` | Flag | Non-interactive auto-approval of tool calls |
| `--max-turns N` | Integer | Maximum conversation turns (default: 30) |
| `-c, --continue` | Flag | Resume most recent session |
| `--resume ID` | String | Resume specific session checkpoint by ID |
| `--help` | Flag | Display help message and exit |
