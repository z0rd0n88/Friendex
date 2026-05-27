"""One-shot JSON-to-SQLite migrator for the original bot's data files.

The original single-file Friendex bot persisted its state as four JSON files in
a ``data/`` directory (``users.json``, ``prices.json``, ``funds.json``,
``fund_penalties.json`` — see ``docs/spec/original-skeleton.md``). This module
reads those files and writes their records into the new SQLite schema through
the Phase 6 repositories, so an existing deployment can be cut over to the
hexagonal rebuild without losing data.

Run it as a module::

    python -m friendex.adapters.persistence.migrate_json_to_sqlite \\
        --source data/ \\
        --target sqlite+aiosqlite:///data/friendex.db \\
        --guild-id <discord-guild-id>

**Per-guild scope (ADR-0001).** The original bot was single-guild — its JSON
keys are bare ``user_id`` strings with no guild dimension. The new schema keys
every row by ``(guild_id, user_id)``, so the migration targets exactly one
guild: ``--guild-id`` says which. All migrated rows are written under that one
guild.

**Dry-run, report, and orphan check (Phase 15b).** ``--dry-run`` parses every
fixture and exercises the full mapping pipeline but writes into a throwaway
in-memory engine — the target URL is left untouched, so a dry-run against a
live database has zero side effects. ``--report`` prints the per-table row
counts (the keys of :func:`migrate`'s return dict) to stdout in a stable,
sortable ``<table>: <count>`` form, with or without ``--dry-run``. After every
run — real *or* dry — the migrator walks every long / short position's
``target_user_id`` and logs ``WARNING`` for each one that does not resolve to a
known ``UserAccount``; the check is advisory and never raises, so an orphan
does not block the cutover.

**Decimal, not float (Phase 3.1 invariant).** Numbers are decoded with
``parse_float=Decimal`` so a JSON literal like ``9876.54`` becomes
``Decimal('9876.54')`` directly from its text — never via a lossy intermediate
``float``. Money and price fields therefore keep their exact value and
quantisation across the migration.

**UTC-aware datetimes (Phase 3.1 invariant).** The original bot wrote naive
``datetime.utcnow().isoformat()`` strings, so timestamps are parsed and then
*localised* to UTC. An already-aware timestamp is converted to UTC; a naive one
is interpreted as UTC. The ``UtcDateTime`` column type rejects naive datetimes
at bind time, so this localisation is mandatory, not cosmetic.

**Idempotent.** Writing goes through the repositories' ``upsert`` /
``append_history``, which ``session.merge`` on the natural primary keys. Running
the migration twice over the same source therefore yields the same row counts —
the second pass is an UPDATE of each existing row, never a duplicate insert.
(Price history is the one append-only table; its rows are de-duplicated by
clearing a stock's existing history before re-appending, so re-runs do not
accumulate duplicate points.)

**FK-safe ordering.** SQLite FK enforcement is ON (ADR-0002), so parent rows
must exist before their children. The migration writes in dependency order —
stocks before price history, users/funds/penalties as self-contained
aggregates — and each aggregate repository inserts its own parent before its
children internally.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete

from friendex.adapters.persistence.db import build_engine, build_sessionmaker
from friendex.adapters.persistence.fund_repo import SqlFundRepository
from friendex.adapters.persistence.orm import PriceHistoryORM
from friendex.adapters.persistence.penalty_repo import SqlPenaltyRepository
from friendex.adapters.persistence.price_repo import SqlPriceRepository
from friendex.adapters.persistence.user_repo import SqlUserRepository
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    FundPenalty,
    HedgeFund,
    LongPosition,
    PricePoint,
    ShortPosition,
    Stock,
    UserAccount,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


class MigrationError(Exception):
    """A source data file could not be migrated because it is corrupt.

    Raised at the load / record-mapping boundary when a source file is
    structurally wrong (top level is not an object) or a record carries a
    missing required field or an un-parseable money / timestamp value. The
    message names the offending file (and, where known, the record key and
    field) so an operator can fix the JSON; the original exception is chained
    via ``raise ... from`` so the technical cause is preserved in the log.
    """


# Source file names, fixed by the original bot (``docs/spec/original-skeleton.md``).
_USERS_FILE = "users.json"
_PRICES_FILE = "prices.json"
_FUNDS_FILE = "funds.json"
_PENALTIES_FILE = "fund_penalties.json"

# Activity-bucket discriminators in the source ``activity`` object.
_TODAY = "today"
_WEEK = "week"


# ---------------------------------------------------------------------------
# Decoding helpers
# ---------------------------------------------------------------------------


def _load_json_object(path: Path) -> dict[str, Any]:
    """Load one source file as a ``{id: record}`` mapping.

    A missing file is treated as an empty data set (the original bot created
    files lazily, so a fresh deployment may lack some of them). Numbers are
    decoded straight to :class:`~decimal.Decimal` via ``parse_float`` so money
    never round-trips through ``float``.

    :raises MigrationError: if the file is not valid JSON, or its top level is
        not a JSON object (the migrator expects an ``{id: record}`` mapping).
    """
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        try:
            data: Any = json.load(handle, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise MigrationError(
                f"{path.name}: file is not valid JSON ({exc})"
            ) from exc
    if not isinstance(data, dict):
        raise MigrationError(
            f"{path.name}: expected a JSON object mapping id -> record, "
            f"got a top-level {type(data).__name__}"
        )
    return data


def _to_decimal(value: Any, field: str | None = None) -> Decimal:
    """Coerce a decoded JSON number to :class:`~decimal.Decimal` exactly.

    Floats are already decoded to ``Decimal`` by ``parse_float``; an integer
    literal arrives as ``int`` and is converted via its ``str`` form so the
    value stays exact.

    :param field: optional source field name; when supplied, a non-numeric
        value is reported as a :class:`MigrationError` naming the field and the
        offending value instead of leaking a raw ``decimal.InvalidOperation``.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    # A bare ``str`` (or anything else) is converted via its text form.
    try:
        return Decimal(str(value))
    except ArithmeticError as exc:
        if field is None:
            raise
        raise MigrationError(f"field {field!r} is not a number: {value!r}") from exc


def _to_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string and localise it to tz-aware UTC.

    The original bot stored naive ``datetime.utcnow()`` strings; a naive parse
    result is interpreted as UTC, and an already-aware one is converted to UTC.
    ``None`` (e.g. an unclaimed daily reward) passes through unchanged.
    """
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _require_utc(value: str | None) -> datetime:
    """Like :func:`_to_utc` but for fields that must carry a timestamp."""
    parsed = _to_utc(value)
    if parsed is None:
        raise ValueError("expected a timestamp, got null")
    return parsed


@contextmanager
def _record_context(filename: str, record_id: str) -> Iterator[None]:
    """Map a record-mapping failure into a :class:`MigrationError` with context.

    Wraps the mapping of one source record so the anticipated corrupt-data
    failures — a missing required key (:class:`KeyError`), an un-parseable money
    value (:class:`decimal.InvalidOperation`, an :class:`ArithmeticError`), or a
    bad timestamp / wrong type (:class:`ValueError`, :class:`TypeError`) — become
    a single, actionable error naming the file, the record id, and the offending
    field. Programmer errors are *not* mapped; they propagate unchanged.
    """
    try:
        yield
    except MigrationError as exc:
        # A mapper already produced a field-level message (e.g. _to_decimal);
        # prepend the file + record so the operator can locate it.
        raise MigrationError(f"{filename}: record {record_id!r}: {exc}") from exc
    except KeyError as exc:
        # ``KeyError.args[0]`` is the missing field name.
        field = exc.args[0] if exc.args else "<unknown>"
        raise MigrationError(
            f"{filename}: record {record_id!r} is missing required field {field!r}"
        ) from exc
    except (ArithmeticError, ValueError, TypeError) as exc:
        raise MigrationError(
            f"{filename}: record {record_id!r} has an invalid value: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Record -> domain mappers
# ---------------------------------------------------------------------------


def _build_activity_bucket(raw: Mapping[str, Any]) -> ActivityBucket:
    """Map one ``activity[today|week]`` object to an :class:`ActivityBucket`."""
    return ActivityBucket(
        text_msgs=int(raw.get("text_msgs", 0)),
        media_msgs=int(raw.get("media_msgs", 0)),
        voice_minutes=float(raw.get("voice_minutes", 0.0)),
        voice_unique_channels=[str(c) for c in raw.get("voice_unique_channels", [])],
        reaction_count=int(raw.get("reaction_count", 0)),
        reply_count=int(raw.get("reply_count", 0)),
        role_ping_joins=float(raw.get("role_ping_joins", 0.0)),
        role_ping_join_minutes=float(raw.get("role_ping_join_minutes", 0.0)),
        bucket_start=_require_utc(raw.get("timestamp")),
    )


def _build_long_position(target_id: str, raw: Mapping[str, Any]) -> LongPosition:
    return LongPosition(
        target_user_id=target_id,
        shares=int(raw["shares"]),
        avg_entry=_to_decimal(raw["avg_entry"]),
    )


def _build_short_position(target_id: str, raw: Mapping[str, Any]) -> ShortPosition:
    return ShortPosition(
        target_user_id=target_id,
        shares=int(raw["shares"]),
        entry_price=_to_decimal(raw["entry_price"]),
        locked_cash=_to_decimal(raw.get("locked_cash", 0)),
        locked_fund=_to_decimal(raw.get("locked_fund", 0)),
        created_at=_require_utc(raw["created_at"]),
        frozen=bool(raw.get("frozen", False)),
    )


def _build_user_account(user_id: str, raw: Mapping[str, Any]) -> UserAccount:
    """Map one ``users.json`` record to a :class:`UserAccount` aggregate."""
    portfolio = raw.get("portfolio", {})
    long_raw: Mapping[str, Any] = portfolio.get("long", {})
    short_raw: Mapping[str, Any] = portfolio.get("short", {})
    activity = raw.get("activity", {})
    daily = raw.get("daily", {})

    return UserAccount(
        user_id=user_id,
        cash_balance=_to_decimal(raw["cash_balance"], field="cash_balance"),
        net_worth=_to_decimal(
            raw.get("net_worth", raw["cash_balance"]), field="net_worth"
        ),
        month_start_net_worth=_to_decimal(
            raw.get("month_start_net_worth", raw["cash_balance"]),
            field="month_start_net_worth",
        ),
        long_positions={
            tid: _build_long_position(tid, pos) for tid, pos in long_raw.items()
        },
        short_positions={
            tid: _build_short_position(tid, pos) for tid, pos in short_raw.items()
        },
        today=_build_activity_bucket(activity.get(_TODAY, {})),
        week=_build_activity_bucket(activity.get(_WEEK, {})),
        daily=DailyProgress(
            last_claim=_to_utc(daily.get("last_claim")),
            streak=int(daily.get("streak", 0)),
        ),
        last_activity=_require_utc(raw["last_activity"]),
        opt_in=bool(raw.get("opt_in", True)),
        intro_shown=bool(raw.get("intro_shown", False)),
    )


def _build_stock(user_id: str, raw: Mapping[str, Any]) -> Stock:
    """Map one ``prices.json`` record's scalars to a :class:`Stock` (no history)."""
    current = _to_decimal(raw["current"])
    return Stock(
        user_id=user_id,
        current=current,
        history=[],
        high_24h=_to_decimal(raw.get("high_24h", current)),
        low_24h=_to_decimal(raw.get("low_24h", current)),
        all_time_high=_to_decimal(raw.get("all_time_high", current)),
    )


def _build_price_points(raw: Mapping[str, Any]) -> list[PricePoint]:
    """Map a ``prices.json`` record's ``history`` list to :class:`PricePoint`s."""
    return [
        PricePoint(
            price=_to_decimal(point["price"]),
            timestamp=_require_utc(point["timestamp"]),
        )
        for point in raw.get("history", [])
    ]


def _build_fund(fund_id: str, raw: Mapping[str, Any]) -> HedgeFund:
    """Map one ``funds.json`` record to a :class:`HedgeFund` aggregate."""
    investors_raw: Mapping[str, Any] = raw.get("investors", {})
    return HedgeFund(
        fund_id=fund_id,
        name=str(raw.get("name", f"Fund {fund_id}")),
        manager_id=str(raw.get("manager_id", fund_id)),
        cash_balance=_to_decimal(raw.get("cash_balance", 0)),
        investors={
            investor_id: _to_decimal(amount)
            for investor_id, amount in investors_raw.items()
        },
    )


def _build_penalty(user_id: str, raw: Mapping[str, Any]) -> FundPenalty:
    """Map one ``fund_penalties.json`` record to a :class:`FundPenalty`."""
    return FundPenalty(
        user_id=user_id,
        penalty_apr=_to_decimal(raw.get("penalty_apr", 0)),
        penalty_until=_require_utc(raw["penalty_until"]),
    )


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


async def _migrate_users(
    source: Path, repo: SqlUserRepository, guild_id: str
) -> tuple[int, int, int]:
    """Migrate ``users.json``; return ``(users, long_positions, short_positions)``."""
    records = _load_json_object(source / _USERS_FILE)
    users = longs = shorts = 0
    for user_id, raw in records.items():
        with _record_context(_USERS_FILE, user_id):
            account = _build_user_account(user_id, raw)
        await repo.upsert(guild_id, account)
        users += 1
        longs += len(account.long_positions)
        shorts += len(account.short_positions)
    return users, longs, shorts


async def _migrate_prices(
    source: Path,
    repo: SqlPriceRepository,
    maker: async_sessionmaker[AsyncSession],
    guild_id: str,
) -> tuple[int, int]:
    """Migrate ``prices.json``; return ``(stocks, price_history_points)``.

    The scalar stock row is upserted first (FK parent), then its history is
    cleared and re-appended so a re-run does not accumulate duplicate points.
    """
    records = _load_json_object(source / _PRICES_FILE)
    stocks = history_points = 0
    for user_id, raw in records.items():
        with _record_context(_PRICES_FILE, user_id):
            stock = _build_stock(user_id, raw)
            points = _build_price_points(raw)
        await repo.upsert(guild_id, stock)
        stocks += 1
        await _clear_price_history(maker, guild_id, user_id)
        for point in points:
            await repo.append_history(guild_id, user_id, point)
            history_points += 1
    return stocks, history_points


async def _clear_price_history(
    maker: async_sessionmaker[AsyncSession], guild_id: str, user_id: str
) -> None:
    """Delete a stock's existing price-history rows ahead of a re-append.

    Price history is the one append-only table, so idempotency cannot rely on a
    PK ``merge`` (each point has a surrogate id). Clearing first makes a re-run
    replace the history rather than duplicate it.
    """
    async with maker() as session:
        await session.execute(
            delete(PriceHistoryORM).where(
                PriceHistoryORM.guild_id == guild_id,
                PriceHistoryORM.user_id == user_id,
            )
        )
        await session.commit()


async def _migrate_funds(
    source: Path, repo: SqlFundRepository, guild_id: str
) -> tuple[int, int]:
    """Migrate ``funds.json``; return ``(hedge_funds, fund_investors)``."""
    records = _load_json_object(source / _FUNDS_FILE)
    funds = investors = 0
    for fund_id, raw in records.items():
        with _record_context(_FUNDS_FILE, fund_id):
            fund = _build_fund(fund_id, raw)
        await repo.upsert(guild_id, fund)
        funds += 1
        investors += len(fund.investors)
    return funds, investors


async def _migrate_penalties(
    source: Path, repo: SqlPenaltyRepository, guild_id: str
) -> int:
    """Migrate ``fund_penalties.json``; return the penalty row count."""
    records = _load_json_object(source / _PENALTIES_FILE)
    count = 0
    for user_id, raw in records.items():
        with _record_context(_PENALTIES_FILE, user_id):
            penalty = _build_penalty(user_id, raw)
        await repo.upsert(guild_id, penalty)
        count += 1
    return count


async def migrate(
    source: Path,
    maker: async_sessionmaker[AsyncSession],
    *,
    guild_id: str,
) -> dict[str, int]:
    """Migrate every source JSON file into SQLite; return per-table row counts.

    :param source: Directory holding the original ``*.json`` files.
    :param maker: Session factory bound to the target engine.
    :param guild_id: The single guild every migrated row is written under
        (ADR-0001 — the original data is guild-less).
    :returns: ``{table_name: rows_written}`` for every table touched. The
        counts are *records processed*, which for an idempotent re-run equal the
        live row counts (no duplicates).

    Aggregates are written via their repositories so parents precede children
    and writes are idempotent (``session.merge`` on the natural keys).
    """
    user_repo = SqlUserRepository(maker)
    price_repo = SqlPriceRepository(maker)
    fund_repo = SqlFundRepository(maker)
    penalty_repo = SqlPenaltyRepository(maker)

    users, longs, shorts = await _migrate_users(source, user_repo, guild_id)
    stocks, history_points = await _migrate_prices(source, price_repo, maker, guild_id)
    funds, investors = await _migrate_funds(source, fund_repo, guild_id)
    penalties = await _migrate_penalties(source, penalty_repo, guild_id)

    counts = {
        "users": users,
        "long_positions": longs,
        "short_positions": shorts,
        "stocks": stocks,
        "price_history": history_points,
        "hedge_funds": funds,
        "fund_investors": investors,
        "fund_penalties": penalties,
    }
    for table, rows in counts.items():
        logger.info("migrated %d row(s) into %s", rows, table)
    return counts


# ---------------------------------------------------------------------------
# Post-migration orphan-warning check
# ---------------------------------------------------------------------------


def _warn_orphan_positions(source: Path) -> None:
    """Log a ``WARNING`` for every long / short position with no matching account.

    Walks ``users.json`` once and checks every ``LongPosition.target_user_id``
    and ``ShortPosition.target_user_id`` against the set of known user ids.
    The check is purely advisory — per the Phase 15 spec it warns rather than
    fails, so a corrupt position reference does not block the cutover. Both
    the real-run and ``--dry-run`` paths invoke it so an operator gets the
    same diagnostic without having to commit to a write.

    Source-side rather than DB-side: every migrated row originates in
    ``users.json``, so the source carries the same orphan set as the
    persisted state but is also available on the dry-run path (where nothing
    is persisted).
    """
    users = _load_json_object(source / _USERS_FILE)
    known_user_ids = set(users.keys())
    for owner_id, raw in users.items():
        portfolio = raw.get("portfolio", {})
        for side, key in (("long", "long"), ("short", "short")):
            positions: Mapping[str, Any] = portfolio.get(key, {})
            for target_id in positions:
                if target_id not in known_user_ids:
                    logger.warning(
                        "orphan %s position: owner=%s target=%s has no matching "
                        "UserAccount",
                        side,
                        owner_id,
                        target_id,
                    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the migrator CLI."""
    parser = argparse.ArgumentParser(
        prog="friendex.adapters.persistence.migrate_json_to_sqlite",
        description=(
            "One-shot migration of the original bot's JSON data files into the "
            "new SQLite schema."
        ),
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Directory holding users.json / prices.json / funds.json / "
        "fund_penalties.json.",
    )
    parser.add_argument(
        "--target",
        required=True,
        help="Target SQLAlchemy async URL, e.g. sqlite+aiosqlite:///data/friendex.db.",
    )
    parser.add_argument(
        "--guild-id",
        required=True,
        help="Discord guild id every migrated row is written under (ADR-0001).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Parse every fixture and exercise the full mapping pipeline against "
            "a throwaway in-memory engine; the target URL is not touched."
        ),
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help=(
            "After the run, print per-table row counts to stdout as '<table>: "
            "<count>' lines, sorted by table name. Composes with --dry-run."
        ),
    )
    return parser


# When --dry-run is set, the migration runs against this throwaway URL instead
# of the operator-supplied --target. Each ``create_async_engine`` call with
# ``:memory:`` allocates its own independent database, so the target URL is
# never opened, schema-created, or written.
_DRY_RUN_TARGET = "sqlite+aiosqlite:///:memory:"


async def _run(
    source: Path, target: str, guild_id: str, *, dry_run: bool = False
) -> dict[str, int]:
    """Build the engine/schema and run the migration once.

    :param dry_run: when ``True``, the migration runs against a throwaway
        in-memory engine instead of ``target`` so nothing is persisted. The
        target URL is not opened, so a dry-run against a live database has
        zero side effects.
    """
    from friendex.adapters.persistence.db import Base

    effective_target = _DRY_RUN_TARGET if dry_run else target
    engine = build_engine(effective_target)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = build_sessionmaker(engine)
        counts = await migrate(source, maker, guild_id=guild_id)
    finally:
        await engine.dispose()

    # Orphan check is advisory and runs on both real and dry-run paths so the
    # operator gets the same diagnostic regardless of whether they committed.
    _warn_orphan_positions(source)
    return counts


def _print_report(counts: Mapping[str, int]) -> None:
    """Print ``<table>: <count>`` lines for ``counts`` to stdout, sorted by table.

    Sorting is deterministic so the CLI output is reliable for downstream
    tooling and tests (no flaky ordering between dict-iteration orders).
    """
    for table in sorted(counts):
        print(f"{table}: {counts[table]}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args, run the migration, report row counts.

    Returns ``0`` on success and a non-zero code on failure (e.g. a missing
    source directory), so it composes cleanly with shells and CI.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    from pathlib import Path as _Path

    source = _Path(args.source)
    if not source.is_dir():
        logger.error("source directory does not exist: %s", source)
        return 1

    try:
        counts = asyncio.run(
            _run(source, args.target, args.guild_id, dry_run=args.dry_run)
        )
    except MigrationError as exc:
        # Corrupt-but-parseable source data: the message already names the file,
        # record, and field, so report it plainly without a raw traceback.
        logger.error("migration failed — corrupt source data: %s", exc)
        return 1
    except OSError as exc:
        # File / IO problems (unreadable source file, target path issues).
        logger.error("migration failed — I/O error: %s", exc)
        return 1

    if args.report:
        _print_report(counts)

    total = sum(counts.values())
    if args.dry_run:
        logger.info(
            "dry-run complete: %d row(s) across %d tables (no writes persisted)",
            total,
            len(counts),
        )
    else:
        logger.info(
            "migration complete: %d row(s) across %d tables", total, len(counts)
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
