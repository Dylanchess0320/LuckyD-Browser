# LuckyD Browser — Setup & Troubleshooting FAQ

The questions people actually hit during install and setup. (For product
questions — pricing, privacy, Agent Mesh — see the docs-site
[FAQ](docs/faq.md).)

## 1. How do I get the free local AI working?

Install Ollama from [ollama.com](https://ollama.com), then pull the model
LuckyD uses by default:

```text
ollama pull llama3.2:3b
```

That's it — no account, no key, no subscription. The sidebar, summarization,
and the coding agent then run fully offline on your machine.

## 2. Windows SmartScreen says "Unknown publisher." Is it safe?

The builds are currently **unsigned** (a code-signing certificate costs
money the project doesn't have yet), so SmartScreen warns on every new
installer. The code is fully open source — you can read every line and build
it yourself (see [DEV-QUICKSTART.md](DEV-QUICKSTART.md)). Click "More info" →
"Run anyway" if you trust the source you downloaded it from (always use the
official [releases page](https://github.com/Dylanchess0320/LuckyD-Browser/releases)).

## 3. My work PC blocks the installer (Halcyon / IT-managed). What now?

Corporate endpoint protection sometimes quarantines the setup exe no matter
what. Two options:

- Use the **portable ZIP** from the releases page — no installer, just unzip
  and run. Same browser, no admin rights needed.
- Ask your IT admin to allowlist the file hash. The portable ZIP's hash is
  the stable one to give them.

## 4. My Cline credits show $0.00 and the agent got dumber. What happened?

When your Cline/Pass balance hits zero, paid models stop working and LuckyD
Code falls back to the **free local Ollama model** (`llama3.2:3b`) — weaker,
but free forever. Cheapest working paths, in order:

1. **Ollama local** — free, unlimited, offline (see question 1).
2. **A free-tier API key** — e.g. a Gemini API key on the free tier, set as
   `GOOGLE_API_KEY`. (Note: the Google AI Pro *app* subscription does not
   grant API quota — you still need the key.)
3. **Top up Cline credits** if you want the credit-billed models back.

## 5. Which Python do I need to run it from source?

**Python 3.10, 3.11, or 3.12** (64-bit). 3.13 is not supported yet.

## 6. How do I run the tests?

```text
python -m pytest
```

From the repo root, with dependencies installed. The suite collects a little
over 3,100 tests. `python -m pytest --collect-only` is the quick sanity check
that your environment is set up right.

## 7. Where do I download releases?

The [releases page](https://github.com/Dylanchess0320/LuckyD-Browser/releases)
on GitHub. Each release ships two files:

- `LuckyDBrowserSetup-*.exe` — the installer (per-user, no admin needed).
- `LuckyDBrowser-Portable-*.zip` — unzip-and-run, for locked-down PCs.

## 8. How do updates work?

The browser checks for updates itself and can install them in-app with one
click — the installer runs silently, relaunches the browser, and your tabs,
profile, settings, and workspaces carry over. You can also just download the
new setup exe and install it over the old one; nothing is wiped.

## 9. Is there a Discord?

No official Discord server right now. For bugs, open a
[GitHub issue](https://github.com/Dylanchess0320/LuckyD-Browser/issues).
For ideas and questions, GitHub Discussions (seed posts drafted in
[DISCUSSIONS-SEEDS.md](DISCUSSIONS-SEEDS.md)).

## 10. My antivirus quarantined the exe. False positive?

Most likely, yes — unsigned binaries trip heuristic scanners, especially
ones that bundle a browser engine and automation hooks. If you're unsure,
don't run the binary: clone the repo and build from source instead, or use
the portable ZIP and scan it with your AV first. The source is the ground
truth; the exe is just a convenience.
