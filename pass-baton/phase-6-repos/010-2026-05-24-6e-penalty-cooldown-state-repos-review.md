# Pass-Baton: 6e review — penalty / cooldown / system-state repos (VERDICT CLEAN)

**Date:** 2026-05-24
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** 9387225 feat(phase-6): SqlPenaltyRepository + SqlTradeCooldownRepository + SqlSystemStateRepository

## Where things stand

Independent review of sub-unit **6e** (diff `f4aabd5..HEAD`) is complete.
**VERDICT: CLEAN** — gate green, no CRITICAL/HIGH/MEDIUM, all four ACs met.
Three SQLAlchemy adapters landed (`penalty_repo.py`, `cooldown_repo.py`,
`system_state_repo.py`), all re-exported from `persistence/__init__.py`, with
28 passing tests (8/12/8). No new dependencies (pyproject/uv.lock untouched).
6e is closeable; only **6f** (JSON→SQLite migrator) remains in Phase 6.

## Verification (actual output)

- `scripts/gate.sh .../gate-phase-6e-iter-1/` → **GATE: PASS** (exit 0):
  pytest PASS · ruff-check PASS · ruff-format PASS · mypy PASS.
- `uv run pytest tests/adapters/persistence/test_{penalty,cooldown,system_state}_repo.py`
  → **28 passed in 0.56s**.
- mypy structural conformance proven: each test holds a typed
  `conforming: I<X>Repo = repo` assignment (penalty L100, cooldown L94,
  system-state L96); gate mypy green ⇒ all three Protocols satisfied by shape,
  including cooldown's extra optional `*, now` kwarg (Protocol-compatible).

## AC-by-AC findings

- **AC1 (penalty CRUD + expired):** met. `get`/`upsert`/`delete`/`list_all`
  match `IPenaltyRepo` exactly. "Plain store, not a filter" verified:
  `test_get_returns_expired_penalty_unfiltered` + `test_list_all_includes_live_and_expired`.
  Round-trip asserts `Decimal` scale (`_same_scale`) + tz-aware UTC.
- **AC2 (cooldown TTL):** met and **non-tautological**. `get` uses strict
  `expires_at > cutoff`; `purge_expired` uses `expires_at <= now` — exactly
  complementary (a row is hidden by `get` iff deleted by `purge`). The exact-
  boundary tests pin the operator: a row at `NOW` is expired, a row 1µs before/
  after flips the outcome. Confirmed by mutation: forcing `get` to `>=` makes the
  exact-boundary row visible ⇒ `test_get_treats_exact_boundary_as_expired` fails.
  Bulk purge is one `DELETE` (no loop), returns `rowcount`.
- **AC3 (system-state single-row):** met. `guild_id` is the whole PK;
  `upsert` = `session.merge` ⇒ UPDATE on repeat. `test_upsert_is_idempotent_single_row`
  asserts both updated value AND `COUNT == 1` via direct DB count — catches a
  duplicate-row bug. `list_all()` unscoped; `get` → `None` for absent guild.
- **AC4 (TDD/UTC/lint/N+1):** met. UTC-aware assertions throughout
  (`.utcoffset() == timedelta(0)`). No N+1 (every read one `select`; deletes
  single statements). ruff/mypy clean.

## Notes (NOTE-level, no action required)

- **RED not persisted as an artifact.** Baton 009 + `log.md` claim "3
  ModuleNotFoundErrors RED→GREEN"; only the GREEN gate log lives in
  `selfcheck-6e/`. This matches the established 6c/6d convention (no RED dir),
  so it is consistent, not a 6e regression. The RED claim is plausible (test
  imports of not-yet-existing modules raise ModuleNotFoundError).
- **`session.merge` is read-then-write (TOCTOU).** A concurrent same-PK INSERT
  could in theory race; not exploitable under SQLite + single-writer bot.
  `INSERT ... ON CONFLICT DO UPDATE` would harden it. Phase-wide convention
  (6c/6d also use `merge`), so out of scope for 6e.
- **TTL TEXT-comparison correctness:** `UtcDateTime` stores
  `astimezone(UTC).isoformat()`; SQLite compares lexicographically. Verified
  sound for all boundary values (isoformat zero-pads µs to 6 digits; `+` (0x2B)
  < any digit, so whole-second values sort before fractional successors).

## Next steps

1. Close 6e; proceed to **6f** (`migrate_json.py`) per baton 009 §"Next steps".
2. 6f must honor these table conventions when writing rows — see
   `baton-runner/br-2026-05-24-phase-6/digest-phase-6e.md`.

## References

- Issues: #2 (live phase status)
- Reviewed diff: `f4aabd5..HEAD` (commit 9387225)
- Work baton: `pass-baton/phase-6-repos/009-2026-05-24-6e-penalty-cooldown-state-repos.md`
- Gate log: `baton-runner/br-2026-05-24-phase-6/gate-phase-6e-iter-1/` (GATE: PASS)
- Digest: `baton-runner/br-2026-05-24-phase-6/digest-phase-6e.md`
- Code: `src/friendex/adapters/persistence/{penalty,cooldown,system_state}_repo.py`
- Contracts: `src/friendex/application/interfaces.py`
  (`IPenaltyRepo` L210, `ITradeCooldownRepo` L235, `ISystemStateRepo` L265)
