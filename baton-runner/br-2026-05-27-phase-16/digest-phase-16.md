# Phase 16 — Exit Digest

**Branch:** feat/phase-16-cutover  **HEAD:** 5b8da42 chore: smoke test driver script
**Verdict:** CLEAN (gate green; M1-M6 mutation menu all RED-verified under revert)

## Public surface added

- `scripts/smoke_test_commands.py` (NEW, 492 lines)
  - `SmokeCategory = Literal["startup","slash","listener","background","shutdown"]`
  - `@dataclass(frozen=True, slots=True) class SmokeStep` — fields
    `id:int`, `category:SmokeCategory`, `name:str`, `command:str`,
    `expected:str`. **Both** immutability layers (frozen + slots) required.
  - `STEPS: Final[tuple[SmokeStep, ...]]` — 32 entries, ids 1..32,
    strictly increasing; ordering is contractual.
  - `def main() -> int` — prints a deterministic, byte-stable, numbered
    checklist. Exit code 0. Format pin: each step header is
    `Step <id>. <name>  (<category>)` — `test_main_prints_steps_in_strict_id_order`
    parses this prefix.
- `tests/scripts/test_smoke_test_commands.py` (NEW, 13 tests)
- `docs/runbook-smoke-test.md` (NEW, 167 lines) — operator runbook.
- `scripts/__init__.py` + `tests/scripts/__init__.py` — one-line docstring shims.

## Decisions Phase 17 MUST honour

1. **`/fund invest` step (id=18) is the carry-forward pin.** When
   Phase 17 implements `FundService.invest()`, you MUST update
   `STEPS[id=18].expected` to drop the "NotImplementedError" /
   "deferred to Phase 17" language and describe the real success +
   error paths. The test `test_fund_invest_step_notes_not_implemented_error`
   in `tests/scripts/test_smoke_test_commands.py:122` MUST be deleted
   or rewritten in the same PR — leaving it pinned will fail the gate
   the moment the new expected text loses "notimplementederror".
2. **Runbook step-count ranges.** `docs/runbook-smoke-test.md`
   sign-off table cites id ranges per section (e.g. "Slash commands
   4–20"). Phase 17 will not add new STEPS but if any phase
   ever does, update the ranges in the same commit.
3. **Single-source-of-truth contract.** The runbook MUST NOT
   enumerate the step list. If a future change is tempted to
   inline commands into the runbook, push them into `STEPS` instead
   and reference the script.
4. **`STEPS` ordering + immutability are load-bearing.** Do not
   re-sort, do not convert to list, do not mutate. Tests M4 and M5
   pin both invariants.
5. **No `discord` import in `scripts/` or `tests/scripts/`.** The
   smoke-test driver is intentionally side-effect-free; Phase 9 +
   Phase 12 no-discord-in-non-Discord-layers invariant extends here.
6. **No new top-level deps.** This phase added zero
   `pyproject.toml` / `uv.lock` changes; Phase 17 should hold the line.

## Verification (from gate-phase-16-iter-1/)

- pytest: 803 passed, 1 warning (~13s)
- ruff check / ruff format / mypy: all green
- Determinism: two runs of `uv run python scripts/smoke_test_commands.py`
  produce md5 `2bce67e1d4993e0f1242d8ee3a236aa4` identically.

## References

- Spec: `docs/04-migration-plan.md` §Phase 16 (lines 828-854)
- Implementation baton: `baton-pass/phase-16/000-2026-05-27-smoke-test-driver-implemented.md`
- Review baton: `baton-pass/phase-16/001-2026-05-27-review-clean.md`
- Phase tracker: GitHub issue #2
