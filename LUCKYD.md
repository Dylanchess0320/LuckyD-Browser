# LuckyD Code — Project Rules

These rules are loaded into every agent session via `core/rules_loader.py`. They
define how LuckyD Code should behave in this project.

## Skill Auto-Trigger Rules

When the user's request matches any of the patterns below, **proactively run the
matching skill via `SkillRun`** before answering. Do not ask for permission — just
use the skill. If multiple skills match, pick the most specific one.

| User intent (examples) | Skill to run |
|---|---|
| "top X", "best X", "which X should I use", "is there anything I'm missing", "rank X" | `top-picks` |
| "AI news", "what's new in AI", "top AI stories", "AI briefing", "daily digest" | `ai-news-brief` |
| "what should I watch", "movie suggestions", "show recommendations", "something like X" | `movie-picker` |

After running a skill, follow its workflow exactly (search, memory recall, rank,
personalize). The skill markdown is the source of truth for output format.

## Memory-Aware Behavior

- For any "top/best/news/watch" query, **always** run `MemoryRecall` first with the
  category + "tools" or "movies" or "news" to avoid repeating past suggestions.
- If the user has asked for the same category recently, lead with what's **new**
  since last time rather than re-shuffling the same list.

## Coding Standards

- Read files before editing. Match existing patterns.
- Keep diffs minimal. No dead code, no silent errors.
- Verify changes work (run the code, check logs, or test the CLI).

## Current Setup Notes

- Provider: ClinePass, model: `cline-pass/kimi-k3`.
- Skills live in `skills/*.md` with YAML frontmatter (name, description, version,
  author, tags). They are auto-discovered by `tools/skill_tools.py`.
- Memory is stored in `data/memory/` and is searchable via `MemoryRecall`.
