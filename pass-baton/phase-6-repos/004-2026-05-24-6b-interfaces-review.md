# Pass-Baton: Sub-unit 6b review — repo Protocol interfaces (VERDICT CLEAN)

**Date:** 2026-05-24
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** 40726d3 feat(phase-6): repository protocol interfaces

## Where things stand

Independent review of sub-unit 6b (`src/friendex/application/interfaces.py` +
`tests/application/test_interfaces.py`, diff `8394a0d..HEAD`) is complete.
**VERDICT: CLEAN** — gate green, all four acceptance criteria met, no
CRITICAL/HIGH findings. Only two LOW notes, both non-blocking. The phase-exit
digest is written to `baton-runner/br-2026-05-24-phase-6/digest-phase-6b.md`.
Ready for sub-units 6c–6f (the Sql repo implementations).

## Verification (actual output, this session)

- Gate `scripts/gate.sh baton-runner/br-2026-05-24-phase-6/gate-phase-6b-iter-1/`
  → **GATE: PASS** (pytest, ruff-check, ruff-format, mypy all PASS; exit 0).
- `pytest tests/application/test_interfaces.py` → **18 passed**.
- Negative mypy probe (a `_BadUserRepo` omitting `list_active_in_last`,
  assigned to `IUserRepo`) → mypy **error: missing protocol member
  list_active_in_last**. Conformance is real, not tautological — removing a
  Protocol member genuinely breaks the impl gate.
- `grep -nE 'friendex\.adapters' interfaces.py` → only line 13 (docstring
  prose). No actual adapters import. The AST-based test (parses imports, not
  raw text) correctly ignores the prose mention.
- No dependency changes (`pyproject.toml` / `uv.lock` untouched in diff).

## Acceptance criteria — all met

1. **Six Protocols present:** IUserRepo, IPriceRepo, IFundRepo, IPenaltyRepo,
   ITradeCooldownRepo, ISystemStateRepo. ✓
2. **Method surface complete** vs `docs/04-migration-plan.md` §Phase 6 and
   `domain/models.py`: common CRUD on each + IPriceRepo.append_history /
   get_history(*, since) / prune_history_older_than, IUserRepo.list_active_in_last,
   IFundRepo.ensure_events_wallet, ITradeCooldownRepo.purge_expired,
   ISystemStateRepo unscoped list_all. All async, hinted with **domain models**
   (never ORM). Cross-checked field names: `get_history(since)` filters
   `PricePoint.timestamp`, which the ORM maps to `PriceHistoryORM.recorded_at`
   — consistent. ✓
3. **Architecture invariant:** imports only stdlib + (TYPE_CHECKING) domain
   models + datetime; zero adapters import (grep + AST test). ✓
4. **mypy-clean, ruff-clean** (gate). ✓

## DTO decision (the special-scrutiny item) — SOUND, no finding

`SystemState` and `TradeCooldown` (both `@dataclass(frozen=True)`) hosted in
`interfaces.py`. `SystemStateORM` / `TradeCooldownORM` have **no domain mirror**
(confirmed in `orm.py:467-513`) and the interfaces must not import the ORM, so a
typed payload has to live in the application layer. Verdict: architecturally
acceptable. They are pure adapter-bookkeeping (TTL sweep state, reset
timestamps), not game-domain concepts, so they correctly do **not** belong in
`domain/models.py` — putting them there would pollute the domain with
persistence mechanics. They are plain immutable value objects consistent with
project style: frozen, datetimes typed tz-aware UTC (`datetime`), no float
money fields (neither carries money). **Not flagged even as MEDIUM** — hosting
them in the application layer is the right call given the no-ORM-import rule.

## Findings (both LOW, non-blocking)

- **LOW** — `test_interfaces.py:204,241,263,294,316`: the five non-IUserRepo
  fakes only `assert repo is not None` (smoke check). Their real value is the
  typed annotation that only mypy enforces. Acceptable: documented in the module
  docstring and the negative probe proves mypy is a genuine gate. Optional
  follow-up: add a round-trip for IPriceRepo append/get_history/prune if 6c
  wants symmetry. No fix required.
- **LOW** — `interfaces.py:164`: `get_history` doc cites "§Open-Q9", an internal
  marker that may be hard to locate. Cosmetic. No fix required.

## Next steps

1. Proceed to 6c–6f: implement `SqlUserRepository` etc. in
   `src/friendex/adapters/persistence/{user,price,fund,penalty,cooldown,system_state}_repo.py`.
   Structural conformance only — **no inheritance** from the Protocols (mypy
   verifies by shape; negative check confirmed it rejects a non-matching impl).
2. Honour the split upsert signatures: User/Price/Fund/Penalty take
   `upsert(guild_id, <obj>)`; Cooldown/SystemState take `upsert(<dto>)` (scope
   inside the frozen DTO).
3. **Security obligation for 6c–6f (not a defect here):** the SQL behind
   `prune_history_older_than`, `purge_expired`, `get_history(since)` must use
   parameterized queries — never f-string interpolation of the datetime/cutoff.
4. Resolve the two Phase-5 carry-forwards (Decimal-quantisation ORM assertions +
   first migration drift test) inside the Sql-repo / migrator sub-units.

## Open questions / risks

- None blocking.

## References

- Reviewed diff: `git diff 8394a0d..HEAD`
- Implementation baton: `pass-baton/phase-6-repos/003-2026-05-24-6b-repo-interfaces.md`
- Digest: `baton-runner/br-2026-05-24-phase-6/digest-phase-6b.md`
- Gate log: `baton-runner/br-2026-05-24-phase-6/gate-phase-6b-iter-1/`
- Code: `src/friendex/application/interfaces.py`; `tests/application/test_interfaces.py`
- Plan: `docs/04-migration-plan.md` §"Phase 6 — Persistence" (345–388)
- ORM (DTO no-mirror evidence): `src/friendex/adapters/persistence/orm.py:467-513`
- Issue: #2 (live phase status)
