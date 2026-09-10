# FAQ

## Is LuckyD Browser really free?

Yes. The local AI (Ollama + `llama3.2:3b`) is free and unlimited — no account, no key, no subscription. The browser itself is MIT open source. Cloud providers (OpenAI, Anthropic, Gemini, …) need your own API keys and bill through those providers.

## Does it work offline?

Yes — with the local Ollama model, the AI sidebar, summarization, and the agent work fully offline. Cloud providers and Deep Research's web search need a connection.

## Which platforms are supported?

Windows 10/11 x64 only, for now.

## Do I need admin rights to install?

No. The installer is per-user and lands in `%LOCALAPPDATA%\Programs\LuckyDBrowser`.

## How is this different from Comet, Dia, or Edge Copilot?

Those rent you *their* AI behind an account. LuckyD runs *your* models locally, works offline, lets the agent drive your live tabs, and puts a coding agent plus real terminals in your tabs — all open source under MIT.

## Where do my prompts go?

Nowhere, unless you choose a cloud provider. With Ollama, everything stays on your machine. No telemetry is collected at all.

## How do updates work?

The browser checks for updates itself and can install them in-app with one click (the Inno installer runs silently and relaunches the browser; session restore brings your tabs back). You can also just install the new setup exe over the old one — profile, settings, and workspaces carry over.

## Can the AI really control my tabs?

Yes — the sidebar agent (and harness mode) drives the visible tab through the local Control API: it snapshots the page, clicks, types, and scrolls. It only ever acts on the tab you're looking at.

## What's Agent Mesh?

`Ctrl+Alt+M` opens a dock with four terminal panes so you can run multiple agents side by side. A `doctor` command checks each backend is healthy before launch.

## What's the Cline bridge?

A ClinePass-compatible API (`127.0.0.1:8317`) that lets Cline-style clients use your configured providers. Since 4.0 it requires a bearer token (`CLINE_BRIDGE_TOKEN`) and refuses to serve without one.

## I found a bug / want a feature.

Open an issue on [GitHub](https://github.com/Dylanchess0320/LuckyD-Browser/issues) — and check the [Changelog](changelog.md) first, it might already be fixed.

Next: [Changelog →](changelog.md)
