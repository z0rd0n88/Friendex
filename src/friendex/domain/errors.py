"""Exception taxonomy for Friendex.

Two parallel base classes — never confuse them:

* :class:`DomainError` and its subclasses represent **game-rule violations**.
  Each carries a ``user_facing_message`` that the Discord error handler can
  relay directly to the user.
* :class:`FriendexError` and its subclasses represent **infrastructure
  failures** (persistence, Discord adapter). They are logged and surfaced
  as generic "internal error" messages — never shown verbatim to users.

``PersistenceError`` deliberately does NOT inherit from ``DomainError`` so
the seam between user-facing rule violations and operator-facing system
failures is enforced by the type system.

Derived from ``docs/02-target-architecture.md`` §Error Handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import time
    from decimal import Decimal


class DomainError(Exception):
    """Base class for game-rule violations shown to the user."""

    def __init__(self, user_facing_message: str) -> None:
        super().__init__(user_facing_message)
        self.user_facing_message = user_facing_message


class FriendexError(Exception):
    """Base class for infrastructure failures (persistence, Discord, etc.)."""


class PersistenceError(FriendexError):
    """Raised when a persistence layer operation fails."""

    def __init__(self, operation: str, detail: str) -> None:
        super().__init__(f"persistence error during {operation}: {detail}")
        self.operation = operation
        self.detail = detail


class DiscordError(FriendexError):
    """Raised when the Discord adapter encounters a failure."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class InsufficientFunds(DomainError):
    def __init__(self, need: Decimal, have: Decimal) -> None:
        super().__init__(f"Insufficient funds: need ${need:,.2f}, have ${have:,.2f}.")
        self.need = need
        self.have = have


class MarketClosed(DomainError):
    def __init__(self, open_at: time, close_at: time) -> None:
        super().__init__(
            "Market is closed "
            f"(hours: {open_at.strftime('%H:%M')}-{close_at.strftime('%H:%M')})."
        )
        self.open_at = open_at
        self.close_at = close_at


class PositionFrozen(DomainError):
    def __init__(self, target_id: str) -> None:
        super().__init__(
            f"Position on <@{target_id}> is frozen — wait for the cooldown."
        )
        self.target_id = target_id


class OnCooldown(DomainError):
    def __init__(self, seconds_remaining: int) -> None:
        super().__init__(f"On cooldown — {seconds_remaining}s remaining.")
        self.seconds_remaining = seconds_remaining


class OptedOut(DomainError):
    def __init__(self, target_id: str) -> None:
        super().__init__(f"<@{target_id}> has opted out of trading.")
        self.target_id = target_id


class NoPosition(DomainError):
    """Raised when a user tries to act on a long/short position they do not hold.

    ``position_type`` is narrowed to ``Literal["long", "short"]`` (#84 L) so
    a callsite typo (``"shor"``) surfaces as a static type error rather than
    only as a wonky user-visible message.
    """

    def __init__(
        self,
        target_id: str,
        position_type: Literal["long", "short"],
    ) -> None:
        super().__init__(f"You have no {position_type} position on <@{target_id}>.")
        self.target_id = target_id
        self.position_type: Literal["long", "short"] = position_type


class InsufficientShares(DomainError):
    def __init__(self, requested: int, held: int) -> None:
        super().__init__(f"Insufficient shares: requested {requested}, hold {held}.")
        self.requested = requested
        self.held = held


class SelfTrade(DomainError):
    def __init__(self) -> None:
        super().__init__("You cannot trade your own stock.")


class InvalidAmount(DomainError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"Invalid amount: {reason}.")
        self.reason = reason


class FundInsufficientBalance(DomainError):
    def __init__(self, need: Decimal, have: Decimal) -> None:
        super().__init__(f"Fund balance too low: need ${need:,.2f}, have ${have:,.2f}.")
        self.need = need
        self.have = have


class AlreadyOptedIn(DomainError):
    def __init__(self) -> None:
        super().__init__("You are already opted in.")


class AlreadyOptedOut(DomainError):
    def __init__(self) -> None:
        super().__init__("You are already opted out.")


class AlreadyClaimedToday(DomainError):
    """Raised by :meth:`DailyService.claim_daily` on a same-day repeat claim.

    The original spec (``original-skeleton.md:960``) gates the daily reward on
    a 24-hour cooldown from the previous ``last_claim``; a second claim inside
    that window surfaces this domain error so the Discord error handler can
    relay the user-facing copy directly.
    """

    def __init__(self, seconds_remaining: int) -> None:
        hours, remainder = divmod(seconds_remaining, 3600)
        minutes = remainder // 60
        super().__init__(
            f"You already claimed your daily reward! Next claim in {hours}h {minutes}m."
        )
        self.seconds_remaining = seconds_remaining


class FundNotFound(DomainError):
    """Raised when an operation targets a hedge fund that does not exist.

    Pre-#82 H17 ``FundService.invest`` and ``send_to_events`` repurposed
    :class:`InvalidAmount` to signal a missing-fund condition — semantically
    wrong (the amount was fine; the *fund* was the problem) and impossible
    for callers to discriminate from a real "amount must be positive" error.
    A dedicated class restores a 1-to-1 mapping between fault and exception
    type and lets the Discord error handler render the user-facing copy
    cleanly.
    """

    def __init__(self, fund_id: str) -> None:
        super().__init__(f"Fund <@{fund_id}> does not exist.")
        self.fund_id = fund_id


class NotFundManager(DomainError):
    """Raised when a user without manager rights tries a manager-only action.

    Currently covers two paths:

    * ``FundService.invest`` rejects the fund's manager investing in their
      own fund (a tautological move). Pre-#82 H17 this branched into a
      repurposed :class:`InvalidAmount` ``"cannot invest in own fund"``.
    * Future manager-gated actions (rename, delete, settings) share the
      same exception type for a single discriminator.

    The user-facing message is action-specific so the same exception can
    surface different copy depending on the rejected path.
    """

    def __init__(self, user_facing_message: str) -> None:
        super().__init__(user_facing_message)
