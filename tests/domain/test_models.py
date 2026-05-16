"""Tests for ``friendex.domain.models``.

Each model gets:

* a happy-path construction test (with sensible defaults exercised),
* failing-input tests for every ``__post_init__`` invariant
  (must raise ``ValueError``, not ``AssertionError``),
* equality semantics (dataclass-generated ``__eq__``),
* and the ``voice_unique_channels`` int→str normalisation for
  :class:`ActivityBucket`.
"""

from datetime import UTC, datetime, time

import pytest

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
    VcExtraBoost,
    VoicePingSession,
    VoiceSession,
)

NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# ActivityBucket
# ---------------------------------------------------------------------------


def test_activity_bucket_defaults() -> None:
    bucket = ActivityBucket()
    assert bucket.text_msgs == 0
    assert bucket.media_msgs == 0
    assert bucket.voice_minutes == 0.0
    assert bucket.voice_unique_channels == []
    assert bucket.reaction_count == 0
    assert bucket.reply_count == 0
    assert bucket.role_ping_joins == 0.0
    assert bucket.role_ping_join_minutes == 0.0
    assert isinstance(bucket.bucket_start, datetime)


def test_activity_bucket_normalises_int_channels_to_str() -> None:
    bucket = ActivityBucket(voice_unique_channels=[123, 456])  # type: ignore[list-item]
    assert bucket.voice_unique_channels == ["123", "456"]
    assert all(isinstance(c, str) for c in bucket.voice_unique_channels)


def test_activity_bucket_preserves_string_channels() -> None:
    bucket = ActivityBucket(voice_unique_channels=["abc", "789"])
    assert bucket.voice_unique_channels == ["abc", "789"]


def test_activity_bucket_mixed_channel_types_all_become_str() -> None:
    bucket = ActivityBucket(voice_unique_channels=["x", 42, "y"])  # type: ignore[list-item]
    assert bucket.voice_unique_channels == ["x", "42", "y"]


def test_activity_bucket_equality() -> None:
    a = ActivityBucket(text_msgs=5, bucket_start=NOW)
    b = ActivityBucket(text_msgs=5, bucket_start=NOW)
    assert a == b


def test_activity_bucket_inequality() -> None:
    a = ActivityBucket(text_msgs=5, bucket_start=NOW)
    b = ActivityBucket(text_msgs=6, bucket_start=NOW)
    assert a != b


# ---------------------------------------------------------------------------
# DailyProgress
# ---------------------------------------------------------------------------


def test_daily_progress_happy() -> None:
    progress = DailyProgress(last_claim=NOW, streak=3)
    assert progress.last_claim == NOW
    assert progress.streak == 3


def test_daily_progress_zero_streak_ok() -> None:
    progress = DailyProgress(last_claim=None, streak=0)
    assert progress.streak == 0


def test_daily_progress_rejects_negative_streak() -> None:
    with pytest.raises(ValueError, match="streak must be non-negative"):
        DailyProgress(last_claim=NOW, streak=-1)


def test_daily_progress_equality() -> None:
    a = DailyProgress(last_claim=NOW, streak=2)
    b = DailyProgress(last_claim=NOW, streak=2)
    assert a == b


# ---------------------------------------------------------------------------
# LongPosition
# ---------------------------------------------------------------------------


def test_long_position_happy() -> None:
    pos = LongPosition(target_user_id="u1", shares=10, avg_entry=100.0)
    assert pos.shares == 10
    assert pos.avg_entry == 100.0


def test_long_position_rejects_zero_shares() -> None:
    with pytest.raises(ValueError, match="shares must be positive"):
        LongPosition(target_user_id="u1", shares=0, avg_entry=100.0)


def test_long_position_rejects_negative_shares() -> None:
    with pytest.raises(ValueError, match="shares must be positive"):
        LongPosition(target_user_id="u1", shares=-1, avg_entry=100.0)


def test_long_position_rejects_zero_entry() -> None:
    with pytest.raises(ValueError, match="avg_entry must be positive"):
        LongPosition(target_user_id="u1", shares=10, avg_entry=0.0)


def test_long_position_rejects_negative_entry() -> None:
    with pytest.raises(ValueError, match="avg_entry must be positive"):
        LongPosition(target_user_id="u1", shares=10, avg_entry=-5.0)


def test_long_position_equality() -> None:
    a = LongPosition(target_user_id="u1", shares=10, avg_entry=100.0)
    b = LongPosition(target_user_id="u1", shares=10, avg_entry=100.0)
    assert a == b


# ---------------------------------------------------------------------------
# ShortPosition
# ---------------------------------------------------------------------------


def _short(**overrides: object) -> ShortPosition:
    base: dict[str, object] = {
        "target_user_id": "u1",
        "shares": 5,
        "entry_price": 100.0,
        "locked_cash": 250.0,
        "locked_fund": 250.0,
        "created_at": NOW,
    }
    base.update(overrides)
    return ShortPosition(**base)  # type: ignore[arg-type]


def test_short_position_happy() -> None:
    pos = _short()
    assert pos.shares == 5
    assert pos.frozen is False


def test_short_position_frozen_flag_settable() -> None:
    pos = _short(frozen=True)
    assert pos.frozen is True


def test_short_position_rejects_zero_shares() -> None:
    with pytest.raises(ValueError, match="shares must be positive"):
        _short(shares=0)


def test_short_position_rejects_negative_shares() -> None:
    with pytest.raises(ValueError, match="shares must be positive"):
        _short(shares=-3)


def test_short_position_rejects_zero_entry_price() -> None:
    with pytest.raises(ValueError, match="entry_price must be positive"):
        _short(entry_price=0.0)


def test_short_position_rejects_negative_entry_price() -> None:
    with pytest.raises(ValueError, match="entry_price must be positive"):
        _short(entry_price=-1.0)


def test_short_position_rejects_negative_locked_cash() -> None:
    with pytest.raises(ValueError, match="locked collateral must be non-negative"):
        _short(locked_cash=-1.0)


def test_short_position_rejects_negative_locked_fund() -> None:
    with pytest.raises(ValueError, match="locked collateral must be non-negative"):
        _short(locked_fund=-1.0)


def test_short_position_zero_collateral_allowed() -> None:
    pos = _short(locked_cash=0.0, locked_fund=0.0)
    assert pos.locked_cash == 0.0
    assert pos.locked_fund == 0.0


def test_short_position_equality() -> None:
    assert _short() == _short()


# ---------------------------------------------------------------------------
# UserAccount
# ---------------------------------------------------------------------------


def _account(**overrides: object) -> UserAccount:
    base: dict[str, object] = {
        "user_id": "u1",
        "cash_balance": 10_000.0,
        "net_worth": 10_000.0,
        "month_start_net_worth": 10_000.0,
        "long_positions": {},
        "short_positions": {},
        "today": ActivityBucket(),
        "week": ActivityBucket(),
        "daily": DailyProgress(last_claim=None, streak=0),
        "last_activity": NOW,
    }
    base.update(overrides)
    return UserAccount(**base)  # type: ignore[arg-type]


def test_user_account_happy() -> None:
    account = _account()
    assert account.cash_balance == 10_000.0
    assert account.opt_in is True
    assert account.intro_shown is False


def test_user_account_zero_cash_allowed() -> None:
    account = _account(cash_balance=0.0)
    assert account.cash_balance == 0.0


def test_user_account_rejects_negative_cash() -> None:
    with pytest.raises(ValueError, match="cash_balance must be non-negative"):
        _account(cash_balance=-0.01)


def test_user_account_equality() -> None:
    bucket_today = ActivityBucket(bucket_start=NOW)
    bucket_week = ActivityBucket(bucket_start=NOW)
    daily = DailyProgress(last_claim=NOW, streak=1)
    a = _account(today=bucket_today, week=bucket_week, daily=daily)
    b = _account(today=bucket_today, week=bucket_week, daily=daily)
    assert a == b


# ---------------------------------------------------------------------------
# PricePoint
# ---------------------------------------------------------------------------


def test_price_point_happy() -> None:
    point = PricePoint(price=99.5, timestamp=NOW)
    assert point.price == 99.5
    assert point.timestamp == NOW


def test_price_point_equality() -> None:
    a = PricePoint(price=99.5, timestamp=NOW)
    b = PricePoint(price=99.5, timestamp=NOW)
    assert a == b


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------


def _stock(**overrides: object) -> Stock:
    base: dict[str, object] = {
        "user_id": "u1",
        "current": 100.0,
        "history": [],
        "high_24h": 100.0,
        "low_24h": 100.0,
        "all_time_high": 100.0,
    }
    base.update(overrides)
    return Stock(**base)  # type: ignore[arg-type]


def test_stock_happy() -> None:
    stock = _stock()
    assert stock.current == 100.0
    assert stock.history == []


def test_stock_zero_price_allowed() -> None:
    stock = _stock(current=0.0)
    assert stock.current == 0.0


def test_stock_rejects_negative_price() -> None:
    with pytest.raises(ValueError, match="price must be non-negative"):
        _stock(current=-0.01)


def test_stock_equality() -> None:
    a = _stock()
    b = _stock()
    assert a == b


# ---------------------------------------------------------------------------
# HedgeFund
# ---------------------------------------------------------------------------


def _fund(**overrides: object) -> HedgeFund:
    base: dict[str, object] = {
        "fund_id": "u1",
        "name": "Test Fund",
        "manager_id": "u1",
        "cash_balance": 1_000.0,
        "investors": {},
    }
    base.update(overrides)
    return HedgeFund(**base)  # type: ignore[arg-type]


def test_hedge_fund_happy() -> None:
    fund = _fund()
    assert fund.cash_balance == 1_000.0
    assert fund.investors == {}


def test_hedge_fund_zero_cash_allowed() -> None:
    fund = _fund(cash_balance=0.0)
    assert fund.cash_balance == 0.0


def test_hedge_fund_rejects_negative_cash() -> None:
    with pytest.raises(ValueError, match="fund cash must be non-negative"):
        _fund(cash_balance=-0.01)


def test_hedge_fund_equality() -> None:
    a = _fund()
    b = _fund()
    assert a == b


# ---------------------------------------------------------------------------
# FundPenalty
# ---------------------------------------------------------------------------


def test_fund_penalty_happy() -> None:
    penalty = FundPenalty(user_id="u1", penalty_apr=0.05, penalty_until=NOW)
    assert penalty.penalty_apr == 0.05
    assert penalty.penalty_until == NOW


def test_fund_penalty_equality() -> None:
    a = FundPenalty(user_id="u1", penalty_apr=0.05, penalty_until=NOW)
    b = FundPenalty(user_id="u1", penalty_apr=0.05, penalty_until=NOW)
    assert a == b


# ---------------------------------------------------------------------------
# VoiceSession
# ---------------------------------------------------------------------------


def test_voice_session_happy() -> None:
    session = VoiceSession(
        user_id="u1",
        channel_id=12345,
        start=NOW,
        from_ping_message_ids={1, 2, 3},
    )
    assert session.channel_id == 12345
    assert session.from_ping_message_ids == {1, 2, 3}


def test_voice_session_equality() -> None:
    a = VoiceSession(user_id="u1", channel_id=1, start=NOW, from_ping_message_ids=set())
    b = VoiceSession(user_id="u1", channel_id=1, start=NOW, from_ping_message_ids=set())
    assert a == b


# ---------------------------------------------------------------------------
# VoicePingSession
# ---------------------------------------------------------------------------


def test_voice_ping_session_happy() -> None:
    session = VoicePingSession(
        message_id=42,
        host_id="u1",
        channel_id=99,
        timestamp=NOW,
        first_10_joiners=["u2", "u3"],
        extra_joiners=[],
    )
    assert session.first_10_joiners == ["u2", "u3"]
    assert session.extra_joiners == []


def test_voice_ping_session_equality() -> None:
    a = VoicePingSession(
        message_id=42,
        host_id="u1",
        channel_id=99,
        timestamp=NOW,
        first_10_joiners=[],
        extra_joiners=[],
    )
    b = VoicePingSession(
        message_id=42,
        host_id="u1",
        channel_id=99,
        timestamp=NOW,
        first_10_joiners=[],
        extra_joiners=[],
    )
    assert a == b


# ---------------------------------------------------------------------------
# VcExtraBoost
# ---------------------------------------------------------------------------


def test_vc_extra_boost_happy() -> None:
    boost = VcExtraBoost(
        user_id="u1",
        ping_time=NOW,
        last_boost=NOW,
        end_time=NOW,
    )
    assert boost.user_id == "u1"


def test_vc_extra_boost_equality() -> None:
    a = VcExtraBoost(user_id="u1", ping_time=NOW, last_boost=NOW, end_time=NOW)
    b = VcExtraBoost(user_id="u1", ping_time=NOW, last_boost=NOW, end_time=NOW)
    assert a == b


# ---------------------------------------------------------------------------
# Sanity: invariant violations raise ValueError, not AssertionError
# (`assert` would be stripped under `python -O` so this is load-bearing).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: DailyProgress(last_claim=None, streak=-1),
        lambda: LongPosition(target_user_id="u1", shares=0, avg_entry=1.0),
        lambda: ShortPosition(
            target_user_id="u1",
            shares=0,
            entry_price=1.0,
            locked_cash=0.0,
            locked_fund=0.0,
            created_at=NOW,
        ),
        lambda: Stock(
            user_id="u1",
            current=-1.0,
            history=[],
            high_24h=0.0,
            low_24h=0.0,
            all_time_high=0.0,
        ),
        lambda: HedgeFund(
            fund_id="u1",
            name="x",
            manager_id="u1",
            cash_balance=-1.0,
            investors={},
        ),
    ],
)
def test_invariant_violations_raise_valueerror_not_assertion(
    constructor: object,
) -> None:
    with pytest.raises(ValueError):
        constructor()  # type: ignore[operator]


def test_invariant_holds_under_python_optimised_mode_semantics() -> None:
    """`-O` strips ``assert``; the spec amendment requires ``raise ValueError``.

    We cannot easily run a sub-interpreter with ``-O`` from inside a test
    suite, but we can confirm the catch-class is ``ValueError`` (which is
    not stripped) rather than ``AssertionError`` (which would be).
    """
    with pytest.raises(ValueError):
        DailyProgress(last_claim=None, streak=-1)
    # Confirm by negation: AssertionError must NOT be raised.
    try:
        DailyProgress(last_claim=None, streak=-1)
    except AssertionError:  # pragma: no cover - defensive
        pytest.fail("invariant raised AssertionError; would be stripped under -O")
    except ValueError:
        pass


# Cover the `time` import even though not used in models — keep an explicit
# reference so static analysers don't flag the import as unused. (No-op.)
_TIME_SENTINEL = time(0, 0)
