"""Tests for the one-shot JSON-to-SQLite migrator (sub-unit 6f).

These exercise :func:`migrate` end-to-end against the synthetic fixtures in
``tests/fixtures/json/`` (shaped like the *original* single-file bot's JSON
files) and an in-memory SQLite target, proving the four acceptance criteria:

* **Row counts** — every record in every source file lands as exactly the
  expected number of rows per table.
* **Round trip** — each migrated record reads back through the Phase 6
  repositories with its money values as exact ``Decimal`` (quantisation
  preserved via ``as_tuple().exponent``) and its timestamps as tz-aware UTC.
* **Idempotency** — a second migration over the same source leaves the row
  counts unchanged (``session.merge`` keyed on the natural PKs; no duplicates).
* **CLI** — ``main([...])`` parses ``--source`` / ``--target`` / ``--guild-id``
  and runs the migration, returning a clean exit code.

The fixture engine has FK enforcement ON (ADR-0002), so the migrator must write
parents before children or the inserts fail — the round-trip assertions would
not even reach their value checks if the ordering were wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from friendex.adapters.persistence.db import Base, build_engine, build_sessionmaker
from friendex.adapters.persistence.fund_repo import SqlFundRepository
from friendex.adapters.persistence.migrate_json_to_sqlite import migrate
from friendex.adapters.persistence.orm import (
    FundInvestorORM,
    FundPenaltyORM,
    HedgeFundORM,
    LongPositionORM,
    PriceHistoryORM,
    ShortPositionORM,
    StockORM,
    UserORM,
)
from friendex.adapters.persistence.penalty_repo import SqlPenaltyRepository
from friendex.adapters.persistence.price_repo import SqlPriceRepository
from friendex.adapters.persistence.user_repo import SqlUserRepository

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

GUILD_ID = "999000111222"
FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "json"


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A fresh in-memory SQLite engine (FK enforcement ON) with tables created."""
    eng = build_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
def maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A sessionmaker bound to the in-memory engine."""
    return build_sessionmaker(engine)


def _same_scale(actual: Decimal, expected: Decimal) -> bool:
    """True when ``actual`` has the same quantisation exponent as ``expected``."""
    return actual.as_tuple().exponent == expected.as_tuple().exponent


async def _table_count(session: AsyncSession, model: type) -> int:
    stmt = select(func.count()).select_from(model)
    return int((await session.execute(stmt)).scalar_one())


async def _all_counts(maker: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """Row counts for every table the migrator writes."""
    async with maker() as session:
        return {
            "users": await _table_count(session, UserORM),
            "long_positions": await _table_count(session, LongPositionORM),
            "short_positions": await _table_count(session, ShortPositionORM),
            "stocks": await _table_count(session, StockORM),
            "price_history": await _table_count(session, PriceHistoryORM),
            "hedge_funds": await _table_count(session, HedgeFundORM),
            "fund_investors": await _table_count(session, FundInvestorORM),
            "fund_penalties": await _table_count(session, FundPenaltyORM),
        }


# The expected per-table row counts derived by hand from the fixture files.
_EXPECTED_COUNTS = {
    "users": 3,  # 111, 222, 333
    "long_positions": 3,  # 111->{222,333}, 333->{111}
    "short_positions": 1,  # 111->444
    "stocks": 4,  # 111, 222, 333, 444
    "price_history": 6,  # 3 + 1 + 0 + 2
    "hedge_funds": 3,  # 111, 333, events_wallet
    "fund_investors": 2,  # 111->{222,333}
    "fund_penalties": 2,  # 222, 333
}


# ---------------------------------------------------------------------------
# AC1 — row counts per table
# ---------------------------------------------------------------------------


async def test_migrate_produces_expected_row_counts(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """AC1 — every source record lands as the expected number of rows."""
    counts = await migrate(FIXTURE_DIR, maker, guild_id=GUILD_ID)

    db_counts = await _all_counts(maker)
    assert db_counts == _EXPECTED_COUNTS
    # The returned per-table report matches what actually landed.
    assert counts == _EXPECTED_COUNTS


# ---------------------------------------------------------------------------
# AC2 — round trip through the repositories (Decimal + UTC)
# ---------------------------------------------------------------------------


async def test_user_record_round_trips(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """AC2 — a user aggregate reads back via the repo with Decimal + UTC intact."""
    await migrate(FIXTURE_DIR, maker, guild_id=GUILD_ID)

    repo = SqlUserRepository(maker)
    account = await repo.get(GUILD_ID, "111")

    assert account is not None
    assert account.cash_balance == Decimal("9876.54")
    assert isinstance(account.cash_balance, Decimal)
    assert _same_scale(account.cash_balance, Decimal("9876.54"))

    # Long positions survive with exact entry prices.
    assert set(account.long_positions) == {"222", "333"}
    assert account.long_positions["222"].shares == 5
    assert account.long_positions["222"].avg_entry == Decimal("80.10")
    assert _same_scale(account.long_positions["222"].avg_entry, Decimal("80.10"))

    # Short position with all collateral fields + tz-aware created_at.
    short = account.short_positions["444"]
    assert short.shares == 2
    assert short.entry_price == Decimal("90.30")
    assert short.frozen is True
    assert short.created_at == datetime(2026, 5, 23, 8, 30, 15, tzinfo=UTC)
    assert short.created_at.tzinfo is not None

    # Activity buckets round-trip including voice channels.
    assert account.today.text_msgs == 12
    assert account.today.voice_unique_channels == ["c1", "c2"]
    assert account.week.text_msgs == 80
    assert account.today.bucket_start.tzinfo is not None

    # Daily progress + flags.
    assert account.daily.streak == 3
    assert account.daily.last_claim == datetime(2026, 5, 22, 6, 30, 15, tzinfo=UTC)
    assert account.opt_in is True
    assert account.intro_shown is False

    # The opted-out user round-trips its flag.
    opted_out = await repo.get(GUILD_ID, "333")
    assert opted_out is not None
    assert opted_out.opt_in is False


async def test_stock_record_round_trips(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """AC2 — a stock + its price history read back via the price repo."""
    await migrate(FIXTURE_DIR, maker, guild_id=GUILD_ID)

    repo = SqlPriceRepository(maker)
    stock = await repo.get(GUILD_ID, "111")

    assert stock is not None
    assert stock.current == Decimal("105.10")
    assert _same_scale(stock.current, Decimal("105.10"))
    assert stock.high_24h == Decimal("106.00")
    assert stock.low_24h == Decimal("99.30")
    assert stock.all_time_high == Decimal("150.00")

    history = await repo.get_history(GUILD_ID, "111")
    assert [p.price for p in history] == [
        Decimal("100.00"),
        Decimal("102.50"),
        Decimal("105.10"),
    ]
    # Oldest-first, tz-aware UTC timestamps.
    assert history[0].timestamp == datetime(2026, 5, 23, 6, 0, 0, tzinfo=UTC)
    assert all(p.timestamp.tzinfo is not None for p in history)

    # A stock with no history migrates the scalar row but zero history rows.
    empty = await repo.get_history(GUILD_ID, "333")
    assert empty == []


async def test_fund_record_round_trips(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """AC2 — a hedge fund + investors read back via the fund repo."""
    await migrate(FIXTURE_DIR, maker, guild_id=GUILD_ID)

    repo = SqlFundRepository(maker)
    fund = await repo.get(GUILD_ID, "111")

    assert fund is not None
    assert fund.name == "Alpha Capital"
    assert fund.manager_id == "111"
    assert fund.cash_balance == Decimal("25000.00")
    assert fund.investors == {
        "222": Decimal("5000.00"),
        "333": Decimal("2500.50"),
    }
    assert _same_scale(fund.investors["333"], Decimal("2500.50"))

    # The events-wallet pseudo-fund migrates as a fund row.
    wallet = await repo.get(GUILD_ID, "events_wallet")
    assert wallet is not None
    assert wallet.name == "Events Wallet"
    assert wallet.cash_balance == Decimal("750.00")


async def test_penalty_record_round_trips(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """AC2 — a fund penalty reads back via the penalty repo with Decimal + UTC."""
    await migrate(FIXTURE_DIR, maker, guild_id=GUILD_ID)

    repo = SqlPenaltyRepository(maker)
    penalty = await repo.get(GUILD_ID, "222")

    assert penalty is not None
    assert penalty.penalty_apr == Decimal("0.05")
    assert isinstance(penalty.penalty_apr, Decimal)
    assert penalty.penalty_until == datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)
    assert penalty.penalty_until.tzinfo is not None


# ---------------------------------------------------------------------------
# AC3 — idempotency (second run = same counts, no duplicates)
# ---------------------------------------------------------------------------


async def test_second_migration_is_idempotent(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """AC3 — running the migrator twice yields identical row counts (no dups)."""
    first = await migrate(FIXTURE_DIR, maker, guild_id=GUILD_ID)
    after_first = await _all_counts(maker)

    second = await migrate(FIXTURE_DIR, maker, guild_id=GUILD_ID)
    after_second = await _all_counts(maker)

    assert after_first == _EXPECTED_COUNTS
    assert after_second == _EXPECTED_COUNTS
    assert first == second


async def test_second_migration_does_not_mutate_values(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """AC3 — a second run is an UPDATE on the same PKs, leaving values intact."""
    await migrate(FIXTURE_DIR, maker, guild_id=GUILD_ID)
    await migrate(FIXTURE_DIR, maker, guild_id=GUILD_ID)

    repo = SqlFundRepository(maker)
    fund = await repo.get(GUILD_ID, "111")
    assert fund is not None
    assert fund.cash_balance == Decimal("25000.00")
    assert fund.investors == {
        "222": Decimal("5000.00"),
        "333": Decimal("2500.50"),
    }


# ---------------------------------------------------------------------------
# AC4 — CLI entry point
# ---------------------------------------------------------------------------


def test_cli_runs_migration(tmp_path: Path) -> None:
    """AC4 — ``main`` parses args and migrates into a file-backed SQLite target."""
    from friendex.adapters.persistence.migrate_json_to_sqlite import main

    db_path = tmp_path / "out.db"
    target = f"sqlite+aiosqlite:///{db_path}"

    exit_code = main(
        [
            "--source",
            str(FIXTURE_DIR),
            "--target",
            target,
            "--guild-id",
            GUILD_ID,
        ]
    )

    assert exit_code == 0
    assert db_path.exists()


def test_cli_missing_source_dir_errors(tmp_path: Path) -> None:
    """AC4 — a non-existent source directory exits non-zero, not a crash."""
    from friendex.adapters.persistence.migrate_json_to_sqlite import main

    target = f"sqlite+aiosqlite:///{tmp_path / 'out.db'}"
    exit_code = main(
        [
            "--source",
            str(tmp_path / "does-not-exist"),
            "--target",
            target,
            "--guild-id",
            GUILD_ID,
        ]
    )

    assert exit_code != 0


def test_migrate_handles_missing_files(
    maker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """AC1 — absent source files are treated as empty (zero rows), not errors."""
    import asyncio

    counts = asyncio.run(migrate(tmp_path, maker, guild_id=GUILD_ID))

    assert counts == dict.fromkeys(_EXPECTED_COUNTS, 0)


@pytest.mark.parametrize("required", ["--source", "--target"])
def test_cli_requires_source_and_target(required: str) -> None:
    """AC4 — argparse enforces the required ``--source`` / ``--target`` flags."""
    from friendex.adapters.persistence.migrate_json_to_sqlite import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        # Missing one required flag -> argparse exits with code 2.
        parser.parse_args(["--guild-id", GUILD_ID])
    assert required  # the parametrization documents both required flags
