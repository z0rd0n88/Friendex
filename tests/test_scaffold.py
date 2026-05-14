"""Phase 1 scaffold sanity test.

This is the minimum viable test that proves the package layout is
correctly declared in ``pyproject.toml`` and resolvable on the import
path. Real coverage starts at phase 2.
"""

from __future__ import annotations


def test_package_importable() -> None:
    import friendex  # noqa: F401
