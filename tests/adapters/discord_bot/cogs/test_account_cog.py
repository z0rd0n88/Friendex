"""Tests for :class:`AccountCog` — ``/balance``, ``/optin``, ``/optout``.

Each command callback is invoked directly via ``Cog.command.callback(...)``
since ``dpytest`` simulates message events, not slash interactions. Assertions
walk ``interaction.response.send_message.call_args`` and the embed's
``to_dict()`` (Phase 10 review convention).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import discord
import structlog

from friendex.adapters.discord_bot.cogs.account_cog import AccountCog
from friendex.adapters.discord_bot.embeds import (
    COLOR_NEUTRAL,
    COLOR_SUCCESS,
    build_intro_embed,
)
from friendex.application.snapshot_models import PortfolioSnapshot

# ---------------------------------------------------------------------------
# Helpers


def _snapshot(
    *,
    user_id: str = "9876543210",
    cash: Decimal = Decimal("9500.00"),
    net_worth: Decimal = Decimal("12345.67"),
    fund: Decimal = Decimal("500.00"),
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        user_id=user_id,
        cash_balance=cash,
        net_worth=net_worth,
        month_start_net_worth=Decimal("10000.00"),
        fund_balance=fund,
        long_positions={},
        short_positions={},
    )


def _send_call_kwargs(interaction) -> dict:  # type: ignore[no-untyped-def]
    """Return the kwargs dict of the last user-visible reply.

    Wave 1 (issue #82 H13) routed every cog reply through
    ``interaction.followup.send`` after ``interaction.response.defer(...)``.
    The helper inspects ``followup.send`` (the new reply seam).
    """
    assert interaction.followup.send.await_count >= 1
    return interaction.followup.send.await_args.kwargs


# ---------------------------------------------------------------------------
# /balance


async def test_balance_calls_portfolio_snapshot_for_invoking_user(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    portfolio_service.portfolio_snapshot.return_value = _snapshot()
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction(user_id=4242, guild_id=1010)
    await AccountCog.balance.callback(cog, interaction)
    portfolio_service.portfolio_snapshot.assert_awaited_once_with(user_id="4242")


async def test_balance_routes_through_per_guild_factory(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """The factory must be called with ``str(interaction.guild.id)``."""
    portfolio_service.portfolio_snapshot.return_value = _snapshot()
    seen_guild_ids: list[str] = []

    def factory(guild_id: str):
        seen_guild_ids.append(guild_id)
        return portfolio_service

    cog = AccountCog(
        portfolio_service_factory=factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction(user_id=1, guild_id=999)
    await AccountCog.balance.callback(cog, interaction)
    assert seen_guild_ids == ["999"]


async def test_balance_reply_is_ephemeral_and_uses_balance_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    portfolio_service.portfolio_snapshot.return_value = _snapshot(
        cash=Decimal("1234.50")
    )
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()
    await AccountCog.balance.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    # Mutation-hardening: ephemeral flag is load-bearing.
    assert kwargs.get("ephemeral") is True
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "$1,234.50" in rendered


async def test_balance_with_no_account_replies_ephemerally(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``portfolio_snapshot`` returning ``None`` still yields an ephemeral reply."""
    portfolio_service.portfolio_snapshot.return_value = None
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()
    await AccountCog.balance.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True
    # The reply still carries an embed so the user sees structured output.
    assert isinstance(kwargs.get("embed"), discord.Embed)


# ---------------------------------------------------------------------------
# /optin · /optout


def _attach_user_send(interaction, *, raises: BaseException | None = None) -> AsyncMock:
    """Attach an :class:`AsyncMock` ``send`` to ``interaction.user``.

    The conftest's ``fake_interaction`` leaves ``interaction.user`` as a
    permissive :class:`MagicMock`, so ``interaction.user.send`` would be a
    :class:`MagicMock` (not awaitable). Q10's auto-DM intro path awaits it —
    every C2 test pins the spelling here.
    """
    send = AsyncMock(name="user.send")
    if raises is not None:
        send.side_effect = raises
    interaction.user.send = send
    return send


async def test_optin_first_time_dms_intro_and_acks_ephemerally(
    fake_interaction,  # type: ignore[no-untyped-def]
    activity_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """C2(a): first-time /optin (``opt_in_and_consume_intro`` returns ``True``)
    fires the intro DM AND still sends the ephemeral confirmation.
    """
    activity_service.opt_in_and_consume_intro.return_value = True
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction(user_id=4242, guild_id=99)
    user_send = _attach_user_send(interaction)

    await AccountCog.optin.callback(cog, interaction)

    activity_service.opt_in_and_consume_intro.assert_awaited_once_with("4242")
    # Intro DM fired exactly once with the canonical intro embed.
    assert user_send.await_count == 1
    dm_kwargs = user_send.await_args.kwargs
    assert dm_kwargs["embed"].to_dict() == build_intro_embed().to_dict()
    # AllowedMentions.none() is load-bearing (Phase 10 invariant).
    assert isinstance(dm_kwargs["allowed_mentions"], discord.AllowedMentions)
    # Ephemeral confirmation still goes through (Discord 3 s ack invariant).
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True
    assert isinstance(kwargs["embed"], discord.Embed)
    # The ephemeral reply carries ONE embed (the confirmation) — the intro
    # rode on the DM.
    assert "embeds" not in kwargs


async def test_optin_subsequent_does_not_dm(
    fake_interaction,  # type: ignore[no-untyped-def]
    activity_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """C2(b): a /optin after the intro has been consumed does NOT DM."""
    activity_service.opt_in_and_consume_intro.return_value = False
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction(user_id=4242, guild_id=99)
    user_send = _attach_user_send(interaction)

    await AccountCog.optin.callback(cog, interaction)

    activity_service.opt_in_and_consume_intro.assert_awaited_once_with("4242")
    assert user_send.await_count == 0
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True
    assert isinstance(kwargs["embed"], discord.Embed)
    data = kwargs["embed"].to_dict()
    assert data["color"] == COLOR_SUCCESS.value


async def test_optin_dm_closed_falls_back_to_ephemeral_with_intro_attached(
    fake_interaction,  # type: ignore[no-untyped-def]
    activity_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """C2(c): ``discord.Forbidden`` on the DM falls back — the ephemeral
    reply carries TWO embeds (intro + confirmation) so the user still sees
    the intro inline.
    """
    activity_service.opt_in_and_consume_intro.return_value = True
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction(user_id=4242, guild_id=99)
    # ``discord.Forbidden`` requires a response object — build the minimum
    # the constructor needs.
    forbidden = discord.Forbidden(
        response=_DummyResponse(status=403, reason="Forbidden"),
        message="Cannot send messages to this user",
    )
    user_send = _attach_user_send(interaction, raises=forbidden)

    await AccountCog.optin.callback(cog, interaction)

    # The cog attempted the DM exactly once before falling back.
    assert user_send.await_count == 1
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True
    # Fallback signature: ``embeds=[intro, confirmation]``.
    embeds = kwargs.get("embeds")
    assert isinstance(embeds, list)
    assert len(embeds) == 2
    assert embeds[0].to_dict() == build_intro_embed().to_dict()
    assert embeds[1].to_dict()["color"] == COLOR_SUCCESS.value
    # The single-embed kwarg must NOT be set when ``embeds=...`` is used.
    assert "embed" not in kwargs


async def test_optin_logs_when_intro_dm_is_forbidden(
    fake_interaction,  # type: ignore[no-untyped-def]
    activity_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """Phase 17 follow-up (17c INFO carry-forward): the ``discord.Forbidden``
    DM-fallback path must emit ONE structured ``INFO`` log so operators can
    see how often the intro DM gets blocked.

    The cog already swallows ``Forbidden`` silently (Phase 17c shipped that
    fallback). This pin adds observability without changing user-visible
    behaviour: same fallback, plus a single log record with stable event
    name + ``user_id`` / ``guild_id`` keys. The log MUST fire BEFORE the
    fallback send so the failure is recorded even if the fallback itself
    later fails. Embed contents are deliberately NOT logged.

    PR #94 review (M1): pre-fix this callsite used ``logging.getLogger`` +
    ``extra={...}`` so the structured fields were silently dropped at the
    production ``%(message)s`` formatter. The migration to structlog routes
    ``user_id`` / ``guild_id`` as top-level keys via the JSON renderer.
    Capture mechanism switches from ``caplog`` to
    ``structlog.testing.capture_logs()`` for the same reason as the
    ``voice_listener`` test — the production logger factory bypasses
    stdlib.
    """
    activity_service.opt_in_and_consume_intro.return_value = True
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction(user_id=4242, guild_id=99)
    forbidden = discord.Forbidden(
        response=_DummyResponse(status=403, reason="Forbidden"),
        message="Cannot send messages to this user",
    )
    _attach_user_send(interaction, raises=forbidden)

    with structlog.testing.capture_logs() as captured:
        await AccountCog.optin.callback(cog, interaction)

    matching = [
        rec
        for rec in captured
        if rec.get("event") == "account.optin_intro_dm_forbidden"
    ]
    assert len(matching) == 1, (
        f"expected exactly one 'account.optin_intro_dm_forbidden' structlog "
        f"entry, got {len(matching)} (all captured records: {captured!r})"
    )
    rec = matching[0]
    assert rec["log_level"] == "info"
    # PR #94 review (M1): the structured fields MUST be top-level keys,
    # not nested in an ``extra`` sub-dict — that's the silent-failure trap
    # this fix removes. ``user_id`` / ``guild_id`` are the only operator
    # signals (embed payload is deliberately omitted).
    assert rec["user_id"] == "4242"
    assert rec["guild_id"] == "99"


async def test_optin_consumes_intro_before_acking(
    fake_interaction,  # type: ignore[no-untyped-def]
    activity_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """Phase 17 follow-up (17c LOW-1 pin): the service call MUST run before
    any Discord reply.

    The intro-consume step is an atomic RMW that decides whether the user
    sees the intro DM (or fallback). Acking first would race the consume
    against the Discord 3 s deadline and break the one-shot signal —
    Phase 17c's mutation M2 (ack-first reorder) stayed green because no test
    pinned the ordering. This is that test.

    Uses ``parent.attach_mock`` to fold ``opt_in_and_consume_intro`` and
    every Discord send into one ordered call log, then asserts the FIRST
    recorded call resolves to ``consume`` — proving no Discord side-effect
    runs before the service.
    """
    activity_service.opt_in_and_consume_intro.return_value = False
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction(user_id=4242, guild_id=99)
    user_send = _attach_user_send(interaction)

    parent = MagicMock(name="parent")
    parent.attach_mock(activity_service.opt_in_and_consume_intro, "consume")
    parent.attach_mock(user_send, "user_send")
    parent.attach_mock(interaction.response.send_message, "send_message")

    await AccountCog.optin.callback(cog, interaction)

    # At least one call landed (sanity: the cog ran), and the FIRST
    # recorded call is the service consume — not a Discord send.
    assert parent.mock_calls, "expected at least one attached call"
    first_name = parent.mock_calls[0][0]
    assert first_name == "consume", (
        f"expected first call to be 'consume' (the service), "
        f"got {first_name!r}; full call log: {parent.mock_calls!r}"
    )


async def test_optout_calls_set_opt_in_false(
    fake_interaction,  # type: ignore[no-untyped-def]
    activity_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/optout`` stays on ``set_opt_in(False)`` — Q10 only touches /optin."""
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction(user_id=4242, guild_id=99)
    await AccountCog.optout.callback(cog, interaction)
    activity_service.set_opt_in.assert_awaited_once_with("4242", False)


async def test_optout_reply_is_ephemeral_success_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()
    await AccountCog.optout.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True  # mutation-hardening
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value


class _DummyResponse:
    """Minimal stand-in for the ``aiohttp.ClientResponse`` that
    :class:`discord.Forbidden` requires in its constructor.

    discord.py inspects ``status`` and ``reason`` when formatting the error;
    nothing else is touched at construction time.
    """

    def __init__(self, *, status: int, reason: str) -> None:
        self.status = status
        self.reason = reason


# ---------------------------------------------------------------------------
# Slash-command registration sanity


def test_account_cog_registers_balance_optin_optout_app_commands() -> None:
    """All three commands are registered as ``app_commands.Command`` instances."""
    import discord.app_commands as app_commands

    assert isinstance(AccountCog.balance, app_commands.Command)
    assert isinstance(AccountCog.optin, app_commands.Command)
    assert isinstance(AccountCog.optout, app_commands.Command)


# ---------------------------------------------------------------------------
# Wave 1: defer + followup ack-within-3s contract (issue #82 H13)


async def test_balance_defers_ephemerally_before_followup(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/balance`` defers ephemerally, then sends via followup.

    Mutation-hardening: the defer must run BEFORE the service call (the
    3 s ack deadline is the whole point). Order check uses ``parent.attach_mock``.
    """
    portfolio_service.portfolio_snapshot.return_value = _snapshot()
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()

    parent = MagicMock(name="parent")
    parent.attach_mock(interaction.response.defer, "defer")
    parent.attach_mock(portfolio_service.portfolio_snapshot, "snapshot")
    parent.attach_mock(interaction.followup.send, "followup")

    await AccountCog.balance.callback(cog, interaction)

    # Defer ran with ephemeral=True (the /balance reply is ephemeral).
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    # Followup carried the embed; initial response was NOT used.
    interaction.followup.send.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()

    # Order: defer → service → followup
    call_names = [c[0] for c in parent.mock_calls if c[0]]
    assert call_names[0] == "defer", f"expected defer first, got {call_names!r}"
    assert call_names.index("snapshot") < call_names.index("followup")


async def test_optin_defers_ephemerally(
    fake_interaction,  # type: ignore[no-untyped-def]
    activity_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/optin`` defers ephemerally (Discord 3 s ack deadline)."""
    activity_service.opt_in_and_consume_intro.return_value = False
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()
    _attach_user_send(interaction)

    await AccountCog.optin.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)


async def test_optout_defers_ephemerally(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/optout`` defers ephemerally."""
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()

    await AccountCog.optout.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)


# ---------------------------------------------------------------------------
# Wave 1: @app_commands.guild_only() — every command refuses DM dispatch


def test_account_cog_commands_are_guild_only() -> None:
    """Wave 1 (#82 H14): every command carries ``@app_commands.guild_only``.

    discord.py stamps the decorated command's ``guild_only`` attribute as
    ``True``. The gateway then never dispatches the command in a DM
    context. ``guild_id_of`` raises ``NoPrivateMessage`` as a belt-and-
    braces guard if Discord ever lets one through.
    """
    for cmd in (AccountCog.balance, AccountCog.optin, AccountCog.optout):
        # discord.py 2.4+ exposes this as ``guild_only`` on the Command.
        assert getattr(cmd, "guild_only", None) is True, (
            f"{cmd.name}: must be decorated @app_commands.guild_only()"
        )


# ---------------------------------------------------------------------------
# Issue #84 L — every send carries AllowedMentions.none()
# ---------------------------------------------------------------------------


async def test_balance_passes_allowed_mentions_none(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """Issue #84 L — ``/balance`` must not be able to broadcast mass mentions.

    The balance embed today only contains numeric snapshot data, but the
    I2 carry-forward pins ``allowed_mentions=AllowedMentions.none()`` on
    every cog send so a future embed change that includes a role-mention
    cannot silently ping anyone.
    """
    portfolio_service.portfolio_snapshot.return_value = _snapshot()
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()
    await AccountCog.balance.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    assert isinstance(kwargs.get("allowed_mentions"), discord.AllowedMentions)


async def test_balance_no_account_path_passes_allowed_mentions_none(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """Issue #84 L — the no-account branch of ``/balance`` also carries the guard."""
    portfolio_service.portfolio_snapshot.return_value = None
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()
    await AccountCog.balance.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    assert isinstance(kwargs.get("allowed_mentions"), discord.AllowedMentions)


async def test_optout_passes_allowed_mentions_none(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """Issue #84 L — ``/optout`` must not be able to broadcast mass mentions."""
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()
    await AccountCog.optout.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    assert isinstance(kwargs.get("allowed_mentions"), discord.AllowedMentions)
