"""Reversibility + no-drift tests for the Alembic baseline migration.

These tests run the hand-checked-in baseline migration (``0001_baseline``)
against a *temporary, file-backed* SQLite database — never the configured
``Settings.database_url`` — and assert two properties the Phase 5 gate cares
about:

#. **Reversibility.** ``alembic upgrade head`` creates every expected table,
   and ``alembic downgrade base`` drops them all again (back to just Alembic's
   own ``alembic_version`` bookkeeping table).
#. **No schema drift.** The schema produced by the migration matches the schema
   produced by ``Base.metadata.create_all`` — i.e. the baseline is a faithful
   snapshot of the ORM. SQLite's dynamic typing makes a *strict*
   ``compare_metadata`` autogenerate diff noisy (it round-trips our
   ``TypeDecorator``-backed columns through generic affinities), so we assert
   the **table set and per-table column-name set** match exactly, which is the
   contract that actually matters for the cutover. See the pass-baton for the
   rationale behind this choice.

The temp DB lives under pytest's ``tmp_path`` and the async ``DATABASE_URL`` is
injected via ``monkeypatch`` so the Alembic ``env.py`` (which reads
``os.environ["DATABASE_URL"]``) targets it. Alembic's commands are synchronous;
under our async driver ``env.py`` drives the engine through ``asyncio.run`` —
calling ``command.upgrade`` from a plain (non-async) test is therefore correct
and must NOT be awaited.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

# ``friendex.adapters.persistence.orm`` is imported for its side effect: each ORM
# class self-registers on ``Base.metadata`` at definition time. The baseline
# migration and ``create_all`` both read that registry, so the import must run
# before either is exercised. ``noqa: F401`` marks it as intentionally unused.
import friendex.adapters.persistence.orm  # noqa: F401
from friendex.adapters.persistence.db import Base

if TYPE_CHECKING:
    import pytest

# Repo root = three parents up from this file
# (tests/adapters/persistence/test_migrations.py -> repo root).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"

# The full Option B table set (ADR-0001 per-guild schema) the baseline owns.
_EXPECTED_TABLES = frozenset(
    {
        "users",
        "long_positions",
        "short_positions",
        "activity_buckets",
        "voice_unique_channels",
        "stocks",
        "price_history",
        "hedge_funds",
        "fund_investors",
        "fund_penalties",
        "system_state",
        "trade_cooldowns",
    }
)


def _make_config(database_url: str) -> Config:
    """Build an Alembic ``Config`` rooted at the repo's ``alembic.ini``.

    The ``sqlalchemy.url`` is set explicitly too so the config is
    self-contained, but ``env.py`` ultimately reads ``DATABASE_URL`` from the
    environment (set by the caller via ``monkeypatch``).
    """
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _table_names(database_url: str) -> set[str]:
    """Return the user table names present in ``database_url``.

    Uses a *sync* SQLAlchemy inspector against the equivalent ``sqlite``
    (non-async) URL so we can introspect without an event loop. Alembic's own
    ``alembic_version`` table is excluded.
    """
    sync_url = database_url.replace("sqlite+aiosqlite", "sqlite")
    engine = sa.create_engine(sync_url)
    try:
        inspector = sa.inspect(engine)
        return {
            name for name in inspector.get_table_names() if name != "alembic_version"
        }
    finally:
        engine.dispose()


def _columns_by_table(database_url: str) -> dict[str, set[str]]:
    """Return ``{table: {column, ...}}`` for every user table in the DB."""
    sync_url = database_url.replace("sqlite+aiosqlite", "sqlite")
    engine = sa.create_engine(sync_url)
    try:
        inspector = sa.inspect(engine)
        return {
            name: {col["name"] for col in inspector.get_columns(name)}
            for name in inspector.get_table_names()
            if name != "alembic_version"
        }
    finally:
        engine.dispose()


def test_baseline_upgrade_creates_all_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #1 (upgrade) — ``upgrade head`` creates every expected table."""
    # Arrange
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    config = _make_config(db_url)

    # Act
    command.upgrade(config, "head")

    # Assert — every Option B table exists after the baseline runs.
    assert _table_names(db_url) == set(_EXPECTED_TABLES)


def test_baseline_downgrade_drops_all_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #1 (downgrade) — ``downgrade base`` removes every table.

    Proves the baseline is fully reversible: after going to ``head`` and back to
    ``base`` no domain tables remain (only Alembic's ``alembic_version``).
    """
    # Arrange
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    config = _make_config(db_url)

    # Act
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    # Assert — no domain tables survive the downgrade.
    assert _table_names(db_url) == set()


def test_baseline_matches_orm_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #2 (no drift) — migrated schema == ``create_all`` schema.

    Builds two databases from the same source of truth — one via the Alembic
    baseline, one via ``Base.metadata.create_all`` — and asserts their table
    sets and per-table column-name sets are identical. This is the practical
    no-diff guarantee for SQLite (a strict ``compare_metadata`` is too noisy for
    our ``TypeDecorator`` columns; see module docstring).
    """
    # Arrange — DB #1: built by the Alembic baseline migration.
    migrated_url = f"sqlite+aiosqlite:///{tmp_path / 'migrated.db'}"
    monkeypatch.setenv("DATABASE_URL", migrated_url)
    command.upgrade(_make_config(migrated_url), "head")

    # Arrange — DB #2: built directly from the ORM metadata.
    created_path = tmp_path / "created.db"
    created_engine = sa.create_engine(f"sqlite:///{created_path}")
    try:
        Base.metadata.create_all(created_engine)
    finally:
        created_engine.dispose()
    created_url = f"sqlite+aiosqlite:///{created_path}"

    # Act
    migrated_tables = _table_names(migrated_url)
    created_tables = _table_names(created_url)

    # Assert — same tables, and the expected set, with no extras or omissions.
    assert migrated_tables == created_tables
    assert migrated_tables == set(_EXPECTED_TABLES)

    # Assert — identical columns per table (catches a column added to the ORM
    # but forgotten in the baseline, or vice versa).
    assert _columns_by_table(migrated_url) == _columns_by_table(created_url)
