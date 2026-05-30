"""Shared fixtures for application-layer (service) tests.

Provides a fresh in-memory fake repository per test for each persistence port,
plus a :class:`LockManager` and a default :class:`Settings` instance, so the
Phase 8a-8f service tests get a clean, database-free world every time.

``asyncio_mode = "auto"`` (see ``pyproject.toml``) means ``async def test_*``
functions run under ``pytest-asyncio`` without per-test decorators; these
fixtures are plain sync factories that hand back fresh instances.

**PR #94 review (L1) — structlog teardown.** The
:func:`_restore_structlog_defaults` autouse fixture snapshots structlog's
process-global configuration (the binding wrapper class + processor chain)
*before* each test and restores it on teardown. This is needed because
some tests (e.g. :mod:`tests.application.test_daily_service`) call
``structlog.reset_defaults()`` to undo the filtering wrapper that
``configure_logging`` installs — without teardown, the unfiltered config
leaks into the rest of the session and a later test asserting that a
DEBUG event is *filtered out* would silently start passing for the wrong
reason. The autouse fixture absorbs the leak without requiring every
caller to write a try/finally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import structlog

from friendex.adapters.config import Settings
from friendex.application.lock_manager import LockManager
from tests.application.fakes.fake_repos import (
    FakeFundRepo,
    FakePenaltyRepo,
    FakePriceRepo,
    FakeSystemStateRepo,
    FakeTradeCooldownRepo,
    FakeUserRepo,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def fake_user_repo() -> FakeUserRepo:
    """A fresh in-memory :class:`FakeUserRepo` per test."""
    return FakeUserRepo()


@pytest.fixture
def fake_price_repo() -> FakePriceRepo:
    """A fresh in-memory :class:`FakePriceRepo` per test."""
    return FakePriceRepo()


@pytest.fixture
def fake_fund_repo() -> FakeFundRepo:
    """A fresh in-memory :class:`FakeFundRepo` per test."""
    return FakeFundRepo()


@pytest.fixture
def fake_penalty_repo() -> FakePenaltyRepo:
    """A fresh in-memory :class:`FakePenaltyRepo` per test."""
    return FakePenaltyRepo()


@pytest.fixture
def fake_cooldown_repo() -> FakeTradeCooldownRepo:
    """A fresh in-memory :class:`FakeTradeCooldownRepo` per test."""
    return FakeTradeCooldownRepo()


@pytest.fixture
def fake_system_state_repo() -> FakeSystemStateRepo:
    """A fresh in-memory :class:`FakeSystemStateRepo` per test."""
    return FakeSystemStateRepo()


@pytest.fixture
def lock_manager() -> LockManager:
    """A fresh process-local :class:`LockManager` per test."""
    return LockManager()


@pytest.fixture
def default_settings() -> Settings:
    """A valid default :class:`Settings` built without reading any ``.env``.

    ``discord_token`` is the only required field; ``_env_file=None`` keeps the
    instance deterministic and independent of any ``.env`` on disk, and every
    other field falls back to its documented default.
    """
    return Settings(discord_token="test-token", _env_file=None)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def _restore_structlog_defaults() -> Iterator[None]:
    """Snapshot + restore structlog's process-global config per test.

    PR #94 review (L1): some tests call :func:`structlog.reset_defaults`
    plus rebind a module-level ``_log`` proxy so a filtering wrapper class
    (installed by ``adapters.config.configure_logging`` in earlier
    ``test_configure_logging_*`` runs) does not drop DEBUG events captured
    by :func:`structlog.testing.capture_logs`. Without teardown the
    unfiltered config leaks into the rest of the session — a downstream
    test that asserts "DEBUG IS filtered" would then silently fail to
    detect a regression. This autouse fixture absorbs the leak.

    The snapshot uses :func:`structlog.get_config` (a shallow copy of the
    config dict) and the restore uses :func:`structlog.configure` with the
    captured values; both are the documented support API for tests.
    """
    snapshot = structlog.get_config()
    try:
        yield
    finally:
        # ``get_config`` returns a dict that ``configure`` accepts verbatim.
        structlog.configure(**snapshot)
