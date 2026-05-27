# Pass-Baton: Phase 11a foundation slice — code-complete, gate clean

**Date:** 2026-05-26
**Scope:** phase-11a
**Branch:** feat/phase-11a-cogs-foundation
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-26-phase-11
**HEAD:** 2b5c6b1 feat(phase-10): Discord embed builders (15 builders + 35 tests) (#54)

## Where things stand

All 8 ACs (A1-A8) GREEN. Foundation slice of Phase 11 is code-complete and
the verification gate is clean. The shared conftest + per-guild factory
fixtures are designed to be re-used by Phase 11b (portfolio, stats) and
Phase 11c (trading, fund) without modification. No commits yet; the
manager owns staging and PR creation.

## ACs — all GREEN

- A1 — `tests/adapters/discord_bot/cogs/__init__.py`
- A2 — `tests/adapters/discord_bot/cogs/conftest.py` (`fake_interaction`
  factory + every-service AsyncMock + per-guild service-factory fixtures,
  also exporting `stats_service`, `trading_service`, `fund_service` for 11b/11c)
- A3 — `account_cog.py`: `/balance`, `/optin`, `/optout` (all ephemeral)
- A4 — `daily_cog.py`: `/daily` (public) + `AlreadyClaimedToday` propagation test
- A5 — `admin_cog.py`: `/help` (ephemeral), `/game_intro` (public,
  `@app_commands.checks.has_permissions(manage_guild=True)`)
- A6 — per-guild factory ctor on every cog; shared
  `cogs/_interaction.py::guild_id_of` narrows `interaction.guild` (`Guild | None`)
- A7 — every cog test invokes `Cog.command.callback(cog, interaction, ...)`
  directly; assertions read `interaction.response.send_message.call_args`
  and embed `to_dict()`
- A8 — gate clean (see "Verification gate" below)

## Verification gate (final)

```
$ uv run pytest
======================== 649 passed, 1 warning in 7.51s ========================

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
121 files already formatted

$ uv run mypy src/friendex
Success: no issues found in 57 source files

$ uv run pytest tests/adapters/discord_bot/cogs/ \
    --cov=src/friendex/adapters/discord_bot/cogs --cov-fail-under=80
Name                                                     Stmts   Miss Branch BrPart  Cover
----------------------------------------------------------------------------------------
src/friendex/adapters/discord_bot/cogs/__init__.py           0      0      0      0   100%
src/friendex/adapters/discord_bot/cogs/_interaction.py       5      0      0      0   100%
src/friendex/adapters/discord_bot/cogs/account_cog.py       31      0      2      0   100%
src/friendex/adapters/discord_bot/cogs/admin_cog.py         17      0      0      0   100%
src/friendex/adapters/discord_bot/cogs/daily_cog.py         18      0      0      0   100%
----------------------------------------------------------------------------------------
TOTAL                                                       71      0      2      0   100%
Required test coverage of 80% reached. Total coverage: 100.00%
======================== 33 passed, 1 warning in 0.63s =========================
```

100% line + branch coverage on each new cog file (≥ 80% gate satisfied).
33 new tests; the existing 616 pytest baseline is undisturbed (total 649).

## Mutation-hardening evidence

Verified by manual mutation under reversion (mutate → run → revert):

- **`ephemeral=True` → `ephemeral=False` on `AccountCog`:** 4 tests fail
  (`test_balance_reply_is_ephemeral_and_uses_balance_embed`,
  `test_balance_with_no_account_replies_ephemerally`,
  `test_optin_reply_is_ephemeral_success_embed`,
  `test_optout_reply_is_ephemeral_success_embed`).
- **`/daily` adds `ephemeral=True`:** 1 test fails
  (`test_daily_reply_is_public_and_uses_daily_embed`).
- **`@has_permissions(manage_guild=True)` removed on `/game_intro`:** 1
  test fails (`test_game_intro_has_manage_guild_permission_check`).

## Choices recorded (per work-unit spec)

- **`/balance` with `snapshot is None`:** the cog builds a tiny inline
  `discord.Embed` (`COLOR_NEUTRAL`, body "No account found yet — run
  `/daily` to open one and claim your starter cash.") rather than adding a
  new public builder. Reply stays ephemeral; covered by
  `test_balance_with_no_account_replies_ephemerally`.
- **`setup()` / `register()` factory:** **not** added; Phase 13/14 owns
  cog wiring through container code (not `bot.load_extension`). Each cog
  file ends at the class body.
- **Shared `_interaction.guild_id_of`:** the work-unit said cogs use
  `interaction.guild.id`, but `discord.Interaction.guild: Guild | None`
  trips mypy strict. Introduced a tiny private helper
  `cogs/_interaction.py::guild_id_of(interaction) -> str` that asserts
  non-None (DM slash commands intentionally unsupported per signoff
  decision 3) — covers every cog's per-guild routing call with one
  point of narrowing. Already lint-clean and 100% covered by the
  per-cog routing tests.
- **TDD methodology:** manual RED→GREEN per AC; `/tdd` slash command not
  available in this environment. Verbatim RED logs captured in the prior
  baton (`001-2026-05-26-checkpoint-a1-a4-green.md`) for A1-A4 and below
  for A5.
- **No new dependencies.** No `pyproject.toml` / `uv.lock` change.

## RED→GREEN log (A5 — admin_cog)

RED (`uv run pytest tests/adapters/discord_bot/cogs/test_admin_cog.py`):
```
ModuleNotFoundError: No module named 'friendex.adapters.discord_bot.cogs.admin_cog'
```
GREEN: 4 passed, 1 warning in 0.28s.

(A1-A4 RED logs captured verbatim in baton 001.)

## Files created / modified

Created:
- `src/friendex/adapters/discord_bot/cogs/_interaction.py`
- `src/friendex/adapters/discord_bot/cogs/account_cog.py`
- `src/friendex/adapters/discord_bot/cogs/admin_cog.py`
- `src/friendex/adapters/discord_bot/cogs/daily_cog.py`
- `tests/adapters/discord_bot/cogs/__init__.py`
- `tests/adapters/discord_bot/cogs/conftest.py`
- `tests/adapters/discord_bot/cogs/test_account_cog.py`
- `tests/adapters/discord_bot/cogs/test_admin_cog.py`
- `tests/adapters/discord_bot/cogs/test_conftest_self.py`
- `tests/adapters/discord_bot/cogs/test_daily_cog.py`

Modified: `pass-baton/INDEX.md` (skill-managed).

No source files in `src/friendex/application/`, `src/friendex/domain/`,
`src/friendex/adapters/persistence/`, or `src/friendex/adapters/tasks/`
touched. `embeds.py` untouched (Phase 10 surface preserved).

## Carry-forward to phases 11b / 11c

- **`conftest.py` is canonical** — every service mock + factory fixture is
  pre-provisioned. 11b/11c should not re-define `stats_service`,
  `trading_service`, or `fund_service` fixtures.
- **`guild_id_of(interaction)`** is the per-guild routing primitive; every
  new cog should import it from `cogs/_interaction.py` rather than
  re-narrowing `interaction.guild` inline.
- **`/balance` snapshot=None pattern** generalises to any read command that
  may face a brand-new account: build a small ephemeral inline embed rather
  than threading sentinel DTOs through the builders.
- **Mutation-hardening bar:** every cog test file must include at least one
  test that flips on `ephemeral=`, the public-reply flag, or any permission
  decorator. The three cogs in this slice all carry such tests.
- **Cogs propagate `DomainError` uncaught** — Phase 13 will install the
  tree-wide handler. 11b/11c trading + fund cogs likewise must not catch
  `DomainError`; the `test_daily_propagates_already_claimed_today` shape
  is the template.

## Next steps

1. Manager — open PR for `feat/phase-11a-cogs-foundation` against `main`,
   following `.github/pull_request_template.md` and referencing #2.
2. Phase 11b (next slice) — `portfolio_cog.py` + `stats_cog.py` (and their
   tests). Both re-use the cogs conftest unchanged.
3. Phase 11c (final slice) — `trading_cog.py` + `fund_cog.py` (the
   `fund_cog` uses `app_commands.Group` for the `/fund` subcommands per
   the migration plan).

## Open questions / risks

- None blocking. Pre-existing carry-forward items from earlier digests
  (phase-8b M1, phase-8c M2, phase-8d L1) remain untouched per contract.

## References

- Spec: `docs/04-migration-plan.md` §Phase 11 (lines 660-698)
- Issue: #2 (live phase status)
- Prior batons in scope:
  - `pass-baton/phase-11a/000-2026-05-26-phase-11a-work.md`
  - `pass-baton/phase-11a/001-2026-05-26-checkpoint-a1-a4-green.md`
- Continuity digests:
  - `baton-runner/br-2026-05-26-phase-10/digest-phase-10.md` (embed surface)
  - `baton-runner/br-2026-05-25-phase-9/digest-phase-9.md` (service_factory)
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md` (composite lock)
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8d.md` (PortfolioSnapshot)
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8e.md` (DailyService)
- Code:
  - `src/friendex/adapters/discord_bot/cogs/_interaction.py`
  - `src/friendex/adapters/discord_bot/cogs/account_cog.py`
  - `src/friendex/adapters/discord_bot/cogs/daily_cog.py`
  - `src/friendex/adapters/discord_bot/cogs/admin_cog.py`
  - `tests/adapters/discord_bot/cogs/conftest.py`
  - `tests/adapters/discord_bot/cogs/test_{account,daily,admin,conftest_self}_cog.py`
