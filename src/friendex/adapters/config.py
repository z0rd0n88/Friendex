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
from pydantic import Field, field_validator, model_validator
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
    # Commands are slash commands (``app_commands``), registered with Discord
    # and synced to ``guild_id`` for instant availability — so there is no
    # message-content command prefix.  ``guild_id`` doubles as the home guild
    # whose command tree the bot syncs in ``setup_hook``.
    discord_token: str
    guild_id: int

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

    # Photo bonus
    photo_bonus_channel_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list,
    )

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
