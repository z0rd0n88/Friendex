"""Shared fixtures for ``tests/domain``.

Provides two fixtures the Phase 4 domain tests rely on:

* ``frozen_now`` — a fixed, timezone-aware UTC ``datetime`` pinned with
  :mod:`freezegun` so clock-dependent behaviour is deterministic. The fixture
  yields the frozen instant while the freeze is in effect; tests that only need
  the value (not a live frozen clock) can use it directly.
* ``default_settings`` — a :class:`~friendex.adapters.config.Settings` built
  from a static dict so the domain tests can source tunables
  (``price_impact_k``, ``min_price``, ``inactivity_decay`` …) from the same
  contract production uses, without reading the process environment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from freezegun import freeze_time

from friendex.adapters.config import Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

# A fixed instant used across the domain test-suite. Timezone-aware UTC to
# honour the Phase 3.1 invariant (no naive datetimes in domain code).
FROZEN_INSTANT = datetime(2026, 5, 23, 12, 0, 0, tzinfo=UTC)

# Static settings payload — no secrets read from the environment. ``Settings``
# requires a non-placeholder ``discord_token``; everything else falls back to
# the documented defaults in ``adapters/config.py``.
_SETTINGS_PAYLOAD: dict[str, object] = {"discord_token": "test-token"}


@pytest.fixture
def frozen_now() -> Iterator[datetime]:
    """Yield a fixed UTC datetime with a live :mod:`freezegun` freeze active."""
    with freeze_time(FROZEN_INSTANT):
        yield FROZEN_INSTANT


@pytest.fixture
def default_settings() -> Settings:
    """Return a :class:`Settings` built from a static, env-free payload."""
    return Settings.model_validate(_SETTINGS_PAYLOAD)
