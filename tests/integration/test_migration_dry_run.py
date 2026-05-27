"""Integration: --dry-run, --report, and post-migration orphan-warning behaviour.

Phase 15b verification. These tests exercise the three CLI features the
migrator gained on top of the Phase 15a end-to-end path:

* ``--dry-run`` — the migration runs end-to-end (every fixture is parsed,
  every row would-be-written) but nothing is persisted into the target.
* ``--report`` — per-table row counts are written to stdout in a stable,
  sortable ``<table>: <count>`` form.
* Orphan consistency check — every ``LongPosition.target_user_id`` /
  ``ShortPosition.target_user_id`` that lacks a matching ``UserAccount`` is
  reported via ``logger.warning(...)``; the migrator still exits ``0``.

The dry-run / report tests reuse the Phase 15a realistic fixture set
(``tests/fixtures/json/realistic/``) so the per-table counts are the same
shape the Phase 15a round-trip test pins. The orphan test uses a tiny
dedicated fixture set (``tests/fixtures/json/realistic_orphan/``) — the
Phase 15a digest explicitly forbids mutating the realistic fixtures to
introduce orphans.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from friendex.adapters.persistence.db import (
    Base,
    build_engine,
    build_sessionmaker,
)
from friendex.adapters.persistence.migrate_json_to_sqlite import main, migrate
from friendex.adapters.persistence.user_repo import SqlUserRepository

if TYPE_CHECKING:
    import pytest

_GUILD_ID = "999"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "json" / "realistic"
_ORPHAN_FIXTURES = (
    Path(__file__).resolve().parents[1] / "fixtures" / "json" / "realistic_orphan"
)

# Tables the migrator's return dict (and therefore the --report output) carries.
_EXPECTED_TABLES = (
    "fund_investors",
    "fund_penalties",
    "hedge_funds",
    "long_positions",
    "price_history",
    "short_positions",
    "stocks",
    "users",
)


def _target_url(tmp_path: Path) -> str:
    """Return an aiosqlite URL pointing at a fresh on-disk DB under ``tmp_path``.

    Using a file (rather than ``:memory:``) lets the test re-open the same
    database after :func:`main` returns to assert that --dry-run wrote
    nothing.
    """
    return f"sqlite+aiosqlite:///{tmp_path / 'target.db'}"


# ---------------------------------------------------------------------------
# B1 — --dry-run leaves the target database empty
# ---------------------------------------------------------------------------


async def _list_users(target: str) -> list[object]:
    """Open ``target``, create the schema if absent, and list all users.

    The dry-run path leaves the target untouched, so this helper installs
    the schema on the fresh DB before querying — otherwise
    :meth:`SqlUserRepository.list_all` would raise ``no such table``.
    """
    engine = build_engine(target)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = build_sessionmaker(engine)
        return list(await SqlUserRepository(maker).list_all(_GUILD_ID))
    finally:
        await engine.dispose()


def test_dry_run_writes_nothing_to_target(tmp_path: Path) -> None:
    """``--dry-run`` exits 0 and the target DB has zero migrated rows."""
    target = _target_url(tmp_path)

    exit_code = main(
        [
            "--source",
            str(_FIXTURES),
            "--target",
            target,
            "--guild-id",
            _GUILD_ID,
            "--dry-run",
        ]
    )
    assert exit_code == 0

    accounts = asyncio.run(_list_users(target))
    assert accounts == [], (
        f"--dry-run must not persist any users, but list_all returned "
        f"{len(accounts)} account(s)"
    )


# ---------------------------------------------------------------------------
# B2 — --report prints would-have-migrated counts in stable order
# ---------------------------------------------------------------------------


async def _expected_counts_via_migrate(source: Path) -> dict[str, int]:
    """Run :func:`migrate` against a throwaway in-memory engine and return counts.

    Same fixtures, same code path as the CLI, so the dict it returns is what
    ``--report`` is expected to print.
    """
    scratch = build_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with scratch.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        scratch_maker = build_sessionmaker(scratch)
        return await migrate(source, scratch_maker, guild_id=_GUILD_ID)
    finally:
        await scratch.dispose()


def test_report_prints_counts_in_sorted_order(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--dry-run --report`` prints ``<table>: <count>`` for every table.

    The format is one ``<table>: <count>`` per line, sorted by table name
    for determinism. Counts match those that :func:`migrate` would return
    against the same realistic fixtures.
    """
    target = _target_url(tmp_path)

    expected_counts = asyncio.run(_expected_counts_via_migrate(_FIXTURES))

    capsys.readouterr()  # drain any logging noise from the helper above

    exit_code = main(
        [
            "--source",
            str(_FIXTURES),
            "--target",
            target,
            "--guild-id",
            _GUILD_ID,
            "--dry-run",
            "--report",
        ]
    )
    assert exit_code == 0

    captured = capsys.readouterr()
    stdout_lines = captured.out.splitlines()

    # Every expected table must appear exactly once on a "<table>: <count>" line.
    table_lines = [
        line
        for line in stdout_lines
        if any(line.startswith(f"{t}:") for t in _EXPECTED_TABLES)
    ]
    assert len(table_lines) == len(_EXPECTED_TABLES), (
        f"expected one report line per table ({len(_EXPECTED_TABLES)}), "
        f"got {len(table_lines)}: {table_lines!r}"
    )

    # Lines are sorted by table name (stable / deterministic).
    assert table_lines == sorted(table_lines), (
        f"report lines are not sorted: {table_lines!r}"
    )

    # Each line carries the count migrate() would return.
    parsed = {
        line.split(":", 1)[0]: int(line.split(":", 1)[1].strip())
        for line in table_lines
    }
    assert parsed == expected_counts, (
        f"report counts {parsed!r} != migrate() counts {expected_counts!r}"
    )


# ---------------------------------------------------------------------------
# B3 — orphan-warning consistency check
# ---------------------------------------------------------------------------


def test_orphan_position_is_warned_not_failed(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Long/short positions targeting an unknown user log a warning, not raise.

    The orphan fixture has user ``2001`` holding a long position on user
    ``9999`` — and ``9999`` has no ``UserAccount`` entry. The migrator must
    finish cleanly (exit 0) and emit a ``WARNING`` on its own logger that
    names the offending target id.
    """
    target = _target_url(tmp_path)

    # ``alembic.env`` calls ``logging.config.fileConfig`` with the default
    # ``disable_existing_loggers=True`` when ``test_migrations`` runs in the
    # same session, which silently disables this logger. Re-enable it
    # explicitly so the WARNING emission isn't swallowed (mirrors the same
    # defensive pattern used in ``tests/adapters/test_container.py``).
    logger_name = "friendex.adapters.persistence.migrate_json_to_sqlite"
    logging.getLogger(logger_name).disabled = False
    caplog.set_level(logging.WARNING, logger=logger_name)

    exit_code = main(
        [
            "--source",
            str(_ORPHAN_FIXTURES),
            "--target",
            target,
            "--guild-id",
            _GUILD_ID,
            "--dry-run",
        ]
    )
    assert exit_code == 0

    orphan_warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "9999" in record.getMessage()
    ]
    assert orphan_warnings, (
        "expected at least one WARNING naming the orphan target id 9999; "
        f"got records: {[(r.levelname, r.getMessage()) for r in caplog.records]!r}"
    )
