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
from decimal import Decimal
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Any, Literal

import structlog
from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

if TYPE_CHECKING:
    from collections.abc import MutableMapping

_PLACEHOLDER_TOKEN = "your_bot_token_here"

# Module-level structlog logger for validator diagnostics.  ``parse_int_list``
# logs malformed env entries through this so a bad VC_PING_ROLE_IDS token does
# not silently kill the list during boot.
_log = structlog.get_logger(__name__)


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
    #
    # ``SecretStr`` keeps the raw value out of ``repr(settings)``,
    # ``str(settings)``, and any default exception/log rendering.  Call
    # ``settings.discord_token.get_secret_value()`` once at the consumer
    # boundary (only ``main.py`` does this today) and never again.
    discord_token: SecretStr
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
    # Open-Q2 toggle: when True the ``/buy`` slash command treats Sunday as a
    # normal trading day (preserves the original spec's Sunday exception);
    # set to False to make Sunday a fully closed day for buys as well.
    # ``/sell``, ``/short``, ``/cover`` ignore this flag (they always reject
    # Sunday). See ``docs/02-target-architecture.md`` §Open-Questions Q2.
    sunday_buy_allowed: bool = True
    # Open-Q3 toggle: when True an ``opt_in=False`` target raises
    # :class:`~friendex.domain.errors.OptedOut` from every trade direction;
    # set to False to make opt-out advisory (the user no longer appears in
    # opt-in-only listings but trades against them still succeed). See
    # ``docs/02-target-architecture.md`` §Open-Questions Q3.
    opt_out_blocks_trading: bool = True

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
    # Open-Q8 toggle: cadence over which ``settings.hedge_fund_base_apy`` is
    # accrued by :meth:`FundService.accrue_apy` — ``"monthly"`` credits
    # ``balance * apy / 12`` (the historic Phase-8e behaviour), ``"annual"``
    # credits the full ``balance * apy`` in a single call. See
    # ``docs/02-target-architecture.md`` §Open-Questions Q8.
    hedge_fund_base_apy_period: Literal["monthly", "annual"] = "monthly"
    early_withdraw_penalty: float = 0.05
    penalty_duration_days: int = 14

    # Logging
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # ------------------------------------------------------------------
    # Decimal-typed views of float-sourced money / rate fields (#82 H20)
    # ------------------------------------------------------------------
    #
    # The source fields above stay ``float`` so the existing pydantic-settings
    # env-var binding contract (``INITIAL_CASH=10000`` parses as ``float``)
    # is preserved verbatim.  Services that need a :class:`~decimal.Decimal`
    # for money math should read the ``*_d`` computed counterpart below
    # rather than re-constructing ``Decimal(str(settings.foo))`` at every
    # call site (~20 of those existed at the time the field was added;
    # consolidating them is deferred to the ``simplify/dead-code-sweep``
    # branch).
    #
    # ``Decimal(str(value))`` is the only safe ``float → Decimal`` conversion
    # — the documented pattern that preserves the literal env-string value
    # without introducing binary float artefacts.
    #
    # **Cosmetic note (review #87 L2):**  these properties return values
    # such as ``Decimal("10000.0")`` (one decimal place) rather than the
    # project-canonical ``Decimal("10000.00")`` (two decimals, cents-quantised).
    # The two forms are **semantically equivalent** (``==`` and ``hash``
    # match) and every downstream call site round-trips money through
    # ``_quantise(...)`` before storing or comparing — so functionally
    # this is a no-op.  We intentionally do NOT pre-quantise inside the
    # property because doing so would couple this module to the cents
    # contract, and any future field added here (e.g. a non-money rate)
    # would have to override the quantisation.  The trade-off: a future log
    # line that renders one of these values raw will show ``10000.0``,
    # not ``10000.00`` — accept this in exchange for the looser coupling.
    #
    # **Repetition note (review #87 L1):**  the 15 properties below all
    # follow the same ``Decimal(str(self.foo))`` shape.  The duplication is
    # intentional — each is a separate ``@computed_field`` for pydantic's
    # schema and serialisation, and ``@computed_field`` does not compose
    # cleanly with a meta-factory or decorator stack.  Keep the pattern
    # uniform so a new ``*_d`` view costs three lines and stays grep-able.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def initial_cash_d(self) -> Decimal:
        return Decimal(str(self.initial_cash))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def initial_price_d(self) -> Decimal:
        return Decimal(str(self.initial_price))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def daily_reward_d(self) -> Decimal:
        return Decimal(str(self.daily_reward))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def streak_bonus_d(self) -> Decimal:
        return Decimal(str(self.streak_bonus))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def min_price_d(self) -> Decimal:
        return Decimal(str(self.min_price))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def price_impact_k_d(self) -> Decimal:
        return Decimal(str(self.price_impact_k))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def inactivity_decay_d(self) -> Decimal:
        return Decimal(str(self.inactivity_decay))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def liquidation_threshold_d(self) -> Decimal:
        return Decimal(str(self.liquidation_threshold))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def discipline_penalty_d(self) -> Decimal:
        return Decimal(str(self.discipline_penalty))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def activity_tick_k_d(self) -> Decimal:
        return Decimal(str(self.activity_tick_k))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def vc_extra_boost_multiplier_d(self) -> Decimal:
        return Decimal(str(self.vc_extra_boost_multiplier))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def voice_ping_join_boost_d(self) -> Decimal:
        return Decimal(str(self.voice_ping_join_boost))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def voice_stay_boost_d(self) -> Decimal:
        return Decimal(str(self.voice_stay_boost))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hedge_fund_base_apy_d(self) -> Decimal:
        return Decimal(str(self.hedge_fund_base_apy))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def early_withdraw_penalty_d(self) -> Decimal:
        return Decimal(str(self.early_withdraw_penalty))

    @field_validator(
        "vc_ping_role_ids",
        "photo_bonus_channel_ids",
        mode="before",
    )
    @classmethod
    def parse_int_list(cls, v: object, info: ValidationInfo) -> list[int]:
        """Split comma-separated env strings into ``list[int]``.

        ``pydantic-settings`` v2 does not auto-split comma-separated env
        strings into list fields, so we normalise here.  Empty strings and
        already-parsed lists are tolerated.

        Per-token recovery: a malformed entry (one that cannot be parsed as
        ``int``) is logged as a structured warning and dropped.  Previously
        a single bad token raised ``ValueError`` and the whole field rolled
        back to its default with no operator-visible diagnostic.

        The warning binds the owning field name from
        :class:`~pydantic.ValidationInfo` so an operator reading the log
        line knows *which* list (``vc_ping_role_ids`` vs
        ``photo_bonus_channel_ids``) carried the bad token without
        having to grep the env file.
        """
        if v is None:
            return []
        if isinstance(v, str):
            tokens: list[str] = [x.strip() for x in v.split(",") if x.strip()]
        elif isinstance(v, list):
            tokens = [str(x).strip() for x in v]
        else:
            raise ValueError(
                f"Expected str or list for int-list field, got {type(v).__name__}"
            )

        parsed: list[int] = []
        for token in tokens:
            try:
                parsed.append(int(token))
            except ValueError:
                # Bind the bad value under ``value`` rather than ``token`` so the
                # structlog redaction processor (which scrubs ``token`` /
                # ``discord_token`` keys) does not eat our diagnostic.  Bind the
                # owning field name under ``field`` so the operator can tell
                # which list (``vc_ping_role_ids`` vs ``photo_bonus_channel_ids``)
                # produced the bad token.
                _log.warning(
                    "config.parse_int_list.malformed_token",
                    field=info.field_name,
                    value=token,
                )
        return parsed

    @model_validator(mode="after")
    def validate_secrets(self) -> Settings:
        """Reject empty / placeholder tokens at boot time."""
        raw_token = self.discord_token.get_secret_value()
        if raw_token in ("", _PLACEHOLDER_TOKEN):
            raise ValueError("DISCORD_TOKEN is not configured")
        return self


# Structlog event-dict keys whose value is scrubbed before serialisation.
#
# DANGER — narrow contract:
# This tuple is an **allow-list of key names**, NOT a content filter.  A raw
# token bound under any key that is NOT listed here WILL leak verbatim to
# the log sink.  The two watched keys are:
#
# * ``"discord_token"`` — the canonical :class:`Settings` field name.
# * ``"token"`` — the generic alias used by callers outside this module.
#
# Sibling keys like ``bot_token``, ``auth_token``, ``api_key``, ``secret``,
# ``password``, etc. are **NOT** scrubbed.  The intentional reliance on
# :class:`~pydantic.SecretStr.__str__` (which masks the value as
# ``"**********"`` regardless of binding key) is the only thing that
# prevents a leak when a ``SecretStr`` instance is logged under a
# non-watched key — the moment a caller pulls the raw ``str`` via
# ``.get_secret_value()`` (only ``main.py:52`` does this today, by
# necessity for ``discord.Client.start``) any subsequent binding of that
# raw value under a non-watched key is unprotected.
#
# If we ever grow a second consumer of ``.get_secret_value()`` the
# correct fix is to **either** bind the value under one of the watched
# keys above, **or** extend this tuple — *not* to assume the redactor
# will catch the leak.
_REDACTED_KEYS: tuple[str, ...] = ("discord_token", "token")


def redact_token(
    logger: Any,
    method: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Structlog processor that scrubs token-bearing keys from every record.

    Installed as the *first* processor in the chain so it runs before any
    serialisation.  The watched keys are listed in :data:`_REDACTED_KEYS`:

    * ``"discord_token"`` — the canonical field name on :class:`Settings`,
      so a slip like ``log.info("boot", discord_token=settings.discord_token)``
      never leaks.
    * ``"token"`` — kept for callers that bind the value under a generic
      key from outside the config module.

    Any non-empty value under a watched key is replaced with the literal
    string ``"REDACTED"``.  :class:`~pydantic.SecretStr` already redacts
    itself when formatted, but the processor stays in place as a
    belt-and-braces guarantee in case a caller passes
    ``.get_secret_value()`` directly.

    .. warning::

       The watched-key contract is narrow — see the docstring on
       :data:`_REDACTED_KEYS` for the full DANGER note.  Raw tokens
       (the result of ``SecretStr.get_secret_value()``) bound under any
       key other than ``"discord_token"`` or ``"token"`` will leak.
    """
    for key in _REDACTED_KEYS:
        value = event_dict.get(key)
        if value:
            event_dict[key] = "REDACTED"
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

    Constructs via the canonical no-argument ``Settings()`` rather than
    ``Settings.model_validate({})``.  ``model_validate`` is documented in
    pydantic-settings as a non-canonical entry point — using it can suppress
    the env / ``.env`` source pipeline in subtle ways and was the cause of a
    "DISCORD_TOKEN is silently ignored at runtime" regression flagged in
    issue #84.
    """
    # ``Settings()`` resolves ``discord_token`` from the env / ``.env`` source
    # pipeline at runtime, which mypy can't see — pydantic's static stubs
    # treat the field as required-at-construction.  The ``call-arg`` ignore is
    # scoped to this one line so the rest of the type-safety surface stays
    # honest.  Two structural alternatives (a ``Settings.from_env()`` factory
    # whose return type encodes the env-load contract, or a project-wide
    # mypy override for the pydantic-settings constructor pattern) are
    # tracked as a follow-up — see review #87 M2.  Same ignore lives at
    # ``main.py:44`` for the same reason.
    return Settings()  # type: ignore[call-arg]
