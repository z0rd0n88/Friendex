---
name: docs
description: ARCH.md / ADR / baton-pass drift check; propose inline patches when small.
reviewers:
  - documentation-expert
  - code-explorer
---

# `docs` mode

Single read pass over the docs surface. If drift is small, propose a
minimal patch in the synthesizer output instead of filing a tracker
issue.

## Targets

- `ARCH.md` drift vs. source tree — if stale, run
  `python3 .githooks/gen_arch.py` and propose a regen commit on a worktree.
- `docs/adr/` coverage for service-level changes since the last ADR.
- `baton-pass/INDEX.md` freshness vs. current PR state.

## Layer-slice usage

```
xan-multi-agent-review dir docs/  --mode docs
xan-multi-agent-review file ARCH.md --mode docs
xan-multi-agent-review file baton-pass/INDEX.md --mode docs
```

See `.claude/xan-review-modes/README.md` for the exclusion-list step.
