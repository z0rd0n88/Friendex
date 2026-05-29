---
name: code
description: Correctness + atomicity + idiom + typing review using complementary lenses.
reviewers:
  - code-reviewer
  - python-pro
  - python-reviewer
---

# `code` mode

Two-lens fan-out: `code-reviewer` (correctness, atomicity, money math, error
taxonomy) plus `python-pro` / `python-reviewer` (idioms, typing, PEP 8).
Complementary findings on the same file without duplication.

## Layer-slice usage

Invoke once per slice from `ARCH.md`:

```
xan-multi-agent-review dir src/friendex/domain/         --mode code
xan-multi-agent-review dir src/friendex/application/    --mode code
xan-multi-agent-review dir src/friendex/adapters/persistence/ --mode code
xan-multi-agent-review dir src/friendex/adapters/discord_bot/ --mode code
xan-multi-agent-review dir src/friendex/adapters/tasks/ --mode code
```

## Invariants every reviewer must respect

- Money = `Decimal`, quantised to `$0.01`, `ROUND_HALF_EVEN`
- Datetimes = tz-aware UTC; `UtcDateTime` rejects naive on bind
- Per-guild markets; composite locks `"<guild_id>:<user_id>"`
- `application/` imports nothing from `adapters/`

See `.claude/xan-review-modes/README.md` for the exclusion-list step.
