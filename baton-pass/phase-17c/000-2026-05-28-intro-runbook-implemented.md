# Pass-Baton: Phase 17c — Q10 intro DM + 17b carry-forwards + runbook

**Date:** 2026-05-28
**Scope:** phase-17c
**Branch:** feat/phase-17c-intro-runbook
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-28-phase-17
**Base:** feat/phase-17b-invest @ ffd0f73 (HEAD at session start)
**HEAD:** ffd0f73 chore(phase-17b): review CLEAN + phase-exit digest (work uncommitted — manager owns git)

## Where things stand

All six acceptance criteria (C1–C6) are implemented; the four-check
gate at `baton-runner/br-2026-05-28-phase-17/gate-phase-17c-work/` is
**GATE: PASS**. Work is uncommitted in the worktree — the manager owns
every git mutation.

What landed:

- **C1 (already present at session start)** —
  `ActivityService.opt_in_and_consume_intro(user_id) -> bool` is the
  atomic RMW under `self._lock_key(user_id)` that flips `opt_in=True`
  and consumes `intro_shown` in one write. Three behavioural tests in
  `tests/application/test_activity_service.py` cover the three
  branches (fresh `intro_shown=False`, already-shown, auto-seeded
  unknown user) — see lines 424-487. `set_opt_in` / `mark_intro_shown`
  retained for `/optout` and any future caller that does not need the
  first-time signal.
- **C2** — `AccountCog.optin` now calls
  `opt_in_and_consume_intro`, attempts `interaction.user.send(embed=
  build_intro_embed(), allowed_mentions=AllowedMentions.none())` on
  the first-time signal, and falls back to attaching the intro embed
  to the ephemeral confirmation (`embeds=[intro, confirmation]`) when
  Discord raises `discord.Forbidden` (DMs closed). The ephemeral ack
  is sent on every path (Discord's 3 s interaction-ack invariant).
  The only `try/except` introduced is the narrow `discord.Forbidden`
  — `DomainError` continues to propagate uncaught to the Phase 13
  tree-wide handler. `/optout` is unchanged.
- **C3** — `scripts/smoke_test_commands.py` STEPS[id=18] rewritten;
  the step now describes the live invest semantics (debit invoker,
  credit fund, record stake; self-invest blocked by `InvalidAmount`;
  insufficient investor cash → `InsufficientFunds`). STEPS tuple
  ordering and length unchanged. The script docstring's stale
  "deferred to Phase 17" reference is also refreshed to point at the
  Phase 17b digest.
- **C4** — `test_fund_invest_step_notes_not_implemented_error`
  deleted; replaced by `test_fund_invest_step_describes_live_invest_path`
  that asserts on the live-invest narrative (`"stake"` present,
  `"invalidamount"` present, `"notimplementederror"` and `"deferred"`
  absent).
- **C5** — `docs/runbook-smoke-test.md` gained two sub-sections under
  "Step-by-step verification": **Invest flow** (happy path,
  self-invest blocked, insufficient investor cash) and **Intro DM**
  (first-time DM lands, subsequent /optin does not DM, DM-closed
  fallback attaches the intro to the ephemeral). The single-source-of-
  truth contract is preserved — the runbook still points operators at
  the script rather than enumerating STEPS inline.
- **C6** — `FundGroup.invest` now sends a public `discord.Embed`
  ack (`title="Invested"`, `color=COLOR_SUCCESS`) with
  `allowed_mentions=AllowedMentions.none()` after the service call,
  satisfying Discord's 3 s ack window per the 17b digest §1.
  `test_fund_invest_propagates_not_implemented_uncaught` retired
  (17b digest §5) and replaced with three positive pins:
  decimal-conversion call signature, public+allowed-mentions reply
  shape, and `InvalidAmount` propagation. No `try/except` added.

## RED-first captures

**C1.** The three opt-in tests were already present in the worktree
from prior work and run green against the present
`opt_in_and_consume_intro` implementation; this session did not move
the C1 needle.

**C2.** Three new tests added to `test_account_cog.py`. Captured RED
before implementing the cog change (full `uv run pytest
tests/adapters/discord_bot/cogs/test_account_cog.py --tb=line`):

```
FAILED test_optin_first_time_dms_intro_and_acks_ephemerally
    AssertionError: Expected opt_in_and_consume_intro to have been awaited once. Awaited 0 times.
FAILED test_optin_subsequent_does_not_dm
    AssertionError: Expected opt_in_and_consume_intro to have been awaited once. Awaited 0 times.
FAILED test_optin_dm_closed_falls_back_to_ephemeral_with_intro_attached
    AssertionError: assert 0 == 1
     +  where 0 = <AsyncMock name='user.send'>.await_count
3 failed, 7 passed
```

After implementation: 10/10 pass.

**C3+C4.** Rewrote the smoke-test pin first. Captured the failure
before rewriting STEPS[id=18]:

```
FAILED test_fund_invest_step_describes_live_invest_path
    AssertionError: assert 'stake' in 'invoke /fund invest <fund> <amount>: the cog surfaces a notimplementederror as an ephemeral user-facing error (deferred to phase 17 per phase 8e open-q5 + phase 11c digest); no state is mutated.'
```

After implementation: 13/13 pass — including the four STEPS-immutability
pins (`test_steps_is_a_tuple_not_a_list`, `test_steps_tuple_rejects_append`,
`test_steps_tuple_rejects_item_assignment`,
`test_main_prints_steps_in_strict_id_order`) and the byte-stable run pin
(`test_main_output_is_byte_stable_across_runs`).

**C6.** Captured before adding the ack call to the cog:

```
FAILED test_fund_invest_reply_is_public_with_allowed_mentions_none
    AssertionError: assert 0 >= 1
     +  where 0 = <AsyncMock name='response.send_message'>.await_count
```

After implementation: 22/22 pass in `test_fund_cog.py`.

## Verification

- **Gate.** `bash scripts/gate.sh baton-runner/br-2026-05-28-phase-17/gate-phase-17c-work/`
  prints `GATE: PASS`. All four checks green:
  pytest / ruff-check / ruff-format / mypy.
- **Per-file coverage (cogs).** From `uv run pytest
  tests/adapters/discord_bot/cogs/test_account_cog.py
  tests/adapters/discord_bot/cogs/test_fund_cog.py --cov
  --cov-report=term-missing`:
  - `account_cog.py` — 100% line + branch (40 stmts, 4 branches).
  - `fund_cog.py` — 100% line + branch (64 stmts, 2 branches).
- **No new deps.** `git diff origin/main -- pyproject.toml uv.lock`
  is empty (verified equivalently with `git diff ffd0f73 --
  pyproject.toml uv.lock` — base branch tip).
- **Smoke driver new baseline md5.** `uv run python
  scripts/smoke_test_commands.py | md5sum` →
  `3843e386d99898b44d65fb1aaec00d7a`. The md5 changes vs the
  Phase-16 baseline because STEP 18 text changed — expected per the
  spec. Reproducible across runs (the byte-stable pin still passes).
- **Modified files (allow-list 9/9):**
  ```
  docs/runbook-smoke-test.md
  scripts/smoke_test_commands.py
  src/friendex/adapters/discord_bot/cogs/account_cog.py
  src/friendex/adapters/discord_bot/cogs/fund_cog.py
  src/friendex/application/activity_service.py
  tests/adapters/discord_bot/cogs/test_account_cog.py
  tests/adapters/discord_bot/cogs/test_fund_cog.py
  tests/application/test_activity_service.py
  tests/scripts/test_smoke_test_commands.py
  ```
  No edits outside the allow-list. `src/friendex/adapters/discord_bot/embeds.py`,
  `src/friendex/domain/`, `src/friendex/adapters/persistence/`,
  `alembic/`, `fund_service.py`, and `trading_service.py` are
  untouched.

## Invariants preserved

- Decimal-at-the-boundary (`Decimal(str(amount))`) — the invest cog
  ack uses `decimal_amount` for the embed description format string;
  no `Decimal(amount)` regression.
- UTC-aware datetimes — `opt_in_and_consume_intro` does not touch
  money or time fields; existing helpers (`_get_or_create`,
  `datetime.now(tz=UTC)`) used.
- Composite `(guild_id, user_id)` lock keys — the new RMW uses
  `self._lock_key(user_id)` (composite key already includes
  `self._guild_id`); LockManager non-reentrant.
- No `discord` import in `domain/`, `application/`, `persistence/`,
  `tasks/`, or `scripts/`. The Q10 auto-DM uses `discord.Forbidden`
  exclusively in the cog layer.
- Phase 13: no `try/except DomainError` in cogs/listeners; the only
  `try/except` introduced is the narrow `discord.Forbidden` in
  `AccountCog.optin`.
- `AllowedMentions.none()` on every send: the new `interaction.user.send`,
  the new fallback `interaction.response.send_message(embeds=[...])`,
  the unchanged ephemeral confirmation, and the new `/fund invest`
  ack.
- STEPS tuple immutability + id ordering (Phase 16 M4/M5 mutation pins):
  `test_steps_is_a_tuple_not_a_list`, `test_steps_tuple_rejects_append`,
  `test_steps_tuple_rejects_item_assignment`,
  `test_main_prints_steps_in_strict_id_order` all stay green.
- Single-source-of-truth runbook: the new "Invest flow" and "Intro DM"
  subsections describe behaviour to verify; they do NOT re-enumerate
  the STEPS commands.

## Decisions 17c-followup (or a downstream phase) may want to honour

1. **Cog-layer DM stub for ack visibility.** The DM-closed fallback
   keeps the intro embed visible by attaching it to the ephemeral
   reply. If a future phase decides DMs should never block the
   ephemeral ack (e.g. always attach the intro inline), the change
   localises to the same `optin` cog body — service surface is
   unchanged.
2. **`/fund invest` ack copy.** The C6 ack currently renders
   `"Invested **$X.XX** into <@user.id>'s hedge fund."`
   (mention-suppressed via `AllowedMentions.none()`, per Phase 10
   I2). If product wants a different verb-phrase or fund-name echo
   the change is one Edit.
3. **Run-book intro DM verification step.** Operators need a fresh
   `intro_shown=False` test user. A future enhancement could expose
   an admin slash (or a one-off `python -m friendex.admin reset
   intro_shown <user>`) so QA doesn't have to mutate SQLite by hand.
4. **DM Forbidden logging.** The cog swallows `discord.Forbidden`
   silently and falls back. A downstream observability pass might
   want a structured log line — kept out of 17c because it would
   require a logger plumbed through the cog ctor and was outside
   the allow-list.
5. **17b LOW test gap (defensive `dict(...)` clone on
   `fund.investors`) is still open** per the 17b digest §LOW. Not
   addressed in 17c (fund_service.py is outside this allow-list).

## What's next

Manager / merge sequence:

1. Commit the 9 files in their natural groups (e.g. one commit for
   the activity_service + tests, one for the account_cog + tests,
   one for the fund_cog + tests, one for the smoke script + tests,
   one for the runbook).
2. PR onto `feat/phase-17b-invest` (the stacked base). Pointer to
   GitHub issue #2 §Phase 17.
3. Drive Phase 17 review on the stacked PR if 17a/17b have already
   merged; otherwise rebase after 17a → 17b → 17c land in that order.
