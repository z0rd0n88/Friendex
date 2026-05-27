# Pass-Baton: Phase 16 smoke-test driver + runbook implemented

**Date:** 2026-05-27
**Scope:** phase-16
**Branch:** feat/phase-16-cutover
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-16
**HEAD:** a1f40ab Phase 15b — Migrator --dry-run, --report, orphan consistency check (#67)

## Where things stand

Phase 16 (Production Smoke Test cutover artifacts) implementation is complete in the worktree and unstaged — ready for the manager to commit. The deliverables are three new files under the existing repo layout (plus two `__init__.py` shims):

- `scripts/__init__.py` — **new** (declared per containment contract; the package previously did not exist).
- `scripts/smoke_test_commands.py` — defines `SmokeStep` (frozen, slots) + immutable `STEPS: tuple[SmokeStep, ...]` (32 entries, ids 1..32, strictly increasing) + `main()` byte-stable printer.
- `tests/scripts/__init__.py` — pre-existed in the worktree scaffold; left alone per AC6.
- `tests/scripts/test_smoke_test_commands.py` — 13 tests covering AC3(a)..(e) plus structural pins. Pre-staged in the worktree as a TDD scaffold; I fixed lint issues (pairwise, TYPE_CHECKING-gated Iterator, ERA001 comment rewrite) without weakening assertions.
- `docs/runbook-smoke-test.md` — Pre-flight / Bot launch / Step-by-step verification / Post-flight / Sign-off; runbook explicitly delegates the step list to the script as single source of truth.

All Phase 16 acceptance criteria (AC1-AC6) are satisfied. Current blocking state: **none — ready for manager commit.**

## TDD red→green captured

`pytest tests/scripts/test_smoke_test_commands.py` initial run, with the test scaffold present but the script absent:

```
ImportError while importing test module 'tests/scripts/test_smoke_test_commands.py'.
tests/scripts/test_smoke_test_commands.py:27: in <module>
    from scripts.smoke_test_commands import STEPS, SmokeStep, main
E   ModuleNotFoundError: No module named 'scripts.smoke_test_commands'
```

After creating `scripts/__init__.py` + `scripts/smoke_test_commands.py` with `command` containing arg placeholders (e.g. `/buy <user> <shares>`), 2/13 tests failed:

- `test_every_slash_command_is_represented_exactly_once` — `EXPECTED_SLASH_COMMANDS` holds bare slash names (`/buy`), so set comparison failed with both "Extra items in the left set" and missing items.
- `test_fund_invest_step_notes_not_implemented_error` — looked for the literal step with `command == "/fund invest"`; got 0 matches because the step's command was `/fund invest <fund> <amount>`.

Resolution: move usage / argument hints from `command` into the `expected` text; keep `command` as the bare slash literal. Re-ran → 13/13 green.

## Verification gates (all green, from the worktree)

- `uv run ruff check scripts/ tests/scripts/ src/ tests/` → `All checks passed!`
- `uv run ruff format --check scripts/ tests/scripts/ src/ tests/` → `151 files already formatted`
- `uv run mypy src/friendex` → `Success: no issues found in 70 source files`
- `uv run pytest` → `803 passed, 1 warning in 16.09s` (790 previous + 13 new in `tests/scripts/`)
- `uv run python scripts/smoke_test_commands.py` → exit 0; output is deterministic across two consecutive runs (asserted via `test_main_output_is_byte_stable_across_runs`).

## Notes for the manager / next session

- I added `scripts/__init__.py` so `from scripts.smoke_test_commands import ...` works under the project's `pythonpath` resolution. AC6 allows the file as an optional add; it's a one-line docstring.
- `tests/scripts/__init__.py` already existed in the scaffold — untouched.
- No `pyproject.toml` / `uv.lock` changes. No edits under `src/friendex/`. No edits to existing tests.
- The runbook references `data/friendex.db` per `Settings.database_url` default; if the deployment overrides `DATABASE_URL` the post-flight DB-file check generalises trivially.
- Phase-15b carry-forward honoured: migrator pre-flight is documented as **OPTIONAL** with the exact flag set (`--guild-id <id> --dry-run --report`), notes alphabetical `--report` ordering, in-memory dry-run target isolation, warn-not-fail orphan check.
- Phase-11c carry-forward honoured: `/fund invest` step expected text explicitly calls out "NotImplementedError" + "ephemeral" so the operator does not flag it as a regression.

## Next steps

1. Manager commits the unstaged changes in two logical units if desired (script + tests as one, runbook as the second) or as a single Phase 16 commit. Suggested message: `feat(phase-16): production smoke-test driver + runbook (Refs #2)`.
2. Open PR against `origin/main` using `.github/pull_request_template.md`; mark Verification gates green with the output above. Note `Refs #2` for the phase tracker.
3. Once merged, run `python3 .githooks/gen_arch.py` (or rely on the pre-commit hook) to refresh `ARCH.md`.
4. The smoke test itself runs at production cutover time — the runbook is the operator-facing artifact.

## Open questions / risks

- None blocking. One soft point: `test_main_output_is_byte_stable_across_runs` exercises determinism within a single process; if a future change introduces locale-dependent formatting or non-deterministic dict ordering, the test would catch it on the next CI run.

## References

- Issues: #2 (phase tracker)
- Docs: `docs/04-migration-plan.md` §Phase 16 (lines 828-854); `docs/runbook-smoke-test.md` (new)
- Code: `scripts/smoke_test_commands.py:1`, `tests/scripts/test_smoke_test_commands.py:1`
- Prior digests honoured: `baton-runner/br-2026-05-27-phase-15/digest-phase-15b.md`, `baton-runner/br-2026-05-27-phase-14/digest-phase-14.md`, `baton-runner/br-2026-05-27-phase-13/digest-phase-13.md`, `baton-runner/br-2026-05-27-phase-12/digest-phase-12a.md`, `baton-runner/br-2026-05-27-phase-12/digest-phase-12b.md`, `baton-runner/br-2026-05-26-phase-11/digest-phase-11c.md`, `baton-runner/br-2026-05-25-phase-9/digest-phase-9.md`
