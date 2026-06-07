---
name: friendex-total-review
description: Multi-agent codebase review for Friendex (Discord bot, hexagonal Python). Modes — code, cleanup, security, architecture, test, perf, docs, pre-pr. Triggers on /friendex-total-review or "review pass", "security sweep", "architecture audit", "pre-PR triage".
---

# friendex-total-review

Project wrapper for the shared [`total-review`](https://github.com/z0rd0n88/ClaudeConfig/blob/main/skills/total-review/SKILL.md) pattern (lives at `~/.claude/skills/total-review/`).

Follow `~/.claude/skills/total-review/REFERENCE.md` step-by-step using this directory's [`config.yml`](config.yml). **Read `<repo>/ARCH.md` first** — it is the authoritative file-tree map.

## Friendex-specific notes

These supplement the global REFERENCE.md guidance; reviewers should treat them as additional invariants on top of what `config.yml` carries.

- **Money math** — every monetary field is `Decimal`, quantised to `$0.01`, `ROUND_HALF_EVEN`. `DecimalText` SQLAlchemy type round-trips it. Floats in money paths are a bug.
- **Datetimes** — tz-aware UTC. `UtcDateTime` rejects naive on bind. `datetime.utcnow()` is banned.
- **Per-guild composite locks** — `application/lock_manager.py` keys are `"<guild_id>:<user_id>"`. Composite key violations are the canonical atomicity smell.
- **Dataclass mutation by convention** — domain models are *not* `frozen=True`; mutation happens via `dataclasses.replace`. Enforced socially, not structurally.
- **No adapter imports inside `application/`** — services depend on Protocols in `application/interfaces.py` only. Reverse-direction imports are an architecture-mode failure.
- **`LiquidationService → TradingService._cover_internal`** — this private cross-service call is the canonical "private-API coupling" smell; check for new instances.

## Adversaries to model in `security` mode

In addition to OWASP A01–A10:
- Malicious guild member (self-trade, sandwich, sock-puppet activity botting)
- Malicious fund manager (rugpull paths around `withdraw` / `send_to_events`)
- Guild admin (weaponising timeout/ban discipline penalty for short profit)
- Discord-level abuse (markdown injection, embed character DoS, mention escape)

## Mode quick-start

| Mode | What it does |
|---|---|
| `code` | Correctness + atomicity + idiom + typing — files issue |
| `cleanup` | Dead code + duplication + unused helpers — files issue |
| `security` | OWASP + money flow + adversaries above — files issue |
| `architecture` | Hexagonal boundaries + deepening + silent-failure ladder — files issue |
| `test` | Coverage + fake parity + `AsyncMock(spec=...)` adequacy — files issue |
| `perf` | N+1, SQLite contention (5-min/15-min tick boundaries), `Decimal` hot-path, embed limits, `_rebuild_many` 999-var chunking — files issue |
| `docs` | `ARCH.md` / ADR / `baton-pass/INDEX.md` drift — inline patch or small issue |
| `pre-pr` | Diff-only sanity check before opening a PR — inline summary, no issue |

`all` = every mode except `pre-pr`, one issue per mode.

## Canonical worked example

The 2026-05-28 review pass that produced issue [#82](https://github.com/z0rd0n88/Friendex/issues/82) is the canonical `code`-mode example — 4 `code-explorer` agents mapped, 6 review agents in parallel, 3 CRITICAL · 17 HIGH · 14 MEDIUM · 6 LOW, BLOCK verdict. Issues #83 and #84 add cleanup-mode and security-mode worked examples. The follow-up passes added `python-pro`, `security-reviewer`, `silent-failure-hunter` lenses without re-flagging anything from #82/#83 — proof that the exclusion-list discipline works.
