"""Tests for ``friendex.adapters.config``.

Covers requirements (a)-(e) from `docs/04-migration-plan.md` §Phase 2,
plus the three Phase 3a corrections from `docs/03-python-review.md`
§Configuration, Secrets, Logging.

These tests intentionally avoid any dependency on the domain or
application layers — they exercise the settings module in isolation.
"""

from __future__ import annotations

import logging
from datetime import time
from pathlib import Path
from typing import Any

import pytest
import structlog
from pydantic import ValidationError

from friendex.adapters.config import (
    Settings,
    configure_logging,
    get_settings,
    redact_token,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_ENV = FIXTURES_DIR / "test.env"


def _load_from_env_file(path: Path) -> Settings:
    """Load a :class:`Settings` instance from an explicit env-file path.

    Bypasses ``get_settings`` so tests get a fresh instance each call and
    can target any fixture file.
    """
    return Settings(_env_file=str(path))  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Reset the lru_cache before and after every test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any settings env vars that may leak in from the harness.

    Without this, a stray ``DISCORD_TOKEN`` exported in the parent shell
    could mask the "missing token raises ValidationError" case.
    """
    for key in (
        "DISCORD_TOKEN",
        "GUILD_ID",
        "COMMAND_PREFIX",
        "DATABASE_URL",
        "MARKET_OPEN",
        "MARKET_CLOSE",
        "TIMEZONE_OFFSET_HOURS",
        "INITIAL_CASH",
        "INITIAL_PRICE",
        "MIN_PRICE",
        "LOG_LEVEL",
        "LOG_FORMAT",
        "VC_PING_ROLE_IDS",
        "PHOTO_BONUS_CHANNEL_IDS",
        "TRADE_COOLDOWN_SECONDS",
        "LIQUIDATION_THRESHOLD",
        "HEDGE_FUND_BASE_APY",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# (a) Loads required fields from a temp .env
# ---------------------------------------------------------------------------


def test_loads_required_fields_from_fixture_env() -> None:
    settings = _load_from_env_file(SAMPLE_ENV)

    assert settings.discord_token == "test-token-abc-123"
    assert settings.guild_id == 111111111111111111


def test_loads_required_fields_from_temp_env(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\n"
        "GUILD_ID=987654321098765432\n",
        encoding="utf-8",
    )

    settings = _load_from_env_file(env_path)

    assert settings.discord_token == "tmp-token"
    assert settings.guild_id == 987654321098765432


# ---------------------------------------------------------------------------
# (b) Raises ValidationError when DISCORD_TOKEN is missing or placeholder
# ---------------------------------------------------------------------------


def test_missing_discord_token_raises_validation_error(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("GUILD_ID=123456789012345678\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        _load_from_env_file(env_path)


def test_placeholder_discord_token_raises_validation_error(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=your_bot_token_here\n"
        "GUILD_ID=123456789012345678\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as excinfo:
        _load_from_env_file(env_path)

    assert "DISCORD_TOKEN is not configured" in str(excinfo.value)


def test_empty_discord_token_raises_validation_error(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=\n"
        "GUILD_ID=123456789012345678\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        _load_from_env_file(env_path)


# ---------------------------------------------------------------------------
# (c) Parses VC_PING_ROLE_IDS / PHOTO_BONUS_CHANNEL_IDS as list[int]
# ---------------------------------------------------------------------------


def test_parses_vc_ping_role_ids_from_csv() -> None:
    settings = _load_from_env_file(SAMPLE_ENV)

    assert settings.vc_ping_role_ids == [
        222222222222222222,
        333333333333333333,
        444444444444444444,
    ]


def test_parses_photo_bonus_channel_ids_from_csv() -> None:
    settings = _load_from_env_file(SAMPLE_ENV)

    assert settings.photo_bonus_channel_ids == [
        555555555555555555,
        666666666666666666,
    ]


def test_parse_int_list_handles_whitespace_and_empties(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\n"
        "GUILD_ID=1\n"
        "VC_PING_ROLE_IDS= 100 , 200 ,, 300 \n",
        encoding="utf-8",
    )

    settings = _load_from_env_file(env_path)
    assert settings.vc_ping_role_ids == [100, 200, 300]


def test_parse_int_list_passthrough_list_input() -> None:
    """Validator must accept already-parsed list[int]/list[str] inputs."""
    settings = Settings(
        discord_token="tmp",
        guild_id=1,
        vc_ping_role_ids=["10", "20", 30],  # type: ignore[arg-type]
    )
    assert settings.vc_ping_role_ids == [10, 20, 30]


def test_parse_int_list_none_input_defaults_to_empty() -> None:
    settings = Settings(
        discord_token="tmp",
        guild_id=1,
        vc_ping_role_ids=None,  # type: ignore[arg-type]
    )
    assert settings.vc_ping_role_ids == []


def test_parse_int_list_rejects_invalid_type() -> None:
    with pytest.raises(ValidationError):
        Settings(
            discord_token="tmp",
            guild_id=1,
            vc_ping_role_ids=42,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# (d) Parses MARKET_OPEN / MARKET_CLOSE as datetime.time
# ---------------------------------------------------------------------------


def test_parses_market_hours_as_time() -> None:
    settings = _load_from_env_file(SAMPLE_ENV)

    assert settings.market_open == time(6, 30)
    assert settings.market_close == time(4, 30)
    assert isinstance(settings.market_open, time)
    assert isinstance(settings.market_close, time)


# ---------------------------------------------------------------------------
# (e) Defaults match the documented values
# ---------------------------------------------------------------------------


def test_defaults_match_target_architecture() -> None:
    """Spot-check defaults from `docs/02-target-architecture.md`."""
    settings = Settings(discord_token="tmp", guild_id=1)

    assert settings.command_prefix == "$"
    assert settings.database_url == "sqlite+aiosqlite:///data/friendex.db"

    assert settings.market_open == time(6, 30)
    assert settings.market_close == time(4, 30)
    assert settings.timezone_offset_hours == 0

    assert settings.initial_cash == 10_000.0
    assert settings.initial_price == 100.0
    assert settings.daily_reward == 500.0
    assert settings.streak_bonus == 500.0
    assert settings.min_price == 70.0
    assert settings.price_impact_k == 0.5
    assert settings.inactivity_threshold_seconds == 4 * 3600
    assert settings.inactivity_decay == 0.04
    assert settings.liquidation_threshold == 1.5
    assert settings.short_freeze_minutes == 30
    assert settings.trade_cooldown_seconds == 900
    assert settings.discipline_penalty == 0.17

    assert settings.activity_tick_minutes == 15

    assert settings.vc_ping_role_ids == []
    assert settings.voice_ping_window_seconds == 5400
    assert settings.fast_response_seconds == 120
    assert settings.medium_response_seconds == 300
    assert settings.vc_extra_boost_interval_seconds == 900

    assert settings.photo_bonus_channel_ids == []

    assert settings.hedge_fund_base_apy == 0.15
    assert settings.early_withdraw_penalty == 0.05
    assert settings.penalty_duration_days == 14

    assert settings.log_level == "INFO"
    assert settings.log_format == "json"


# ---------------------------------------------------------------------------
# Phase 3a (b) — Literal typing on log_format
# ---------------------------------------------------------------------------


def test_invalid_log_format_raises_validation_error(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\n"
        "GUILD_ID=1\n"
        "LOG_FORMAT=text\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        _load_from_env_file(env_path)


def test_console_log_format_is_accepted(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\n"
        "GUILD_ID=1\n"
        "LOG_FORMAT=console\n",
        encoding="utf-8",
    )

    settings = _load_from_env_file(env_path)
    assert settings.log_format == "console"


# ---------------------------------------------------------------------------
# Phase 3a (c) — redact_token processor and configure_logging wiring
# ---------------------------------------------------------------------------


def test_redact_token_replaces_token_value() -> None:
    event = {"event": "boot", "token": "super-secret-token"}
    out = redact_token(logger=None, method="info", event_dict=event)
    assert out["token"] == "REDACTED"


def test_redact_token_leaves_other_fields_alone() -> None:
    event = {"event": "boot", "token": "secret", "user_id": "42"}
    out = redact_token(logger=None, method="info", event_dict=event)
    assert out["user_id"] == "42"
    assert out["event"] == "boot"


def test_redact_token_handles_empty_token() -> None:
    event: dict[str, Any] = {"event": "boot", "token": ""}
    out = redact_token(logger=None, method="info", event_dict=event)
    assert out["token"] == ""


def test_redact_token_handles_missing_token_key() -> None:
    event: dict[str, Any] = {"event": "boot"}
    out = redact_token(logger=None, method="info", event_dict=event)
    assert "token" not in out


def test_configure_logging_sets_up_structlog_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(
        discord_token="tmp",
        guild_id=1,
        log_format="json",
        log_level="INFO",
    )
    configure_logging(settings)

    log = structlog.get_logger("test-logger")
    log.info("trade.buy", token="should-be-redacted", user_id="42")

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "REDACTED" in output
    assert "should-be-redacted" not in output
    assert "trade.buy" in output


def test_configure_logging_console_format(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(
        discord_token="tmp",
        guild_id=1,
        log_format="console",
        log_level="DEBUG",
    )
    configure_logging(settings)

    log = structlog.get_logger("test-logger")
    log.info("startup", token="leaked-token")

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "REDACTED" in output
    assert "leaked-token" not in output


def test_configure_logging_clears_stdlib_handlers() -> None:
    discord_logger = logging.getLogger("discord")
    # Plant a sentinel handler we expect to be cleared.
    sentinel = logging.NullHandler()
    discord_logger.addHandler(sentinel)
    assert sentinel in discord_logger.handlers

    settings = Settings(discord_token="tmp", guild_id=1)
    configure_logging(settings)

    assert sentinel not in discord_logger.handlers


# ---------------------------------------------------------------------------
# Memoisation of get_settings()
# ---------------------------------------------------------------------------


def test_get_settings_is_memoised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "memo-token")
    monkeypatch.setenv("GUILD_ID", "1")

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.discord_token == "memo-token"


def test_get_settings_cache_clear_returns_fresh_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "memo-token")
    monkeypatch.setenv("GUILD_ID", "1")

    first = get_settings()
    get_settings.cache_clear()
    second = get_settings()

    assert first is not second
    assert first == second
