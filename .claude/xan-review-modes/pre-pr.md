---
name: pre-pr
description: Diff-only sanity check on the current worktree before opening a PR.
reviewers:
  - ecc-code-reviewer
  - ecc-python-reviewer
  - ecc-silent-failure-hunter
---

# `pre-pr` mode

Cheap sanity check on the diff before `gh pr create`. **Does not file
an issue** — the synthesizer's output is for the author's eyes only.

## Usage

```
xan-multi-agent-review pr <draft-pr-number> --mode pre-pr
```

If a CRITICAL or HIGH lands, fix on the same worktree before opening.
Optionally chain the `superpowers:verification-before-completion` skill
before declaring the diff clean.

The exclusion-list step in `.claude/xan-review-modes/README.md` is
**optional** for this mode — pre-PR review is local-only and not
intended to feed a tracker issue.
