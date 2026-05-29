---
name: test
description: Coverage gaps + fake parity + mock-spec adequacy.
reviewers:
  - ecc-tdd-guide
  - ecc-python-reviewer
  - ecc-code-reviewer
---

# `test` mode

Lenses:

- `ecc-tdd-guide` — coverage gap + write-tests-first discipline
- `ecc-python-reviewer` — fixture quality + `AsyncMock(spec=...)` drift
- `ecc-code-reviewer` — parity between
  `tests/application/fakes/fake_repos.py` and concrete repos

## Layer-slice usage

```
xan-multi-agent-review dir tests/ --mode test
```

## Pin a coverage baseline first

```
uv run pytest --cov=friendex --cov-report=term-missing
```

Paste the per-module summary at the top of the resulting issue so the
follow-up PR has a target delta to beat.

See `.claude/xan-review-modes/README.md` for the exclusion-list step.
