# ADR-0002 — SQLite foreign-key enforcement via PRAGMA

| Field | Value |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-05-25 |
| **Deciders** | Alex Fielder |
| **Supersedes** | — |
| **Superseded by** | — |

## Context

Phase 5 (ORM + Alembic baseline) defined twelve tables with composite foreign keys
(`(guild_id, …)` everywhere, per ADR-0001). SQLite accepts `FOREIGN KEY` DDL but
silently ignores violations unless `PRAGMA foreign_keys = ON` is issued on each
connection. Phase 5 explicitly deferred the enforcement strategy to Phase 6.

Two options were on the table:

**Option A — `PRAGMA foreign_keys = ON` on each engine connection.**
SQLite enforces referential integrity itself. `ON DELETE CASCADE` annotations on FK
columns fire automatically. A single event listener on the SQLAlchemy engine's
`connect` event covers every connection for the lifetime of the process.

**Option B — App-level cascade in each repository.**
Each repository `delete` method explicitly deletes child rows before the parent.
No PRAGMA needed; fully portable to other databases.

## Decision

**Use `PRAGMA foreign_keys = ON` (Option A).**

Wire it in `adapters/persistence/db.py` via a SQLAlchemy synchronous `connect`
event listener:

```python
from sqlalchemy import event

@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
```

Add `ON DELETE CASCADE` to child FK columns in a Phase 6 Alembic migration.

## Rationale

1. **The schema already declares the FKs.** Enforcement makes those declarations do
   real work instead of serving as documentation only.

2. **Phase 6 introduces the repository layer.** Establishing the enforcement strategy
   before any delete logic is written avoids retrofitting every repository later.

3. **Defense-in-depth for game invariants.** A short position that loses its parent
   `UserMarket` without cascading leaves locked cash in limbo — an unrecoverable
   game-state corruption. DB-level enforcement makes this impossible regardless of
   which code path triggered the delete (repository, bulk delete, migration,
   admin script).

4. **App-level cascade spreads concern.** Every repository owning a parent entity
   would need to know about every current and future child table. Adding a new child
   table in a later phase requires auditing all parent repositories. The PRAGMA
   approach has a single declaration point.

5. **The single-engine constraint is already met.** All DB access goes through the
   SQLAlchemy async engine; there are no raw `aiosqlite` escape hatches. The
   per-connection PRAGMA fires reliably for every connection the engine opens.

6. **SQLAlchemy ORM cascade coexists correctly.** `relationship(cascade="all,
   delete-orphan")` continues to work for ORM-loaded objects; the PRAGMA is the
   backstop for bulk deletes (`session.execute(delete(...))`) and anything that
   bypasses the ORM.

## Consequences

- **`db.py`** gains the `event.listens_for` block in Phase 6.
- **Alembic migration (Phase 6)** adds `ON DELETE CASCADE` to all child FK columns.
- **Repository `delete` methods** do not need explicit child-deletion logic; the DB
  handles it.
- **Test fixtures** that insert child rows without a parent will raise
  `IntegrityError` — test setup must insert parents first (correct ordering, already
  the natural pattern).
- **Portability note:** `PRAGMA foreign_keys` is SQLite-specific. PostgreSQL enforces
  FKs by default; if the backend ever changes, the listener becomes a no-op and the
  `ON DELETE CASCADE` annotations remain correct.
