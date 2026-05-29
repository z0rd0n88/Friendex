---
name: architecture
description: Hexagonal boundary check, deepening opportunities, silent-failure ladder.
reviewers:
  - ecc-code-architect
  - ecc-silent-failure-hunter
  - critical-thinking
---

# `architecture` mode

Read `ARCH.md` first. Specifically verify:

- `application/` imports nothing from `adapters/`
- ADR coverage for any service added since the last ADR (see `docs/adr/`)
- No private cross-service calls (the
  `LiquidationService → TradingService._cover_internal` pattern is the
  canonical smell)

## Layer-slice usage

Single pass on the inward layers; reviewers trace dependency direction:

```
xan-multi-agent-review dir src/friendex/        --mode architecture
```

For deepening opportunities specifically, also consider the user-scope
`improve-codebase-architecture` skill — it uses `CONTEXT.md` + `docs/adr/`
and emits a different shape of finding.

See `.claude/xan-review-modes/README.md` for the exclusion-list step.
