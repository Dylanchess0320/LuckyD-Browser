# /compact - Summarize the conversation so far to reclaim context

You are compacting the current conversation to free up context window space.

Summarize the session so far into a compact handoff note covering:

1. **Goal** — what the user is trying to accomplish.
2. **Key decisions** — choices made and why.
3. **Files changed** — paths touched and what was done in each.
4. **Open threads** — unresolved questions, pending work, next steps.
5. **Facts to keep** — names, constraints, preferences, credentials-free context the next session needs.

Additional args from the user: {args}

Write the summary as a dense, scannable note. Omit small talk. After summarizing,
continue working with the compacted context as if the earlier transcript had been
replaced by this note.
