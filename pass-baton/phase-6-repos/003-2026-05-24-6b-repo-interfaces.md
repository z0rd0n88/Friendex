# Pass-Baton: Sub-unit 6b — repository Protocol interfaces

**Date:** 2026-05-24
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** 8394a0d chore(phase-6): 6a review CLEAN + phase-exit digest

## Where things stand

Sub-unit 6b is COMPLETE. `src/friendex/application/interfaces.py` now defines
the six repository `typing.Protocol` ports that the SqlXxxRepository adapters
(sub-units 6c–6f) implement against. Gate is green:
`scripts/gate.sh baton-runner/br-2026-05-24-phase-6/selfcheck-6b/` → **GATE:
PASS** (pytest 289 passed, ruff check, ruff format, mypy all pass). No new
dependencies. Changes are not yet committed — the manager owns git.

## What was built (the contract 6c–6f must satisfy)

All methods are `async`, all type-hinted with **domain models** (never ORM).
Every per-guild method takes `guild_id` explicitly (ADR-0001; domain models
are guild-agnostic). Common CRUD on every Protocol: `get`, `upsert`, `delete`,
`list_all` — plus model-specific methods below.

- **IUserRepo** — `get(guild_id, user_id) -> UserAccount | None`,
  `upsert(guild_id, account)`, `delete(guild_id, user_id)`,
  `list_all(guild_id) -> list[UserAccount]`,
  `list_active_in_last(guild_id, seconds: float) -> list[UserAccount]`.
- **IPriceRepo** — CRUD over `Stock` +
  `append_history(guild_id, user_id, point: PricePoint)`,
  `get_history(guild_id, user_id, *, since: datetime | None = None) -> list[PricePoint]`
  (oldest-first; `since` window drives dynamic 24h high/low per §Open-Q9),
  `prune_history_older_than(cutoff: datetime) -> int` (cross-guild
  `DELETE WHERE recorded_at < cutoff`, returns rows removed).
- **IFundRepo** — CRUD over `HedgeFund` +
  `ensure_events_wallet(guild_id) -> HedgeFund` (idempotent; fund_id
  `"events_wallet"`).
- **IPenaltyRepo** — CRUD over `FundPenalty` only.
- **ITradeCooldownRepo** — `get(guild_id, user_id) -> TradeCooldown | None`
  (**excludes expired rows**), `upsert(cooldown: TradeCooldown)` (scope carried
  in DTO), `delete(guild_id, user_id)`, `list_all(guild_id)` (includes expired),
  `purge_expired(now: datetime) -> int` (the TTL sweep).
- **ISystemStateRepo** — `get(guild_id) -> SystemState | None`,
  `upsert(state: SystemState)`, `delete(guild_id)`, `list_all() -> list[SystemState]`
  (**unscoped** — reset tasks iterate every guild).

## Decisions 6c–6f MUST honor

- **Two new app-layer DTOs live in `interfaces.py`:** `SystemState` and
  `TradeCooldown` (both `@dataclass(frozen=True)`). `SystemStateORM` and
  `TradeCooldownORM` have NO domain mirror, and `interfaces.py` cannot import
  the ORM (architecture invariant), so the typed payloads live here. The Sql
  repos map ORM⇆these DTOs (mirror the existing ORM `to_domain`/`from_domain`
  pattern). Import them from `friendex.application.interfaces`, do not
  re-declare.
- **`upsert` signatures differ by scope source.** User/Price/Fund/Penalty:
  `upsert(guild_id, <domain_obj>)` — guild is a separate arg. Cooldown/SystemState:
  `upsert(<dto>)` — guild_id is a field on the frozen DTO. This is intentional
  (the DTOs already carry their own scope); match it exactly or mypy in 6c–6f
  will reject the impl.
- **Protocols are structural** — Sql repos do NOT subclass these; conformance
  is by shape, verified by mypy (negative check confirmed: a non-matching impl
  IS rejected). Do not add `class SqlUserRepository(IUserRepo)` inheritance.
- **`delete` does no child cleanup** — DB-level CASCADE (ADR-0002, digest 6a).
- **Architecture invariant:** `interfaces.py` imports only `friendex.domain` +
  stdlib/typing. Domain-model + `datetime` imports sit under `TYPE_CHECKING`
  (annotation-only use; `from __future__ import annotations` makes that safe and
  keeps ruff TCH happy). Do not break this when extending.

## Verification (RED→GREEN recorded)

- RED: `tests/application/test_interfaces.py` import → `ModuleNotFoundError:
  No module named 'friendex.application.interfaces'` (captured before the file
  existed).
- GREEN: 18 conformance tests pass — per-Protocol CRUD presence, named
  model-specific methods present, AST-based no-adapters-import check, and a
  typed in-memory fake per Protocol anchoring the signatures. mypy is the real
  signature gate and rejects a deliberately non-conforming fake.

## Next steps

1. Sub-unit 6c+: implement `SqlUserRepository` etc. in
   `src/friendex/adapters/persistence/{user,price,fund,penalty,cooldown,system_state}_repo.py`
   against these Protocols (no inheritance; mypy verifies conformance).
2. Map ORM⇆`SystemState`/`TradeCooldown` DTOs inside cooldown_repo /
   system_state_repo (no domain mirror exists for these two tables).
3. Re-export repo classes from `src/friendex/adapters/persistence/__init__.py`
   (per plan §Files modified) once the Sql repos land.

## Open questions / risks

- None blocking. The plan also carries two non-blocking Phase-5-review
  carry-forwards (Decimal-quantisation ORM assertions + first real migration
  drift test) — those land with the Sql repo / migrator sub-units, not here.

## References

- Plan: `docs/04-migration-plan.md` §"Phase 6 — Persistence" (lines 345–388)
- ADRs: `docs/adr/0001-per-guild-markets.md`, `docs/adr/0002-sqlite-fk-enforcement.md`
- Prior: `baton-runner/br-2026-05-24-phase-6/digest-phase-6a.md`;
  `pass-baton/phase-6-repos/002-2026-05-24-6a-fk-migration-review.md`
- Code: `src/friendex/application/interfaces.py`;
  `tests/application/test_interfaces.py`
- Models: `src/friendex/domain/models.py`; ORM: `src/friendex/adapters/persistence/orm.py`
- Gate log: `baton-runner/br-2026-05-24-phase-6/selfcheck-6b/`
- Issue: #2 (live phase status)
