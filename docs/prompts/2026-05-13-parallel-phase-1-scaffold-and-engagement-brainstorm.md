/superpowers:dispatching-parallel-agents

Dispatch two fully-independent parallel tracks against the Friendex repo at `/home/alex/Friendex` (branch `feat/refactor-plan`). The tracks share no files; do not serialize them.

## Pre-flight — Create private GitHub remote

The local repo has no `origin` yet. Before either track starts, create the GitHub repo (private) and push existing branches. Both tracks need the remote to push their feature branches and open PRs.

```bash
cd /home/alex/Friendex
gh repo create z0rd0n88/Friendex --private --description "Discord bot stock-exchange game"
git remote add origin https://github.com/z0rd0n88/Friendex.git
git push -u origin main
git push -u origin feat/refactor-plan
```

Then open a draft PR for the planning docs so they're visible to reviewers:
```bash
gh pr create --base main --head feat/refactor-plan --draft \
  --title "docs: phase 1-3 planning (current state, target architecture, python review, migration plan, testing strategy)" \
  --body "Planning artifacts for the Friendex greenfield build. See \`docs/01-\` through \`docs/05-\`. Implementation tracking lives in the master issue (created by the next phase)."
```

After this completes, both tracks below can run in parallel.

## Track A — Phase 0 + Phase 1 implementation kickoff

Begin executing the authoritative build plan in `docs/04-migration-plan.md`. **Scope this session to Phase 0 and Phase 1 only.** Stop at the Phase 1 verification gate so the user can review before any game-logic decisions land.

Setup (mandatory, per `CLAUDE.md` worktree rule):
```bash
cd /home/alex/Friendex
git worktree add .worktrees/phase-1-scaffold -b feat/phase-1-scaffold main
cd .worktrees/phase-1-scaffold
```
Branch from `main`, **not** from `feat/refactor-plan` — planning docs and scaffold ship as separate PRs.

Deliverables:
- **Phase 0**: Open the GitHub master tracking issue exactly as specified in `docs/04-migration-plan.md` §Phase 0 (use `gh issue create`; `GH_TOKEN` is in env).
- **Phase 1**: Produce only the files listed in `docs/04-migration-plan.md` §Phase 1 — `pyproject.toml`, lint/type/test toolchain config, CI workflow under `.github/workflows/`, and the empty `src/friendex/` package tree with `__init__.py` files. No domain code, no adapters, no bot logic.
- Run the Phase 1 verification gate (lint, type-check, pytest collection on empty suite, CI workflow syntax check) and paste the output into the final commit message or PR description.
- Commit in logical chunks using Conventional Commits. Push with `-u` and open a draft PR linked to the master tracking issue (`Refs #N`, not `Closes`).

Hard constraint: if Phase 1 verification fails, fix and re-run — do not advance to Phase 2.

## Track B — Engagement brainstorming

Invoke `/product-management:product-brainstorming` to ideate ways to make Friendex more engaging for Discord players. Pure ideation — touches no code.

Inputs to ground the agent:
- Game premise: `CLAUDE.md` §Project and `docs/spec/original-skeleton.md`.
- Current command surface and mechanics: `docs/01-current-state.md` §Bot Commands and §Data Model.

Deliverable: write `docs/product/2026-05-13-engagement-ideas.md` containing:
1. **Executive summary** (≤150 words).
2. **Idea table** grouped by theme (social mechanics, progression, scarcity events, prediction markets, seasonal cycles, others as warranted). Columns: idea, one-line description, effort (S/M/L), impact (Low/Med/High).
3. **Top-3 shortlist** with rationale for each (why high-leverage given the existing price/activity engine).
4. **Non-goals** — ideas explicitly rejected because they break the bot's premise, exceed scope, or conflict with the layered architecture in `docs/02-target-architecture.md`.

Commit the doc on a new worktree: `git worktree add .worktrees/engagement-ideas -b docs/engagement-ideas main`. Open a draft PR.

## Reporting

After both tracks finish, return a single consolidated summary:
- Pre-flight: remote URL, planning-docs PR URL.
- Track A: branch name, commit SHAs, PR URL, verification-gate output (pass/fail per check), master issue number.
- Track B: path to the engagement-ideas doc, PR URL, and a one-paragraph teaser of the top-ranked idea.
