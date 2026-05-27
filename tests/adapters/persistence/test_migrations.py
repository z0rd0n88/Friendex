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
   contract that actually matters for the cutover. See the baton-pass for the
   rationale behind this choice.

The temp DB lives under pytest's ``tmp_path`` and the async ``DATABASE_URL`` is
injected via ``monkeypatch`` so the Alembic ``env.py`` (which reads
``os.environ["DATABASE_URL"]``) targets it. Alembic's commands are synchronous;
under our async driver ``env.py`` drives the engine through ``asyncio.run`` —
calling ``command.upgrade`` from a plain (non-async) test is therefore correct
and must NOT be awaited.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

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

# The six child FK constraints carrying ``ON DELETE CASCADE`` after 0002:
# long_positions→users, short_positions→users, activity_buckets→users,
# voice_unique_channels→activity_buckets, price_history→stocks,
# fund_investors→hedge_funds. (``fund_penalties`` / ``system_state`` /
# ``trade_cooldowns`` declare no FK and are not children — see orm.py.)
_EXPECTED_CASCADE_FK_COUNT = 6

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

    # Assert — identical columns per table. NOTE: this column-level check is
    # *tautological by design* for the current baseline. ``upgrade()`` is
    # ``Base.metadata.create_all`` (see ``0001_baseline``) and the "created" DB
    # is also ``create_all``, so both sides derive from the same
    # ``Base.metadata`` — adding/removing an ORM column moves both sides together
    # and this assertion can never go RED for an ORM<->baseline mismatch today.
    # It only becomes load-bearing in Phase 6+, when the first hand-authored
    # incremental migration emits DDL independent of ``create_all``. The
    # table-set assertions above ARE real (renaming a table fails them); they are
    # the baseline's actual drift guard.
    assert _columns_by_table(migrated_url) == _columns_by_table(created_url)


def _cascade_fk_count(database_url: str) -> int:
    """Count child FKs whose ``ON DELETE`` action is ``CASCADE``.

    Uses SQLite's ``PRAGMA foreign_key_list`` per table via the sync inspector.
    SQLAlchemy's reflected ``on_delete`` lives in each FK's ``options`` dict.
    """
    sync_url = database_url.replace("sqlite+aiosqlite", "sqlite")
    engine = sa.create_engine(sync_url)
    try:
        inspector = sa.inspect(engine)
        count = 0
        for name in inspector.get_table_names():
            if name == "alembic_version":
                continue
            for fk in inspector.get_foreign_keys(name):
                options = fk.get("options") or {}
                if options.get("ondelete", "").upper() == "CASCADE":
                    count += 1
        return count
    finally:
        engine.dispose()


def test_0002_round_trips_head_base_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #2 (reversibility) — head→base→head applies cleanly.

    Exercises the batch-mode 0002 migration's ``upgrade`` and ``downgrade`` in
    both directions against a temp DB; a broken batch recreate (or an
    irreversible migration) would raise here.
    """
    # Arrange
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    config = _make_config(db_url)

    # Act — full forward, full back, full forward again.
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    # Assert — schema survives the round trip intact.
    assert _table_names(db_url) == set(_EXPECTED_TABLES)


def test_0002_upgrade_sets_cascade_on_child_fks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #2 — after upgrade, every child FK carries ON DELETE CASCADE.

    The downgrade must strip the cascade; the upgrade must restore it. Proves
    0002 is a *real* incremental migration whose two directions differ, not a
    no-op wrapper.
    """
    # Arrange
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    config = _make_config(db_url)

    # Act / Assert — at head, all five child FKs cascade.
    command.upgrade(config, "head")
    assert _cascade_fk_count(db_url) == _EXPECTED_CASCADE_FK_COUNT

    # Act / Assert — downgrade to the baseline strips every cascade.
    command.downgrade(config, "0001_baseline")
    assert _cascade_fk_count(db_url) == 0


async def test_parent_delete_cascades_to_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #2 — with FK enforcement on, deleting a parent cascades.

    Builds the schema via the migration (to head), then through the
    PRAGMA-enabled async engine inserts a parent ``users`` row plus child
    ``long_positions`` / ``short_positions`` rows, deletes the parent, and
    asserts the children are gone — proving the DB-level cascade fires.
    """
    from datetime import UTC, datetime
    from decimal import Decimal

    from sqlalchemy import delete, func, select

    from friendex.adapters.persistence.db import build_engine, build_sessionmaker
    from friendex.adapters.persistence.orm import (
        LongPositionORM,
        ShortPositionORM,
        UserORM,
    )
    from friendex.domain.models import (
        ActivityBucket,
        DailyProgress,
        LongPosition,
        ShortPosition,
        UserAccount,
    )

    # Arrange — migrate a temp file DB to head, then open the prod engine on it.
    # Alembic's online ``env.py`` drives the async engine via ``asyncio.run``,
    # which cannot nest inside pytest-asyncio's running loop; run the upgrade in
    # a worker thread so it owns its own event loop.
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'cascade.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    config = _make_config(db_url)
    await asyncio.to_thread(command.upgrade, config, "head")

    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    guild = "g-1"
    account = UserAccount(
        user_id="u-1",
        cash_balance=Decimal("10000.00"),
        net_worth=Decimal("10000.00"),
        month_start_net_worth=Decimal("10000.00"),
        long_positions={},
        short_positions={},
        today=ActivityBucket(bucket_start=now),
        week=ActivityBucket(bucket_start=now),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=now,
    )
    long_pos = LongPosition(target_user_id="t-1", shares=5, avg_entry=Decimal("80.00"))
    short_pos = ShortPosition(
        target_user_id="t-2",
        shares=2,
        entry_price=Decimal("90.00"),
        locked_cash=Decimal("180.00"),
        locked_fund=Decimal("0.00"),
        created_at=now,
    )

    engine = build_engine(db_url)
    try:
        maker = build_sessionmaker(engine)
        async with maker() as session:
            # Insert the parent first (ADR-0002: FK enforcement is on, so the
            # parent row must exist before its children).
            session.add(UserORM.from_domain(guild, account))
            await session.flush()
            session.add(LongPositionORM.from_domain(guild, "u-1", long_pos))
            session.add(ShortPositionORM.from_domain(guild, "u-1", short_pos))
            await session.commit()

        # Act — delete the parent user row.
        async with maker() as session:
            await session.execute(
                delete(UserORM).where(
                    UserORM.guild_id == guild, UserORM.user_id == "u-1"
                )
            )
            await session.commit()

        # Assert — children cascaded away with the parent.
        async with maker() as session:
            longs = (
                await session.execute(select(func.count()).select_from(LongPositionORM))
            ).scalar_one()
            shorts = (
                await session.execute(
                    select(func.count()).select_from(ShortPositionORM)
                )
            ).scalar_one()
        assert longs == 0
        assert shorts == 0
    finally:
        await engine.dispose()


def test_no_drift_after_head_compare_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance #4 (real drift test) — ``compare_metadata`` finds no diffs.

    Now that 0002 is a hand-authored incremental migration emitting DDL
    independent of ``create_all``, this is no longer tautological: upgrade a
    temp DB to head, then ask Alembic's autogenerate engine to diff the live
    schema against ``Base.metadata``. Any drift between the migration chain and
    the ORM (a missed column, a forgotten table, a stale type) surfaces as a
    non-empty diff and fails the test (verified RED: dropping a table from the
    migrated schema yields an ``add_table`` diff).

    NB: ``compare_metadata`` does *not* reflect/compare SQLite FK ``ondelete``
    actions, so the cascade behaviour added by 0002 is pinned separately by
    ``test_0002_upgrade_sets_cascade_on_child_fks`` (PRAGMA-list reflection) and
    ``test_parent_delete_cascades_to_children`` (end-to-end behaviour).
    """
    # Arrange — migrate a temp DB to head.
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'drift.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    command.upgrade(_make_config(db_url), "head")

    # Act — diff the migrated schema against the ORM metadata.
    sync_url = db_url.replace("sqlite+aiosqlite", "sqlite")
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diffs = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    # Assert — the migration chain reproduces the ORM exactly.
    assert diffs == []
