# Pass-Baton: phase-11b portfolio + stats cogs GREEN, gate clean

**Date:** 2026-05-26
**Scope:** phase-11b
**Branch:** feat/phase-11b-cogs-read
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-26-phase-11
**HEAD:** 65a8902 chore(phase-11a): review CLEAN — digest + state + log
*(working tree clean for src; baton-pass files staged on disk, uncommitted; manager owns git)*

## Where things stand

Phase 11b work-unit COMPLETE. Two new cog files implemented behind the same
factory-ctor convention Phase 11a established, with 20 new tests
(6 portfolio + 14 stats). Full gate is green and 100 % line+branch coverage
on each new cog file (≥ 80 % floor met; matches Phase 11a's 100 % bar).

Files added (all inside worktree):

- `src/friendex/adapters/discord_bot/cogs/portfolio_cog.py` — `/portfolio [user]`
  (EPHEMERAL). `discord.Member | None = None`; defaults to `interaction.user`;
  routes via `_portfolio_factory(guild_id_of(interaction))`; calls
  `portfolio_snapshot(user_id=str(target_user.id))`. `None`-snapshot fallback
  renders an inline `COLOR_NEUTRAL` embed pointing at `/daily` — mirrors
  `AccountCog.balance` (Phase 11a digest §convention 4).
- `src/friendex/adapters/discord_bot/cogs/stats_cog.py` — `/trending` (PUBLIC),
  `/mystats` (EPHEMERAL), `/price <user>` (EPHEMERAL), `/mystock` (EPHEMERAL).
  All four resolve `StatsService` via factory. `None`-return paths each render
  a small inline `COLOR_NEUTRAL` embed (no builder). `/mystock` is a
  separate `@app_commands.command` with no `user` parameter, sharing
  `build_price_embed` with `/price`.
- `tests/adapters/discord_bot/cogs/test_portfolio_cog.py`
- `tests/adapters/discord_bot/cogs/test_stats_cog.py`

No edits to Phase 11a files, conftest, embeds, or any service.

### Gate output (final run)

```
pytest                  : 669 passed, 1 warning
ruff check              : All checks passed
ruff format --check     : 125 files already formatted
mypy src/friendex       : Success: no issues found in 59 source files
coverage (new cog files):
  portfolio_cog.py   20 stmts /  2 branches  → 100 %
  stats_cog.py       41 stmts /  6 branches  → 100 %
```

### Mutation-hardening (live runs, restored after each)

| Mutation                                       | Tests failed | Restored |
|------------------------------------------------|--------------|----------|
| portfolio_cog `ephemeral=True` → `False`       | 2            | 6 pass   |
| portfolio_cog drop default-self fallback       | 4            | 6 pass   |
| stats_cog `ephemeral=True` → `False` (all)     | 6            | 14 pass  |
| stats_cog add `ephemeral=True` to `/trending`  | 1            | 14 pass  |
| stats_cog give `/mystock` a `user` param       | 1            | 14 pass  |

All five mutations were caught by an existing test — each AC is load-bearing.

### Conventions honoured

- Per-guild factory ctor (kw-only) on every cog; resolved via `_factory(guild_id_of(interaction))`.
- `discord.Member | None = None` default for optional user args; fallback to `interaction.user`.
- `DomainError` propagation untouched — no `try/except`, no `build_error_embed`.
- `COLOR_NEUTRAL` reuse for read embeds; no inline embed where a builder exists *except* the brand-new-account fallback (documented exception from Phase 11a digest).
- No new deps; no module-level constants in cogs; `Decimal` + UTC invariants preserved.
- Call style: `portfolio_snapshot(user_id=…)`, `user_stats(user_id=…)`, `get_price_stats(user_id=…)` are kwarg-style per the acceptance criteria; `trending_snapshot()` takes no args (default `limit=15`).

## Next steps

1. Manager owns review + commit + PR; this work-unit returns COMPLETE.
2. Phase 11c (separate work-unit) builds the trading + fund cogs on the same
   foundation. The conftest already exposes `trading_service`,
   `trading_service_factory`, `fund_service`, `fund_service_factory`. Apply
   the embeds I2 carry-forward (`AllowedMentions.none()` in `fund_cog`) per
   Phase 10 review.
3. If a reviewer wants RED captures replayed, the originals are:
   - `ModuleNotFoundError: No module named 'friendex.adapters.discord_bot.cogs.portfolio_cog'` (before `portfolio_cog.py` existed).
   - `ModuleNotFoundError: No module named 'friendex.adapters.discord_bot.cogs.stats_cog'` (before `stats_cog.py` existed).

## Open questions / risks

- TDD discipline used the same RED→GREEN cadence Phase 11a established
  (no project-registered `tdd` slash command). Each cog's import error was
  captured verbatim above before implementation.
- The `None`-fallback embed prose is bot-author-visible only; if the project
  later prefers `build_error_embed` for "no account yet" we can rewire — but
  Phase 11a's `AccountCog.balance` uses the same shape, so this stays
  consistent.

## References

- Phase 11a digest: `baton-runner/br-2026-05-26-phase-11/digest-phase-11a.md`
- Phase 8d digest (service signatures): `baton-runner/br-2026-05-25-phase-8/digest-phase-8d.md`
- Phase 10 embeds digest: `baton-runner/br-2026-05-26-phase-10/digest-phase-10.md`
- Code: `src/friendex/adapters/discord_bot/cogs/portfolio_cog.py`
- Code: `src/friendex/adapters/discord_bot/cogs/stats_cog.py`
- Tests: `tests/adapters/discord_bot/cogs/test_portfolio_cog.py`
- Tests: `tests/adapters/discord_bot/cogs/test_stats_cog.py`
- Issue: #2 (live phase status — checklist + merged PRs)
- Previous baton: `baton-pass/phase-11b/000-2026-05-26-phase-11b-work.md`
