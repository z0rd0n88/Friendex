#!/usr/bin/env bash
# Deterministic quality gate for baton-runner review units.
#
# Usage: scripts/gate.sh <log-dir>
#   Runs the repo's checks, tees each to <log-dir>, prints GATE: PASS|FAIL,
#   and exits non-zero if ANY check fails. All checks run even if an earlier
#   one fails, so the review unit sees the full picture in one pass.
#
# The gate is intentionally fixed and committed: verification must be the same
# every run, not improvised per spawn. Adjust the check list here (under review)
# rather than in agent prompts.
#
# Run note (br-2026-05-23-p4p5, gate option A): ruff is scoped to `src tests`
# rather than `.` because the repo has pre-existing ruff/format violations in
# `.githooks/gen_arch.py` (unrelated to phases 4-5). Scoping keeps the gate
# meaningful for package code; the .githooks cleanup is tracked as a separate
# deferred follow-up.
set -uo pipefail

LOG_DIR="${1:?usage: gate.sh <log-dir>}"
mkdir -p "$LOG_DIR"

run() {
  local name="$1"; shift
  echo "=== gate: ${name} (\$*: $*) ==="
  if "$@" >"${LOG_DIR}/${name}.log" 2>&1; then
    echo "PASS ${name}"
    return 0
  fi
  local rc=$?
  echo "FAIL ${name} (exit ${rc}) -> ${LOG_DIR}/${name}.log"
  return 1
}

fail=0
run pytest      uv run pytest                                  || fail=1
run ruff-check  uv run ruff check src tests alembic            || fail=1
run ruff-format uv run ruff format --check src tests alembic   || fail=1
run mypy        uv run mypy src/friendex                       || fail=1

echo "----"
if [ "${fail}" -eq 0 ]; then
  echo "GATE: PASS"
else
  echo "GATE: FAIL"
fi
exit "${fail}"
