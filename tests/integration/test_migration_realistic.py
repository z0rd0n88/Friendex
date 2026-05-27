"""Integration: run the migrator over the realistic JSON fixtures, end-to-end.

Phase 15a verification. The fixtures under
``tests/fixtures/json/realistic/`` simulate a "live" deployment dataset (50
users, 50 stocks, 30 funds + ``events_wallet``, 10 active fund penalties).
This test:

1. Runs :func:`migrate` against an in-memory SQLite engine and asserts the
   per-table row counts match values derived directly from the source JSON.
2. Spot-checks every read-side repository method against expectations parsed
   from the same source JSON — proving the round-trip preserves cash
   balances, current prices, history lengths, investor counts, and penalty
   APR/expiry.
3. Re-runs :func:`migrate` on the same engine and asserts the per-table row
   counts are identical to the first run — proving idempotency end-to-end
   (no duplicate inserts, no orphaned history).

Decimal money and tz-aware UTC datetimes (Phase 3.1 invariant) are preserved
across the migration; we parse the source JSON with ``parse_float=Decimal``
locally so derived expectations have the same exact quantisation as the
migrator's output.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from friendex.adapters.persistence.db import (
    Base,
    build_engine,
    build_sessionmaker,
)
from friendex.adapters.persistence.fund_repo import SqlFundRepository
from friendex.adapters.persistence.migrate_json_to_sqlite import migrate
from friendex.adapters.persistence.penalty_repo import SqlPenaltyRepository
from friendex.adapters.persistence.price_repo import SqlPriceRepository
from friendex.adapters.persistence.user_repo import SqlUserRepository

if TYPE_CHECKING:
    from collections.abc import Mapping

_GUILD_ID = "999"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "json" / "realistic"


# ---------------------------------------------------------------------------
# Fixture-derived expectations (parsed once per test from the source JSON)
# ---------------------------------------------------------------------------


def _load(name: str) -> dict[str, Any]:
    """Load a realistic fixture as ``{id: record}``; numbers stay :class:`Decimal`."""
    with (_FIXTURES / name).open(encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle, parse_float=Decimal)
    return data


def _expected_counts(
    users: Mapping[str, Any],
    prices: Mapping[str, Any],
    funds: Mapping[str, Any],
    penalties: Mapping[str, Any],
) -> dict[str, int]:
    """Derive the expected per-table row counts from the source JSON.

    Mirrors the migrator's return-dict keys (``users``, ``long_positions``,
    ``short_positions``, ``stocks``, ``price_history``, ``hedge_funds``,
    ``fund_investors``, ``fund_penalties``) so the assertion is structural,
    not magic-numbered.
    """
    return {
        "users": len(users),
        "long_positions": sum(
            len(u.get("portfolio", {}).get("long", {})) for u in users.values()
        ),
        "short_positions": sum(
            len(u.get("portfolio", {}).get("short", {})) for u in users.values()
        ),
        "stocks": len(prices),
        "price_history": sum(len(p.get("history", [])) for p in prices.values()),
        "hedge_funds": len(funds),
        "fund_investors": sum(len(f.get("investors", {})) for f in funds.values()),
        "fund_penalties": len(penalties),
    }


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migrate_realistic_fixtures_round_trip_and_idempotent() -> None:
    """End-to-end migration + read-side spot-checks + idempotency re-run."""
    users = _load("users.json")
    prices = _load("prices.json")
    funds = _load("funds.json")
    penalties = _load("fund_penalties.json")
    expected = _expected_counts(users, prices, funds, penalties)

    # Pin the high-level shape so a fixture regression (e.g. accidental drop)
    # is named directly instead of leaking into a downstream mismatch.
    assert expected["users"] == 50
    assert expected["stocks"] == 50
    assert expected["hedge_funds"] == 31  # 30 funds + events_wallet
    assert expected["fund_penalties"] == 10

    engine = build_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = build_sessionmaker(engine)

        # --- (a) first migrate -> counts match expectations -----------------
        first = await migrate(_FIXTURES, maker, guild_id=_GUILD_ID)
        assert first == expected, f"first-run counts {first!r} != expected {expected!r}"

        # --- (b) read-side spot-checks --------------------------------------
        user_repo = SqlUserRepository(maker)
        price_repo = SqlPriceRepository(maker)
        fund_repo = SqlFundRepository(maker)
        penalty_repo = SqlPenaltyRepository(maker)

        # (b.i) every user is present with the right cash balance.
        accounts = await user_repo.list_all(_GUILD_ID)
        assert len(accounts) == expected["users"]
        accounts_by_id = {a.user_id: a for a in accounts}
        for sample_id in ("1001", "1010", "1025"):
            expected_cash = Decimal(str(users[sample_id]["cash_balance"]))
            assert accounts_by_id[sample_id].cash_balance == expected_cash

        # (b.ii) every fund is present; investor count matches for a sample.
        all_funds = await fund_repo.list_all(_GUILD_ID)
        assert len(all_funds) == expected["hedge_funds"]
        funds_by_id = {f.fund_id: f for f in all_funds}
        # Pick a fund the fixture guarantees has >= 2 investors.
        sample_fund_id = next(
            fid for fid, raw in funds.items() if len(raw.get("investors", {})) >= 2
        )
        assert len(funds_by_id[sample_fund_id].investors) == len(
            funds[sample_fund_id]["investors"]
        )

        # (b.iii) every stock is present; current price matches for samples.
        stocks = await price_repo.list_all(_GUILD_ID)
        assert len(stocks) == expected["stocks"]
        stocks_by_id = {s.user_id: s for s in stocks}
        for sample_id in ("1001", "1020", "1050"):
            expected_current = Decimal(str(prices[sample_id]["current"]))
            assert stocks_by_id[sample_id].current == expected_current

        # (b.iv) price history row count matches the source for one stock.
        sample_history_id = "1001"
        history = await price_repo.get_history(_GUILD_ID, sample_history_id)
        assert len(history) == len(prices[sample_history_id]["history"])

        # (b.v) penalties round-trip with matching APR + expiry for one sample.
        live_penalties = await penalty_repo.list_all(_GUILD_ID)
        assert len(live_penalties) == expected["fund_penalties"]
        sample_penalty_id = next(iter(penalties))
        sample_penalty = next(
            p for p in live_penalties if p.user_id == sample_penalty_id
        )
        raw_penalty = penalties[sample_penalty_id]
        assert sample_penalty.penalty_apr == Decimal(str(raw_penalty["penalty_apr"]))
        expected_until = datetime.fromisoformat(raw_penalty["penalty_until"]).replace(
            tzinfo=UTC
        )
        assert sample_penalty.penalty_until == expected_until

        # --- (c) idempotency: re-run on the same engine ---------------------
        # The migrator's docstring promises ``session.merge`` on natural keys
        # and explicit history clear-and-re-append, so the per-table counts
        # from the second pass must match the first exactly.
        second = await migrate(_FIXTURES, maker, guild_id=_GUILD_ID)
        assert second == first, (
            f"idempotency violated: second-run counts {second!r} != "
            f"first-run counts {first!r}"
        )

        # Live row counts after the re-run still match expectations (no
        # duplicates, no orphans).
        assert len(await user_repo.list_all(_GUILD_ID)) == expected["users"]
        assert len(await price_repo.list_all(_GUILD_ID)) == expected["stocks"]
        assert len(await fund_repo.list_all(_GUILD_ID)) == expected["hedge_funds"]
        assert len(await penalty_repo.list_all(_GUILD_ID)) == expected["fund_penalties"]
        assert len(await price_repo.get_history(_GUILD_ID, sample_history_id)) == len(
            prices[sample_history_id]["history"]
        )
    finally:
        await engine.dispose()
