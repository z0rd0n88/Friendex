# End-to-End Simulation Suite

Config-driven simulation of a Discord server: YAML files under `scenarios/`
define a fake guild, its members, and a timestamped timeline of actions;
the harness executes each timeline against a **real `Container` over
in-memory SQLite** under a **freezegun master clock** and asserts the
expectations declared on each action.

## Executive summary

- One scenario file = one pytest case (`test_simulation.py` parametrizes over
  `scenarios/*.yml`).
- Actions come in three kinds: `command` (slash commands, invoked through the
  cog callbacks with errors routed through the production error handler),
  `event` (gateway listeners: messages, reactions, voice, moderation), and
  `task` (background-task single ticks).
- Expectations are declarative: expected error class, reply visibility +
  content, per-user DB state (cash, positions, streaks, activity buckets),
  stock prices, fund state, and liquidation-event counts.
- `test_coverage_matrix.py` guards completeness: every command (20), event,
  task (8), and reachable error path must appear in at least one scenario.
- Failure policy is hybrid: value mismatches are collected across the whole
  timeline; an unexpected exception fails fast.

## Scenario format

```yaml
name: my-scenario
description: What this covers.
start_at: "2026-05-25 12:00:00"        # UTC; Monday inside market hours
guild: {id: 900100, name: Sim Server}
settings:                               # optional Settings overrides
  price_impact_k: 50.0
users:
  alice: {id: 1111}                     # seeded, opted-in, $10k by default
  bob:                                  # every field optional except id
    id: 2222
    opted_in: false                     # for OptedOut error paths
    seed: false                         # no account until first touch
    cash: "100000.00"
    price: "100.00"                     # seed a stock at this price
    fund_balance: "500.00"              # seed a personal fund
    manage_guild: true                  # passes the /game_intro gate
    dms_blocked: true                   # optin intro DM raises Forbidden
timeline:
  - label: human-readable name shown in failures
    at: "+5m"                           # relative to previous action, or absolute
    command: buy                        # exactly one of command / event / task
    actor: alice
    args: {user: bob, shares: 2}
    repeat: 3                           # optional; expectation checked after last
    expect:
      error: OptedOut                   # DomainError class name / CheckFailure /
                                        # ValueError / PersistenceError
      reply: {ephemeral: true, contains: ["opted out"]}
      state:
        alice:
          cash: "10300.00"              # scalars compare exactly (Decimal-safe)
          long: {bob: 2}
          short: {bob: 0}
          short_frozen: {bob: true}
          streak: 1
          opt_in: true
          today: {text_msgs: 3, voice_minutes: {ge: 29, le: 31}}
      price:
        bob: {gt: "100"}                # ops: eq ne gt ge lt le approx+tol
      fund:
        bob: {exists: true, name: X, balance: "500.00", investors: {alice: "250.00"}}
        events_wallet: {balance: "50.00"}
      liquidations: 1                   # new LiquidationEvents from this action
```

Events: `message` (`author`, `attachment`, `reply`, `channel`, `mention_role`,
`role_members`, `author_bot`), `reaction` (`reactor`, `message_author`,
`message_author_bot`), `voice_join`/`voice_leave` (`user`, `channel`),
`voice_switch` (`user`, `from`, `to`), `member_timeout`/`member_ban`
(`target`), `guild_remove`, and the synthetic `raise_unexpected` /
`raise_persistence` (exercise the error handler's two non-domain branches).

Tasks: `activity_tick`, `liquidation`, `freeze_check`, `inactivity_decay`,
`vc_boost`, `daily_reset`, `weekly_reset`, `monthly_rollover`.

## Deliberate non-goals

- `NoPosition("long")`, `AlreadyOptedIn`, `AlreadyOptedOut`, `DiscordError`
  are defined in the error taxonomy but unreachable from any user action —
  no scenario asserts them (see `test_coverage_matrix.py`).
- Discord's own input validation (`Range[int, 1, 1_000_000]`) runs at the
  gateway; the harness bypasses it by design, which lets scenarios prove the
  service-level defence-in-depth checks behind it.
- `/game_intro`'s `manage_guild` check is emulated at the dispatch layer
  (discord.py evaluates permission checks before the callback; direct
  callback invocation would skip them silently).

## Harness layout

| Module | Role |
|---|---|
| `harness/schema.py` | YAML → validated frozen dataclasses (strict, fail-fast) |
| `harness/stubs.py` | Hand-rolled discord.py stubs (same idiom as the cog/listener conftests) |
| `harness/world.py` | Container + engine + seeded accounts/stocks/funds + captured error handler |
| `harness/actions.py` | Executors: command dispatch, event injection, task ticks |
| `harness/expect.py` | Declarative assertions + matcher semantics |
| `harness/runner.py` | freezegun clock, timeline loop, hybrid failure policy |
