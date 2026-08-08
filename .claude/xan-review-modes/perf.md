---
name: perf
description: N+1, SQLite contention, hot-path Decimal cost, embed limits, task jitter.
reviewers:
  - performance-optimizer
  - python-pro
  - code-explorer
---

# `perf` mode

Friendex-specific concerns:

- **SQLite write-lock contention** at the 5-min / 15-min tick boundaries
- **`_rebuild_many` IN-query chunking** against the 999-var SQLite limit
- **Embed character DoS** (Discord 6000-char total cap)
- **`apply_floor_stall` attenuation math** on the hot tick path
- Repeated `Decimal(str(...))` cost on hot paths
- `asyncio.TaskGroup` / `asyncio.timeout` opportunities

## Layer-slice usage

```
xan-multi-agent-review dir src/friendex/adapters/persistence/ --mode perf
xan-multi-agent-review dir src/friendex/adapters/tasks/       --mode perf
xan-multi-agent-review dir src/friendex/application/          --mode perf
```

For wall-clock alignment of tick cohorts, the `code-explorer` agent
traces the loop scheduling rather than scoring it.

See `.claude/xan-review-modes/README.md` for the exclusion-list step.
