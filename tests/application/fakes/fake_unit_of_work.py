"""Snapshot-based fake :class:`IUnitOfWork` for application tests.

The in-memory fakes (:class:`FakeUserRepo`, :class:`FakeFundRepo`, ...)
store aggregates in plain ``dict`` references. :class:`FakeUnitOfWork`
turns those dicts into a savepointable structure for tests: on
``transaction()`` enter it captures a shallow copy of every fake's
``_store``; on exception it restores every store and re-raises; on clean
exit it simply discards the snapshot.

This mirrors the contract :class:`SqlUnitOfWork` makes good on at the
SQLite layer (one transaction; commit-or-rollback) so the trading and
fund service tests can pin the "money is not destroyed on mid-sequence
failure" invariant end-to-end without spinning up a database.

:class:`FakeUnitOfWork` accepts any number of repo objects whose state
lives under a ``_store`` attribute; the snapshot includes that store and
any auxiliary state (e.g. :class:`FakePriceRepo._history`).
"""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class FakeUnitOfWork:
    """Test :class:`IUnitOfWork` implementation with savepoint semantics.

    Pass every fake repo whose state should participate in the
    transaction; on exception the fakes' ``_store`` (and ``_history`` on
    the price repo, if present) is rolled back to its pre-transaction
    value.
    """

    def __init__(self, *repos: object) -> None:
        self._repos = repos
        # Tracks how many transactions completed cleanly — handy for
        # tests that want to pin the seam was entered at all.
        self.commits = 0
        self.rollbacks = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Snapshot every fake's ``_store`` on enter; restore on exception."""
        snapshots: list[tuple[object, dict[str, Any]]] = []
        for repo in self._repos:
            captured: dict[str, Any] = {}
            store = getattr(repo, "_store", None)
            if store is not None:
                captured["_store"] = copy.deepcopy(store)
            history = getattr(repo, "_history", None)
            if history is not None:
                captured["_history"] = copy.deepcopy(history)
            snapshots.append((repo, captured))
        try:
            yield
        except BaseException:
            for repo, captured in snapshots:
                for attr, value in captured.items():
                    setattr(repo, attr, value)
            self.rollbacks += 1
            raise
        else:
            self.commits += 1
