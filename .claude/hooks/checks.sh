#!/usr/bin/env bash
#
# PostToolUse advisory checks for Friendex.
#
# After an Edit/Write/MultiEdit to a Python file under src/ or tests/:
#   1. auto-format + autofix the edited file with ruff (deterministic, safe)
#   2. run the NARROWEST relevant test (mapped src -> tests, or the test file
#      itself) and print a concise PASS/FAIL summary to stderr.
#
# ADVISORY BY DESIGN: this hook always exits 0, so it never blocks a tool call.
# Friendex follows TDD — a blocking test hook would fight the RED phase (a test
# you just wrote and expect to fail) and mid-refactor states. The summary lands
# on stderr so regressions stay visible without sabotaging red-green.
# Comprehensive gating lives in CI / pre-commit.
#
# The edited path is read from the hook's stdin JSON (tool_input.file_path) —
# there is no CLAUDE_FILE_PATH env var. Project root comes from CLAUDE_PROJECT_DIR.
set -euo pipefail

root="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

file_path="$(python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print(""); sys.exit(0)
ti = data.get("tool_input") or {}
print(ti.get("file_path") or "")
' 2>/dev/null || true)"

# Only act on .py files under src/ or tests/.
case "$file_path" in
    *.py) ;;
    *) exit 0 ;;
esac
case "$file_path" in
    "$root"/src/* | "$root"/tests/* | src/* | tests/*) ;;
    *) exit 0 ;;
esac

cd "$root"
rel="${file_path#"$root"/}"

# 1. Format + autofix the edited file. Advisory: never fail the hook on this.
uv run ruff format "$rel" >/dev/null 2>&1 || true
uv run ruff check --fix "$rel" >/dev/null 2>&1 || true

# 2. Pick the narrowest relevant test file.
target=""
case "$rel" in
    tests/*)
        target="$rel"                       # editing a test -> run that test file
        ;;
    src/friendex/*)
        sub="${rel#src/friendex/}"          # <subdir...>/<mod>.py  (or <mod>.py)
        dir="$(dirname "$sub")"
        mod="$(basename "$sub" .py)"
        if [ "$dir" = "." ]; then
            cand="tests/test_${mod}.py"
        else
            cand="tests/${dir}/test_${mod}.py"
        fi
        [ -f "$cand" ] && target="$cand" || true
        ;;
esac

if [ -z "$target" ]; then
    printf '\xe2\x9c\x93 ruff applied to %s (no matching test file; tests skipped)\n' "$rel" >&2
    exit 0
fi

out="$(mktemp)"
trap 'rm -f "$out"' EXIT
if uv run python -m pytest "$target" -q >"$out" 2>&1; then
    printf '\xe2\x9c\x93 friendex checks passed (ruff + %s)\n' "$target" >&2
else
    printf '\xe2\x9a\xa0 friendex tests FAILED in %s (advisory - not blocking)\n' "$target" >&2
    tail -20 "$out" >&2 || true
fi
exit 0
