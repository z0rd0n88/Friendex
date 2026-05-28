# Phase 17c — Phase-Exit Digest

**Branch:** feat/phase-17c-intro-runbook
**HEAD:** ce9b905
**Verdict:** CLEAN (gate green; M1, M3, M4, M5, M6 all load-bearing; M2 → LOW-1)

## Public surface added in 17c

1. **`ActivityService.opt_in_and_consume_intro(user_id: str) -> bool`**
   (`src/friendex/application/activity_service.py:292`). Atomic RMW
   under `self._lock_key(user_id)`: sets `opt_in=True` + flips
   `intro_shown` to `True`, returns the pre-write `not intro_shown`
   as a one-shot "fire the intro DM" signal. Auto-seeds absent
   users via `_get_or_create`. Existing `set_opt_in` and
   `mark_intro_shown` retained for `/optout` and other callers.

2. **`AccountCog.optin` Q10 auto-DM**
   (`src/friendex/adapters/discord_bot/cogs/account_cog.py:92`).
   First-time signal → `interaction.user.send(embed=
   build_intro_embed(), allowed_mentions=AllowedMentions.none())`;
   on `discord.Forbidden` falls back to ephemeral with
   `embeds=[intro, confirmation]`. Ephemeral ack always sent.
   `/optout` unchanged. Only `try/except` introduced is the
   narrow `discord.Forbidden`.

3. **`FundGroup.invest` happy-path ack**
   (`src/friendex/adapters/discord_bot/cogs/fund_cog.py:267`).
   Public `Embed(title="Invested", color=COLOR_SUCCESS)` with
   `AllowedMentions.none()` after the service call. Stale
   `test_fund_invest_propagates_not_implemented_uncaught` retired;
   3 positive pins replace it (decimal call sig, public + mentions
   shape, `InvalidAmount` propagation).

4. **STEPS[id=18] live-invest text** + new smoke driver md5
   baseline `3843e386d99898b44d65fb1aaec00d7a` (byte-stable across
   two runs). The retired `test_fund_invest_step_notes_not_
   implemented_error` pin is replaced by
   `test_fund_invest_step_describes_live_invest_path`.

5. **Runbook sub-sections** ("Invest flow" + "Intro DM" under
   "Step-by-step verification" in `docs/runbook-smoke-test.md`).
   Single-source-of-truth contract preserved — operator still runs
   the script for the canonical step list.

## Phase-17 closure notes (what 18+ inherits)

- **LOW-1 (17c).** No test pins the ordering of consume → ack in
  `AccountCog.optin`. Mutation M2 (ack-first) stays green. Fix is
  one `mock_calls` ordering assertion on a parent mock.
- **LOW-1 (17b).** Defensive `dict(...)` clone on `fund.investors`
  in `fund_service.invest` is unregressed (M5 mutation stays green).
  Two-line test fix when convenient.
- **Deferred `/fund divest` (investor withdraw).** The
  manager-cap withdraw path landed in 17b; an investor-stake
  withdraw is not yet wired. Phase 18+ scope.
- **DM Forbidden observability.** `AccountCog.optin` swallows
  `discord.Forbidden` silently. A future observability pass might
  add a structured log line (logger plumbing required — out of 17c
  allow-list).
- **C8 deviation.** The `baton-pass` skill is not invocable inside
  subagent runtimes, so INDEX.md got hand-appended for both the
  17c work and review batons. Tooling fix, not a product concern.
