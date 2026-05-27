"""Friendex package — Discord stock-exchange game bot.

Re-exports :func:`friendex.main.main` so ``python -m friendex`` (via the
:mod:`friendex.__main__` shim) and the ``[project.scripts]`` ``friendex``
entry both resolve to the same callable.
"""

from __future__ import annotations

from friendex.main import main

__all__ = ["main"]
