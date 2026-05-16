"""Tests for ``friendex.domain.errors``.

Every :class:`DomainError` subclass carries a ``user_facing_message`` ready
for the Discord error handler. Those messages are part of the public
contract — they appear verbatim in user replies — so we assert the exact
content for each subclass.

The :class:`PersistenceError` / :class:`DomainError` seam is also enforced
here: a persistence failure must never be classified as a game-rule
violation, because the Discord error handler dispatches on
``isinstance(exc, DomainError)`` to decide whether the exception's message
is safe to show to the user.
"""

from datetime import time

import pytest

from friendex.domain.errors import (
    AlreadyOptedIn,
    AlreadyOptedOut,
    DiscordError,
    DomainError,
    FriendexError,
    FundInsufficientBalance,
    InsufficientFunds,
    InsufficientShares,
    InvalidAmount,
    MarketClosed,
    NoPosition,
    OnCooldown,
    OptedOut,
    PersistenceError,
    PositionFrozen,
    SelfTrade,
)

# ---------------------------------------------------------------------------
# Class hierarchy invariants
# ---------------------------------------------------------------------------


def test_persistence_error_is_not_a_domain_error() -> None:
    """The seam: infra failures must never be classified as user-rule violations."""
    assert not issubclass(PersistenceError, DomainError)


def test_discord_error_is_not_a_domain_error() -> None:
    assert not issubclass(DiscordError, DomainError)


def test_persistence_error_is_friendex_error() -> None:
    assert issubclass(PersistenceError, FriendexError)


def test_discord_error_is_friendex_error() -> None:
    assert issubclass(DiscordError, FriendexError)


def test_domain_error_is_not_a_friendex_error() -> None:
    """Domain errors live in a parallel branch of the exception tree."""
    assert not issubclass(DomainError, FriendexError)


@pytest.mark.parametrize(
    "subclass",
    [
        InsufficientFunds,
        MarketClosed,
        PositionFrozen,
        OnCooldown,
        OptedOut,
        NoPosition,
        InsufficientShares,
        SelfTrade,
        InvalidAmount,
        FundInsufficientBalance,
        AlreadyOptedIn,
        AlreadyOptedOut,
    ],
)
def test_every_domain_subclass_inherits_from_domain_error(
    subclass: type[DomainError],
) -> None:
    assert issubclass(subclass, DomainError)


# ---------------------------------------------------------------------------
# Base classes carry the constructor-supplied message
# ---------------------------------------------------------------------------


def test_domain_error_base_carries_message() -> None:
    err = DomainError("custom message")
    assert err.user_facing_message == "custom message"
    assert str(err) == "custom message"


def test_persistence_error_carries_operation_and_detail() -> None:
    err = PersistenceError(operation="save_users", detail="disk full")
    assert err.operation == "save_users"
    assert err.detail == "disk full"
    assert "save_users" in str(err)
    assert "disk full" in str(err)


def test_discord_error_carries_detail() -> None:
    err = DiscordError(detail="bot disconnected")
    assert err.detail == "bot disconnected"
    assert str(err) == "bot disconnected"


# ---------------------------------------------------------------------------
# Each DomainError subclass produces a well-formed user-facing message
# ---------------------------------------------------------------------------


def test_insufficient_funds_message() -> None:
    err = InsufficientFunds(need=1234.5, have=10.0)
    assert err.need == 1234.5
    assert err.have == 10.0
    assert err.user_facing_message == (
        "Insufficient funds: need $1,234.50, have $10.00."
    )


def test_market_closed_message_well_formed() -> None:
    err = MarketClosed(open_at=time(6, 30), close_at=time(4, 30))
    assert err.open_at == time(6, 30)
    assert err.close_at == time(4, 30)
    assert err.user_facing_message == ("Market is closed (hours: 06:30-04:30).")


def test_market_closed_padding_for_single_digit_hour() -> None:
    err = MarketClosed(open_at=time(9, 5), close_at=time(0, 0))
    assert "09:05" in err.user_facing_message
    assert "00:00" in err.user_facing_message


def test_position_frozen_message() -> None:
    err = PositionFrozen(target_id="123456789")
    assert err.target_id == "123456789"
    assert err.user_facing_message == (
        "Position on <@123456789> is frozen — wait for the cooldown."
    )


def test_on_cooldown_message() -> None:
    err = OnCooldown(seconds_remaining=42)
    assert err.seconds_remaining == 42
    assert err.user_facing_message == "On cooldown — 42s remaining."


def test_opted_out_message() -> None:
    err = OptedOut(target_id="555")
    assert err.target_id == "555"
    assert err.user_facing_message == "<@555> has opted out of trading."


def test_no_position_message() -> None:
    err = NoPosition(target_id="777", position_type="long")
    assert err.target_id == "777"
    assert err.position_type == "long"
    assert err.user_facing_message == ("You have no long position on <@777>.")


def test_no_position_message_short_variant() -> None:
    err = NoPosition(target_id="777", position_type="short")
    assert err.user_facing_message == ("You have no short position on <@777>.")


def test_insufficient_shares_message() -> None:
    err = InsufficientShares(requested=100, held=3)
    assert err.requested == 100
    assert err.held == 3
    assert err.user_facing_message == ("Insufficient shares: requested 100, hold 3.")


def test_self_trade_message() -> None:
    err = SelfTrade()
    assert err.user_facing_message == "You cannot trade your own stock."


def test_invalid_amount_message() -> None:
    err = InvalidAmount(reason="negative shares")
    assert err.reason == "negative shares"
    assert err.user_facing_message == "Invalid amount: negative shares."


def test_fund_insufficient_balance_message() -> None:
    err = FundInsufficientBalance(need=500.0, have=10.5)
    assert err.need == 500.0
    assert err.have == 10.5
    assert err.user_facing_message == (
        "Fund balance too low: need $500.00, have $10.50."
    )


def test_already_opted_in_message() -> None:
    err = AlreadyOptedIn()
    assert err.user_facing_message == "You are already opted in."


def test_already_opted_out_message() -> None:
    err = AlreadyOptedOut()
    assert err.user_facing_message == "You are already opted out."


# ---------------------------------------------------------------------------
# DomainError subclasses can be caught polymorphically
# ---------------------------------------------------------------------------


def test_caught_as_domain_error() -> None:
    with pytest.raises(DomainError) as exc_info:
        raise InsufficientFunds(need=10.0, have=1.0)
    assert isinstance(exc_info.value, InsufficientFunds)


def test_persistence_error_caught_as_friendex_error() -> None:
    with pytest.raises(FriendexError):
        raise PersistenceError(operation="x", detail="y")


def test_persistence_error_not_caught_by_domain_handler() -> None:
    """If a handler dispatches on ``DomainError`` it must miss ``PersistenceError``."""
    caught: list[str] = []
    try:
        raise PersistenceError(operation="save", detail="boom")
    except DomainError:  # pragma: no cover - must not run
        caught.append("domain")
    except FriendexError:
        caught.append("infra")
    assert caught == ["infra"]
