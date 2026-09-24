# LuckyD Browser — Dev Quickstart

From zero to a green test suite in four commands. Windows 10/11, Python
3.10–3.12 required.

```text
git clone https://github.com/Dylanchess0320/LuckyD-Browser.git
cd LuckyD-Browser
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m pytest --collect-only
```

That last command should collect **3,100+ tests**. If it does, your
environment is good. Then run the real thing:

```text
python -m pytest
```

Want the dev extras (lint, type checks)?

```text
pip install -r requirements-dev.txt
```

Curious what the coding agent itself can do? From the repo root:

```text
py main.py --help
```

That's LuckyD Code — the headless CLI agent. (It needs either Ollama
running locally or a provider API key to actually do work.)

For the full contributor picture — branching, test conventions, CI —
read [CONTRIBUTING.md](../CONTRIBUTING.md) next.
