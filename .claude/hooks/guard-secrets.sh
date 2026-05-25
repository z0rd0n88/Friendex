#!/usr/bin/env bash
#
# PreToolUse guard: block Edit/Write/MultiEdit into secret / lock files —
# .env (any variant except *.example templates) and uv.lock.
#
# Rationale: secrets belong in the environment, never in tracked files; uv.lock
# is managed by `uv` (uv lock / uv sync) and must not be hand-edited.
#
# Exit 2 blocks the tool call and feeds the message back to Claude. Tools that
# write via Bash (e.g. `uv lock`) are unaffected — this guards only the file
# tools. The path is read from the hook's stdin JSON (tool_input.file_path);
# there is no CLAUDE_FILE_PATH env var.
set -euo pipefail

file_path="$(python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print(""); sys.exit(0)
ti = data.get("tool_input") or {}
print(ti.get("file_path") or "")
' 2>/dev/null || true)"

if [ -z "$file_path" ]; then
    exit 0
fi

base="$(basename "$file_path")"

case "$base" in
    *.example)
        # .env.example and other templates are safe, tracked, meant to be edited.
        exit 0
        ;;
    .env | .env.* | uv.lock)
        printf 'Blocked: refusing to edit %s directly.\n' "$base" >&2
        printf '  .env* holds secrets — keep them in the environment, not tracked files.\n' >&2
        printf '  uv.lock is managed by uv — run `uv lock` / `uv sync`, do not hand-edit.\n' >&2
        exit 2
        ;;
esac
exit 0
