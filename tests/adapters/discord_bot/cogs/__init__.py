"""Discord cog tests.

Tests in this package exercise modules under
``src/friendex/adapters/discord_bot/cogs/``. Cog tests invoke each command's
:attr:`~discord.app_commands.Command.callback` directly with a stub
:class:`discord.Interaction` (``dpytest`` simulates message events, not slash
interactions). Service dependencies are :class:`unittest.mock.AsyncMock`
stand-ins built by per-service fixtures in :mod:`conftest`.
"""

from __future__ import annotations
