"""Shared pytest configuration.

`asyncio_mode = "auto"` is configured in `pyproject.toml` so that all
``async def test_*`` functions are picked up by ``pytest-asyncio`` without
needing per-test decorators. This module is intentionally minimal — phase 1
ships no fixtures.
"""

from __future__ import annotations
