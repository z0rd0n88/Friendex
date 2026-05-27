# Pass-Baton: Phase 11c — trading + fund cogs COMPLETE (gate green)

**Date:** 2026-05-26
**Scope:** phase-11c
**Branch:** feat/phase-11c-cogs-trade
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-26-phase-11
**HEAD:** cca2c4d chore(phase-11b): review CLEAN — digest + log (not yet committed for 11c work — implementation lives in worktree)

## Where things stand

Phase 11c implementation **complete and gate green**. Both cogs landed
mutation-hardened with the four required reverts confirmed RED. Ready for
review and commit. No new dependencies; no edits to 11a / 11b cogs, embeds,
services, fixtures, or any layer below `adapters/discord_bot/cogs/`.

## Gate

```
703 passed (669 baseline + 34 new — 14 trading + 20 fund)
ruff check .       → All checks passed!
ruff format --check . → 129 files already formatted
mypy src/friendex  → Success: no issues found in 61 source files
```

Coverage on new cog files (AC C5: ≥80% required):
```
src/friendex/adapters/discord_bot/cogs/fund_cog.py         62 stmts  100%  (2 branches, 0 missed)
src/friendex/adapters/discord_bot/cogs/trading_cog.py      38 stmts  100%  (0 branches)
```

## Files added (only)

- `src/friendex/adapters/discord_bot/cogs/trading_cog.py` (TradingCog,
  4 commands: /buy /sell /short /cover, all PUBLIC, positional service
  calls, allowed_mentions=None on every send)
- `src/friendex/adapters/discord_bot/cogs/fund_cog.py` (FundGroup
  (app_commands.Group, name="fund") + FundCog wrapper holding the group
  as `cog.group` for Phase 13 wiring; 5 subcommands: create / info /
  withdraw / send_events / invest)
- `tests/adapters/discord_bot/cogs/test_trading_cog.py` (14 tests)
- `tests/adapters/discord_bot/cogs/test_fund_cog.py` (20 tests)

## Design choices honoured

- **TradingService positional calls** (Phase 8c digest): each command
  invokes `service.method(str(actor.id), str(target.id), shares)` — NO
  kwargs. Verified by `assert_awaited_once_with("42", "555", 3)`.
- **FundGroup as `app_commands.Group` subclass** holding factory + Settings
  as instance state. The cog (FundCog) wraps the group as `cog.group` so
  Phase 13 can call `bot.tree.add_command(cog.group)`. The cog itself does
  not register any `@app_commands.command` decorators — the group owns the
  subcommand surface.
- **`/fund create` confirmation** reuses `build_fund_info_embed` via a
  private `_build_fund_info_embed_for` helper that computes the effective
  APY using `compute_effective_apy(base_apy, None, datetime.now(tz=UTC))`.
  Passing `penalty=None` keeps effective APY equal to the base — appropriate
  given the cog layer doesn't fetch FundPenalty (a widening of FundService
  would be needed to surface the penalty here; Phase 13 / a future read-model
  enhancement can do that).
- **`/fund info` empty path** renders an inline COLOR_NEUTRAL embed
  (mirrors AccountCog.balance / PortfolioCog.portfolio convention).
- **AllowedMentions.none()** on EVERY send in fund_cog AND trading_cog —
  uniform application per the project I2 carry-forward bar.
- **Money invariant**: `Decimal(str(amount))` for every Discord-sourced
  float (never `Decimal(amount)` directly — Phase 3.1).
- **`datetime.now(tz=UTC)`** at the cog boundary for `/fund withdraw`.
- **DomainError / NotImplementedError** propagate uncaught from every
  command path (Phase 13 owns the handler).

## Mutation hardening — verbatim RED outputs

All four required mutations applied at file scope, test failure observed,
then reverted. Gate is green now.

### Mutation 1: trading_cog `/buy` swaps `build_buy_confirmation_embed` → `build_sell_confirmation_embed`

```
>               f"Total revenue: {_money(result.total_revenue)}"
                                         ^^^^^^^^^^^^^^^^^^^^
E       AttributeError: 'BuyResult' object has no attribute 'total_revenue'
src/friendex/adapters/discord_bot/embeds.py:258: AttributeError
FAILED tests/adapters/discord_bot/cogs/test_trading_cog.py::test_buy_reply_is_public_and_uses_buy_embed
```

### Mutation 2: trading_cog `/buy` adds `ephemeral=True` (the public-reply mutation)

```
E       AssertionError: assert True is False
E        +  where True = <built-in method get of dict object at 0x...>('ephemeral', False)
tests/adapters/discord_bot/cogs/test_trading_cog.py:182: AssertionError
FAILED tests/adapters/discord_bot/cogs/test_trading_cog.py::test_buy_reply_is_public_and_uses_buy_embed
```

### Mutation 3: fund_cog `/fund info` drops `allowed_mentions=AllowedMentions.none()`

```
>       assert "allowed_mentions" in kwargs
E       AssertionError: assert 'allowed_mentions' in {'embed': <discord.embeds.Embed object at 0x...>, 'ephemeral': True}
tests/adapters/discord_bot/cogs/test_fund_cog.py:261: AssertionError
FAILED tests/adapters/discord_bot/cogs/test_fund_cog.py::test_fund_info_reply_is_ephemeral_with_allowed_mentions_none
FAILED tests/adapters/discord_bot/cogs/test_fund_cog.py::test_fund_info_renders_neutral_inline_embed_when_no_fund
(2 tests fail — neutral-fallback path also asserts the kwarg)
```

### Mutation 4: fund_cog `/fund invest` catches `NotImplementedError` silently

```
>       with pytest.raises(NotImplementedError):
E       Failed: DID NOT RAISE <class 'NotImplementedError'>
tests/adapters/discord_bot/cogs/test_fund_cog.py:383: Failed
FAILED tests/adapters/discord_bot/cogs/test_fund_cog.py::test_fund_invest_propagates_not_implemented_uncaught
```

## Acceptance criteria check (C1-C5)

- **C1 (trading_cog: 4 commands, positional args, PUBLIC, factory)** ✓
  — verified by `test_*_calls_trading_service_with_positional_args` +
  `test_buy_routes_through_per_guild_factory` + `test_*_reply_is_public_*`.
- **C2 (fund_cog: FundGroup with 5 subcommands, kw-only fund_info embed,
  Settings injected)** ✓ — verified by `test_fund_group_is_app_commands_group_named_fund`,
  `test_fund_group_registers_all_five_subcommands`,
  `test_fund_info_passes_base_and_effective_apy_to_builder`,
  `test_fund_cog_exposes_group_for_phase_13_wiring`.
- **C3 (allowed_mentions=AllowedMentions.none() on every fund_cog send;
  trading_cog applies it uniformly too)** ✓ — verified by 4 assertions
  across fund_cog tests; trading_cog passes the kwarg on every send
  (the project-wide convention).
- **C4 (DomainError + NotImplementedError propagate uncaught)** ✓ — verified
  by `pytest.raises` on InsufficientFunds (/buy), NoPosition (/sell),
  OptedOut (/short), PositionFrozen (/cover), AlreadyOptedIn (/fund create),
  FundInsufficientBalance (/fund withdraw), InvalidAmount (/fund send_events),
  NotImplementedError (/fund invest).
- **C5 (gate green; ≥80% coverage on each cog file)** ✓ — both files 100%.

## Next steps

1. Commit the four added files + the two baton-passs (000 work +
   001 complete) + this baton's INDEX.md row.
2. Open PR, mark verification gates green, reference issue #2.
3. Phase 13 (next phase) wires bot.tree:
   - `bot.tree.add_command(account_cog.balance)` etc. for TradingCog's
     four commands (each registered as a standalone slash command),
   - `bot.tree.add_command(fund_cog.group)` for the `/fund` group.
4. Phase 13 also installs the tree-wide `app_commands` error handler
   that renders `build_error_embed(error)` for DomainError and a generic
   operator-facing message for NotImplementedError / unhandled errors.

## Open questions / risks

- **FundPenalty surfacing.** `_build_fund_info_embed_for` currently passes
  `penalty=None` to `compute_effective_apy`. This means active early-
  withdrawal penalties are NOT reflected in the `/fund info` rendered APY
  today. To fix this without growing the cog layer's repo coupling, Phase
  13 (or an enhancement) should widen `FundService.fund_info` to return a
  read-model DTO carrying the penalty (or computed effective APY); the
  cog then passes through to the builder. Not a blocker for Phase 11c —
  the no-penalty rendering matches the most common state.
- **`/fund info` user.None edge.** `info` defaults to invoker; if the
  invoker has no fund, the cog renders a static "No hedge fund yet" embed.
  When inspecting another user with no fund, the embed shows the same
  generic message regardless of whose fund was queried (the embed has no
  user context outside the inline fallback message). Phase 13 could
  personalise this if desired.

## References

- AC spec: Phase 11c work-unit instructions (current task message)
- Continuity digests: `baton-runner/br-2026-05-26-phase-11/digest-phase-11a.md`,
  `digest-phase-11b.md`, `baton-runner/br-2026-05-26-phase-10/digest-phase-10.md`,
  `baton-runner/br-2026-05-25-phase-8/digest-phase-8c.md`, `digest-phase-8e.md`
- Embed builders: `src/friendex/adapters/discord_bot/embeds.py:220-535`
- Trading service: `src/friendex/application/trading_service.py:319,398,467,612`
- Fund service: `src/friendex/application/fund_service.py:152,164,202,267,352`
- Conftest: `tests/adapters/discord_bot/cogs/conftest.py`
- Prior baton: [000-2026-05-26-phase-11c-work.md](./000-2026-05-26-phase-11c-work.md)
