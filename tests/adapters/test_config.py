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
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import structlog
from pydantic import SecretStr, ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator

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
def _clear_settings_cache() -> Iterator[None]:
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
        "DEV_GUILD_ID",
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
# (a) Loads the required token; the dev-sync guild is optional
# ---------------------------------------------------------------------------


def test_loads_token_and_dev_guild_from_fixture_env() -> None:
    settings = _load_from_env_file(SAMPLE_ENV)

    # ``discord_token`` is :class:`SecretStr` — never compare the raw value
    # directly; always go through ``.get_secret_value()``.
    assert isinstance(settings.discord_token, SecretStr)
    assert settings.discord_token.get_secret_value() == "test-token-abc-123"
    # The fixture's legacy ``GUILD_ID`` is accepted as a backward-compatible
    # alias for the now-optional dev-sync guild.
    assert settings.dev_guild_id == 111111111111111111


def test_loads_dev_guild_id_from_temp_env(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\nDEV_GUILD_ID=987654321098765432\n",
        encoding="utf-8",
    )

    settings = _load_from_env_file(env_path)

    assert settings.discord_token.get_secret_value() == "tmp-token"
    assert settings.dev_guild_id == 987654321098765432


def test_dev_guild_id_defaults_to_none_when_absent(tmp_path: Path) -> None:
    """A multi-tenant bot must not require a guild at startup."""
    env_path = tmp_path / ".env"
    env_path.write_text("DISCORD_TOKEN=tmp-token\n", encoding="utf-8")

    settings = _load_from_env_file(env_path)

    assert settings.dev_guild_id is None


def test_legacy_guild_id_env_aliases_dev_guild_id(tmp_path: Path) -> None:
    """The pre-multi-guild ``GUILD_ID`` env name still populates the field."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\nGUILD_ID=424242424242424242\n",
        encoding="utf-8",
    )

    settings = _load_from_env_file(env_path)

    assert settings.dev_guild_id == 424242424242424242


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
        "DISCORD_TOKEN=your_bot_token_here\nGUILD_ID=123456789012345678\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as excinfo:
        _load_from_env_file(env_path)

    assert "DISCORD_TOKEN is not configured" in str(excinfo.value)


def test_empty_discord_token_raises_validation_error(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=\nGUILD_ID=123456789012345678\n",
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
        "DISCORD_TOKEN=tmp-token\nGUILD_ID=1\nVC_PING_ROLE_IDS= 100 , 200 ,, 300 \n",
        encoding="utf-8",
    )

    settings = _load_from_env_file(env_path)
    assert settings.vc_ping_role_ids == [100, 200, 300]


def test_parse_int_list_passthrough_list_input() -> None:
    """Validator must accept already-parsed list[int]/list[str] inputs."""
    settings = Settings(
        discord_token="tmp",
        vc_ping_role_ids=["10", "20", 30],  # type: ignore[list-item]
    )
    assert settings.vc_ping_role_ids == [10, 20, 30]


def test_parse_int_list_none_input_defaults_to_empty() -> None:
    settings = Settings(
        discord_token="tmp",
        vc_ping_role_ids=None,  # type: ignore[arg-type]
    )
    assert settings.vc_ping_role_ids == []


def test_parse_int_list_rejects_invalid_type() -> None:
    with pytest.raises(ValidationError):
        Settings(
            discord_token="tmp",
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
    settings = Settings(discord_token="tmp")

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
    assert settings.hedge_fund_base_apy_period == "monthly"
    assert settings.early_withdraw_penalty == 0.05
    assert settings.penalty_duration_days == 14

    # Phase 17a toggles (Open-Q2 / Q3 / Q8). Defaults preserve the historic
    # behaviour so the toggle landing is a no-op until an operator flips one.
    assert settings.sunday_buy_allowed is True
    assert settings.opt_out_blocks_trading is True

    assert settings.log_level == "INFO"
    assert settings.log_format == "json"


# ---------------------------------------------------------------------------
# Phase 3a (b) — Literal typing on log_format
# ---------------------------------------------------------------------------


def test_invalid_log_format_raises_validation_error(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\nGUILD_ID=1\nLOG_FORMAT=text\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        _load_from_env_file(env_path)


def test_console_log_format_is_accepted(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\nGUILD_ID=1\nLOG_FORMAT=console\n",
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

    settings = Settings(discord_token="tmp")
    configure_logging(settings)

    assert sentinel not in discord_logger.handlers


# ---------------------------------------------------------------------------
# Memoisation of get_settings()
# ---------------------------------------------------------------------------


def test_get_settings_is_memoised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "memo-token")

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.discord_token.get_secret_value() == "memo-token"


def test_get_settings_cache_clear_returns_fresh_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "memo-token")

    first = get_settings()
    get_settings.cache_clear()
    second = get_settings()

    assert first is not second
    assert first == second


# ---------------------------------------------------------------------------
# Phase 17a — Open-Q2/Q3/Q8 toggle env overrides
# ---------------------------------------------------------------------------


def test_sunday_buy_allowed_env_override_false(tmp_path: Path) -> None:
    """Open-Q2: ``SUNDAY_BUY_ALLOWED=false`` flips the Sunday-buy exception off."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\nSUNDAY_BUY_ALLOWED=false\n",
        encoding="utf-8",
    )

    settings = _load_from_env_file(env_path)

    assert settings.sunday_buy_allowed is False


def test_hedge_fund_base_apy_period_env_override_annual(tmp_path: Path) -> None:
    """Open-Q8: ``HEDGE_FUND_BASE_APY_PERIOD=annual`` selects the annual cadence."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\nHEDGE_FUND_BASE_APY_PERIOD=annual\n",
        encoding="utf-8",
    )

    settings = _load_from_env_file(env_path)

    assert settings.hedge_fund_base_apy_period == "annual"


def test_hedge_fund_base_apy_period_rejects_invalid_value(tmp_path: Path) -> None:
    """Open-Q8: only the Literal members ``monthly``/``annual`` are accepted."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\nHEDGE_FUND_BASE_APY_PERIOD=weekly\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        _load_from_env_file(env_path)


def test_opt_out_blocks_trading_env_override_false(tmp_path: Path) -> None:
    """Open-Q3: ``OPT_OUT_BLOCKS_TRADING=false`` disarms the ``OptedOut`` gate."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\nOPT_OUT_BLOCKS_TRADING=false\n",
        encoding="utf-8",
    )

    settings = _load_from_env_file(env_path)

    assert settings.opt_out_blocks_trading is False


# ---------------------------------------------------------------------------
# Wave 1 / fix/config-settings — env-binding integrity (#84 H)
# ---------------------------------------------------------------------------


def test_get_settings_reads_discord_token_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_settings()`` must construct via ``Settings()`` so env binding fires.

    ``Settings.model_validate({})`` is a documentation-non-canonical entry
    point for pydantic-settings; the canonical constructor is ``Settings()``.
    This test pins the canonical form so regressions to ``model_validate({})``
    cannot land silently.
    """
    monkeypatch.setenv("DISCORD_TOKEN", "env-bound-token")
    monkeypatch.setenv("DEV_GUILD_ID", "919191919191919191")

    settings = get_settings()

    assert settings.discord_token.get_secret_value() == "env-bound-token"
    assert settings.dev_guild_id == 919191919191919191


def test_get_settings_body_contains_no_model_validate_call() -> None:
    """Regression pin (#84 H): ``get_settings`` never calls ``model_validate``.

    ``Settings.model_validate({})`` is a non-canonical entry point for
    pydantic-settings that silently suppresses the env / ``.env`` source
    pipeline in some configurations — the exact regression class flagged
    in issue #84.  We walk the parsed function body with an
    :class:`ast.NodeVisitor` and reject any ``X.model_validate(...)``
    call attribute reference whose receiver name is ``Settings`` or
    ``cls``.  This survives benign refactors (adding a guard clause,
    a logging breadcrumb, an ``os.environ`` short-circuit, …) while
    still catching the exact regression the original issue reported.

    The receiver-name check intentionally tolerates ``cls.model_validate``
    (used inside ``@classmethod`` definitions) but rejects it on the
    grounds that ``get_settings`` is a module-level function — if anyone
    moves it onto :class:`Settings` as a classmethod, the reviewer of
    that future PR will rightly want to revisit the env-load contract.
    """
    import ast
    import inspect

    from friendex.adapters import config as config_module

    src = inspect.getsource(config_module.get_settings)
    tree = ast.parse(src)
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)

    class _ModelValidateRejector(ast.NodeVisitor):
        """Reject any ``Settings.model_validate`` / ``cls.model_validate`` call."""

        def __init__(self) -> None:
            self.violations: list[str] = []

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if (
                node.attr == "model_validate"
                and isinstance(node.value, ast.Name)
                and node.value.id in {"Settings", "cls"}
            ):
                self.violations.append(
                    f"{node.value.id}.model_validate at line {node.lineno}"
                )
            self.generic_visit(node)

    rejector = _ModelValidateRejector()
    rejector.visit(func)
    assert not rejector.violations, (
        "`get_settings()` must not call `Settings.model_validate(...)` "
        f"(issue #84 regression); found: {rejector.violations}"
    )


def test_get_settings_returns_a_settings_call() -> None:
    """Documented invariant: ``get_settings`` returns a ``Settings(...)`` call.

    The canonical implementation is ``return Settings()`` (no kwargs).
    Future refactors may add a guard clause or a logging breadcrumb before
    the return, but the *return value* itself must remain a direct
    ``Settings(...)`` invocation rather than a backdoor like
    ``Settings.model_validate``.  This complements
    :func:`test_get_settings_body_contains_no_model_validate_call` —
    together they pin the env-load contract without over-constraining
    the function body.
    """
    import ast
    import inspect

    from friendex.adapters import config as config_module

    src = inspect.getsource(config_module.get_settings)
    tree = ast.parse(src)
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)

    returns: list[ast.Return] = [
        node for node in ast.walk(func) if isinstance(node, ast.Return)
    ]
    assert returns, "get_settings must have at least one return statement"

    for ret in returns:
        assert isinstance(ret.value, ast.Call), (
            f"return at line {ret.lineno} is not a direct call; "
            "got a non-Call expression"
        )
        call = ret.value
        assert isinstance(call.func, ast.Name) and call.func.id == "Settings", (
            f"return at line {ret.lineno} does not call ``Settings(...)``; "
            f"got {ast.dump(call.func)!r}"
        )


# ---------------------------------------------------------------------------
# Wave 1 / fix/config-settings — SecretStr propagation (#84 M)
# ---------------------------------------------------------------------------


def test_discord_token_is_secret_str_typed() -> None:
    settings = Settings(discord_token="raw-token-value")
    assert isinstance(settings.discord_token, SecretStr)
    assert settings.discord_token.get_secret_value() == "raw-token-value"


def test_settings_repr_does_not_leak_token() -> None:
    """``repr(settings)`` must redact the token, even in stack traces."""
    settings = Settings(discord_token="super-secret-token-9000")

    rendered = repr(settings)

    assert "super-secret-token-9000" not in rendered
    # Pydantic's ``SecretStr.__repr__`` is ``SecretStr('**********')``.
    assert "**********" in rendered


def test_settings_str_does_not_leak_token() -> None:
    settings = Settings(discord_token="super-secret-token-9000")
    rendered = str(settings)
    assert "super-secret-token-9000" not in rendered


def test_secret_str_str_renders_redacted() -> None:
    """``str(secret_str)`` is the redaction text, never the raw value."""
    settings = Settings(discord_token="super-secret-token-9000")
    assert "super-secret-token-9000" not in str(settings.discord_token)


# ---------------------------------------------------------------------------
# Wave 1 / fix/config-settings — redaction key fix (#84 M)
# ---------------------------------------------------------------------------


def test_redact_token_redacts_discord_token_field() -> None:
    """The processor must redact the actual field name on ``Settings``."""
    event: dict[str, Any] = {
        "event": "boot",
        "discord_token": "should-be-redacted",
    }
    out = redact_token(logger=None, method="info", event_dict=event)
    assert out["discord_token"] == "REDACTED"


def test_redact_token_redacts_discord_token_in_log_pipeline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end: a real structlog event with ``discord_token=...`` is scrubbed."""
    settings = Settings(
        discord_token="tmp",
        log_format="json",
        log_level="INFO",
    )
    configure_logging(settings)

    log = structlog.get_logger("config-test")
    log.info("startup", discord_token="leaked-bot-token")

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "REDACTED" in output
    assert "leaked-bot-token" not in output


# ---------------------------------------------------------------------------
# Wave 1 / fix/config-settings — parse_int_list per-token recovery (#84 M)
# ---------------------------------------------------------------------------


def test_parse_int_list_skips_malformed_token_and_logs_warning(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """A malformed entry must not silently kill the whole list.

    Today an invalid token raises ``ValueError`` and the field silently rolls
    back to its default — operators get no diagnostic. We log a structured
    warning identifying the bad token and keep the good ones.

    ``capsys`` captures structlog's :class:`structlog.PrintLoggerFactory`
    output rather than stdlib ``caplog`` (the module logs through structlog,
    not the root stdlib logger).
    """
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\nVC_PING_ROLE_IDS=100,not_a_number,300\n",
        encoding="utf-8",
    )

    settings = _load_from_env_file(env_path)

    assert settings.vc_ping_role_ids == [100, 300]
    output = capsys.readouterr().out + capsys.readouterr().err
    # The warning must identify the bad token by its raw value.  We bind it
    # under ``value`` (not ``token``) so the redaction processor leaves it
    # alone.
    assert "not_a_number" in output, (
        f"Expected structlog warning mentioning malformed token; got: {output!r}"
    )
    assert "config.parse_int_list.malformed_token" in output


def test_parse_int_list_skips_malformed_list_entry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Per-entry recovery also applies to direct list constructor input."""
    settings = Settings(
        discord_token="tmp",
        vc_ping_role_ids=["10", "not_an_int", 30],  # type: ignore[list-item]
    )

    assert settings.vc_ping_role_ids == [10, 30]
    output = capsys.readouterr().out + capsys.readouterr().err
    assert "not_an_int" in output


def test_parse_int_list_warning_binds_field_name_vc_ping(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """L3 fix: the structured warning identifies *which* list field was bad.

    An operator reading the log line for a malformed token must know
    whether the bad value came from ``vc_ping_role_ids`` or
    ``photo_bonus_channel_ids`` without having to grep the env file.
    """
    Settings(
        discord_token="tmp",
        vc_ping_role_ids=["10", "broken_vc", 30],  # type: ignore[list-item]
    )

    output = capsys.readouterr().out + capsys.readouterr().err
    assert "vc_ping_role_ids" in output, (
        f"Expected field name in warning binding; got: {output!r}"
    )


def test_parse_int_list_warning_binds_field_name_photo_bonus(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """L3 fix: ``photo_bonus_channel_ids`` malformed tokens carry the field name."""
    Settings(
        discord_token="tmp",
        photo_bonus_channel_ids=["1", "broken_photo", 2],  # type: ignore[list-item]
    )

    output = capsys.readouterr().out + capsys.readouterr().err
    assert "photo_bonus_channel_ids" in output, (
        f"Expected field name in warning binding; got: {output!r}"
    )


# ---------------------------------------------------------------------------
# Wave 1 / fix/config-settings — Decimal computed fields (#82 H20)
# ---------------------------------------------------------------------------


def test_money_fields_expose_decimal_view() -> None:
    """Each float-typed money field must have a ``*_d`` Decimal counterpart.

    Services should read ``settings.initial_cash_d`` directly instead of
    constructing ``Decimal(str(settings.initial_cash))`` at 20+ call sites.
    The float source field stays untouched for env-var binding stability.
    """
    settings = Settings(discord_token="tmp")

    # Money / cash
    assert settings.initial_cash_d == Decimal("10000.0")
    assert settings.initial_price_d == Decimal("100.0")
    assert settings.daily_reward_d == Decimal("500.0")
    assert settings.streak_bonus_d == Decimal("500.0")
    assert settings.min_price_d == Decimal("70.0")
    # Rates / multipliers
    assert settings.price_impact_k_d == Decimal("0.5")
    assert settings.inactivity_decay_d == Decimal("0.04")
    assert settings.liquidation_threshold_d == Decimal("1.5")
    assert settings.discipline_penalty_d == Decimal("0.17")
    assert settings.activity_tick_k_d == Decimal("0.3")
    assert settings.vc_extra_boost_multiplier_d == Decimal("1.03")
    assert settings.voice_ping_join_boost_d == Decimal("1.2")
    assert settings.voice_stay_boost_d == Decimal("1.5")
    # Hedge fund
    assert settings.hedge_fund_base_apy_d == Decimal("0.15")
    assert settings.early_withdraw_penalty_d == Decimal("0.05")


def test_money_fields_decimal_view_types() -> None:
    """Every ``*_d`` field is a real ``Decimal`` (not a string or float)."""
    settings = Settings(discord_token="tmp")

    for name in (
        "initial_cash_d",
        "initial_price_d",
        "daily_reward_d",
        "streak_bonus_d",
        "min_price_d",
        "price_impact_k_d",
        "inactivity_decay_d",
        "liquidation_threshold_d",
        "discipline_penalty_d",
        "activity_tick_k_d",
        "vc_extra_boost_multiplier_d",
        "voice_ping_join_boost_d",
        "voice_stay_boost_d",
        "hedge_fund_base_apy_d",
        "early_withdraw_penalty_d",
    ):
        value = getattr(settings, name)
        assert isinstance(value, Decimal), (
            f"{name} is {type(value).__name__}, want Decimal"
        )


def test_money_decimal_view_tracks_overrides(tmp_path: Path) -> None:
    """``*_d`` fields reflect the env-overridden source value, not the default."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DISCORD_TOKEN=tmp-token\nINITIAL_CASH=25000\nMIN_PRICE=42\n",
        encoding="utf-8",
    )

    settings = _load_from_env_file(env_path)

    assert settings.initial_cash_d == Decimal("25000.0")
    assert settings.min_price_d == Decimal("42.0")
