# Pass-Baton: FK enforcement decision closed — ADR-0002 written, PR #36 open

**Date:** 2026-05-24
**Scope:** phase-5-orm
**Branch:** docs/adr-0002-sqlite-fk
**Worktree:** /home/alex/Friendex/.claude/worktrees/adr-0002-fk
**HEAD:** 3955eaa docs(adr): ADR-0002 SQLite FK enforcement via PRAGMA foreign_keys=ON

## Where things stand

The open question from the Phase 5 review ("decide SQLite `PRAGMA foreign_keys=ON`
vs. app-level cascade in the repositories") is now resolved. The decision is
**`PRAGMA foreign_keys=ON`** (Option A) — wire a `@event.listens_for(engine.sync_engine,
"connect")` listener in `adapters/persistence/db.py` and add `ON DELETE CASCADE` to
child FK columns via a Phase 6 Alembic migration. PR #36 documents this in three
places: the new ADR-0002, the ADR README index, and the Phase 6 migration plan entry
(which also folds in the two Phase 5 carry-forward items). PR #36 is awaiting merge;
no code changes are in flight yet.

## Next steps

1. Merge PR #36 (`docs/adr-0002-sqlite-fk` → `main`).
2. Start Phase 6 (`feat/phase-6-repos`). The Phase 6 plan entry in
   `docs/04-migration-plan.md` is now complete — read it before implementing.
   First commit in that phase: `feat(persistence): PRAGMA foreign_keys=ON +
   ON DELETE CASCADE migration` (wire the PRAGMA listener in `db.py` and ship
   the Alembic migration adding `ON DELETE CASCADE` to all child FKs).
3. In the same Phase 6 pass, resolve the two Phase 5 carry-forward items:
   - Decimal quantisation assertion (one `as_tuple().exponent` check per
     `DecimalText` column + a float-inexact fixture).
   - Real column-drift test once the first hand-authored incremental migration lands.

## Open questions / risks

- None remaining for the FK decision itself.
- Phase 6 introduces the first real Alembic autogenerate migration — verify that
  `alembic revision --autogenerate` detects the `ON DELETE CASCADE` changes
  correctly against the SQLite dialect (SQLite's limited `ALTER TABLE` support
  means Alembic may need `render_as_batch=True` in `env.py`).

## References

- PRs: #36 (docs only, no phase tick needed)
- Issues: #2 (Phase 6 box is next)
- ADR: `docs/adr/0002-sqlite-fk-enforcement.md`
- Plan: `docs/04-migration-plan.md` §Phase 6
- Prior review baton: `baton-pass/phase-5-orm/002-2026-05-23-phase-5-review.md`
