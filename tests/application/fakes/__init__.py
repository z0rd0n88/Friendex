"""In-memory fake repositories + test-double infrastructure.

These fakes implement the application-layer repository ``Protocol`` interfaces
(:mod:`friendex.application.interfaces`) with plain in-memory ``dict`` storage so
application-service tests (Phases 8a-8f) can run without a database. Each fake
mirrors the observable semantics of its SQLAlchemy-backed adapter
(``src/friendex/adapters/persistence``) — per-``(guild_id, id)`` keying,
append-only price history, idempotent ``ensure_events_wallet``, TTL-filtered
cooldown ``get`` / ``purge_expired`` — without any of the persistence machinery.

Domain aggregates are frozen-style dataclasses; the fakes store references and
never mutate them in place (immutable style), matching the real adapters which
build fresh domain objects on every read.
"""

from __future__ import annotations
