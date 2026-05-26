# Pass-Baton: Phase 10 embed builders complete — ready for review

**Date:** 2026-05-26
**Scope:** phase-10
**Branch:** feat/phase-10-embeds
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-26-phase-10
**HEAD:** 64fbbe6 chore(agents): add 15 project agents + ignore Zone.Identifier sidecars (#53)
(no new commit yet — manager owns git)

## Where things stand

Phase 10 implement-unit is COMPLETE. All 15 embed builders are implemented in
`src/friendex/adapters/discord_bot/embeds.py` (the first `discord`-importing
file in the codebase, per the Phase 9 convention), plus a semantic color
palette exposed as module-level constants (`COLOR_SUCCESS` / `COLOR_ERROR` /
`COLOR_WARNING` / `COLOR_INFO` / `COLOR_NEUTRAL`). Tests live in
`tests/adapters/discord_bot/test_embeds.py` — 35 tests, 100% coverage on the
embeds module, full project gate (`scripts/gate.sh`) **PASS**. Ready for the
review unit; no new dependencies were added.

## Verification gate output (live)

```
$ uv run pytest tests/adapters/discord_bot/test_embeds.py \
    --cov=friendex.adapters.discord_bot.embeds \
    --cov-fail-under=95 --cov-report=term-missing
... 35 passed
Name                                          Stmts   Miss Branch BrPart  Cover
src/friendex/adapters/discord_bot/embeds.py     128      0     10      0   100%
Required test coverage of 95% reached. Total coverage: 100.00%

$ uv run ruff check src/friendex/adapters/discord_bot/embeds.py tests/adapters/discord_bot/test_embeds.py
All checks passed!

$ uv run ruff format --check src/friendex/adapters/discord_bot/embeds.py tests/adapters/discord_bot/test_embeds.py
2 files already formatted

$ uv run mypy src/friendex/adapters/discord_bot/embeds.py
Success: no issues found in 1 source file

$ bash scripts/gate.sh /tmp/gate-phase-10
PASS pytest (616 passed, +35 from 581 baseline)
PASS ruff-check
PASS ruff-format
PASS mypy
GATE: PASS
```

**Coverage CLI note:** the work-unit spec uses `--cov=src/friendex/adapters/discord_bot/embeds`
(slashed path) which the installed `coverage` 7.x reports as 0% (Phase-7 baton
already documented this); the canonical dotted form
`--cov=friendex.adapters.discord_bot.embeds` reports 100%. The substantive
coverage is what counts — both forms run the same tests; only the reporter
differs.

## Acceptance-criteria status with RED-first evidence

**Single RED-first capture (test-module-level)** — running tests against the
empty target produced the import failure below; that one collection error is
load-bearing for every AC because the entire module is the unit under test:

```
$ uv run pytest tests/adapters/discord_bot/test_embeds.py -v
collected 0 items / 1 error
ERROR collecting tests/adapters/discord_bot/test_embeds.py
tests/adapters/discord_bot/test_embeds.py:19: in <module>
    from friendex.adapters.discord_bot.embeds import (
E   ModuleNotFoundError: No module named 'friendex.adapters.discord_bot.embeds'
=========================== 1 warning, 1 error in 0.35s ==========================
```

**Per-AC sign-off:**

* **AC1 — module exists, exports 15 builders.** GREEN. All 15 names imported
  in `tests/adapters/discord_bot/test_embeds.py:25-39`; module file at
  `src/friendex/adapters/discord_bot/embeds.py`.
* **AC2 — each builder returns `discord.Embed` with title + body + color, no
  I/O.** GREEN. Verified by 35 tests; no service/repo/lock import in the
  module (verified by inspection — only `discord`, stdlib, and TYPE_CHECKING
  imports of result/snapshot DTOs).
* **AC3 — semantic palette as module-level constants.** GREEN. Five
  `COLOR_*` constants at module top; sanity-tested for distinctness
  (`test_color_palette_is_exported_as_module_constants`).
* **AC4 — natural service-result inputs (least coupling).** GREEN. Choices
  documented in "Deviations / signature decisions" below.
* **AC5 — structural assertions via `Embed.to_dict()`.** GREEN. Every test
  uses `embed.to_dict()`; no live bot or network round-trip.
* **AC6 — coverage ≥ 95%, full `scripts/gate.sh` passes.** GREEN. 100% on
  the embeds module, `gate.sh` PASS (above).
* **AC7 — money two-decimal, datetimes ISO/Discord-tag.** GREEN. `_money`
  helper renders `f"${value:,.2f}"` (e.g. `$1,234.56`); `_relative_timestamp`
  emits `<t:UNIX:R>` Discord tags so the client renders timezone-correct
  relative time. Tests pin `$500.00`, `$1,234.50`, `$11,111.11`, etc.
* **AC8 — error embed shows `user_facing_message` verbatim.** GREEN.
  `test_build_error_embed_renders_user_facing_message_verbatim` asserts
  `data["description"] == err.user_facing_message`; cross-checked for
  `InsufficientFunds`, `MarketClosed`, `SelfTrade`, and a custom subclass.

## Public surface (exact signatures)

```python
# Color palette (module-level constants)
COLOR_SUCCESS: discord.Color  # green   — buy/sell/short/cover/daily
COLOR_ERROR:   discord.Color  # red     — DomainError
COLOR_WARNING: discord.Color  # orange  — liquidation
COLOR_INFO:    discord.Color  # blurple — intro/help
COLOR_NEUTRAL: discord.Color  # blue    — read-only embeds

# 15 builders (in module order)
def build_balance_embed(snapshot: PortfolioSnapshot) -> discord.Embed
def build_daily_embed(result: DailyClaimResult) -> discord.Embed
def build_price_embed(stats: PriceStats) -> discord.Embed
def build_buy_confirmation_embed(result: BuyResult) -> discord.Embed
def build_sell_confirmation_embed(result: SellResult) -> discord.Embed
def build_short_confirmation_embed(result: ShortResult) -> discord.Embed
def build_cover_confirmation_embed(result: CoverResult) -> discord.Embed
def build_portfolio_embed(snapshot: PortfolioSnapshot) -> discord.Embed
def build_trending_embed(entries: Sequence[TrendingEntry]) -> discord.Embed
def build_mystats_embed(stats: UserStats) -> discord.Embed
def build_fund_info_embed(
    *,
    fund: HedgeFund,
    base_apy: float,
    effective_apy: float,
    has_penalty: bool,
) -> discord.Embed
def build_intro_embed() -> discord.Embed
def build_help_embed() -> discord.Embed
def build_liquidation_notification_embed(event: LiquidationEvent) -> discord.Embed
def build_error_embed(error: DomainError) -> discord.Embed
```

## Deviations / signature decisions (documented per spec)

1. **`build_balance_embed(snapshot: PortfolioSnapshot)`** — spec offered
   *"`UserAccount` + prices + `HedgeFund | None`, OR a `PortfolioSnapshot`;
   choose the most natural fit"*. **Chose `PortfolioSnapshot`** because
   `PortfolioService.portfolio_snapshot(user_id)` already pre-computes
   `cash_balance`, `net_worth`, `fund_balance`, and the position dicts; the
   alternative would push net-worth math into the cog. Same DTO that
   `/portfolio` already consumes — no new shape to learn.

2. **`build_fund_info_embed(*, fund, base_apy, effective_apy, has_penalty)`**
   — spec offered *"pick the natural input from `application/fund_service.py`
   — likely a snapshot dataclass; if a new read-model dataclass is required,
   define it in `application/snapshot_models.py`"*. **Did not define a new
   dataclass**, because (a) `FundService.fund_info()` already returns
   `HedgeFund | None`, (b) the `FundService` docstring explicitly delegates
   *"rendering the effective APY (with any active penalty) is the embed
   builder's responsibility"*, and (c) a one-off DTO adds coupling without
   buying anything — keyword-only `base_apy` / `effective_apy` /
   `has_penalty` parameters are equally typed and clearer at the call site.
   The Phase-11 `FundCog` will compute these three values from
   `Settings.hedge_fund_base_apy` + `compute_effective_apy(...)` and pass
   them in; standard pattern.

3. **`build_trending_embed(entries: Sequence[TrendingEntry])`** — typed as
   `Sequence` (not `list`) to keep the contract permissive; the
   `StatsService.trending_snapshot` return type is `list[TrendingEntry]`,
   which satisfies `Sequence`. No behavioural change.

4. **`_user_mention(user_id)` helper** — Discord `<@id>` mentions require
   numeric snowflakes; the helper falls back to the raw string for
   non-numeric ids so the test suite's `"target-1"` ids stay readable.
   100%-covered (numeric branch test added explicitly).

## Anything left for the review unit

The review unit should mutation-think the 35 tests against these load-bearing
points:

* **AC8 (`build_error_embed`)** — verify that flipping
  `description=error.user_facing_message` to e.g.
  `description=str(error)` would change observable behaviour for at least
  one error type. Note: `DomainError.__init__` calls
  `super().__init__(user_facing_message)`, so `str(error) ==
  error.user_facing_message` for most subclasses today; the verbatim
  contract is what matters going forward.
* **AC3 palette** — confirm the five colors are *distinct values* (the
  test does this), not just five names pointing at the same int.
* **AC4 fund_info signature** — confirm the keyword-only design is
  enforced (rename the params or call positionally — should fail). Not
  asserted in the test today; could be tightened with an
  `inspect.signature` test if the reviewer wants the contract pinned.
* **Phase-9 no-discord-in-tasks invariant** — the embed module is the
  intentional FIRST place to import `discord`; spot-check that no test
  imports `discord` from inside `adapters/tasks/`.

No deferred work. No new dependencies declared (discord.py ≥ 2.4 was
already in `pyproject.toml`). Commit boundary suggested by spec
(*"Two commits — (1) `feat(discord): embed builders`, (2)
`test(discord): embed structure`"*) is the manager's call.

## Next steps

1. Hand off to the review unit; deliverables under
   `src/friendex/adapters/discord_bot/embeds.py` and
   `tests/adapters/discord_bot/test_embeds.py`.
2. On review CLEAN, manager handles commits + PR per repo workflow.
3. Phase 11 (cogs) will consume these builders and the `COLOR_*` palette;
   no further changes needed here.

## References

- Spec: `docs/04-migration-plan.md` §Phase 10 (lines 635-658)
- Issue: #2
- Code under review:
  - `src/friendex/adapters/discord_bot/embeds.py`
  - `tests/adapters/discord_bot/test_embeds.py`
  - `tests/adapters/discord_bot/__init__.py` (already existed)
- Result/snapshot DTOs consumed:
  - `src/friendex/application/trade_results.py`
  - `src/friendex/application/snapshot_models.py`
  - `src/friendex/application/daily_result.py`
  - `src/friendex/application/liquidation_events.py`
  - `src/friendex/domain/errors.py`
  - `src/friendex/domain/models.py:138-148` (`HedgeFund`)
- Prior digests informing signatures:
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8c.md` (trade results)
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8d.md` (snapshots)
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8e.md` (daily/fund)
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8f.md` (liquidation)
  - `baton-runner/br-2026-05-25-phase-9/digest-phase-9.md` (no-discord-in-tasks rule)
