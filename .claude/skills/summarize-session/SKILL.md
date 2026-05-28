---
name: summarize-session
description: Write a detailed summary of the current session — decisions made, files changed, problems solved, and open items — saved to ~/.claude/summaries/.
---

# summarize-session

Analyze the full conversation and produce a detailed, structured summary. Save it to `~/.claude/summaries/` as a timestamped Markdown file, then print the path and an abbreviated preview to the chat.

## Steps

1. **Determine the timestamp** — run `date +%Y-%m-%d_%H-%M` to get the current time.
2. **Derive the project name** — basename of the git repo root, falling back to the cwd basename when not in a repo. Strip a leading dot and replace any character outside `[A-Za-z0-9._-]` with `-`:
   ```bash
   PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" | sed 's/^\.//; s/[^A-Za-z0-9._-]/-/g')
   ```
   Examples: `~/.claude` → `claude`, `~/BotHaus` → `BotHaus`, `~/VistaTrader` → `VistaTrader`, no repo → cwd basename.
3. **Ensure the output directory exists** — `mkdir -p ~/.claude/summaries`.
4. **Write the summary file** to `~/.claude/summaries/<timestamp>_<project>.md` (timestamp first so the folder stays sorted chronologically).
5. **Print the file path** and a one-paragraph preview in the chat so the user sees it was saved.

## Summary file structure

```markdown
# Session Summary — <YYYY-MM-DD HH:MM>

## Executive Summary
One paragraph: what the session accomplished, what was left open.

## Context
- **Project / repo**: (e.g. ~/.claude, BotHaus, VistaTrader, or "no repo")
- **Working directory**: (cwd at time of summary)
- **Branch / worktree**: (if applicable)

## What Was Done
Chronological bullet list of every meaningful action taken:
- Problem identified / question asked
- Files created, modified, or deleted (include paths)
- Commands run and their outcomes
- Decisions made and rationale
- Agents or skills invoked

## Key Decisions & Rationale
For each non-trivial choice, one line: decision → why.

## Files Changed
Table: | File | Change type | Notes |
List every file that was created, edited, or deleted.
Omit if nothing was changed.

## Problems & Solutions
For each bug, error, or blocker encountered: what it was → how it was resolved (or that it's still open).

## Open Items / Next Steps
Bulleted list of:
- Unfinished work explicitly left open
- Follow-up tasks the user mentioned
- Unresolved errors or questions
- Suggested next actions

## Gotchas & Learnings
Any configuration quirks, env-specific behaviors, or non-obvious facts discovered this session that would help future sessions. This section feeds CLAUDE.md over time.
```

## Scope rules

- Cover the **entire** conversation from the first user message.
- Do **not** duplicate content already in commits, PRDs, or ADRs — reference them by path/URL instead.
- If the session was very short (≤ 3 exchanges), say so and produce a condensed summary (executive summary + open items only).

## After saving

Print:
```
Session summary saved → ~/.claude/summaries/<timestamp>_<project>.md

<executive summary paragraph>
```

Do not append to MEMORY.md automatically — leave that to the user or the `claude-md-management:revise-claude-md` skill.
