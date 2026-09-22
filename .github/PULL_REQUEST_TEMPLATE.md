# LuckyD Browser — Pull Request Guide

Thanks for contributing to LuckyD! Keep PRs small, focused, and green.

## Before you open a PR
- Sync with latest `main` — rebase, don't merge main into your branch.
- Keep it to one concern: one refactor, one perf fix, or one test file.
- New tests go in `tests/test_*.py` and must pass locally:
  `python -m pytest tests/test_your_file.py -q`
- Run lint/format: `ruff check . --fix` and `ruff format .`

## PR checklist
- [ ] Single concern, small diff (prefer < 300 lines)
- [ ] Tests added or updated, all passing
- [ ] `ruff check` and `ruff format` clean
- [ ] No junk files: no `.orig`, `.diff`, `.log`, `.lnk`, local summaries, or build output
- [ ] No secrets: no `.env` contents, tokens, or API keys

## Merge policy
- Jules/bot PRs need one human approval + green CI (`Code Quality` + `Tests`).
- Self-authored PRs can't be self-approved — they merge after CI is green.
- Conflicting or stale PRs will be asked to rebase onto latest `main`.
