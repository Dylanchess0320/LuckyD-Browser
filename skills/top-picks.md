---
name: top-picks
description: Curate ranked "top X" lists for any category (AI tools, games, hardware, books, productivity apps, etc.) with web search, comparison table, and memory-aware personalization
version: "1.0"
author: LuckyD Code
tags: [recommendations, rankings, research, comparisons, shopping]
---

# Top Picks — Universal Ranked Recommender

Use this skill whenever the user asks for "top X", "best X", "which X should I use", or
"is there anything I'm missing" for any category (AI tools, IDEs, games, GPUs, books,
productivity apps, browser extensions, etc.).

## Workflow

1. **Identify the category and implicit criteria**
   - Parse what the user actually values (price, performance, privacy, UX, open-source).
   - If ambiguous, ask 1 clarifying question (e.g., "Do you care more about cost or
     bleeding-edge features?").

2. **Web search for fresh rankings**
   - Run `WebSearch` with queries like:
     - `top <category> tools <current-year>`
     - `<category> comparison reddit`
     - `<category> benchmark site:tomshardware.com OR site:techcrunch.com`
   - Prioritize sources with dates within the last 6–12 months.

3. **Recall user history**
   - Run `MemoryRecall` with the category + "tools" to surface past preferences.
   - If the user previously asked for top AI tools, mention anything genuinely new since
     that list (not the same tools re-shuffled).

4. **Rank and justify**
   - Produce a **Top 5–7** table with: Rank | Name | Why it wins | Price | Best for.
   - Include a "Hidden gem" entry the user likely hasn't seen.
   - Include a "Skip" note: one popular option that isn't worth it right now and why.

5. **Personalize**
   - End with a 1–2 sentence recommendation tailored to what the user already runs
     (e.g., "Since you're already on Kimi K3, the marginal gain from Perplexity Pro is
     mostly the search UI — you can replicate it with these skills.").

## Output Template

```markdown
## Top <N> <Category> — <Month Year>

| Rank | Tool | Why it wins | Price | Best for |
|------|------|-------------|-------|----------|
| 1 | ... | ... | ... | ... |

### Hidden gem
...

### Skip for now
...

### My pick for you
...
```

## Guardrails
- Always search — never rank from training data alone for "top" or "latest" queries.
- Always disclose sources inline (e.g., `(TechCrunch, Oct 2025)`).
- If the category is niche, widen to the closest adjacent category and say so.
- Cap list at 7 items unless the user explicitly asks for more.
