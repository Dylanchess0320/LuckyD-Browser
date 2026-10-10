# Jules async delegate

The agent mesh can dispatch well-scoped coding tasks to **Google Jules** —
an async cloud coding agent — and ingest the resulting pull requests back
into the review flow. Jules becomes the mesh's async cloud worker: the local
loop keeps the interactive work, Jules takes the long-running,
well-specified jobs.

## Setup (one time)

1. Go to [jules.google.com/settings](https://jules.google.com/settings)
   signed in as Dylan's Google account and create an API key.
2. Export it where the browser backend runs:

```bash
export JULES_API_KEY="your-key-here"
```

3. Make sure the repo is a connected Jules **source** (jules.google → Sources,
   via the Jules GitHub App). The LuckyD-Browser repo is already connected —
   that's how PRs #73–#105 arrived.

Without `JULES_API_KEY`, the `jules_*` tools refuse with a setup message.
The key is never logged or stored in the repo.

## The four tools

| Tool | What it does | Approval |
|---|---|---|
| `jules_dispatch` | Create a Jules session for a task (async, returns a session id) | Requires approval — spends quota |
| `jules_status` | Poll state + latest activity + PR URL | Read-only |
| `jules_activities` | Recent activity timeline | Read-only |
| `jules_pr` | PR url/title/description once complete — then review with `gh pr checkout` + git_tools | Read-only |

Dispatch with `automationMode=AUTO_CREATE_PR` by default: a finished Jules
session opens its own PR. Plans auto-approve unless `require_plan_approval`
is set.

## Quotas (Google AI Pro plan)

100 tasks/day, 15 concurrent sessions — enforced locally before any API
call, so a doomed dispatch fails fast with a clear message instead of
burning quota.

## Security notes

- The Jules API is **v1alpha** and may change. Every URL shape, field name,
  and state string is isolated in `tools/jules_delegate.py` behind
  `JulesClient` — a version bump touches one file.
- GitHub issue bodies and fetched web content are **untrusted**. They are
  wrapped in explicit DATA-ONLY delimiters before reaching Jules; the
  adapter never concatenates them raw into a task prompt.

## The proven fallback: Jules GitHub App

Even without the API, Jules works through its GitHub App: assign it an
issue (or let it pick up the repo's queue) and it opens PRs itself —
PRs #73–#105 in v10.6.3 arrived exactly this way. The API adapter above is
the programmatic route; the GitHub App is the battle-tested one. Use
whichever fits the job.
