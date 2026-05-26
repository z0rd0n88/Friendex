"""Shared fixtures for application-layer (service) tests.

Provides a fresh in-memory fake repository per test for each persistence port,
plus a :class:`LockManager` and a default :class:`Settings` instance, so the
Phase 8a-8f service tests get a clean, database-free world every time.

``asyncio_mode = "auto"`` (see ``pyproject.toml``) means ``async def test_*``
functions run under ``pytest-asyncio`` without per-test decorators; these
fixtures are plain sync factories that hand back fresh instances.
"""

from __future__ import annotations

import pytest

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
