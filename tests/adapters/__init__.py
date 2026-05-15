"""Adapter-layer tests.

Tests in this package exercise modules under
``src/friendex/adapters/`` — configuration, persistence, Discord
adapters, and background tasks.  Each test must avoid importing from
``friendex.domain`` or ``friendex.application`` unless the
adapter under test legitimately depends on them.
"""

from __future__ import annotations
