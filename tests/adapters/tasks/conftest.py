"""Shared fixtures for adapter-tasks tests.

Re-exports the in-memory :class:`FakeSystemStateRepo` from the application
fakes so the reset-task tests do not need to import the SQLAlchemy adapter.
The fakes mirror the adapter behaviour exactly (see Phase 8-fakes digest).
"""

from __future__ import annotations

import pytest

from tests.application.fakes.fake_repos import FakeSystemStateRepo


@pytest.fixture
def fake_system_state_repo() -> FakeSystemStateRepo:
    """A fresh in-memory :class:`FakeSystemStateRepo` per test."""
    return FakeSystemStateRepo()
