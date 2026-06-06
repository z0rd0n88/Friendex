"""Regression-pinning tests for #82 H18, H19 and related CI/mypy fixes.

These tests confirm that findings addressed in the ``fix/typing-and-ci`` pass
remain fixed and cannot silently regress:

* **H18** — ``tests/`` is included in the mypy gate.  Verified by inspecting
  ``mypy.ini`` and ``.github/workflows/ci.yml`` for the correct invocation.
  The actual mypy run over ``tests/`` is the real regression guard; this test
  confirms the config files carry the expected strings so a future editor
  cannot remove them without a failing test.

* **H19** — 14 ``# type: ignore[return-value]`` comments in the cog and
  listener conftest fixtures replaced with ``cast(ServiceClass, mock)``.
  Verified by asserting the conftest source contains no ``type: ignore``
  on the factory return statements that were fixed.

* **L6** — ``[mypy-dpytest.*]`` is NOT present in ``mypy.ini`` (it was
  never a valid section for this project; the finding was that it would be
  vestigial if it existed).  ``[mypy-freezegun.*]`` IS present and valid
  (``freezegun`` is imported in multiple test modules).

* **CLAUDE.md / PR template** — the ``Development`` block and PR template
  both reference ``mypy --strict src/ tests/`` rather than the stale
  ``mypy src/friendex`` that caused CI/local divergence in earlier waves.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# H18 — mypy.ini and CI must include tests/ in the mypy run
# ---------------------------------------------------------------------------


def test_mypy_ini_has_tests_relaxed_profile() -> None:
    """``mypy.ini`` must have a ``[mypy-tests.*]`` section (H18 fix).

    Without it the ``tests/`` directory is checked under full strict mode,
    which surfaces ~700 noise errors from common test idioms.  The section
    enables per-module relaxation while keeping ``tests/`` in the gate.
    """
    content = (REPO_ROOT / "mypy.ini").read_text(encoding="utf-8")
    assert "[mypy-tests.*]" in content, (
        "mypy.ini must contain a [mypy-tests.*] section; "
        "removing it drops tests/ from the mypy coverage gate (H18 regression)"
    )


def test_ci_yml_runs_mypy_on_tests_dir() -> None:
    """CI must invoke mypy with both ``src/`` and ``tests/`` targets (H18 fix).

    Earlier waves ran ``mypy src/friendex`` (src-only); Wave 3 extended the
    gate to ``mypy --strict src/ tests/``.  This test ensures no one narrows
    it back.
    """
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    content = ci_path.read_text(encoding="utf-8")
    # The run line must mention both paths.
    assert "src/" in content and "tests/" in content, (
        "CI workflow must pass both src/ and tests/ to mypy; "
        "the gate must not be narrowed back to src-only (H18 regression)"
    )
    # The run line must include --strict.
    assert "--strict" in content, (
        "CI mypy invocation must include --strict (H18 regression)"
    )


def test_ci_coverage_fail_under_is_95_or_higher() -> None:
    """``--cov-fail-under`` in CI must be >= 95 (M9 fix).

    Wave 3 tightened the gate from 0 to 95 to match the ~95.6 % baseline
    measured on 2026-05-30.  This test ensures the gate is not weakened.
    """
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    content = ci_path.read_text(encoding="utf-8")
    match = re.search(r"--cov-fail-under=(\d+)", content)
    assert match is not None, (
        "CI pytest invocation must include --cov-fail-under=N (M9 regression)"
    )
    threshold = int(match.group(1))
    assert threshold >= 95, (
        f"--cov-fail-under={threshold} is below the 95 % minimum; "
        "the gate must not be weakened below the measured baseline (M9 regression)"
    )


# ---------------------------------------------------------------------------
# H19 — cast() replaces # type: ignore[return-value] in conftest fixtures
# ---------------------------------------------------------------------------


def test_cog_conftest_uses_cast_not_type_ignore_return() -> None:
    """Cog conftest factory fixtures must use ``cast(...)`` not ``type: ignore``.

    H19 replaced the 6 ``# type: ignore[return-value]`` annotations on cog
    factory fixtures with ``cast(ServiceClass, mock)`` so the actual return
    type flows correctly through the type checker.
    """
    path = REPO_ROOT / "tests" / "adapters" / "discord_bot" / "cogs" / "conftest.py"
    content = path.read_text(encoding="utf-8")
    # Must import cast.
    assert "from typing" in content and "cast" in content, (
        "cog conftest must import cast from typing (H19 regression)"
    )
    # Must not use type: ignore[return-value] — the H19 fix target.
    assert "type: ignore[return-value]" not in content, (
        "cog conftest must not contain # type: ignore[return-value]; "
        "replace with cast(...) (H19 regression)"
    )


def test_listener_conftest_uses_cast_not_type_ignore_return() -> None:
    """Listener conftest factory fixtures must use ``cast(...)`` not ``type: ignore``.

    H19 replaced the 8 ``# type: ignore[return-value]`` annotations on
    listener factory fixtures with ``cast(ServiceClass, mock)``.
    """
    path = (
        REPO_ROOT / "tests" / "adapters" / "discord_bot" / "listeners" / "conftest.py"
    )
    content = path.read_text(encoding="utf-8")
    assert "from typing" in content and "cast" in content, (
        "listener conftest must import cast from typing (H19 regression)"
    )
    assert "type: ignore[return-value]" not in content, (
        "listener conftest must not contain # type: ignore[return-value]; "
        "replace with cast(...) (H19 regression)"
    )


# ---------------------------------------------------------------------------
# L6 — mypy.ini dpytest/freezegun section validity
# ---------------------------------------------------------------------------


def test_mypy_ini_has_no_dpytest_section() -> None:
    """``mypy.ini`` must not contain a ``[mypy-dpytest.*]`` section (L6 check).

    ``dpytest`` is not used in this project — it is mentioned only in a
    comment explaining why we do NOT use it.  A vestigial mypy section for
    it would suppress import errors for a package that is not installed, and
    ``warn_unused_configs`` would fire if dpytest were ever removed from the
    environment.
    """
    content = (REPO_ROOT / "mypy.ini").read_text(encoding="utf-8")
    assert "[mypy-dpytest.*]" not in content, (
        "mypy.ini must not contain a [mypy-dpytest.*] section; "
        "dpytest is not used in this project (L6 regression)"
    )


def test_mypy_ini_has_freezegun_section() -> None:
    """``mypy.ini`` must retain ``[mypy-freezegun.*]`` (L6 / validity check).

    ``freezegun`` IS imported in multiple test modules; the section provides
    the required ``ignore_missing_imports = True`` stubs workaround and must
    not be removed.
    """
    content = (REPO_ROOT / "mypy.ini").read_text(encoding="utf-8")
    assert "[mypy-freezegun.*]" in content, (
        "mypy.ini must retain [mypy-freezegun.*]; "
        "freezegun is used in tests and needs the stub-suppress section (L6 regression)"
    )


def test_freezegun_is_actually_imported_in_tests() -> None:
    """At least one test module imports ``freezegun`` (L6 / validity check).

    Confirms the ``[mypy-freezegun.*]`` section in ``mypy.ini`` is not
    vestigial — if freezegun is eventually removed from the project the
    mypy section should be removed too and this test should be updated.
    """
    tests_dir = REPO_ROOT / "tests"
    importing_files = [
        p
        for p in tests_dir.rglob("*.py")
        if "freezegun" in p.read_text(encoding="utf-8")
    ]
    assert importing_files, (
        "No test file imports freezegun; "
        "remove [mypy-freezegun.*] from mypy.ini if freezegun is no longer used (L6)"
    )


# ---------------------------------------------------------------------------
# CLAUDE.md / PR template — correct mypy invocation documented
# ---------------------------------------------------------------------------


def test_claude_md_documents_strict_src_tests_mypy() -> None:
    """``CLAUDE.md`` must document ``mypy --strict src/ tests/`` (H18 / doc fix).

    The stale ``mypy src/friendex`` command caused CI/local divergence in
    earlier waves; this test ensures the documented command matches CI.
    """
    content = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "mypy --strict src/ tests/" in content, (
        "CLAUDE.md Development block must show 'mypy --strict src/ tests/'; "
        "the old 'mypy src/friendex' command diverges from CI (H18 doc regression)"
    )


def test_pr_template_documents_strict_src_tests_mypy() -> None:
    """PR template Verification checklist must reference ``mypy --strict src/ tests/``.

    The PR template is the dev-facing gate checklist; it must match CI.
    """
    path = REPO_ROOT / ".github" / "pull_request_template.md"
    content = path.read_text(encoding="utf-8")
    assert "mypy --strict src/ tests/" in content, (
        "PR template must reference 'mypy --strict src/ tests/' in Verification; "
        "the old 'mypy src/friendex' command diverges from CI (H18 doc regression)"
    )
