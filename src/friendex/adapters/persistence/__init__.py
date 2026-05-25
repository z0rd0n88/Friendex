"""Persistence adapters: async SQLAlchemy engine, ORM, and repositories.

Re-exports the SQLAlchemy-backed repository implementations so callers (the DI
container in later phases, tests) import them from one place. Additional
repositories are added here as their Phase 6 sub-units land.
"""

from __future__ import annotations

from friendex.adapters.persistence.user_repo import SqlUserRepository

__all__ = ["SqlUserRepository"]
