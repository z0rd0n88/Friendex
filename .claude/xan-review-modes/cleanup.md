---
name: cleanup
description: Dead code, duplication, and unused-helper sweep — behaviour-preserving only.
reviewers:
  - unused-code-cleaner
  - refactor-cleaner
  - code-simplifier
---

# `cleanup` mode

Behaviour-preserving sweep: dead code, dup consolidation, unused private
helpers, F401/ARG. Does **not** pre-empt items tracked under `code` or
`security` — see the exclusion-list step in
`.claude/xan-review-modes/README.md`.

## Layer-slice usage

Whole-repo pass; reviewers split by their own conventions:

```
xan-multi-agent-review dir src/friendex/ --mode cleanup
```

## Verification gate (per follow-up PR)

```
uv run pytest && uv run mypy src/friendex && uv run ruff check . && uv run ruff format --check .
```
