"""Discord listener tests.

Tests in this package exercise modules under
``src/friendex/adapters/discord_bot/listeners/``. Listener tests invoke each
event handler directly (``await cog.on_reaction_add(reaction, user)``) since
``dpytest`` adds heavy fixture overhead and the direct-callback idiom matches
the Phase 11 cog-test convention. Service dependencies are
:class:`unittest.mock.AsyncMock` stand-ins built by per-service fixtures in
:mod:`conftest`.
"""

from __future__ import annotations
