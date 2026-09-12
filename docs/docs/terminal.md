# Terminal

Real Windows terminals, in tabs — not a web toy. Every shell runs on a genuine **ConPTY** through the local terminal bridge (`127.0.0.1:9881`), rendered with xterm.js.

## Shells

| Shell | Shortcut |
|---|---|
| Agent CLI (coding agent REPL) | `` Ctrl+` `` |
| PowerShell | `Ctrl+Shift+`` ` |
| CMD | shell bar in the terminal page |
| Agent Mesh CLIs | shell bar / `Ctrl+Alt+M` dock |
| Second agent terminal ("Agent 2") | Tools → Agent 2 Terminal |

The shell bar in the terminal page switches shells live — reconnecting starts a fresh session. Each tab is titled per shell, and every shell spawns its own independent ConPTY session.

Shell names are **allowlisted server-side**: the WebSocket request can never inject a command line.

## What you can do

- Run the **agent CLI** and let it work your project while you browse
- Use PowerShell/CMD with full interactive programs (editors, REPLs, pagers)
- Dock **Agent Mesh** (`Ctrl+Alt+M`) for four panes of parallel agents
- Point **Agent 2** at a second project — it boots in that project's folder as its workspace

## Under the hood

- **Bridge:** `browser/browser_core/terminal_server.py` on `127.0.0.1:9881`
- **Environment:** spawned with a sanitized env block (NUL-joined, matching the winpty contract)
- **Limits:** 1 MB max WebSocket frame, bounded scrollback buffer
- **Auth (4.0):** the terminal WebSocket authenticates with the HttpOnly `luckyd_term` session cookie provisioned by the browser — no `?token=` in URLs anymore

Next: [Security →](security.md)
