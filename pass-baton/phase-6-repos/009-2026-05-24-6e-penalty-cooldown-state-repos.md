# Pass-Baton: sub-unit 6e — penalty / cooldown / system-state repositories

**Date:** 2026-05-24
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** f4aabd5 chore(phase-6): 6d review CLEAN + phase-exit digest

## Where things stand

The last repo set landed CLEAN. Three SQLAlchemy adapters now satisfy the
remaining application ports structurally (Protocol duck-typing, no inheritance):
`SqlPenaltyRepository` (`penalty_repo.py`), `SqlTradeCooldownRepository`
(`cooldown_repo.py`), `SqlSystemStateRepository` (`system_state_repo.py`). All
three follow the 6c/6d construction pattern: ctor takes
`async_sessionmaker[AsyncSession]`; each method opens one `async with session`
and ends mutations with `commit()`. These tables have NO FKs and NO child
tables, so deletes are a single `DELETE ... WHERE pk` (no cascade needed).
Mapping is immutable — fresh DTOs/domain objects, never mutating loaded rows.
TDD: RED captured (3 `ModuleNotFoundError`s), then GREEN. Gate is **PASS**
(pytest 350 passed / ruff-check / ruff-format / mypy) — log in
`selfcheck-6e/`. No new dependencies. Only Phase-6 work left is **6f**
(JSON→SQLite migrator + `tests/fixtures/json/`).

## Key design decisions (honor in 6f and the app layer)

- **Penalty repo is a plain store, NOT a TTL filter.** `get` returns an expired
  penalty (`penalty_until` in the past); `list_all` surfaces live + expired.
  The decay task needs to *see* expired rows to `delete` them, and expiry
  *interpretation* is a domain decision (`fund_math.compute_effective_apy`),
  per migration-plan line 287. Do not add expiry filtering to the penalty repo.
- **Cooldown TTL via `expires_at`, inclusive `<=` boundary.** `get` excludes
  rows where `expires_at <= now` (TTL elapsed *at* now is expired);
  `purge_expired(now)` is one bulk `DELETE WHERE expires_at <= now` (unscoped,
  cross-guild) returning `rowcount` (cast to `CursorResult[object]` for mypy).
  Boundary pinned with non-tautological tests (row exactly at `now`, row a
  microsecond after). `list_all` is unfiltered (includes expired).
- **Cooldown `get` has an extra optional `now` kwarg.**
  `get(guild_id, user_id, *, now: datetime | None = None)` defaults to
  `datetime.now(UTC)`. mypy confirms this is still Protocol-compatible with the
  `get(guild_id, user_id)` shape (verified: `uv run mypy tests/.../test_cooldown_repo.py`
  → Success). The app/service layer should pass a deterministic `now` for
  testability; tests MUST pass `now=` (the live-clock default is flaky against
  fixed fixture timestamps — bit two tests during GREEN, fixed by passing `now=NOW`).
- **Scope-in-DTO upserts.** `cooldown_repo.upsert(cooldown)` and
  `system_state_repo.upsert(state)` carry `guild_id` *inside* the DTO — no
  separate `guild_id` arg (unlike penalty's `upsert(guild_id, penalty)`).
- **System state is single-row-per-guild.** `guild_id` is the whole PK;
  `upsert` = `session.merge` → repeat call is an UPDATE (idempotent, asserted
  `COUNT == 1`). `get` returns `None` for an absent guild ("never reset").
  `list_all()` is **unscoped** (every guild — reset tasks iterate all).

## Next steps (6f)

1. Build `src/friendex/adapters/persistence/migrate_json.py` — one-time
   JSON→SQLite migrator reading `users.json` / `prices.json` / `funds.json` /
   `fund_penalties.json`; idempotent (second run no duplicates). Migration plan
   §"Phase 6" lines 357–367.
2. Add `tests/adapters/persistence/test_migrate_json.py` + small (5–10 record)
   fixtures under `tests/fixtures/json/`. Assert row counts, per-record
   round-trip, second-run idempotency.
3. Re-export the migrator entry point from `__init__.py` if it exposes a class
   (follow the existing repo re-export pattern).
4. Run `scripts/gate.sh baton-runner/br-2026-05-24-phase-6/selfcheck-6f/` →
   require GATE: PASS.

## Open questions / risks

- None blocking. The penalty-store-not-filter call is deliberate and matches
  the plan + the `compute_effective_apy` placement; do not second-guess it in 6f.

## References

- Issues: #2 (live phase status)
- Docs: `docs/04-migration-plan.md` §"Phase 6 — Persistence" (lines 345–380)
- Contracts: `src/friendex/application/interfaces.py`
  (`IPenaltyRepo` L210, `ITradeCooldownRepo` L235, `ISystemStateRepo` L265;
  DTOs `SystemState` L58, `TradeCooldown` L72)
- Code: `src/friendex/adapters/persistence/penalty_repo.py`,
  `cooldown_repo.py`, `system_state_repo.py`
- Tests: `tests/adapters/persistence/test_penalty_repo.py` (8),
  `test_cooldown_repo.py` (12), `test_system_state_repo.py` (8)
- Gate log: `baton-runner/br-2026-05-24-phase-6/selfcheck-6e/` (GATE: PASS)
- Prior batons: 007 / 008 (6d price+fund repos); digests
  `baton-runner/br-2026-05-24-phase-6/digest-phase-6{b,c,d}.md`
