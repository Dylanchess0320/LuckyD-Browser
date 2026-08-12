---
name: movie-picker
description: Recommend movies and shows based on mood, streaming services, and past preferences — with memory-aware filtering so you never get the same suggestion twice
version: "1.0"
author: LuckyD Code
tags: [movies, tv, entertainment, recommendations, streaming]
---

# Movie Picker — Mood-Aware Recommender

Use this skill when the user asks for "what should I watch", "movie suggestions",
"something like X but Y", or "best movies on Netflix right now".

## Workflow

1. **Extract the vibe**
   - Parse: mood (chill, intense, cerebral, feel-good), genre, length, era, language.
   - If missing, ask **one** targeted question (e.g., "Do you want something light or
     something that will ruin your week?").

2. **Recall history**
   - Run `MemoryRecall` with "movies", "shows", and any specific titles the user
     mentioned recently.
   - Exclude anything they already watched or explicitly disliked.

3. **Search current availability**
   - Run `WebSearch` with queries like:
     - `best <genre> movies on <service> <month> <year>`
     - `<movie title> similar movies reddit`
     - `new releases <service> <month> <year>`
   - Prioritize: official streaming service blogs, JustWatch, Reddit r/movies,
     r/television, Letterboxd lists.

4. **Curate 3 tiers**
   - **Safe bet** — matches the vibe exactly, high rating, low risk.
   - **Wildcard** — adjacent genre or hidden gem, slightly outside the comfort zone.
   - **Deep cut** — older, foreign, or indie title the user probably hasn't seen.

5. **Present with context**
   - For each pick: title, year, service, 1-sentence logline, 1-sentence "why for you".
   - Include a "Skip if" note (e.g., "Skip if you hate slow burns").

## Output Template

```markdown
## Movie Picks — <Mood/Genre>

| Tier | Title | Year | Where | Why for you |
|------|-------|------|-------|-------------|
| Safe bet | ... | ... | ... | ... |
| Wildcard | ... | ... | ... | ... |
| Deep cut | ... | ... | ... | ... |

### Skip if
- <Title>: <reason>
```

## Guardrails
- Never recommend the same title twice in a session. Use `MemoryRecall` to check.
- If the user mentions a specific title, find 3 similar but distinct options — not
  sequels or the same director's entire filmography.
- Always mention the streaming service and whether it's included with subscription or
  rental/purchase.
- If the user is in a region with different availability, ask or default to US.
