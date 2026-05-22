# Per-Guild Markets — Implementation Plan

> Companion to [ADR-0001](./adr/0001-per-guild-markets.md) (the decision) and
> [`04-migration-plan.md`](./04-migration-plan.md) (the 18-phase roadmap this re-specs).

## Executive summary

Converting Friendex from single-guild to **per-guild (multi-tenant) markets** is *not* a
new phase — it re-shapes existing ones. The build is only at Phase 3 (merged), so the
cheapest path is to **re-spec the not-yet-built phases now** and make one surgical change
to merged code, rather than retrofit later. The chosen implementation is **Approach C**:
`guild_id` lives at the ORM layer and is captured once at the adapter boundary via
`container.for_guild(guild_id)`; domain models stay guild-agnostic and unchanged. Two new
test gates — cross-guild repo isolation and DM rejection — are **blocking** (data-integrity,
not coverage). Two short pre-work PRs land first (docs + config), then the existing phases
proceed in their normal order, each re-specced as below.

## Table of contents

1. [Decision recap](#1-decision-recap)
2. [Changes to already-merged phases](#2-changes-to-already-merged-phases)
3. [Re-spec of not-yet-built phases](#3-re-spec-of-not-yet-built-phases)
4. [Command-sync change](#4-command-sync-change)
5. [DM / no-guild edge case (blocking gates)](#5-dm--no-guild-edge-case-blocking-gates)
6. [Documentation updates](#6-documentation-updates)
7. [Branch / PR sequence](#7-branch--pr-sequence)
8. [Risks and verification gates](#8-risks-and-verification-gates)
9. [Success criteria](#9-success-criteria)

---

## 1. Decision recap

Per-server markets, Approach C. `guild_id` is a runtime dimension that reaches:
**persistence** (composite keys), **repositories** (constructor-bound scope), **services**
(mostly unchanged — they receive scoped repos), **Discord adapters** (extract
`interaction.guild_id`, reject DMs), and **tasks** (iterate per guild). The pure domain
layer (Phase 4) is **unaffected**. See ADR-0001 for the full rationale and the rejection
of Approach A/B and decision #12's "no application-layer changes" claim.

`guild_id` is typed `str` everywhere, matching the existing `user_id: str` convention.

## 2. Changes to already-merged phases

These land in their **own PR before Phase 4 begins**, so no downstream phase is built
against the old shape.

### 2a. `src/friendex/adapters/config.py` (Phase 2) — REQUIRED

- `guild_id: int` (required home guild) → `dev_guild_id: int | None = None`. Keep the
  `GUILD_ID` env name working via a `validation_alias`, or document the rename in
  `.env.example`. A multi-tenant bot must **not** require knowing guilds at startup, so no
  new required field is added.
- Rewrite the docstring/comment block (lines 51–58): commands sync **globally**
  (`bot.tree.sync()`); `dev_guild_id`, when set, additionally copies global commands to
  that one guild for instant dev iteration.
- `validate_secrets` (token check) is unaffected.

### 2b. `src/friendex/domain/models.py` (Phase 3) — NO CHANGE

Under Approach C the domain layer stays guild-agnostic. Leaving this merged, 95%-covered
file (and `tests/domain/test_models.py`) untouched is a primary benefit of the chosen
approach. *(Only Approach A would change it — explicitly avoided.)*

Optionally add a `GuildOnly(DomainError)` to `domain/errors.py` here so the existing
`DomainError → ephemeral embed` path renders DM-rejection uniformly.

## 3. Re-spec of not-yet-built phases

Spec edits to `04-migration-plan.md`; the phases are empty `__init__.py` stubs today.

### Phase 4 — Domain pure functions: **NO CHANGE**
`apply_trade_impact`, `compute_activity_return`, `is_market_open`, `compute_net_worth`,
etc. take primitives/value objects. They are guild-agnostic and **must not** gain
`guild_id`. Affirm this in the spec so it isn't added defensively.

### Phase 5 — Persistence ORM & Alembic baseline: **MAJOR re-spec**
- Add `guild_id` to every per-guild table: users, long/short positions, activity buckets,
  voice unique channels, stocks, price history, hedge funds, fund investors, fund
  penalties, trade cooldowns, system state.
- Composite, `guild_id`-first primary keys:
  - `users` → `(guild_id, user_id)`
  - `long_positions` / `short_positions` → `(guild_id, owner_id, target_id)`
  - `activity_buckets` → `(guild_id, user_id, bucket_type)`
  - `voice_unique_channels` → `(guild_id, user_id, bucket_type, channel_id)`
  - `stocks` → `(guild_id, user_id)`; `price_history` → index `(guild_id, user_id, recorded_at)`
  - `hedge_funds` → `(guild_id, fund_id)`; `fund_investors` → `(guild_id, fund_id, investor_id)`
  - `fund_penalties` → `(guild_id, user_id)`
  - `system_state` → `(guild_id,)` — reset flags become **per-guild** (a guild added
    mid-week must not skip its first reset)
- Author the baseline migration `0001_baseline.py` **with** `guild_id` from the start —
  greenfield, so no separate add-column migration.
- `to_domain()` drops `guild_id`; `from_domain(guild_id, obj)` attaches it. Test: two rows
  with the same `user_id` and different `guild_id` coexist.

### Phase 6 — Repositories & interfaces: **re-spec for constructor-bound scope**
- Protocols stay method-shaped; methods do **not** grow a `guild_id` param. Each
  `Sql*Repository.__init__` takes `guild_id`; every query adds `.where(ORM.guild_id == self._guild_id)`.
- Add explicit cross-guild enumeration for tasks: `list_guild_ids()` (or an
  `IGuildRegistryRepo`), named so it can't be confused with scoped reads.
- The JSON migrator takes `--guild-id` (legacy single-guild data → exactly one tenant).
- **Mandatory new gate:** `test_repo_scoping.py` — a repo bound to guild A never reads or
  writes guild B's rows (incl. `list_all`, `append_history`, `prune_*`). Consider a
  `_scoped_select()` helper in `base_repo` that always injects the filter.

### Phase 7 — Concurrency / LockManager: **small re-spec**
- `locked(*keys)` keys on `(guild_id, user_id)` tuples. Deadlock-prevention sort still
  works on the composite key. Test: `(A, u1)` and `(B, u1)` do not block each other.

### Phases 8a–8f — Services: **minimal re-spec**
- Service constructors keep taking repo interfaces — now guild-bound instances from the
  scope. Method signatures are unchanged for the common case (cross-target trades are
  same-guild by definition).
- Task-invoked methods (`activity_price_tick`, `inactivity_decay_tick`,
  `check_and_liquidate_shorts`, `accrue_apy`, `reset_today_buckets`, `reset_week_buckets`)
  operate within the service's already-scoped repos; the **task** owns the per-guild
  fan-out (Phase 9), keeping services simple. The in-memory `fake_repos.py` must be
  guild-scoped so tests catch leaks.

### Phase 9 — Background tasks: **re-spec to iterate per guild**
- Each "process all users" task becomes "for each guild in `bot.guilds`, get its scope,
  process its users." Sourcing from `bot.guilds` means a guild that removed the bot stops
  being processed.
- `LiquidationEvent` (an adapter-facing DTO, not a domain aggregate) gains `guild_id` so
  the notification callback resolves the right guild's channel.
- `daily_reset_task` / `weekly_reset_task` read/write per-guild `system_state`.
- Wrap each per-guild iteration in the existing `_safe_run` boundary so one guild's failure
  doesn't abort the others. Test multi-guild fan-out + per-guild failure isolation.

### Phases 10–12 — Embeds, cogs, listeners: **adapter-boundary extraction + DM rejection**
- **Cogs (11):** each callback does `if interaction.guild_id is None: <ephemeral guild-only
  reply>; return` *before* any service call, then
  `scope = container.for_guild(str(interaction.guild_id))`. Add `@app_commands.guild_only()`
  as defense-in-depth.
- **Listeners (12):** `on_message`, `on_voice_state_update`, `on_reaction_add`,
  `on_member_update`/`on_member_ban` skip events with no guild. The `on_message` DM guard is
  mandatory (DMs must never create an account).
- **Embeds (10):** pure functions, no change.

### Phase 13 — Container: **the core mechanism**
- `Container.for_guild(guild_id) -> GuildScope` holding the guild-bound repos, the shared
  `LockManager`, `Settings`, and guild-scoped services. Cache scopes in a dict keyed by
  `guild_id`, built lazily. Test: `for_guild("A")` and `for_guild("B")` return distinct,
  guild-bound, cached scopes.

### Phase 14 — Bot factory: **global sync** (see §4).

## 4. Command-sync change

Current spec (`04-migration-plan.md` lines ~760–766): `copy_global_to(guild=...)` +
`sync(guild=settings.guild_id)`.

New `setup_hook`:
- Always `await bot.tree.sync()` (global — registers commands for every current and future
  guild).
- If `dev_guild_id` is set, additionally `copy_global_to(discord.Object(dev_guild_id))` +
  `sync(guild=discord.Object(dev_guild_id))` for instant dev iteration (global propagation
  can take ~1 hour).
- Tests: assert global sync is invoked; with `dev_guild_id` set, assert the extra guild
  sync; with it `None`, assert global only.

## 5. DM / no-guild edge case (blocking gates)

A DM must never mutate any guild's economy. Dedicated, **blocking** tests:
- **Cog-level:** ≥1 command per cog with `interaction.guild_id is None` → no service call,
  ephemeral guild-only reply, no repo write.
- **Listener-level:** DM `on_message` (`message.guild is None`) → no service, no account.
  Reaction/voice/member listeners no-op without a guild.
- **Lock-level:** same-user-different-guild independence (§3, Phase 7).
- **Repo-level:** cross-guild isolation (§3, Phase 6).

## 6. Documentation updates

- **ADR-0001** — created (`docs/adr/0001-per-guild-markets.md`).
- **`02-target-architecture.md` decision #12** — annotated "Superseded by ADR-0001" with a
  one-line erratum on the false "single migration / no app changes" claim. *(Done in the
  ADR PR.)*
- **`02-target-architecture.md`** — re-spec the Persistence schema (composite keys),
  Concurrency Model (composite lock key), Discord Interface Layer (`guild_id` extraction +
  DM rejection + `for_guild`), and Background Tasks (per-guild iteration + per-guild
  `system_state`). *(Per implementing phase, or a focused docs PR.)*
- **`04-migration-plan.md`** — re-spec Phases 5/6/7/8a–8f/9/11/12/13/14; annotate Phase 4
  "no change"; add the pre-work PR rows; add the isolation + DM-rejection rows to the
  Verification Gate Matrix.
- **`CLAUDE.md` (project)** — once implemented, describe `(guild_id, user_id)` keying.
  (The project `CLAUDE.md` is also separately stale — it still describes a single-file
  `bot.py`; that destale is tracked on the in-flight `chore/destale-claude-md` branch.)

## 7. Branch / PR sequence

This is **not a new phase** — it re-shapes existing ones. Each PR carries `Refs #2`
(Phase 17 keeps `Closes #2`). Develop each in its own worktree
(`git worktree add .worktrees/<slug> -b <branch> main`).

1. **`docs/per-guild-markets`** (this PR) — ADR-0001, `docs/adr/` + README, this plan, and
   the decision-#12 superseded note. Docs-only, fast review, lands first.
2. **`feat/multi-guild-config`** — the only edit to merged code: `config.py`
   (`guild_id` → optional `dev_guild_id`), `.env.example`, config tests; optionally the
   `GuildOnly` domain error. Surgical, behavior-preserving (nothing consumes `dev_guild_id`
   until Phase 14).
3. **Existing phases in normal order**, each re-specced per §3–§4: Phase 4
   (`feat/phase-4-domain-funcs`, unchanged) → 5 → 6 → 7 → 8a–8f → 9 → 10 → 11 → 12 → 13 →
   14 → 15–17.

**Issue #2 threading:** add two checklist items ("ADR-0001 + multi-guild re-spec",
"multi-guild config change") via `gh issue edit` (allowed for #2 per commit `c4fcd9d`).

## 8. Risks and verification gates

Gates: `ruff` + `mypy --strict` + `pytest --cov-fail-under` (95% domain, 85%/80% elsewhere).

| Severity | Risk | Mitigation |
|----------|------|------------|
| CRITICAL | Repo query missing its `guild_id` filter → cross-guild leak | One enforcement point per repo (Approach C); blocking `test_repo_scoping.py`; `_scoped_select()` helper |
| CRITICAL | DM event creates/mutates state | Blocking DM-rejection gates (cogs + listeners); `@app_commands.guild_only()` |
| HIGH | Lock-key collision across guilds | Composite `(guild_id, user_id)` key + independence test |
| HIGH | Task fan-out failure not isolated per guild | `_safe_run` per guild iteration + failure-isolation test |
| MEDIUM | Global command propagation delay (~1h) | `dev_guild_id` fast sync; document in runbook (Phase 16) |
| MEDIUM | Global vs per-guild reset flags skip a new guild | Per-guild `system_state`; `freezegun` boundary test |
| MEDIUM | JSON migrator tenancy ambiguity | Required `--guild-id` arg; test asserts all rows carry it |

**Per-phase additive gates:** P5 same-user-different-guild round-trip · P6 cross-guild
isolation (blocking) · P7 lock independence · P9 multi-guild fan-out + failure isolation ·
P11/P12 DM rejection (blocking) · P13 `for_guild` caching/binding · P14 global sync.

## 9. Success criteria

- [ ] ADR-0001 created; decision #12 marked superseded with corrected blast-radius note.
- [ ] `config.py`: `guild_id` → optional `dev_guild_id`; no required guild field; Phase 2 gate green.
- [ ] `domain/models.py` unchanged; 95% coverage intact.
- [ ] ORM tables carry `guild_id` with composite PKs; same `user_id` coexists across two guilds (proven).
- [ ] Repos guild-bound; cross-guild isolation test green and blocking.
- [ ] `LockManager` keyed by `(guild_id, user_id)`; same-user-different-guild independence proven.
- [ ] Tasks iterate per guild with per-guild failure isolation and per-guild `system_state`.
- [ ] Cogs and listeners reject DMs with no state mutation — blocking tests green.
- [ ] `setup_hook` global `bot.tree.sync()`; optional dev-guild fast sync.
- [ ] `container.for_guild(guild_id)` returns cached, guild-bound scopes.
- [ ] All phase gates pass; PRs reference `Refs #2` (Phase 17 `Closes #2`).
