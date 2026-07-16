# Simulation Suite

Config-driven simulation of a Discord server: YAML files under `scenarios/`
define a fake guild, its members, and a timestamped timeline of actions;
the harness executes each timeline against a **real `Container` over
in-memory SQLite** under a **freezegun master clock** and asserts the
expectations declared on each action.

## Not true end-to-end

No bot logs in, no gateway connects, no wall clock ticks, no file database is
touched. Everything **below the Discord adapter boundary is the real
production code** — the same `Container`, cogs, listeners, background tasks,
application services, domain functions, repositories, and the real
`tree.on_error` handler. The simulation replaces exactly three things at the
system's edges:

| Edge | Double | Where |
|---|---|---|
| Discord gateway | hand-rolled `MagicMock`/`AsyncMock` stubs | `harness/stubs.py` |
| Wall clock | freezegun master clock (services read `datetime.now(UTC)` directly — there is no clock port) | `harness/runner.py` |
| Database | fresh in-memory SQLite engine per test | `conftest.py` |

So this is a **full-stack integration simulation**, not a browser/gateway
E2E test. Commands are invoked by calling the cog callback directly
(`AccountCog.balance.callback(...)`), not dispatched through discord.py's
command tree — real E2E could not reach past the gateway to do that.

## Running the suite

```bash
uv run pytest tests/simulation/                 # every scenario + harness unit tests
uv run pytest tests/simulation/ -k trading-happy # one scenario (id == file stem)
uv run pytest tests/simulation/test_coverage_matrix.py  # completeness guard only
```

`asyncio_mode = "auto"` (pyproject) means scenarios need no `@pytest.mark.asyncio`.

## Scenarios

Each file under `scenarios/` is one parametrized `test_scenario` case.

| Scenario | Covers |
|---|---|
| `trading-happy` | Full happy-path trade lifecycle (buy/sell/short/cover) + read commands |
| `trading-errors` | Exhaustive trading error paths and market-hours edges |
| `funds` | Hedge-fund lifecycle plus all fund error paths |
| `activity-listeners` | Listener events (message/reaction/voice) accumulating activity that moves prices |
| `discipline-edge` | Moderation penalties, admin commands, and handler edge branches |
| `liquidation` | Underwater short is auto-liquidated at 1.5× entry |
| `time-lifecycle` | Streaks, resets, APY accrual, and inactivity decay over a week |
| `day-in-the-life` | A realistic multi-user day across all feature areas |

## Executive summary

- One scenario file = one pytest case (`test_simulation.py` parametrizes over
  `scenarios/*.yml`).
- Actions come in three kinds: `command` (slash commands, invoked through the
  cog callbacks with errors routed through the production error handler),
  `event` (gateway listeners: messages, reactions, voice, moderation), and
  `task` (background-task single ticks).
- Expectations are declarative: expected error class, reply visibility +
  content, per-user DB state (cash, net worth, positions, streaks, activity
  buckets), stock prices, fund state, and liquidation-event counts.
- `test_coverage_matrix.py` guards completeness: every command (20), event
  (10), task (8), and reachable error path (16) must appear in at least one
  scenario — the matrix is parsed from the same YAML the runner executes, so it
  can never drift from what actually runs.
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
    seed: false                         # no account until first touch (cold-start path)
    cash: "100000.00"
    price: "100.00"                     # seed a stock at this price
    fund_balance: "500.00"              # seed a personal fund
    manage_guild: true                  # passes the /game_intro gate
    dms_blocked: true                   # optin intro DM raises Forbidden
timeline:
  - label: human-readable name shown in failures
    at: "+5m"                           # relative to previous action, or absolute ISO
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
          net_worth: {ge: "10000"}
          long: {bob: 2}
          short: {bob: 0}
          short_frozen: {bob: true}
          streak: 1
          opt_in: true
          today: {text_msgs: 3, voice_minutes: {ge: 29, le: 31}}
          week: {reaction_count: 2}     # today/week share the same bucket fields
        bob: {exists: false}            # assert an account was never created
      price:
        bob: {gt: "100"}                # ops: eq ne gt ge lt le approx
      fund:
        bob: {exists: true, name: X, balance: "500.00", investors: {alice: "250.00"}}
        events_wallet: {balance: "50.00"}   # the per-guild treasury pseudo-fund
      liquidations: 1                   # new LiquidationEvents from this action
```

### Matchers

Every value slot accepts a scalar (exact equality; money as `Decimal`) or a
mapping of operators: `eq ne gt ge lt le` and `approx`. `approx` compares
within a ratio of the expected value — `{approx: "1150", tol: "0.05"}` passes
when `|actual − 1150| ≤ 1150 × 0.05`; `tol` defaults to `0.01` (1%).

### State fields

`cash`, `net_worth`, `streak`, `opt_in`, `long: {target: shares}`,
`short: {target: shares}`, `short_frozen: {target: bool}`, and the activity
buckets `today` / `week`. `exists: false` asserts no account row (and forbids
other fields); `exists: true` is implied by any field check.

Activity-bucket fields (for `today` and `week`): `text_msgs`, `media_msgs`,
`voice_minutes`, `reaction_count`, `reply_count`, `role_ping_joins`,
`role_ping_join_minutes`.

### Reply / error semantics

`reply.ephemeral` checks the **final** reply's visibility; `reply.contains`
matches across all replies concatenated (embed titles, descriptions, and field
text included). An `expect.error` additionally asserts the central error
handler *rendered a reply* — the whole point of routing errors through it.

### Events and tasks

Events: `message` (`author`, `attachment`, `reply`, `channel`,
`voice_channel`, `mention_role`, `role_members`, `author_bot`), `reaction`
(`reactor`, `message_author`, `message_author_bot`, `channel`),
`voice_join`/`voice_leave` (`user`, `channel`), `voice_switch`
(`user`, `from`, `to`), `member_timeout`/`member_ban` (`target`),
`guild_remove`, and the synthetic `raise_unexpected` / `raise_persistence`
(exercise the error handler's two non-domain branches).

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
