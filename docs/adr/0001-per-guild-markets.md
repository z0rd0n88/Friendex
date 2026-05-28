# ADR-0001: Per-Guild Market Isolation (Multi-Tenancy)

- **Status:** Accepted
- **Date:** 2026-05-22
- **Deciders:** Project owner
- **Supersedes:** Decision #12 ("Multi-guild isolation: in scope?") in [`docs/02-target-architecture.md`](../02-target-architecture.md)
- **Implementation plan:** [`docs/06-per-guild-markets-migration.md`](../06-per-guild-markets-migration.md)

## Executive summary

Friendex will be **multi-tenant**: every Discord server that installs the bot gets its
own independent economy, keyed by `(guild_id, user_id)`. A member's stock price, cash,
portfolio, positions, hedge funds, and activity are scoped to a single guild. This
**reverses** the prior decision (#12) to ship single-guild only. Slash commands sync
**globally** instead of to one home guild. The `guild_id` dimension lives at the ORM
layer and is captured **once** at the Discord adapter boundary via a container scope
(`container.for_guild(guild_id)`); the pure domain layer stays guild-agnostic and the
**already-merged `domain/models.py` is unchanged**.

## Context and problem statement

The migration is a greenfield hex-arch rebuild (`src/friendex/{domain,application,adapters}`)
driven by the 18-phase plan in [`docs/04-migration-plan.md`](../04-migration-plan.md).
Phases 1–3 (config, domain models, errors) are merged.

Decision #12 deliberately scoped the rebuild to **one guild**: `Settings.guild_id: int`
is required, slash commands sync to that guild, and all data is keyed by `user_id` alone.
The product requirement is the opposite — **the bot must run in any server that installs
it**, with each server's market independent of the others. If the bot served many guilds
under the current design, every server would share one global pool of stocks and prices:
trading "Alice" in server A would move her price in server B. That is incorrect for the
intended product.

Decision #12 also asserted that adding multi-guild later would be *"a single Alembic
migration … the application layer requires no changes."* **That claim is wrong** for a
one-bot-many-guilds deployment. It holds only for one-bot-**per**-guild (where `guild_id`
is a process-global constant from `Settings`). With one bot process serving many guilds,
`guild_id` is a real runtime dimension that must reach the persistence layer (composite
keys), the repositories, the services, the Discord adapter (which guild fired this event?),
and the background tasks (which now iterate per guild). This ADR records the true blast
radius so the work is sized honestly.

## Decision drivers

- **Correctness of tenant isolation** — one server's economy must never read or mutate another's.
- **Minimise churn to merged, covered code** — Phases 1–3 are merged; `domain/models.py` carries a 95% coverage gate.
- **Keep the domain layer pure** — guild scoping is a storage/routing concern, not game logic.
- **Honest sizing** — the plan must reflect every layer the `guild_id` dimension touches.
- **Developer iteration speed** — global command propagation can take up to ~1 hour; dev needs a fast path.

## Considered options

### Tenancy model (the product decision)

1. **Per-guild markets (CHOSEN).** Each server is an isolated economy; key `(guild_id, user_id)`; global command sync.
2. **One shared global market.** A user has one stock everywhere; only the command sync changes. Rejected: cross-server price coupling is incoherent for the game.
3. **Stay single-guild.** No code change, docs-only fix. Rejected: contradicts the "installs anywhere" requirement.

### Where `guild_id` lives (the implementation decision)

- **(A) `guild_id` field on every aggregate root.** Self-describing entities, easy composite PK — but pollutes the pure domain layer, forces a `guild_id` arg onto ~12 dataclasses and all their merged tests, and risks entity/repo-key mismatch. Rejected.
- **(B) `guild_id` parameter on every repository method, threaded through services.** Clean domain, explicit dimension — but maximum signature churn across dozens of not-yet-built repo/service methods; one missed forward = a cross-guild data leak. Rejected.
- **(C) Per-guild-scoped repositories via the DI container (CHOSEN).** `container.for_guild(guild_id)` returns a scope of guild-bound repos; `guild_id` is injected once per repo instance and captured once at the adapter boundary. Domain models and most service signatures are unchanged. Adds one scoping mechanism and a `(guild_id, user_id)` lock key.

## Decision outcome

**Per-guild markets, implemented with Approach C.**

- The **ORM layer** carries `guild_id` on every per-guild table, with `guild_id`-first composite primary keys. Domain dataclasses do **not** carry `guild_id` — it is implied by the scope you fetched from. `to_domain()` drops it; `from_domain(guild_id, obj)` re-attaches it.
- The **DI container** gains `for_guild(guild_id) -> GuildScope`, lazily built and cached (guilds are not known at startup).
- **Repositories** are constructed bound to one `guild_id`; every query filters on it at that single enforcement point.
- The **`LockManager`** keys on `(guild_id, user_id)` so the same user in two guilds does not serialise against themselves.
- **Discord adapters** extract `interaction.guild_id` (cogs) / `message.guild` (listeners) and **reject DMs** (`guild_id is None`) with no state mutation.
- **Background tasks** iterate per guild (driven by `bot.guilds`), with per-guild `system_state` reset flags and per-guild failure isolation.
- **Command sync** is global (`bot.tree.sync()`); `Settings.guild_id` is **demoted to an optional `dev_guild_id`** used only for instant dev-time sync.
- **Phase 4 (domain pure functions) is unchanged** — those functions take primitives/value objects and stay guild-agnostic.

## Consequences

**Positive**
- Correct, isolated per-server economies; the bot installs anywhere.
- Merged `domain/models.py` and its 95% coverage are untouched.
- A single `guild_id` enforcement point per repo (smallest leak surface).
- The pure domain layer stays pure.

**Negative / costs**
- One new scoping mechanism (`for_guild` + scope cache) and a composite lock key.
- Two already-merged-phase changes: `config.py` (`guild_id` → optional `dev_guild_id`) and downstream re-spec of Phases 5–14.
- Two new **blocking** test gates: cross-guild repo isolation, and DM rejection.
- Global command propagation latency in production (mitigated by `dev_guild_id` for development).

**Risks** (full register in the implementation plan)
- *CRITICAL* — a repo query missing its `guild_id` filter leaks one server's economy into another.
- *CRITICAL* — a DM event creating/mutating guild state.
- *HIGH* — lock-key collision across guilds; task fan-out failure isolation.

## Confirmation

The decision is satisfied when: ORM tables carry `guild_id` with composite PKs and the
same `user_id` provably coexists across two guilds; the cross-guild repo-isolation test
and the DM-rejection tests are green and block merge; `setup_hook` performs a global sync;
and all existing phase gates (`ruff`, `mypy --strict`, `pytest` coverage thresholds) pass.

## Implementation note (post-build)

The shipped implementation uses **per-call factory closures** rather than the planned
`container.for_guild(guild_id) → GuildScope` object. Each cog / listener holds a
`Callable[[str], XxxService]` injected at construction time; when a slash command or
event fires, the adapter calls `self._xxx_factory(guild_id)`, which constructs a
`FundService` (or equivalent) bound to that guild's repositories.

The practical difference is minimal:
- There is no `GuildScope` dataclass and no `for_guild` cache — guild binding is done
  per-call by the factory closure in `container.py`.
- All isolation invariants from the Decision Outcome section still hold: every
  repository is bound to exactly one `guild_id` at construction; the `LockManager`
  still keys on `(guild_id, user_id)`.
- The `container.py` wires 10 factory closures (`fund_service_factory`,
  `trading_service_factory`, etc.) instead of one scope object.

A `GuildScope` object would reduce ceremony slightly but would not add leverage —
the per-guild isolation is enforced at the ORM / repo layer, not at the scope boundary.
No migration is needed; this note records the deviation so future reviewers do not
conflate "the ADR specified GuildScope" with "the code should have GuildScope."

## Links

- Supersedes decision #12 in [`docs/02-target-architecture.md`](../02-target-architecture.md)
- Implementation plan: [`docs/06-per-guild-markets-migration.md`](../06-per-guild-markets-migration.md)
- Phase roadmap: [`docs/04-migration-plan.md`](../04-migration-plan.md) · Tracking: GitHub issue #2
