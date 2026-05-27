# Pass-Baton: A1-A4 GREEN — checkpoint, A5 next

**Date:** 2026-05-26
**Scope:** phase-11a
**Branch:** feat/phase-11a-cogs-foundation
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-26-phase-11
**HEAD:** 2b5c6b1 feat(phase-10): Discord embed builders (15 builders + 35 tests) (#54)

## Where things stand

Foundation slice of Phase 11 is well along. A1 (cogs test `__init__.py`), A2
(shared conftest with `fake_interaction` + every service AsyncMock + per-guild
factories), A3 (`AccountCog` with `/balance`, `/optin`, `/optout`), and A4
(`DailyCog` with `/daily` + `AlreadyClaimedToday` propagation test) are all
GREEN. About to start A5 (`AdminCog` with `/help` + `/game_intro` and the
`manage_guild` permission check).

## RED→GREEN log so far

### A1 — cogs test `__init__.py`

**RED** (`uv run pytest tests/adapters/discord_bot/cogs/ --collect-only`):
```
ERROR: file or directory not found: tests/adapters/discord_bot/cogs/
```

**GREEN** (same command after creating `tests/adapters/discord_bot/cogs/__init__.py`):
```
collected 0 items
```

### A2 — shared conftest

**RED** (`uv run pytest tests/adapters/discord_bot/cogs/test_conftest_self.py`):
```
fixture 'fund_service_factory' not found
... 6 failed, 9 errors in 0.07s
```

**GREEN** (after creating `conftest.py`):
```
15 passed in 0.07s
```

### A3 — account_cog

**RED** (`uv run pytest tests/adapters/discord_bot/cogs/test_account_cog.py`):
```
ModuleNotFoundError: No module named 'friendex.adapters.discord_bot.cogs.account_cog'
```

**GREEN** (after creating `account_cog.py`):
```
9 passed, 1 warning in 0.29s
```

### A4 — daily_cog

**RED** (`uv run pytest tests/adapters/discord_bot/cogs/test_daily_cog.py`):
```
ERROR tests/adapters/discord_bot/cogs/test_daily_cog.py
ModuleNotFoundError: No module named 'friendex.adapters.discord_bot.cogs.daily_cog'
```

**GREEN** (after creating `daily_cog.py`):
```
5 passed, 1 warning in 0.26s
```

## Choices recorded

- **`/balance` snapshot=None handling:** the cog builds a tiny inline
  `discord.Embed` (title "Account Balance", color `COLOR_NEUTRAL`, body "No
  account found yet — run `/daily` to open one and claim your starter cash.")
  rather than adding a new public builder. Replied ephemerally per AC. Tested
  via `test_balance_with_no_account_replies_ephemerally`.
- **`setup()` / `register()`:** *not* added; Phase 13/14 will wire cogs via
  container code, not `bot.load_extension`. Each cog file ends at the class
  body.
- **TDD methodology:** manual RED→GREEN per AC, no `/tdd` slash command in
  this environment. RED logs captured verbatim above.

## Next steps

1. **A5** — write `test_admin_cog.py` then `admin_cog.py` with `/help`
   (ephemeral, uses `build_help_embed`) and `/game_intro` (public, uses
   `build_intro_embed`) decorated with
   `@app_commands.checks.has_permissions(manage_guild=True)`. Test asserts
   the check is attached via inspecting `default_permissions`.
2. **A6/A7** — already structurally satisfied across A3/A4; the admin cog
   needs the same factory-style ctor (without service deps).
3. **Final gate** — `uv run pytest`, `uv run ruff check`,
   `uv run ruff format --check`, `uv run mypy src/friendex`, and per-cog
   coverage ≥ 80%.

## References

- Spec: `docs/04-migration-plan.md` §Phase 11 (lines 660-698)
- Continuity digests (in `baton-runner/`): phase-10 embeds, phase-9
  service_factory, phase-8a composite lock, phase-8d PortfolioSnapshot,
  phase-8e DailyService.
- Prior baton in scope: `pass-baton/phase-11a/000-2026-05-26-phase-11a-work.md`
- Code:
  - `src/friendex/adapters/discord_bot/cogs/account_cog.py`
  - `src/friendex/adapters/discord_bot/cogs/daily_cog.py`
  - `tests/adapters/discord_bot/cogs/{__init__,conftest,test_conftest_self,test_account_cog,test_daily_cog}.py`
