# Phase 15a — exit digest

Realistic JSON fixtures + migrator integration test are committed at
`3a90e2c`. **Migrator source is unchanged** — diff vs `f1e0e7f` is zero
lines under `src/friendex/adapters/persistence/migrate_json_to_sqlite.py`.

## Fixture shape conventions (what 15b MUST honour)

- **Schema follows the migrator's parser, NOT the original task spec.**
  Concretely:
  - `users.<id>.daily.streak` + `daily.last_claim` (NOT `daily_streak` or
    `last_daily_claim`).
  - `users.<id>.activity.today` + `activity.week` (NOT `activity.yesterday`).
  - No `ping_responses` field anywhere.
  - Activity-bucket keys: `text_msgs`, `media_msgs`, `voice_minutes`,
    `voice_unique_channels`, `reaction_count`, `reply_count`,
    `role_ping_joins`, `role_ping_join_minutes`, `timestamp`.
  - Long position: `{shares, avg_entry}` only. Short: `{shares,
    entry_price, locked_cash, locked_fund, created_at, frozen}`.
  - Fund: `{name, manager_id, cash_balance, investors}`; investor id ->
    contribution amount.
  - Penalty: `{penalty_apr, penalty_until}` only.
  - Timestamps are **naive ISO-8601** (no `Z`, no offset); migrator
    localises to UTC.
- **Counts pinned by the test** (do not change without updating the test):
  users=50, stocks=50, hedge_funds=31 (30 + `events_wallet`),
  fund_penalties=10. The test has hard shape pins at lines 109-112.
- **Ids:** users + stocks share ids `1001..1050`. Funds use `fund_00..fund_29`
  + `events_wallet`. **Phase 15b MUST NOT assume specific ids beyond these
  ranges** — derived counts (longs=68, shorts=40, history=252, investors=48)
  are emergent and may shift if fixtures regenerate.
- **Zero orphans.** Every long/short `target_user_id` resolves in both
  `users.json` and `prices.json`. Every fund manager + investor id resolves
  in `users.json` (`events_wallet` is the documented exception). Phase 15b's
  orphan-warning feature MUST be exercised via a separate fixture or unit
  test — NOT by mutating `tests/fixtures/json/realistic/`.
- **Penalty `penalty_until` values are all in the future** relative to
  2026-05-27 (range `2026-06-01T03:00:00..2026-06-08T22:00:00`). If 15b
  introduces an "active vs expired" filter the fixture is all-active.

## What 15b owns

1. Modify `src/friendex/adapters/persistence/migrate_json_to_sqlite.py`:
   add `--dry-run` (skip writes), `--report` (per-table counts to stdout),
   and a post-migration orphan-warning pass that walks every
   `LongPosition.target_user_id` / `ShortPosition.target_user_id` and logs
   (does NOT fail) any reference lacking a matching `UserAccount`.
2. Keep the existing 15a test green and untouched.

## Carry-forward LOW findings (15a review)

- Redundant `Decimal(str(Decimal(...)))` wrapping at test lines 135, 155, 171.
- History points not consistently ~24h apart (cosmetic; migrator imposes
  no spacing rule).
- `migrate()` return-dict equality at line 182 is informationally redundant
  — the real idempotency guard is the live `list_all` count check at lines
  189-195. Phase 15b touching the migrator should keep both checks.
