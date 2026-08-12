---
name: ai-news-brief
description: Deliver a fresh, personalized AI news briefing — model releases, funding rounds, policy shifts, and tool launches — with memory-aware context so you never get the same headline twice
version: "1.0"
author: LuckyD Code
tags: [news, ai, briefing, research, daily-digest]
---

# AI News Brief — Personalized Daily Digest

Use this skill when the user asks for "AI news", "what's new in AI", "top AI stories",
or "give me a briefing". The goal is a 5-minute read that feels like a smart friend
curated it, not a generic aggregator.

## Workflow

1. **Determine time window**
   - If unspecified, default to the **last 7 days**.
   - If the user says "today", use the last 24 hours.

2. **Recall what the user already saw**
   - Run `MemoryRecall` with "AI news" and "AI headlines".
   - Skip any story the user already reacted to unless there is a major update.

3. **Search fresh sources**
   - Run `WebSearch` for each of these angles (rotate order to keep results varied):
     - `AI news this week <month> <year>`
     - `OpenAI Anthropic Google DeepMind latest`
     - `AI funding round series B 2025`
     - `AI regulation policy announcement`
     - `new AI model benchmark released`
   - Prioritize: TechCrunch, The Verge, Ars Technica, Reuters, Bloomberg, official
     blogs (OpenAI, Anthropic, DeepMind, Meta AI, Mistral).

4. **Cluster and rank**
   - Group into 4 buckets: **Models**, **Money**, **Policy**, **Tools**.
   - Pick the top 2–3 stories per bucket (max 10 total).
   - Rank by impact, not hype. A $50M Series B is less important than a new SOTA model
     that changes pricing or capabilities.

5. **Write the brief**
   - Use the template below.
   - For each story: 1 headline, 2–3 sentences of context, 1 "why it matters to you".
   - End with a "Watchlist" — 2 things likely to happen in the next 7 days.

## Output Template

```markdown
## AI News Brief — <Date Range>

### Models
- **<Headline>** — <context> (Source, date)
  - Why it matters: ...

### Money
- ...

### Policy
- ...

### Tools & Launches
- ...

### Watchlist (next 7 days)
- ...
- ...

---
*Curated by LuckyD. Sources: ...*
```

## Guardrails
- Never invent a date, funding amount, or benchmark score. If unsure, omit the story.
- If a story is older than the window but still dominating conversation, label it
  "Ongoing" and give a 1-sentence recap.
- Always cite the source and publication date inline.
- Keep the total brief under 500 words unless the user asks for a deep dive.
