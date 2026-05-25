"""Tests for the engine wiring in ``db.py``.

ADR-0002 mandates that SQLite foreign-key enforcement is switched on for every
connection the app or tests open, via a ``connect`` event listener that issues
``PRAGMA foreign_keys=ON``. SQLite defaults this PRAGMA to ``OFF`` and applies
it per-connection, so without the listener every ``FOREIGN KEY`` declaration in
``orm.py`` (and every ``ON DELETE CASCADE`` added in migration ``0002``) is
inert. These tests pin the listener so a regression that drops it goes RED.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy import text

from friendex.adapters.persistence.db import build_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A fresh in-memory SQLite engine built via the production factory."""
    eng = build_engine("sqlite+aiosqlite:///:memory:")
    try:
        yield eng
    finally:
        await eng.dispose()


async def test_build_engine_enables_foreign_keys(engine: AsyncEngine) -> None:
    """Acceptance #1 — every connection has ``PRAGMA foreign_keys`` set to ON."""
    # Arrange / Act — open a connection from the factory-built engine.
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA foreign_keys"))
        foreign_keys_enabled = result.scalar_one()

    # Assert — SQLite reports 1 (ON), not its 0 (OFF) default.
    assert foreign_keys_enabled == 1
