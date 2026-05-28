# Pass-Baton: Phase 17 follow-ups implemented (F1/F2/F3) — gate green

**Date:** 2026-05-28
**Scope:** p17-followups
**Branch:** feat/p17-followups
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-28-p17-followups
**HEAD (base):** 994f3d9 Phase 17c — auto-DM intro on first /optin + /fund invest ack + smoke pin update (#73)

## Where things stand

Three Phase-17 review carry-forwards landed as a single follow-up unit. F1
and F2 are test-only pins against existing product code; F3 adds a single
structured ``INFO`` log on the ``discord.Forbidden`` DM-fallback path in
``AccountCog.optin``. Gate is green (pytest + ruff-check + ruff-format +
mypy) via ``baton-runner/br-2026-05-28-p17-followups/gate-work/``, the
allow-list is honoured (only the three contracted files touched), and the
``pyproject.toml``/``uv.lock`` diff against ``origin/main`` is zero bytes.

## What changed (by AC)

### F1 — ``fund.investors`` dict-identity freshness pin (17b LOW-1)

- **Test added:** ``tests/application/test_fund_service.py::test_invest_does_not_mutate_input_investors_dict``
- Pins the existing ``new_investors = dict(fund.investors)`` defensive clone
  in ``FundService.invest`` (``src/friendex/application/fund_service.py:445-450``).
- Asserts BOTH halves of the invariant:
  (a) the dict the test handed to the fund builder is still ``{}`` after
      ``invest`` returned;
  (b) the freshly fetched fund's ``investors`` is a NEW dict object
      (``fresh.investors is not original_investors``) carrying the recorded
      stake.
- RED-first verification: under a temporary mutation that replaced
  ``new_investors = dict(fund.investors)`` with
  ``new_investors = fund.investors``, the new test FAILED at part (a):
  ```
  >       assert original_investors == {}
  E       AssertionError: assert {'user-1': Decimal('150.00')} == {}
  ```
  Mutation reverted; the test now PASSES on the unmodified production code.

### F2 — consume → ack ordering pin in ``AccountCog.optin`` (17c LOW-1)

- **Test added:** ``tests/adapters/discord_bot/cogs/test_account_cog.py::test_optin_consumes_intro_before_acking``
- Uses ``parent = MagicMock()`` + three ``parent.attach_mock(...)`` calls to
  fold ``ActivityService.opt_in_and_consume_intro``, ``interaction.user.send``,
  and ``interaction.response.send_message`` into one ordered call log. The
  test asserts ``parent.mock_calls[0][0] == "consume"`` — the very first
  recorded call MUST be the service consume, never a Discord send.
- RED-first verification: under a temporary mutation that inserted an
  ``interaction.response.send_message(...)`` call BEFORE
  ``opt_in_and_consume_intro``, the new test FAILED with a clear ordering
  message:
  ```
  E   AssertionError: expected first call to be 'consume' (the service),
      got 'send_message'; full call log:
      [call.send_message(content='placeholder', ...), call.consume('4242'),
       call.send_message(embed=<...>, ...)]
  ```
  Mutation reverted; the test now PASSES on the unmodified production code.

### F3 — structured ``INFO`` log on the ``discord.Forbidden`` DM-fallback

- **Product change:** ``src/friendex/adapters/discord_bot/cogs/account_cog.py``
  - Added module-level ``import logging`` and
    ``logger = logging.getLogger(__name__)`` (mirroring the established
    pattern in ``adapters/discord_bot/error_handler.py:64``).
  - Inside the existing ``except discord.Forbidden:`` branch, BEFORE the
    fallback ``interaction.response.send_message(...)`` call, one
    ``logger.info("account.optin_intro_dm_forbidden", extra={"user_id": ...,
    "guild_id": ...})`` call. Embed contents are NOT logged.
  - No new control flow introduced — the ``except discord.Forbidden`` block
    pre-existed (Phase 17c shipped the fallback).
- **Test added:** ``tests/adapters/discord_bot/cogs/test_account_cog.py::test_optin_logs_when_intro_dm_is_forbidden``
  - Uses pytest's ``caplog`` fixture (stdlib ``logging`` integration —
    matches the project's existing logger pattern).
  - Asserts (i) exactly one matching record on the cog's logger; (ii)
    ``record.levelno == logging.INFO``; (iii) ``record.message ==
    "account.optin_intro_dm_forbidden"``; (iv) ``record.user_id == "4242"``
    and ``record.guild_id == "99"`` (set via ``extra=...``).
- RED-first verification: with the test in place but the ``logger.info``
  call NOT yet added, the test FAILED:
  ```
  E   AssertionError: expected exactly one 'account.optin_intro_dm_forbidden'
      log on the cog logger, got 0 (all cog records: [])
  ```
  Log call added; the test now PASSES.

## Verification

- ``bash scripts/gate.sh baton-runner/br-2026-05-28-p17-followups/gate-work/``
  → ``GATE: PASS`` (pytest + ruff-check + ruff-format + mypy all green).
- Coverage on touched product files (via targeted run):
  - ``src/friendex/adapters/discord_bot/cogs/account_cog.py`` → 100%
    (43 stmts / 0 miss / 4 branches / 0 br-part).
  - ``src/friendex/application/fund_service.py`` → 93% (131 stmts / 7 miss
    / 36 branches / 5 br-part).
  - Both ≥ the 85% per-file gate.
- ``git diff origin/main -- pyproject.toml uv.lock`` → zero bytes (no new
  deps, allow-list honoured).
- ``git diff --name-only origin/main`` → exactly the three contracted files:
  ``src/friendex/adapters/discord_bot/cogs/account_cog.py``,
  ``tests/adapters/discord_bot/cogs/test_account_cog.py``,
  ``tests/application/test_fund_service.py``.

## Invariants preserved

- Decimal-at-the-boundary + UTC-aware datetimes — F1 test uses
  ``Decimal("150.00")`` literals and the existing UTC-aware builders; no
  float Decimals or naive datetimes introduced.
- Composite lock keys; ``LockManager`` non-reentrant — no lock surface
  touched. F1's RED capture confirmed the existing single ``locked(...)``
  call site under ``FundService.invest`` continues to wrap the clone.
- ``discord`` import boundary — F3's logger import is stdlib ``logging`` in
  an adapters module; no new ``discord`` imports in
  ``domain/``/``application/``/``persistence/``/``tasks/``/``scripts/``.
- No ``try/except DomainError`` introduced. The only ``try/except`` in the
  diff is the pre-existing ``except discord.Forbidden`` — F3 adds ONE log
  line INSIDE that block, before the fallback send. No new control flow.
- ``AllowedMentions.none()`` on every Discord send (unchanged).
- ADR-0001 per-guild scope (unchanged).

## Deviations / notes

- **INFO — baton-pass skill not invoked via the Skill tool.** This
  baton was hand-written, matching the Phase 17c precedent (digest-phase-17c
  C8: "the ``baton-pass`` skill is not invocable inside subagent runtimes").
  The harness-advertised skill name is ``pass-baton`` while the repo skill
  is ``baton-pass``; rather than guess at a rename, I followed the work-unit
  prompt's documented fallback ("hand-write and note the deviation as
  INFO"). The repo-level CLAUDE.md still references ``pass-baton`` (top of
  file) while the worktree CLAUDE.md and ``.claude/skills/baton-pass/``
  agree on ``baton-pass`` — the rename is partially propagated. Surface to
  the manager.
- ``baton-pass/INDEX.md`` NOT updated by this session. The skill is the
  documented index owner and the work-unit prompt scoped baton writes to
  the ``p17-followups`` subdir only; leaving INDEX.md alone avoids editing
  a file the skill is supposed to overwrite.
- During formatting cleanup, ``uv run ruff format src tests alembic``
  reformatted ``tests/adapters/discord_bot/cogs/test_account_cog.py`` once
  (the new F3 helper split). No production-file format changes.

## Next steps

1. Manager reviews the three-file diff, opens a PR with the standard
   ``.github/pull_request_template.md`` referencing issue #2, and merges.
2. Optional cleanup: align the ``pass-baton`` vs ``baton-pass`` naming so
   subagent runtimes can invoke the skill (would have let this baton land
   via the skill instead of being hand-written).

## References

- Branch: ``feat/p17-followups``
- Base SHA: ``994f3d9``
- Phase 17 digests (continuity sources):
  - ``baton-runner/br-2026-05-28-phase-17/digest-phase-17a.md``
  - ``baton-runner/br-2026-05-28-phase-17/digest-phase-17b.md``
  - ``baton-runner/br-2026-05-28-phase-17/digest-phase-17c.md``
- Product code touched:
  - ``src/friendex/adapters/discord_bot/cogs/account_cog.py`` (logger
    import + ``logger.info`` call inside ``except discord.Forbidden``)
- Tests added:
  - ``tests/application/test_fund_service.py::test_invest_does_not_mutate_input_investors_dict``
  - ``tests/adapters/discord_bot/cogs/test_account_cog.py::test_optin_logs_when_intro_dm_is_forbidden``
  - ``tests/adapters/discord_bot/cogs/test_account_cog.py::test_optin_consumes_intro_before_acking``
- Gate logs: ``baton-runner/br-2026-05-28-p17-followups/gate-work/{pytest,ruff-check,ruff-format,mypy}.log``
