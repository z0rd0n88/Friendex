# friendex-total-review — Reference

Detailed mode definitions, agent maps, prompt templates, and consolidation rules. Loaded on demand from SKILL.md.

## Layer slices

Every mode that fans out uses some subset of these seven slices. Boundaries match `ARCH.md` and ADR-0001 so reviewer scopes don't overlap.

| Slug | Path | What's in scope |
|---|---|---|
| `domain` | `src/friendex/domain/` | Pure models, errors, `market_hours`, `price_engine`, `fund_math`, `activity` |
| `application` | `src/friendex/application/` | Services (trading, fund, portfolio, daily, stats, activity, liquidation, price_tick, discipline, voice_ping, voice_session_store), `interfaces.py`, `lock_manager.py`, DTOs |
| `persistence` | `src/friendex/adapters/persistence/` | ORM, repos, `types.py` (DecimalText/UtcDateTime), `db.py`, `migrate_json_to_sqlite.py` |
| `discord` | `src/friendex/adapters/discord_bot/` | `cogs/`, `listeners/`, `embeds.py`, `error_handler.py`, `bot.py` |
| `tasks` | `src/friendex/adapters/tasks/` | `base_task.py`, `task_runner.py`, all 8 background loops |
| `wiring` | `src/friendex/adapters/config.py`, `container.py`, `src/friendex/main.py` | DI root, Settings, entry point |
| `tests` | `tests/`, `scripts/`, `pyproject.toml`, `.github/workflows/`, `mypy.ini` | Test suite, fixtures, fakes, CI gates |

Invariants every reviewer must respect:
- Money = `Decimal`, quantised to `$0.01`, `ROUND_HALF_EVEN`
- Datetimes = tz-aware UTC; `UtcDateTime` rejects naive on bind
- Per-guild markets; composite locks `"<guild_id>:<user_id>"` via `application/lock_manager.py`
- Services depend on Protocols in `application/interfaces.py`; **no adapter imports inside `application/`**
- Domain dataclasses are mutable on purpose (mutation via `dataclasses.replace`); enforced by convention, not `frozen=True`

## Exclusion list

Before any review fan-out, fetch open-issue findings to skip. Reviewers must never re-report tracked items.

```bash
gh issue list \
  --repo z0rd0n88/Friendex \
  --state open \
  --limit 30 \
  --label review,tech-debt,security,performance,architecture \
  --json number,title,body \
  --jq '.[] | "#\(.number) — \(.title)"'
```

Then, for each matched issue, extract checkbox lines from the body and convert to a flat skip list of the form `<short title> — <file:line>`. Inject this verbatim into every reviewer prompt under a heading like:

> **DO NOT report findings already tracked in:**
> - Issue #N (<short title>): <skip rule 1>, <skip rule 2>, ...
> - Issue #M: ...

Reviewers should be instructed to verify each finding against this list **before** emitting it. Drift here is the #1 source of duplicate work.

## Mode → agents map

Each mode declares: scopes (slices), agent per `(slice, lens)`, word budget per agent.

### Mode `code`

Goal: correctness, atomicity, money math, error taxonomy, idioms, typing. Two lenses per file = complementary findings.

| Slice | Lens 1 (correctness) | Lens 2 (idiom) | Word cap |
|---|---|---|---|
| `domain` | `code-reviewer` | `python-pro` | 1200 |
| `application` | `code-reviewer` | `python-pro` | 1200 |
| `persistence` | `code-reviewer` | `python-reviewer` | 1100 |
| `discord` | `code-reviewer` | `python-reviewer` | 1100 |
| `tasks` | `code-reviewer` | `python-reviewer` | 900 |
| `wiring` | `code-reviewer` | `python-pro` | 1100 |

Map first with `code-explorer` × 4 (domain / application / adapters-all / wiring+tests). 10–12 review agents in parallel in a single tool-use block.

### Mode `cleanup`

Goal: dead code, dup consolidation, unused private helpers, F401/ARG. Behaviour-preserving only — do not pre-empt items tracked under `code`/`security`.

| Step | Tool | Scope |
|---|---|---|
| 1 | `unused-code-cleaner` agent | whole repo |
| 2 | `refactor-cleaner` agent | application + persistence |
| 3 | `code-simplifier` agent | domain + discord |
| 4 | `simplify` skill | one PR-sized batch |
| 5 | `refactor-clean` skill | safe deletes with per-change verification |

Verification gate per produced PR: `uv run pytest && uv run mypy src/friendex && uv run ruff check . && uv run ruff format --check .`.

### Mode `security`

Goal: input/output boundary + money-flow + auth. Two passes; OWASP A01–A10 framing plus economic exploits.

| Pass | Agent / skill | Scope |
|---|---|---|
| 1 — Boundary | `security-reviewer` | `discord` + `persistence` + `wiring` (token/secrets) |
| 2 — Money & auth | `security-reviewer` | `application` + cogs + listeners |
| 3 — Static lint | `ecc-security-review` skill | whole repo |
| 4 — Supply chain (optional) | `mcp__plugin_semgrep_semgrep__semgrep_scan_supply_chain` | `pyproject.toml` + lock |

Adversaries to model:
- Malicious guild member (game-economy exploits: self-trade, sandwich, sock-puppet activity botting)
- Malicious fund manager (rugpull paths around `withdraw` / `send_to_events`)
- Guild admin (weaponising timeout/ban discipline penalty for short profit)
- Discord-level abuse (markdown injection, embed character DoS, mention escape)

### Mode `architecture`

Goal: hexagonal boundary violations, dependency-direction drift, deepening opportunities, silent-failure ladder.

| Agent / skill | Lens | Scope |
|---|---|---|
| `code-architect` | hexagonal seam audit, dependency-direction check | `domain` + `application` + adapter imports |
| `improve-codebase-architecture` skill | deepening opportunities; ADR coverage | whole repo (uses `CONTEXT.md` + `docs/adr/`) |
| `silent-failure-hunter` | error-propagation ladder | `application` + adapters |
| `critical-thinking` agent | private-API coupling, dead placeholders, no-op safety nets | `wiring` + `tasks` |

Read `ARCH.md` first. Specifically verify:
- `application/` imports nothing from `adapters/`
- ADR coverage for any service added since the last ADR
- No private cross-service calls (the `LiquidationService → TradingService._cover_internal` pattern is the canonical smell)

### Mode `test`

Goal: coverage gaps, fake parity, mock-spec adequacy.

| Agent / skill | Lens | Scope |
|---|---|---|
| `tdd-guide` | coverage gap + write-tests-first discipline | `tests` |
| `python-reviewer` | fixture quality + `AsyncMock(spec=...)` drift | `tests` |
| `code-reviewer` | parity between `tests/application/fakes/fake_repos.py` and concrete repos | `tests` + `persistence` |
| `test-coverage` skill | branch-coverage delta, target 80%+ | whole `src/` |

Always run `uv run pytest --cov=friendex --cov-report=term-missing` first; pin the baseline at the top of the issue body.

### Mode `perf`

Goal: N+1, SQLite contention, hot-path Decimal cost, embed limits, task jitter.

| Agent / skill | Lens | Scope |
|---|---|---|
| `performance-optimizer` | N+1, batched IN-query chunking, jitter | `persistence` + `tasks` |
| `ecc-benchmark` skill | hot-path measurement | `application` services |
| `python-pro` | repeated `Decimal(str(...))` cost; `asyncio.TaskGroup` / `asyncio.timeout` opportunities | `application` + `wiring` |
| `code-explorer` | tick-cohort wall-clock alignment trace | `tasks` |

Friendex-specific concerns: SQLite write-lock contention at the 5-min/15-min tick boundaries; `_rebuild_many` IN-query chunking against the 999-var SQLite limit; embed character DoS (Discord 6000-char total cap); `apply_floor_stall` attenuation math on the hot tick path.

### Mode `docs`

Goal: detect drift between code and `ARCH.md`, ADR coverage gaps, baton-pass freshness.

| Agent / skill | Lens | Scope |
|---|---|---|
| `documentation-expert` | drift between `ARCH.md` tree & source | repo root |
| `ecc-architecture-decision-records` skill | ADR gaps for service-level changes | `docs/adr/` + `application/` |
| `code-explorer` | baton-pass health vs. current PR state | `baton-pass/INDEX.md` |

Single read pass. If drift is small, propose a minimal patch instead of filing an issue. If `ARCH.md` is stale, run `python3 .githooks/gen_arch.py` and offer to commit the regen on a worktree.

### Mode `pre-pr` (diff-only)

Goal: cheap sanity check on the current worktree's diff before opening a PR. **No issue filed.**

| Agent | Lens | Scope |
|---|---|---|
| `code-reviewer` | correctness on changed lines | `git diff main...HEAD` |
| `python-reviewer` | idioms on changed lines | same |
| `silent-failure-hunter` | swallowed errors on changed lines | same |

Output: inline summary in chat; severity-bucketed findings; if any CRITICAL/HIGH, recommend fixing before `gh pr create`. Optionally chain `verification-before-completion` skill before reporting clean.

## Issue body template

```markdown
# <Mode> review (<YYYY-MM-DD>)

A parallel sweep using <agents/skills> over <slices>. Findings consolidated below with checkboxes for tracking. Cross-references to in-flight issues are noted; **do not double-tick**.

**Severity counts:** CRITICAL <n> · HIGH <n> · MEDIUM <n> · LOW <n>
**Verdict:** BLOCK | WARN | INFO — <one-line rationale>

---

## CRITICAL (block)
- [ ] **C1 — <one-line title>.** <evidence>. **Fix:** <action>. — `path/to/file.py:LL-LL`

## HIGH (warn)

### <Theme A>
- [ ] **H1 — ...** — `path:LL`

### <Theme B>
- [ ] **H2 — ...** — `path:LL`

## MEDIUM (info)
- [ ] **M1 — ...** — `path:LL`

## LOW (note)
- [ ] **L1 — ...** — `path:LL`

---

## Suggested PR slicing

Branches grouped by shared file/intent so each fits one reviewable PR:

1. **`<branch-name>`** — Cn, Hn, Hn (one-line scope)
2. **`<branch-name>`** — ...

## Source of findings

- Mapped by `<agent>` × N (<slice>, <slice>, ...).
- Reviewed by `<agent>` × N (<lens>) and `<agent>` × N (<lens>).
- Excluded findings already tracked in: #82 (review), #83 (simplifier sweep), ...
- Verdict rationale: <one paragraph>.
```

## Consolidation rules

1. **Dedupe by `(file, line, root cause)`.** If two lenses raise the same call site, merge bodies and note both perspectives — never list twice.
2. **Bucket by severity.** Reviewers' severity stands unless a lens disagrees; in that case pick the higher.
3. **Group HIGH items into themes.** Themes feed the PR-slicing block. Typical themes: *money math*, *atomicity*, *tasks*, *persistence*, *Discord boundary*, *taxonomy/duplication*, *tests/typing*.
4. **Tag every item with the slice slug** as a prefix in the checkbox text *only* if the issue spans multiple slices; otherwise the file path is enough.
5. **If an item is already in an open tracked issue, drop it silently** — never re-report.
6. **Verdict** — BLOCK if any CRITICAL; WARN if no CRITICAL but ≥1 HIGH; INFO otherwise. One-line rationale at the top.

## Failure modes & guards

- **Word-budget overrun.** Agent returns too long. Re-prompt with a stricter cap, or split the slice further. Hard-cap reports at 1500 words per agent.
- **Stale exclusion list.** Re-fetch `gh issue list` before each fan-out; cache only within one invocation.
- **Empty slice.** If a slice has no files (e.g. a brand-new package with no tests yet), skip it; do not spawn an agent.
- **Agent overlap.** If two agents are repeatedly flagging the same single file, narrow the second agent's prompt to "skip findings in `<file>`" and rerun.
- **Discord rate limits / runtime concerns during follow-up fixes.** Out of scope; this skill files issues, it does not push code.
- **GH auth.** Requires `gh auth status` clean. Per project CLAUDE.md, `GH_TOKEN` is sourced from `~/.secrets`; verify before invoking the issue-create step.

## Worked example: `code` mode

The 2026-05-28 review pass (issue #82) is the canonical example:
- **Mapping:** 4 `code-explorer` agents in parallel (domain / application / adapters / tests+entry).
- **Review:** 6 agents in parallel (4 `code-reviewer` + 2 `python-reviewer`) across the priority files surfaced by the explorers.
- **Counts:** 3 CRITICAL · 17 HIGH · 14 MEDIUM · 6 LOW.
- **PR slicing:** 5 themed branches in the issue body, each self-contained.
- **Verdict:** BLOCK — three CRITICALs.

The follow-up pass (this skill's design driver) added `python-pro`, `security-reviewer`, `silent-failure-hunter` lenses without re-flagging anything from #82 or #83 — proof that the exclusion-list discipline works.

## Mode cheat-sheet

```
code         | correctness + idiom        | 10-12 agents | 1 issue
cleanup      | dedup + dead code          |  5 steps     | 1 issue
security     | OWASP + economic exploits  |  4 passes    | 1 issue
architecture | hex boundaries + deepening |  4 agents    | 1 issue
test         | coverage + fake parity     |  4 agents    | 1 issue
perf         | N+1 + contention + Decimal |  4 agents    | 1 issue
docs         | ARCH/ADR/baton drift       |  3 agents    | 0-1 issue
pre-pr       | diff-only sanity           |  3 agents    | 0 issues
all          | every mode except pre-pr   |  ~40 agents  | 7 issues
```
