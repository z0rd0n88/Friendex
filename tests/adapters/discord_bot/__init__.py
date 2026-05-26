"""Discord adapter tests.

Tests in this package exercise modules under
``src/friendex/adapters/discord_bot/`` — embed builders, cogs, listeners.
Embed-builder tests use :meth:`discord.Embed.to_dict` for structural
assertions so they need neither a live bot nor a Discord network round-trip.
"""

from __future__ import annotations
