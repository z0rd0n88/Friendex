"""Background-task wrappers (Phase 9).

Each module here is a thin scheduling wrapper around one (or two) application-
service method(s). The :class:`~friendex.adapters.tasks.base_task.BackgroundTask`
abstract base owns the swallow-and-log error contract and the lifecycle hooks;
concrete tasks declare their cadence as a class attribute
(``interval_minutes`` / ``interval_hours``) — the Phase 14 composition layer
reads that and wraps each :meth:`_run` body in
``discord.ext.tasks.loop(...)``. This package therefore contains **no**
``discord`` imports.
"""

from __future__ import annotations

from friendex.adapters.tasks.activity_tick_task import ActivityTickTask
from friendex.adapters.tasks.base_task import BackgroundTask
from friendex.adapters.tasks.daily_reset_task import DailyResetTask
from friendex.adapters.tasks.freeze_check_task import FreezeCheckTask
from friendex.adapters.tasks.inactivity_decay_task import InactivityDecayTask
from friendex.adapters.tasks.liquidation_task import LiquidationTask
from friendex.adapters.tasks.monthly_rollover_task import MonthlyRolloverTask
from friendex.adapters.tasks.vc_boost_task import VcBoostTask
from friendex.adapters.tasks.weekly_reset_task import WeeklyResetTask

__all__ = [
    "ActivityTickTask",
    "BackgroundTask",
    "DailyResetTask",
    "FreezeCheckTask",
    "InactivityDecayTask",
    "LiquidationTask",
    "MonthlyRolloverTask",
    "VcBoostTask",
    "WeeklyResetTask",
]
