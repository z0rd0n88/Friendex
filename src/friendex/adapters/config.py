"""Application settings and logging configuration.

This module owns all runtime configuration for the bot.  Settings are loaded
from a ``.env`` file (or process environment) via ``pydantic-settings`` and
validated at startup; any deviation from the documented contract is surfaced
as a ``ValidationError`` before the bot is allowed to come online.

The companion :func:`configure_logging` wires :mod:`structlog` with a
``redact_token`` processor that runs first in the chain — so even if a
caller accidentally binds a Discord token to a log record, it never leaves
the process.

Phase 2 deliberately keeps this module free of any import from
``friendex.domain`` or ``friendex.application`` so that the
configuration layer can be exercised in isolation.
"""

from __future__ import annotations

import logging
from datetime import time
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Any, Literal

import structlog
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

if TYPE_CHECKING:
    from collections.abc import MutableMapping

_PLACEHOLDER_TOKEN = "your_bot_token_here"


class Settings(BaseSettings):
    """Strongly-typed runtime configuration loaded from environment.

    Field names map to ``UPPER_SNAKE_CASE`` environment variables (case
    insensitive) and a ``.env`` file at the current working directory.
    See ``docs/02-target-architecture.md`` §Config and Secrets for the
    canonical field list this class mirrors.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Discord
    #
    # Commands are slash commands (``app_commands``) synced **globally**
    # (``bot.tree.sync()`` with no guild argument), so the bot works in every
    # server it is installed in — there is no message-content command prefix.
    #
    # ``dev_guild_id`` is optional and used **only** in development: when set,
    # ``setup_hook`` additionally copies the global command tree to that one
    # guild and syncs it there for instant availability (global command
    # propagation can take up to ~1 hour).  Production leaves it unset.  The
    # legacy ``GUILD_ID`` environment name is still accepted as an alias.  See
    # ADR-0001 (per-guild markets) for why a home guild is no longer required.
    discord_token: str
    dev_guild_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("dev_guild_id", "guild_id"),
    )

    # Database
    database_url: str = "sqlite+aiosqlite:///data/friendex.db"

    # Market hours
    market_open: time = time(6, 30)
    market_close: time = time(4, 30)
    timezone_offset_hours: int = 0

    # Game constants
    initial_cash: float = 10_000.0
    initial_price: float = 100.0
    daily_reward: float = 500.0
    streak_bonus: float = 500.0
    min_price: float = 70.0
    price_impact_k: float = 0.5
    inactivity_threshold_seconds: int = 4 * 3600
    inactivity_decay: float = 0.04
    liquidation_threshold: float = 1.5
    short_freeze_minutes: int = 30
    trade_cooldown_seconds: int = 900
    discipline_penalty: float = 0.17

    # Activity tick
    activity_tick_minutes: int = 15
    # Activity-tick price-return constant ``K`` for ``ΔP = K · ln(1 + score)``
    # (see :func:`friendex.domain.price_engine.compute_activity_return`). Distinct
    # from :attr:`price_impact_k` (which models the per-trade order-book bump).
    #
    # **Initial calibration (2026-05-25).** Original spec leaves K parameterised
    # (``docs/spec/original-skeleton.md`` sets ``ACTIVITY_TICK_MINUTES = 15``
    # but no K; the Phase 4 digest records ``compute_activity_return`` as
    # ``k·ln(1+score)`` with K left parameterised). K=0.3 is the chosen
    # starting point based on game-design math: it keeps moderate users on
    # light pressure-to-engage while damping the runaway price gains K=0.5
    # produced for sustained heavy activity (~+560%/day at 15 units/tick under
    # 15-min cadence). The natural-log shape still rewards activity; the lower
    # K trades raw upside for a more bounded curve. Conservative starting
    # point — **re-tune empirically once Phase 9 wires the live activity
    # tick** against a few representative hourly buckets.
    activity_tick_k: float = 0.3
    # Periodic price multiplier applied by :meth:`PriceTickService.vc_boost_tick`
    # to extra VC responders still in voice (original spec hard-codes ``1.03``).
    vc_extra_boost_multiplier: float = 1.03

    # VC ping
    #
    # ``NoDecode`` keeps pydantic-settings from trying to JSON-decode the
    # raw string value before our :meth:`parse_int_list` validator runs.
    # Without it, ``VC_PING_ROLE_IDS=1,2,3`` would raise a ``SettingsError``
    # because pydantic-settings treats ``list[int]`` as a "complex" type and
    # attempts ``json.loads`` on the value.
    vc_ping_role_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    voice_ping_window_seconds: int = 5400
    fast_response_seconds: int = 120
    medium_response_seconds: int = 300
    vc_extra_boost_interval_seconds: int = 900
    # Number of responders that earn the one-time join price boost; the 11th+
    # joiner is tracked for periodic extra boosts instead (original first-10 cap).
    voice_ping_first_n_joiners: int = 10
    # One-time price multiplier granted to each of the first-N responders.
    voice_ping_join_boost: float = 1.20
    # One-time price multiplier when a user stays >= the stay-bonus threshold.
    voice_stay_boost: float = 1.50
    # Minutes a user must remain in voice to earn the one-time stay boost.
    voice_stay_bonus_minutes: float = 60.0
    # Engagement base points and speed multipliers for a ping responder.
    voice_ping_base_points: float = 5.0
    voice_ping_fast_multiplier: float = 3.0
    voice_ping_medium_multiplier: float = 2.0
    voice_ping_slow_multiplier: float = 1.0
    # Engagement credit the host earns per responder that joins their ping.
    voice_ping_host_credit: float = 0.5

    # Photo bonus
    photo_bonus_channel_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list,
    )
    # Engagement credit (role_ping_join_minutes) for media in a bonus channel.
    photo_bonus_points: float = 10.0

    # Hedge fund
    hedge_fund_base_apy: float = 0.15
    early_withdraw_penalty: float = 0.05
    penalty_duration_days: int = 14

    # Logging
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    @field_validator(
        "vc_ping_role_ids",
        "photo_bonus_channel_ids",
        mode="before",
    )
    @classmethod
    def parse_int_list(cls, v: object) -> list[int]:
        """Split comma-separated env strings into ``list[int]``.

        ``pydantic-settings`` v2 does not auto-split comma-separated env
        strings into list fields, so we normalise here.  Empty strings and
        already-parsed lists are tolerated.
        """
        if v is None:
            return []
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [int(x) for x in v]
        raise ValueError(
            f"Expected str or list for int-list field, got {type(v).__name__}"
        )

    @model_validator(mode="after")
    def validate_secrets(self) -> Settings:
        """Reject empty / placeholder tokens at boot time."""
        if self.discord_token in ("", _PLACEHOLDER_TOKEN):
            raise ValueError("DISCORD_TOKEN is not configured")
        return self


def redact_token(
    logger: Any,
    method: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Structlog processor that scrubs a ``token`` key from every record.

    Installed as the *first* processor in the chain so it runs before any
    serialisation.  Any non-empty ``token`` value is replaced with the
    literal string ``"REDACTED"``.
    """
    token = event_dict.get("token") or ""
    if token:
        event_dict["token"] = "REDACTED"
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Wire :mod:`structlog` and route stdlib loggers through it.

    The processor chain is, in order:

    1. :func:`redact_token` — scrub credentials.
    2. ``merge_contextvars`` — bind any contextvars set via
       ``structlog.contextvars.bind_contextvars``.
    3. ``add_log_level`` — attach the level name.
    4. ``TimeStamper(fmt="iso")`` — ISO-8601 timestamp.
    5. ``StackInfoRenderer`` — render any ``stack_info=True`` records.
    6. ``ExceptionRenderer`` — render exception info if present.
    7. Final renderer — ``JSONRenderer`` in production, ``ConsoleRenderer``
       in development, selected by ``settings.log_format``.

    Noisy stdlib loggers (``discord``, ``sqlalchemy.engine``,
    ``aiosqlite``) are reset so their records flow through the same
    sink rather than competing handlers.
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    final_renderer: structlog.types.Processor
    if settings.log_format == "json":
        final_renderer = structlog.processors.JSONRenderer()
    else:
        final_renderer = structlog.dev.ConsoleRenderer()

    processors: list[structlog.types.Processor] = [
        redact_token,
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
        final_renderer,
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route noisy stdlib loggers through the same handler so a single sink
    # owns the byte stream.  We clear any pre-attached handlers (test
    # harnesses, prior imports) before reconfiguring.
    logging.basicConfig(level=log_level, format="%(message)s", force=True)
    for name in ("discord", "sqlalchemy.engine", "aiosqlite"):
        stdlib_logger = logging.getLogger(name)
        stdlib_logger.handlers.clear()
        stdlib_logger.setLevel(log_level)
        stdlib_logger.propagate = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a memoised :class:`Settings` instance.

    The lru_cache ensures the ``.env`` file is only read once per process;
    tests that need a fresh instance can call ``get_settings.cache_clear()``.
    """
    return Settings.model_validate({})
