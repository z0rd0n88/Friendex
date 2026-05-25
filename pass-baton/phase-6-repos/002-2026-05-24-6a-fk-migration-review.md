# Pass-Baton: Sub-unit 6a FK-migration — independent review (VERDICT CLEAN)

**Date:** 2026-05-24
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** b1692b6 feat(phase-6): PRAGMA foreign_keys=ON + ON DELETE CASCADE migration
**Reviewer:** independent (did not implement); reviewed `git diff f7aa6f1..HEAD`

## Where things stand

Sub-unit **6a-fk-migration** is reviewed and **VERDICT: CLEAN**. The
deterministic gate (`scripts/gate.sh`) prints **GATE: PASS** — pytest 271
passed, ruff check + format clean, mypy clean. All four acceptance criteria are
genuinely met and backed by RED-able (non-tautological) tests. No CRITICAL or
HIGH findings. Code-review and security-review skills both came back clean: the
only raw SQL is the fixed literal `PRAGMA foreign_keys=ON`; the migration uses
SQLAlchemy/Alembic constructs (no string-concatenated DDL); no secrets, no new
deps, no production logic weakened. The 6 "orphan-child" test fixes are
test-data-only (parents flushed before children via new `_minimal_user()` /
`_add_parent()` helpers) — `orm.py` was **not** touched (verified) and `db.py`
is purely additive (PRAGMA listener wired into `build_engine`, dialect-guarded,
cursor closed in `finally`). One MEDIUM and two LOW notes below; none block.

## Findings by severity

### CRITICAL — none.
### HIGH — none.

### MEDIUM

- **M1 — `0002` upgrade is a redundant no-op against a fresh DB; ADR-0002
  narrative is out of sync with the tree.**
  `src/friendex/adapters/persistence/orm.py` already declares
  `ondelete="CASCADE"` on all 6 child FKs (present since the setup commit
  f7aa6f1 — `git show f7aa6f1:.../orm.py | grep -c CASCADE` → 6), and
  `alembic/versions/0001_baseline.py` builds the schema via
  `Base.metadata.create_all`. I confirmed empirically that **after `upgrade
  0001_baseline` alone the cascade FK count is already 6** — so `0002.upgrade`
  changes nothing for any fresh deployment (the only scenario; greenfield, no
  prod DB). `0002` only does real work starting from a downgraded state.
  Meanwhile ADR-0002 (`docs/adr/0002-sqlite-fk-enforcement.md`) Context says
  "Phase 5 explicitly deferred the enforcement strategy to Phase 6" and
  Consequences says "Alembic migration (Phase 6) adds ON DELETE CASCADE to all
  child FK columns" — implying the baseline shipped *without* cascade and 0002
  introduces it. That is not what the tree does.
  *Impact:* none functional — end-state (fresh deploy) correctly enforces
  cascade; defense-in-depth intact. It is process/doc drift + a migration that
  reads as load-bearing but is idempotent on the happy path.
  *Fix (next sub-unit / cleanup, not a blocker):* pick one of —
  (a) leave as-is and add a one-line note to ADR-0002 Consequences that the
  baseline already carries CASCADE (ORM-declared) and 0002 exists to make the
  action explicit/reversible in the migration chain; or
  (b) if the ADR's intent (baseline = plain FK, 0002 = adds cascade) is the
  desired history, that ship has sailed for 0001 — do NOT rewrite the baseline;
  just reconcile the ADR wording. Recommend (a).

### LOW

- **L1 — `test_0002_upgrade_sets_cascade_on_child_fks` upgrade arm is partly
  tautological.** `tests/adapters/persistence/test_migrations.py:256` asserts
  count==6 after `upgrade head`, but 0001 alone already yields 6 (see M1), so
  this arm can't distinguish "0002 ran" from "0001 ran." The **downgrade arm**
  (`downgrade 0001_baseline` → count==0, line 275-276) *is* load-bearing and
  RED-able, which is the baton's cited RED (`assert 6 == 0`). Acceptable, but
  worth knowing the upgrade assertion isn't the guard it appears to be.
  *Fix (optional):* none required; the downgrade arm + the end-to-end
  `test_parent_delete_cascades_to_children` together pin real behavior.

- **L2 — `test_baseline_matches_orm_metadata` column-level assert is
  self-documented as tautological** (`test_migrations.py:198-207`). The author
  flagged it inline (both sides derive from `Base.metadata`). The newer
  `test_no_drift_after_head_compare_metadata` (AC4) is the real drift guard and
  is genuinely RED-able (reflected live schema vs `Base.metadata` are different
  code paths). No action needed — noted for completeness.

## AC verification (all genuine, RED-able)

1. **AC1 PRAGMA on** — `db.py::_enable_sqlite_foreign_keys` listens on
   `engine.sync_engine` "connect", dialect-guarded; `test_db.py::
   test_build_engine_enables_foreign_keys` asserts `PRAGMA foreign_keys == 1`
   (SQLite default 0 → RED if listener dropped). ✓
2. **AC2 CASCADE on every child FK** — all 6 ORM child FKs covered by
   `0002._child_tables`; `fund_penalties`/`system_state`/`trade_cooldowns`
   correctly have NO FK (not children) so are rightly excluded. `env.py` sets
   `render_as_batch=True` on BOTH configure calls. Reversible (head→base→head
   test) + end-to-end parent-delete cascade test (children gone). ✓
   (Redundancy caveat → M1/L1.)
3. **AC3 Decimal quantisation** — per-column `.as_tuple().exponent ==` asserts
   on all DecimalText columns of ShortPosition + a 4-dp rate column. Fixture
   `_FLOAT_INEXACT = Decimal("12345.67")` **verified genuinely float-inexact**:
   `Decimal(float("12345.67")) == 12345.670000000000072...` (≠ exact) and
   exponent shifts -2 → -39, so a Numeric/float regression fails both the value
   and the exponent asserts. ✓
4. **AC4 compare_metadata drift** — `test_no_drift_after_head_compare_metadata`
   diffs reflected live schema vs `Base.metadata`; non-tautological (distinct
   code paths), baton's RED (drop-a-table → add_table diff) is credible. ✓

## Dependencies

**None added** — `pyproject.toml` / `uv.lock` unchanged in the diff. (Baton's
"no new dependencies" claim verified.)

## Next steps

1. Manager: this sub-unit is mergeable. Address M1 doc reconciliation as a
   small follow-up (recommend option (a): one-line ADR-0002 note), not a
   blocker for 6a.
2. 6b+: repository interfaces + Sql*Repository impls; repo `delete` methods
   need NO explicit child deletion (DB cascade handles it, ADR-0002).
3. **Convention the next sub-units MUST honor:** with FK enforcement ON, all
   tests/fixtures insert (and flush) parents before children — use the existing
   `_minimal_user()` / `_add_parent()` helpers pattern in `test_orm.py`.

## References

- Implementation baton: `pass-baton/phase-6-repos/001-2026-05-24-6a-fk-migration-complete.md`
- ADR: `docs/adr/0002-sqlite-fk-enforcement.md` (see M1 re: narrative drift)
- Gate logs: `baton-runner/br-2026-05-24-phase-6/gate-phase-6a-iter-1/`
- Phase-exit digest: `baton-runner/br-2026-05-24-phase-6/digest-phase-6a.md`
- Code: `src/friendex/adapters/persistence/db.py:63`,
  `alembic/versions/0002_fk_cascade.py:160`,
  `tests/adapters/persistence/test_migrations.py:256`
