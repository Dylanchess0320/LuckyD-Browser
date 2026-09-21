# LuckyD Browser v10.1.0 — Google AI Pro edition

## What's new

- **OpenCode is back in the Agent Mesh.** It now sits alongside MiniMax and
  Cline instead of replacing them. Needs your `OPENCODE_API_KEY`.
- **Google Jules joins the Agent Mesh.** Google's async coding agent rides
  alongside the others: kick off a task, and it works your repo on a Google
  cloud VM and returns a pull request. Install with
  `npm install -g @google/jules`, then `jules login`. Quotas: Free 15
  tasks/day · AI Pro 100/day · Ultra 300/day (limits can change — check
  jules.google.com). Needs Node.js and the Jules GitHub App authorized on
  your account/org.
- **Gemini refreshed.** Default chat model is now `gemini-2.5-flash`, with
  2.5 Pro and Gemini 3 previews in the catalog (sidebar, mesh, deep research).
- **Nano Banana image generation.** Make images from text with
  `gemini-2.5-flash-image`, right from LuckyD.
- **Veo 3.1 video generation.** Text-to-video with `veo-3.1-generate-preview`.
- **Google Drive backup.** One-time sign-in, then back up any file or folder
  to your 2TB Drive from inside LuckyD.

## Two ways to install

- **Setup EXE** — normal Windows installer.
- **Portable ZIP** — no install, runs from any folder. For locked-down PCs.

## Please read this first

- Google AI Pro does **not** give LuckyD extra API quota. Gemini features
  still need your own `GOOGLE_API_KEY` (free tier works).
- Veo video bills your Google Cloud project per generation — it needs a
  billing-enabled key.
- Drive backup needs a one-time Google sign-in.
- NotebookLM has no public API, so it can't plug into LuckyD.
- Builds are **unsigned** (no signing certificate yet): Windows SmartScreen
  will show "Unknown publisher", and strict work PCs (Halcyon/IT-managed)
  may block the files until IT allowlists the hash.
