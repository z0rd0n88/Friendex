"""Persistence adapters: async SQLAlchemy engine, ORM, and repositories.

Re-exports the SQLAlchemy-backed repository implementations so callers (the DI
container in later phases, tests) import them from one place. Additional
repositories are added here as their Phase 6 sub-units land.
"""

from __future__ import annotations

from friendex.adapters.persistence.cooldown_repo import SqlTradeCooldownRepository
from friendex.adapters.persistence.fund_repo import SqlFundRepository
from friendex.adapters.persistence.penalty_repo import SqlPenaltyRepository
from friendex.adapters.persistence.price_repo import SqlPriceRepository
from friendex.adapters.persistence.system_state_repo import SqlSystemStateRepository
from friendex.adapters.persistence.user_repo import SqlUserRepository

__all__ = [
    "SqlFundRepository",
    "SqlPenaltyRepository",
    "SqlPriceRepository",
    "SqlSystemStateRepository",
    "SqlTradeCooldownRepository",
    "SqlUserRepository",
]
