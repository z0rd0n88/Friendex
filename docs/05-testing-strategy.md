# StockXchange — Testing Strategy

## Executive Summary

StockXchange's test suite is organized around the three-layer architecture defined in `docs/02-target-architecture.md`: a large base of synchronous unit tests covers every pure function in the domain layer with no mocking required; a mid-tier of async service tests exercises application use-cases against in-memory fake repositories; and a thin top layer of Discord integration and end-to-end smoke tests validates the adapter boundary using `dpytest`. The smallest testable unit — a single pure function operating on typed domain objects — is the primary test vehicle. All external dependencies (Discord API, database, clock) are isolated from every test that does not explicitly declare itself an integration or end-to-end test. The coverage targets are 95%+ on `domain/`, 90%+ on `application/`, and 80% overall; the CI gate fails the build on any regression below 80%.

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Testing Principles](#testing-principles)
3. [Toolchain](#toolchain)
4. [Test Directory Layout](#test-directory-layout)
5. [Domain Layer Tests — Smallest Units](#domain-layer-tests--smallest-units)
   - [price_engine.py](#price_enginepy)
   - [activity.py](#activitypy)
   - [fund_math.py](#fund_mathpy)
   - [market_hours.py](#market_hourspy)
   - [models.py](#modelspy)
6. [Application Layer Tests — With Fake Repos](#application-layer-tests--with-fake-repos)
   - [trading_service.py](#trading_servicepy)
   - [activity_service.py](#activity_servicepy)
   - [fund_service.py](#fund_servicepy)
   - [daily_service.py](#daily_servicepy)
   - [portfolio_service.py](#portfolio_servicepy)
   - [liquidation_service.py](#liquidation_servicepy)
   - [discipline_service.py](#discipline_servicepy)
   - [price_tick_service.py](#price_tick_servicepy)
   - [lock_manager.py](#lock_managerpy)
7. [Persistence Layer Integration Tests](#persistence-layer-integration-tests)
8. [Discord Layer Tests](#discord-layer-tests)
   - [Embed builder tests](#embed-builder-tests)
   - [Cog tests](#cog-tests)
   - [Listener tests](#listener-tests)
9. [Background Task Tests](#background-task-tests)
10. [End-to-End / Smoke Tests](#end-to-end--smoke-tests)
11. [Test Data and Fixtures](#test-data-and-fixtures)
12. [TDD Workflow Per Feature](#tdd-workflow-per-feature)
13. [CI Integration](#ci-integration)
14. [Anti-Patterns to Avoid](#anti-patterns-to-avoid)

---

## Testing Principles

**A unit is the smallest piece of code that can be independently tested.** For this codebase that means a single pure function such as `apply_floor_stall`, `calculate_trending_score`, or `compute_apy_accrual` — not a service, not a cog, not a command handler. Write one test per behaviour of that function, not one test per function.

**The test pyramid applies strictly to layer boundaries.**

```
          /\
         /  \   E2E / smoke tests (few, slow)
        /    \  — dpytest + real aiosqlite DB
       /------\
      /        \ Integration tests (moderate)
     /          \ — async services + real aiosqlite in-memory DB
    /            \ — repo round-trips
   /--------------\
  /                \ Unit tests (many, fast)
 /                  \ — pure domain functions
/____________________\ — fake-repo application services
```

**One assertion per test where reasonable.** When a function returns a composite object, one assertion per field is preferred over a single equality check on the full object. This produces error messages that name the failing field immediately.

**AAA structure in every test.** Every test body follows Arrange / Act / Assert. Comment blocks (`# Arrange`, `# Act`, `# Assert`) are required for any test longer than six lines; for trivial one-liner tests they are implied.

**Fixtures over setUp/tearDown.** Shared state is provided via `pytest` fixtures with explicit scope. No test class inherits from `TestCase`. No test function calls helper functions on a shared class attribute.

**Deterministic tests: no real clock, no real Discord, no real database unless declared.** Any test not in `tests/adapters/persistence/` or `tests/e2e/` must not touch the filesystem, the network, the Discord API, or wall-clock time. Use `freezegun` for any code path that reads `datetime.utcnow()` or `datetime.now()`. Use fake repo implementations for any code path that touches a repository interface.

---

## Toolchain

### Dependencies

```toml
[project.optional-dependencies]
test = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "freezegun>=1.4",
    "dpytest>=0.7",
    "aiosqlite>=0.20",
]
```

### pytest configuration

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
markers = [
    "integration: marks tests that use a real database (deselect with -m 'not integration')",
    "e2e: marks end-to-end tests that start a real bot (deselect with -m 'not e2e')",
]
```

### Coverage configuration

```toml
# pyproject.toml
[tool.coverage.run]
source = ["src/stockxchange"]
omit = ["tests/*", "src/stockxchange/adapters/persistence/migrate_json_to_sqlite.py"]
branch = true

[tool.coverage.report]
fail_under = 80
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
]

[tool.coverage.html]
directory = "htmlcov"
```

### Coverage targets by layer

| Layer | Target | Rationale |
|---|---|---|
| `domain/` | 95%+ | Pure functions; every branch is cheaply testable |
| `application/` | 90%+ | Use-cases; fake repos make full coverage straightforward |
| `adapters/persistence/` | 80%+ | Integration tests cover round-trips; migration script excluded |
| `adapters/discord_bot/` | 75%+ | Cog/listener logic is thin; embed builders are fully covered |
| `adapters/tasks/` | 75%+ | Task bodies are thin wrappers; service logic is covered elsewhere |
| Overall | 80%+ | CI gate |

---

## Test Directory Layout

```
tests/
├── conftest.py                          # Shared fixtures: settings, engine, session, repos, clock
│
├── domain/
│   ├── test_models.py                   # Invariant enforcement on all dataclasses
│   ├── test_price_engine.py             # apply_floor_stall, apply_trade_impact, inactivity_decay
│   ├── test_activity.py                 # calculate_trending_score, get_engagement_tier, reset_activity_bucket
│   ├── test_market_hours.py             # is_market_open boundary conditions, is_trading_day, is_sunday
│   └── test_fund_math.py               # APY accrual, penalty math, net_worth calculation
│
├── application/
│   ├── test_trading_service.py          # buy/sell/short/cover use-cases with fake repos
│   ├── test_activity_service.py         # record_message, record_reaction, record_voice_join/leave
│   ├── test_voice_ping_service.py       # handle_vc_ping_message, reward_voice_ping_response
│   ├── test_fund_service.py             # create_fund, withdraw, send_events, accrue_apy
│   ├── test_daily_service.py            # claim_daily, streak logic
│   ├── test_portfolio_service.py        # calculate_net_worth, portfolio_snapshot
│   ├── test_stats_service.py            # trending_snapshot, user_stats
│   ├── test_price_tick_service.py       # activity_price_tick, inactivity_decay_tick, vc_boost_tick
│   ├── test_liquidation_service.py      # check_and_liquidate_shorts
│   ├── test_discipline_service.py       # apply_discipline_penalty for timeout and ban
│   └── test_lock_manager.py            # Concurrency serialization under asyncio.gather
│
├── adapters/
│   ├── persistence/
│   │   ├── conftest.py                  # async_engine, async_session fixtures (aiosqlite in-memory)
│   │   ├── test_user_repo.py            # UserAccount CRUD round-trips, JSON migration
│   │   ├── test_price_repo.py           # Stock CRUD, history pruning, 24h query
│   │   ├── test_fund_repo.py            # HedgeFund CRUD, events wallet isolation
│   │   └── test_penalty_repo.py        # FundPenalty CRUD, expiry query
│   │
│   ├── discord_bot/
│   │   ├── test_embeds.py               # Pure embed builder functions — given data, assert fields
│   │   ├── test_trading_cog.py          # $buy, $sell, $short, $cover via dpytest
│   │   ├── test_portfolio_cog.py        # $portfolio command via dpytest
│   │   ├── test_fund_cog.py             # $fund subcommands via dpytest
│   │   ├── test_daily_cog.py            # $daily command via dpytest
│   │   ├── test_stats_cog.py            # $trending, $mystats, $price via dpytest
│   │   ├── test_account_cog.py          # $balance, $optin, $optout via dpytest
│   │   ├── test_message_listener.py     # on_message dispatches correct service calls
│   │   ├── test_voice_listener.py       # on_voice_state_update join/leave/switch dispatch
│   │   ├── test_reaction_listener.py    # on_reaction_add dispatches correct service call
│   │   └── test_member_listener.py     # on_member_update and on_member_ban dispatch
│   │
│   └── tasks/
│       ├── test_activity_tick_task.py   # ActivityTickTask invokes price_tick_service
│       ├── test_inactivity_decay_task.py # InactivityDecayTask invokes service
│       ├── test_liquidation_task.py     # LiquidationTask invokes liquidation_service
│       ├── test_freeze_check_task.py    # FreezeCheckTask invokes trading_service
│       ├── test_vc_boost_task.py        # VcBoostTask invokes price_tick_service
│       ├── test_daily_reset_task.py     # DailyResetTask fires once per calendar day (freezegun)
│       └── test_weekly_reset_task.py   # WeeklyResetTask fires on Monday only (freezegun)
│
└── e2e/
    └── test_smoke.py                    # Bot starts, $balance and $buy against mock guild
```

---

## Domain Layer Tests — Smallest Units

Domain tests are synchronous (`def test_...`, not `async def`). They import only from `src/stockxchange/domain/`. No mocks, no fixtures beyond simple factory calls.

### price_engine.py

**`apply_floor_stall(current_price, proposed_price, floor)`**

```
test_returns_proposed_price_when_above_floor
test_returns_floor_when_proposed_is_below_floor
test_returns_floor_when_proposed_equals_floor
test_returns_current_price_when_proposed_equals_current
test_floor_stall_does_not_drop_below_floor_on_zero_proposal
test_raises_value_error_on_negative_proposed_price
test_raises_value_error_on_negative_floor
test_floor_exactly_at_current_returns_current
test_large_proposed_price_passes_through_unchanged
```

**`apply_trade_impact(current_price, shares, is_buy, impact_k, floor)`**

```
test_buy_increases_price_proportional_to_shares
test_sell_decreases_price_proportional_to_shares
test_price_never_drops_below_floor_after_sell
test_buy_of_zero_shares_raises_value_error
test_sell_of_zero_shares_raises_value_error
test_buy_of_negative_shares_raises_value_error
test_large_buy_volume_produces_sublinear_impact_when_k_less_than_one
test_price_after_buy_then_sell_same_shares_is_below_original
test_impact_k_zero_returns_current_price_unchanged
```

**`compute_inactivity_decay(current_price, decay_rate, floor)`**

```
test_decays_price_by_exact_decay_rate_percentage
test_decay_does_not_drop_below_floor
test_decay_at_floor_returns_floor
test_decay_rate_of_zero_returns_current_price
test_decay_rate_of_one_returns_floor
test_raises_value_error_on_negative_decay_rate
test_raises_value_error_on_decay_rate_greater_than_one
```

**`compute_activity_return(week_activity, trending_score)`**

```
test_zero_trending_score_returns_zero_return
test_positive_score_returns_positive_return
test_return_is_bounded_above_by_configured_cap
test_return_scales_monotonically_with_score
test_negative_score_raises_value_error
```

### activity.py

**`calculate_trending_score(bucket)`**

```
test_empty_bucket_returns_zero_score
test_text_msgs_contribute_to_score
test_media_msgs_contribute_more_than_text_msgs_per_unit
test_voice_minutes_contribute_to_score
test_reply_count_contributes_to_score
test_reaction_count_contributes_to_score
test_role_ping_joins_contribute_to_score
test_soft_cap_limits_text_msg_contribution_beyond_threshold
test_soft_cap_limits_voice_minute_contribution_beyond_threshold
test_all_metrics_at_zero_returns_exactly_zero
test_score_is_non_negative_for_any_valid_bucket
test_single_media_msg_in_photo_channel_produces_expected_score
```

**`get_engagement_tier(score, all_scores)`**

```
test_top_10_percent_returns_elite_tier
test_bottom_50_percent_returns_lurker_tier
test_empty_all_scores_list_raises_value_error
test_score_not_in_all_scores_still_classifies_correctly
test_tied_scores_at_tier_boundary_return_same_tier
test_single_score_list_returns_defined_tier
```

**`reset_activity_bucket(bucket)`**

```
test_all_numeric_fields_reset_to_zero
test_voice_unique_channels_reset_to_empty_list
test_bucket_start_updated_to_provided_timestamp
test_reset_returns_new_object_not_mutated_original
test_reset_of_already_empty_bucket_returns_empty_bucket
```

### fund_math.py

**`compute_apy_accrual(balance, base_apy, period)`**

```
test_zero_balance_accrues_zero
test_positive_balance_accrues_positive_amount
test_accrual_for_monthly_period_equals_balance_times_monthly_rate
test_accrual_for_annual_period_equals_balance_times_annual_rate
test_negative_balance_raises_value_error
test_base_apy_of_zero_returns_zero_accrual
test_accrual_scales_linearly_with_balance
```

**`compute_effective_apy(base_apy, penalty_apr)`**

```
test_zero_penalty_returns_base_apy
test_penalty_reduces_effective_apy
test_penalty_greater_than_base_apy_returns_zero_not_negative
test_penalty_exactly_equal_to_base_apy_returns_zero
test_negative_penalty_raises_value_error
test_negative_base_apy_raises_value_error
```

**`compute_penalty_apr(existing_penalty, additional_penalty, now, penalty_duration_days)`**

```
test_new_penalty_sets_penalty_until_to_now_plus_duration
test_subsequent_penalty_resets_penalty_until_from_now
test_penalties_accumulate_additively
test_zero_additional_penalty_does_not_change_existing_penalty
test_negative_additional_penalty_raises_value_error
```

**`compute_net_worth(account, price_lookup)`**

```
test_no_positions_returns_cash_balance_only
test_long_position_adds_market_value_to_net_worth
test_short_position_adds_locked_collateral_to_net_worth
test_multiple_positions_all_included
test_price_lookup_returning_none_raises_value_error
test_zero_cash_with_one_position_returns_position_value
```

### market_hours.py

**`is_market_open(dt, market_open, market_close)`**

```
test_returns_true_at_exactly_market_open
test_returns_false_one_second_before_market_open
test_returns_false_at_exactly_market_close
test_returns_true_one_second_before_market_close
test_overnight_window_returns_true_at_midnight
test_overnight_window_returns_true_at_23_59
test_overnight_window_returns_false_between_close_and_open
test_same_open_and_close_time_is_always_closed
```

**`is_trading_day(dt)`**

```
test_monday_is_trading_day
test_tuesday_is_trading_day
test_wednesday_is_trading_day
test_thursday_is_trading_day
test_friday_is_trading_day
test_saturday_is_trading_day
test_sunday_is_not_trading_day
```

**`is_sunday(dt)`**

```
test_sunday_returns_true
test_monday_returns_false
test_saturday_returns_false
```

### models.py

**`UserAccount.__post_init__`**

```
test_negative_cash_balance_raises_assertion_error
test_zero_cash_balance_is_valid
test_construction_with_valid_fields_succeeds
```

**`LongPosition.__post_init__`**

```
test_zero_shares_raises_assertion_error
test_negative_shares_raises_assertion_error
test_zero_avg_entry_raises_assertion_error
test_negative_avg_entry_raises_assertion_error
test_valid_position_constructs_without_error
```

**`ShortPosition.__post_init__`**

```
test_zero_shares_raises_assertion_error
test_negative_entry_price_raises_assertion_error
test_negative_locked_cash_raises_assertion_error
test_negative_locked_fund_raises_assertion_error
test_valid_position_with_frozen_false_constructs
test_valid_position_with_frozen_true_constructs
```

**`HedgeFund.__post_init__`**

```
test_negative_cash_balance_raises_assertion_error
test_zero_cash_balance_is_valid
test_events_wallet_fund_id_is_valid
```

**`ActivityBucket.__post_init__`**

```
test_integer_channel_ids_are_coerced_to_str
test_string_channel_ids_remain_str
test_mixed_channel_ids_are_all_coerced_to_str
```

**`DailyProgress.__post_init__`**

```
test_negative_streak_raises_assertion_error
test_zero_streak_is_valid
test_none_last_claim_is_valid
```

---

## Application Layer Tests — With Fake Repos

Application tests are `async def`. They use `FakeUserRepo`, `FakePriceRepo`, `FakeFundRepo`, and `FakePenaltyRepo` — in-memory dict-backed implementations of the repository interfaces. These live in `tests/conftest.py` and are shared across all application test modules.

Use `FakeRepo` implementations (not `AsyncMock`) when the test verifies a multi-step read-then-write interaction (e.g., checking the state of the repo after a service call). Use `AsyncMock` only when the test verifies that a service calls a collaborator method with specific arguments and does not care about the resulting state.

### trading_service.py

```
test_buy_deducts_cost_from_buyer_cash
test_buy_creates_new_long_position_when_none_exists
test_buy_updates_weighted_avg_entry_on_existing_position
test_buy_increases_target_stock_price
test_buy_raises_insufficient_funds_when_cash_below_cost
test_buy_raises_market_closed_on_non_trading_day
test_buy_raises_self_trade_when_buyer_equals_target
test_buy_raises_opted_out_when_target_has_opt_in_false
test_buy_on_sunday_succeeds_when_sunday_buy_allowed_is_true
test_buy_on_sunday_raises_market_closed_when_sunday_buy_allowed_is_false

test_sell_adds_revenue_to_seller_cash
test_sell_removes_long_position_when_all_shares_sold
test_sell_decrements_shares_on_partial_sell
test_sell_lowers_target_stock_price
test_sell_raises_no_position_when_long_portfolio_empty
test_sell_raises_insufficient_shares_when_selling_more_than_held
test_sell_raises_market_closed_on_sunday

test_short_deducts_collateral_from_cash
test_short_uses_fund_balance_when_cash_insufficient_for_full_collateral
test_short_creates_short_position_with_correct_entry_price
test_short_lowers_target_stock_price
test_short_raises_insufficient_funds_when_combined_collateral_insufficient
test_short_raises_self_trade_when_buyer_equals_target
test_short_raises_market_closed_on_non_trading_day
test_short_merges_new_into_existing_unfrozen_position
test_short_raises_position_frozen_when_existing_position_is_frozen
test_short_sets_trade_cooldown_after_success
test_short_raises_on_cooldown_when_cooldown_active

test_cover_deducts_cover_cost_from_cash
test_cover_releases_locked_cash_proportionally
test_cover_releases_locked_fund_proportionally
test_cover_credits_profit_to_cash_when_price_dropped
test_cover_does_not_credit_profit_when_price_rose
test_cover_removes_short_position_when_fully_covered
test_cover_raises_no_position_when_short_portfolio_empty
test_cover_raises_position_frozen_when_position_is_frozen
test_cover_raises_insufficient_funds_when_cash_below_cover_cost
test_cover_sets_trade_cooldown_after_success
test_cover_raises_market_closed_on_non_trading_day

test_update_frozen_shorts_sets_frozen_true_after_freeze_period
test_update_frozen_shorts_does_not_freeze_positions_before_freeze_period
test_update_frozen_shorts_skips_already_frozen_positions
```

### activity_service.py

```
test_record_message_increments_today_text_msgs
test_record_message_increments_week_text_msgs
test_record_message_increments_today_media_msgs_on_attachment
test_record_message_increments_reply_count_when_message_is_reply
test_record_message_adds_photo_bonus_minutes_in_photo_channel
test_record_message_does_not_add_photo_bonus_in_non_photo_channel
test_record_message_updates_last_activity_timestamp
test_record_message_skips_opted_out_author

test_record_reaction_increments_today_reaction_count
test_record_reaction_increments_week_reaction_count
test_record_reaction_updates_last_activity

test_record_voice_join_creates_voice_session
test_record_voice_join_records_start_time
test_record_voice_leave_accumulates_voice_minutes
test_record_voice_leave_adds_channel_to_unique_channels
test_record_voice_leave_does_not_duplicate_same_channel
test_record_voice_leave_removes_voice_session_on_complete
test_record_voice_leave_applies_stay_bonus_when_stay_meets_threshold

test_reset_today_buckets_zeroes_all_today_fields_for_all_users
test_reset_week_buckets_zeroes_all_week_fields_for_all_users
```

### fund_service.py

```
test_create_fund_initialises_fund_with_default_name
test_create_fund_with_name_sets_custom_name
test_create_fund_does_not_overwrite_existing_fund

test_withdraw_transfers_amount_from_fund_to_user_cash
test_withdraw_applies_early_withdrawal_penalty_on_non_first_day
test_withdraw_skips_penalty_on_first_of_month
test_withdraw_raises_fund_insufficient_balance_when_fund_empty
test_withdraw_raises_invalid_amount_on_zero_amount
test_withdraw_raises_invalid_amount_on_negative_amount

test_send_events_transfers_amount_to_events_wallet
test_send_events_raises_fund_insufficient_balance_when_underfunded

test_accrue_apy_increases_fund_balance_by_expected_amount
test_accrue_apy_applies_penalty_reduction_to_effective_apy
test_accrue_apy_with_zero_balance_makes_no_change
```

### daily_service.py

```
test_claim_daily_adds_daily_reward_to_cash
test_claim_daily_sets_last_claim_to_now
test_claim_daily_increments_streak_on_consecutive_day
test_claim_daily_resets_streak_after_gap
test_claim_daily_adds_streak_bonus_on_day_seven
test_claim_daily_resets_streak_to_zero_after_day_seven_bonus
test_claim_daily_raises_invalid_amount_when_claimed_today_already
test_claim_daily_is_eligible_at_midnight_next_day
test_claim_daily_accepts_first_ever_claim_when_last_claim_is_none
```

### portfolio_service.py

```
test_calculate_net_worth_sums_cash_and_long_position_values
test_calculate_net_worth_includes_locked_collateral_from_shorts
test_calculate_net_worth_with_no_positions_equals_cash_balance
test_portfolio_snapshot_lists_all_long_positions_with_current_values
test_portfolio_snapshot_lists_all_short_positions_with_pnl
test_portfolio_snapshot_marks_frozen_positions
test_portfolio_snapshot_for_user_with_no_positions_returns_empty_lists
test_capture_month_start_net_worth_writes_current_net_worth
```

### liquidation_service.py

```
test_liquidates_short_when_current_price_exceeds_threshold
test_does_not_liquidate_short_when_price_below_threshold
test_liquidates_frozen_position_despite_frozen_flag
test_liquidation_covers_full_position_not_partial
test_liquidation_releases_all_collateral
test_liquidation_charges_cover_cost_from_cash
test_no_short_positions_results_in_no_action
test_multiple_users_each_checked_independently
```

### discipline_service.py

```
test_apply_timeout_penalty_reduces_stock_price_by_discipline_rate
test_apply_ban_penalty_reduces_stock_price_by_discipline_rate
test_price_does_not_drop_below_floor_after_discipline
test_penalty_applied_to_correct_user_only
test_raises_value_error_on_unknown_user_id
```

### price_tick_service.py

```
test_activity_price_tick_increases_price_for_active_user
test_activity_price_tick_applies_floor_stall_on_low_proposal
test_activity_price_tick_appends_to_price_history
test_inactivity_decay_tick_decays_price_for_inactive_user
test_inactivity_decay_tick_skips_user_active_within_threshold
test_vc_boost_tick_increases_price_for_eligible_vc_boost_entry
test_vc_boost_tick_skips_entry_past_end_time
test_vc_boost_tick_skips_user_not_currently_in_voice
test_vc_boost_tick_skips_entry_boosted_too_recently
test_reset_24h_high_low_derived_from_last_24h_history_only
```

### lock_manager.py

**Concurrency tests use `asyncio.gather` to simulate simultaneous competing operations.**

```
test_single_lock_acquired_and_released_successfully
test_two_concurrent_acquires_on_same_user_serialize_not_overlap
test_two_concurrent_acquires_on_different_users_run_in_parallel
test_locked_context_manager_releases_on_normal_exit
test_locked_context_manager_releases_on_exception
test_locked_with_two_user_ids_acquires_both_locks
test_locked_with_sorted_ids_prevents_deadlock_under_reversed_order
test_concurrent_buy_calls_same_target_produce_consistent_price
```

For `test_two_concurrent_acquires_on_same_user_serialize_not_overlap`, the test pattern is:

```python
async def test_two_concurrent_acquires_on_same_user_serialize_not_overlap():
    # Arrange
    lock_manager = LockManager()
    execution_order = []

    async def task_a():
        async with lock_manager.locked("user1"):
            execution_order.append("a_start")
            await asyncio.sleep(0)   # yield to event loop
            execution_order.append("a_end")

    async def task_b():
        async with lock_manager.locked("user1"):
            execution_order.append("b_start")
            await asyncio.sleep(0)
            execution_order.append("b_end")

    # Act
    await asyncio.gather(task_a(), task_b())

    # Assert
    # Either A completes fully before B starts, or B completes fully before A starts
    assert execution_order in (
        ["a_start", "a_end", "b_start", "b_end"],
        ["b_start", "b_end", "a_start", "a_end"],
    )
```

---

## Persistence Layer Integration Tests

These tests are marked `@pytest.mark.integration`. They use a real `aiosqlite` in-memory database created fresh per test session and rolled back per test function.

### Fixtures (in `tests/adapters/persistence/conftest.py`)

```python
@pytest.fixture(scope="session")
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()

@pytest.fixture
async def async_session(async_engine):
    async with AsyncSession(async_engine) as session:
        async with session.begin():
            yield session
            await session.rollback()
```

### test_user_repo.py

```
test_save_and_fetch_user_returns_equal_account
test_fetch_nonexistent_user_returns_none
test_update_cash_balance_persists_change
test_long_position_saved_and_fetched_correctly
test_short_position_saved_and_fetched_with_frozen_flag
test_activity_bucket_today_round_trip
test_activity_bucket_week_round_trip
test_voice_unique_channels_round_trip_preserves_all_channels
test_delete_long_position_removes_from_db
test_delete_short_position_removes_from_db
test_list_all_user_ids_returns_all_stored_ids
```

**JSON migration test:**

```
test_migrate_users_json_inserts_all_users_into_db
test_migrate_users_json_is_idempotent_on_second_run
test_migrate_preserves_cash_balance_values
test_migrate_preserves_long_positions
test_migrate_preserves_short_positions_with_frozen_flag
```

### test_price_repo.py

```
test_save_and_fetch_stock_returns_equal_stock
test_history_appended_and_fetched_in_order
test_history_pruned_to_last_24h_on_fetch
test_all_time_high_updated_on_new_price_above_ath
test_all_time_high_not_updated_on_price_below_ath
test_high_24h_derived_from_history_window
test_low_24h_derived_from_history_window
test_fetch_nonexistent_stock_returns_none
```

### test_fund_repo.py

```
test_save_and_fetch_fund_returns_equal_fund
test_events_wallet_fetch_after_save
test_update_cash_balance_persists
test_fetch_nonexistent_fund_returns_none
test_investor_entry_saved_and_fetched
```

### test_penalty_repo.py

```
test_save_and_fetch_penalty_returns_equal_record
test_fetch_expired_penalty_returns_none
test_accumulated_penalties_persist_correctly
test_overwrite_existing_penalty_replaces_record
```

---

## Discord Layer Tests

### Embed builder tests

`tests/adapters/discord_bot/test_embeds.py` contains pure synchronous tests. No `dpytest`, no bot instance. Embed builder functions take typed domain objects and return `discord.Embed` instances.

```
test_build_buy_confirmation_embed_sets_title
test_build_buy_confirmation_embed_includes_shares_count
test_build_buy_confirmation_embed_includes_cost
test_build_buy_confirmation_embed_includes_new_price
test_build_sell_confirmation_embed_includes_revenue
test_build_short_confirmation_embed_shows_collateral_split
test_build_cover_confirmation_embed_shows_profit_when_positive
test_build_cover_confirmation_embed_shows_loss_when_negative
test_build_balance_embed_shows_cash_and_net_worth
test_build_portfolio_embed_shows_long_positions
test_build_portfolio_embed_shows_frozen_label_on_frozen_shorts
test_build_trending_embed_limits_to_15_entries
test_build_price_embed_shows_24h_high_and_low
test_build_price_embed_shows_all_time_high
test_build_fund_info_embed_shows_effective_apy
test_build_error_embed_does_not_leak_stack_trace
```

### Cog tests

Cog tests use `dpytest`. Each test creates a minimal bot with the cog loaded, the application service replaced with an `AsyncMock`, and dispatches a command via `dpytest.message`. The test then asserts on the reply embed or message content.

```python
# Example: tests/adapters/discord_bot/test_trading_cog.py

@pytest.fixture
async def trading_service_mock():
    return AsyncMock(spec=TradingService)

@pytest.fixture
async def bot_with_trading_cog(trading_service_mock):
    bot = commands.Bot(command_prefix="$", intents=discord.Intents.default())
    await bot.add_cog(TradingCog(trading_service_mock, make_settings()))
    dpytest.configure(bot)
    return bot
```

**TradingCog tests:**

```
test_buy_command_calls_trading_service_with_correct_args
test_buy_command_sends_embed_on_success
test_buy_command_sends_error_embed_on_insufficient_funds
test_buy_command_sends_error_embed_on_market_closed
test_buy_command_sends_error_embed_on_self_trade
test_buy_command_sends_error_embed_on_opted_out_target
test_sell_command_calls_trading_service_with_correct_args
test_sell_command_sends_error_embed_on_no_position
test_short_command_calls_trading_service_with_correct_args
test_short_command_sends_error_embed_on_frozen_position
test_short_command_sends_error_embed_on_cooldown
test_cover_command_calls_trading_service_with_correct_args
test_cover_command_sends_embed_with_pnl_on_success
```

**DailyCog tests:**

```
test_daily_command_calls_daily_service
test_daily_command_shows_streak_count_in_reply
test_daily_command_sends_error_when_already_claimed_today
```

**AccountCog tests:**

```
test_balance_command_sends_embed_with_net_worth
test_optin_command_calls_service_and_confirms
test_optin_command_shows_intro_on_first_call
test_optout_command_calls_service_and_confirms
```

### Listener tests

Listener tests mock the bot instance and dispatch fake events. No `dpytest` is needed — the listener methods are called directly with mock Discord objects.

```
# test_message_listener.py
test_on_message_calls_activity_service_for_normal_message
test_on_message_calls_activity_service_with_media_flag_for_attachment
test_on_message_calls_voice_ping_service_for_vc_ping_message
test_on_message_ignores_bot_messages
test_on_message_calls_process_commands_after_activity_recording

# test_voice_listener.py
test_on_voice_state_update_join_calls_record_voice_join
test_on_voice_state_update_leave_calls_record_voice_leave
test_on_voice_state_update_channel_switch_finalizes_old_and_starts_new
test_on_voice_state_update_ignores_self_mute_change_with_same_channel

# test_reaction_listener.py
test_on_reaction_add_calls_activity_service
test_on_reaction_add_ignores_bot_reactions

# test_member_listener.py
test_on_member_update_calls_discipline_service_on_new_timeout
test_on_member_update_ignores_existing_timeout_with_same_value
test_on_member_ban_calls_discipline_service
```

---

## Background Task Tests

Each task class receives its service dependency via constructor injection. Tests call the wrapped coroutine (`task._loop.coro(task)`) directly rather than starting the real `tasks.loop` decorator machinery.

```python
# Example: tests/adapters/tasks/test_liquidation_task.py

async def test_liquidation_task_invokes_check_and_liquidate_shorts():
    # Arrange
    service = AsyncMock(spec=LiquidationService)
    task = LiquidationTask(service)

    # Act
    await task._loop.coro(task)

    # Assert
    service.check_and_liquidate_shorts.assert_awaited_once()


async def test_liquidation_task_continues_loop_after_service_exception():
    # Arrange
    service = AsyncMock(spec=LiquidationService)
    service.check_and_liquidate_shorts.side_effect = RuntimeError("db offline")
    task = LiquidationTask(service)

    # Act — must not raise
    await task._loop.coro(task)
    # Assert — no assertion needed; reaching here means the exception was swallowed
```

**DailyResetTask / WeeklyResetTask with `freezegun`:**

```
test_daily_reset_task_fires_on_first_run_after_midnight
test_daily_reset_task_does_not_fire_twice_on_same_calendar_day
test_daily_reset_task_fires_again_after_date_advances
test_weekly_reset_task_fires_on_monday
test_weekly_reset_task_does_not_fire_on_tuesday
test_weekly_reset_task_does_not_fire_twice_same_week
```

Example `freezegun` usage in a task test:

```python
from freezegun import freeze_time

async def test_daily_reset_fires_after_midnight():
    # Arrange
    service = AsyncMock(spec=ActivityService)
    price_svc = AsyncMock(spec=PriceTickService)
    task = DailyResetTask(service, price_svc)

    with freeze_time("2026-05-13 23:59:59"):
        await task._loop.coro(task)
        service.reset_today_buckets.assert_not_awaited()

    with freeze_time("2026-05-14 00:00:30"):
        await task._loop.coro(task)
        service.reset_today_buckets.assert_awaited_once()
```

---

## End-to-End / Smoke Tests

E2E tests are in `tests/e2e/test_smoke.py` and are marked `@pytest.mark.e2e`. They are not run on every commit — only on PRs (see CI section).

The smoke test starts a real bot instance against a `dpytest` mock guild, backed by a real `aiosqlite` in-memory database. It verifies that a sequence of commands mutates the database state correctly end-to-end.

```python
@pytest.mark.e2e
async def test_balance_command_returns_initial_cash_for_new_user():
    # Arrange: bot started by e2e fixture, guild has two members
    # Act
    await dpytest.message("$balance")
    # Assert
    embed = dpytest.get_message().embeds[0]
    assert "$10,000" in embed.description


@pytest.mark.e2e
async def test_buy_and_balance_reflect_deducted_cash():
    # Arrange
    buyer = dpytest.get_config().members[0]
    target = dpytest.get_config().members[1]
    await dpytest.message("$optin", member=target)
    initial_price = 100.0

    # Act
    await dpytest.message(f"$buy {target.mention} 5", member=buyer)

    # Assert — database state
    async with test_session() as session:
        account = await user_repo.fetch(session, str(buyer.id))
    expected_cash = 10_000.0 - (initial_price * 5)
    assert account.cash_balance == pytest.approx(expected_cash)
```

---

## Test Data and Fixtures

### `tests/conftest.py` outline

```python
import pytest
from freezegun import freeze_time
from datetime import datetime
from stockxchange.adapters.config import Settings
from stockxchange.application.lock_manager import LockManager
from stockxchange.domain.models import (
    UserAccount, Stock, HedgeFund, FundPenalty,
    LongPosition, ShortPosition, ActivityBucket, DailyProgress,
)

# ── Settings ──────────────────────────────────────────────────────────────────

@pytest.fixture
def settings() -> Settings:
    return Settings(
        discord_token="test-token",
        guild_id=999_000_000_000_000_000,
        database_url="sqlite+aiosqlite:///:memory:",
        market_open=time(6, 30),
        market_close=time(4, 30),
        min_price=70.0,
        initial_cash=10_000.0,
        initial_price=100.0,
    )

# ── Clock ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def now_frozen():
    """Returns a fixed datetime and activates freezegun for the test's duration."""
    fixed = datetime(2026, 5, 13, 10, 0, 0)  # Tuesday 10:00 UTC — market open
    with freeze_time(fixed):
        yield fixed

# ── LockManager ───────────────────────────────────────────────────────────────

@pytest.fixture
def lock_manager() -> LockManager:
    return LockManager()

# ── Fake Repositories ─────────────────────────────────────────────────────────

@pytest.fixture
def fake_user_repo():
    return FakeUserRepo()   # in-memory dict, implements IUserRepo

@pytest.fixture
def fake_price_repo():
    return FakePriceRepo()

@pytest.fixture
def fake_fund_repo():
    return FakeFundRepo()

@pytest.fixture
def fake_penalty_repo():
    return FakePenaltyRepo()

# ── Domain Object Factories ───────────────────────────────────────────────────

def make_user(
    user_id: str = "user_001",
    cash_balance: float = 10_000.0,
    opt_in: bool = True,
    long_positions: dict | None = None,
    short_positions: dict | None = None,
) -> UserAccount:
    return UserAccount(
        user_id=user_id,
        cash_balance=cash_balance,
        net_worth=cash_balance,
        month_start_net_worth=cash_balance,
        long_positions=long_positions or {},
        short_positions=short_positions or {},
        today=ActivityBucket(),
        week=ActivityBucket(),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=datetime(2026, 5, 13, 9, 0, 0),
        opt_in=opt_in,
        intro_shown=False,
    )

def make_stock(
    user_id: str = "user_001",
    current: float = 100.0,
) -> Stock:
    return Stock(
        user_id=user_id,
        current=current,
        history=[],
        high_24h=current,
        low_24h=current,
        all_time_high=current,
    )

def make_short_position(
    target_user_id: str = "user_002",
    shares: int = 10,
    entry_price: float = 100.0,
    locked_cash: float = 1_000.0,
    locked_fund: float = 0.0,
    frozen: bool = False,
) -> ShortPosition:
    return ShortPosition(
        target_user_id=target_user_id,
        shares=shares,
        entry_price=entry_price,
        locked_cash=locked_cash,
        locked_fund=locked_fund,
        created_at=datetime(2026, 5, 13, 9, 30, 0),
        frozen=frozen,
    )

def make_long_position(
    target_user_id: str = "user_002",
    shares: int = 10,
    avg_entry: float = 100.0,
) -> LongPosition:
    return LongPosition(
        target_user_id=target_user_id,
        shares=shares,
        avg_entry=avg_entry,
    )

def make_fund(
    fund_id: str = "user_001",
    cash_balance: float = 5_000.0,
) -> HedgeFund:
    return HedgeFund(
        fund_id=fund_id,
        name="Test Fund",
        manager_id=fund_id,
        cash_balance=cash_balance,
        investors={},
    )
```

---

## TDD Workflow Per Feature

The mandatory RED-GREEN-REFACTOR cycle from `~/.claude/rules/common/testing.md` applies to every feature in this codebase. The specific application order for StockXchange is:

**Step 1 — Write the domain unit test first (RED).**
Before touching `price_engine.py`, write `test_price_engine.py` with a failing test for the specific function. Run `pytest tests/domain/test_price_engine.py` — it must fail with `ImportError` or `AttributeError` because the function does not exist yet.

**Step 2 — Write the minimal domain function to make it pass (GREEN).**
Implement only enough to make that one test pass. No other tests should be affected. Verify with `pytest tests/domain/test_price_engine.py -x`.

**Step 3 — Add edge-case domain tests and keep green.**
Add the remaining test names listed in this document for that function. Make each one green before moving to the next. Do not batch multiple functions at once.

**Step 4 — Write the application service test with a fake repo (RED).**
Only after the domain function is fully covered, write the service test. Example: `test_buy_deducts_cost_from_buyer_cash` in `test_trading_service.py`. This must fail because `TradingService` does not exist yet.

**Step 5 — Implement the service to pass the service test (GREEN).**
Implement `TradingService.buy` using the domain functions already verified in steps 1–3. The service test passes; domain tests remain green.

**Step 6 — Write the cog test last (RED, then GREEN).**
Only after the service is verified, write the cog test via `dpytest`. The cog test should be thin: it mocks the service and asserts that the cog passes the right arguments and sends the right embed. It does not re-test business logic.

**Do not start from the cog.** Starting from the cog means every test failure could be caused by the embed, the cog wiring, the service logic, or the domain calculation. There are too many variables to isolate quickly. The pyramid ensures each layer is verified in isolation before the layer above it is tested.

**Step 7 — Verify coverage.**

```bash
pytest --cov --cov-report=term-missing --cov-fail-under=80
```

Coverage must not drop. If implementing a feature drops coverage, add the missing tests before committing.

---

## CI Integration

### GitHub Actions workflow

```yaml
# .github/workflows/test.yml
name: test

on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v3
        with:
          version: "latest"
          enable-cache: true
          cache-dependency-glob: "uv.lock"

      - name: Install dependencies
        run: uv sync --all-extras

      - name: Run unit and integration tests
        run: |
          uv run pytest \
            -m "not e2e" \
            --cov=src/stockxchange \
            --cov-report=term-missing \
            --cov-fail-under=80 \
            -x

      - name: Upload coverage report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: coverage-html
          path: htmlcov/

  e2e:
    runs-on: ubuntu-latest
    needs: test
    if: github.event_name == 'pull_request'
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: "latest"
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - name: Install dependencies
        run: uv sync --all-extras
      - name: Run E2E smoke tests
        run: uv run pytest -m e2e -v
```

**Key CI rules:**

- The `test` job runs on every push and every PR. It excludes `@pytest.mark.e2e` tests. The build fails if coverage drops below 80% or any test fails.
- The `e2e` job runs only on PRs (not on every push to feature branches). It depends on `test` — E2E does not run if unit tests are already failing.
- The `.venv/` is cached via `uv`'s built-in cache mechanism, keyed on `uv.lock`. Cache invalidation is automatic on lockfile changes.
- Coverage reports are uploaded as artifacts on every run, including failing runs, so regressions can be diagnosed from CI without a local checkout.

---

## Anti-Patterns to Avoid

**Do not mock the system under test.** If the test is for `TradingService.buy`, mock the repositories that `TradingService` depends on — never mock `TradingService` itself. Mocking the SUT means the test is vacuously true.

**Do not write tests that pass before the implementation exists.** Every new test must be run against the repository in the state before the feature is implemented, and it must produce a red failure. If a test passes before the implementation is written, it is not testing anything.

**Do not write E2E tests when a service-level test would catch the same bug.** If a business rule can be verified by calling a service method with a fake repo, do not write a `dpytest` test for it. E2E tests are for verifying the wiring between layers, not for re-testing domain rules.

**Do not share state between tests.** Every test function must start from a clean state. `FakeRepo` instances are created fresh per test via fixtures. No module-level singleton repos. No class-level `setUp` that mutates shared state.

**Do not test private methods directly.** If `_compute_collateral_split` is a private helper inside `TradingService`, do not call it in a test. Test the public `short()` method that exercises it. If a private method is complex enough to warrant direct testing, extract it to the domain layer as a pure function so it becomes a public, directly testable unit.

**Do not use `time.sleep` in tests.** All time-dependent behaviour is controlled via `freezegun`. Tests that require waiting for a real clock are fragile and slow. If a test appears to need `sleep`, the code under test has an implicit clock dependency that should be made explicit and injectable.

**Do not assert on implementation details (internal state) instead of observable behaviour.** Do not assert that an internal dict was mutated in a specific way. Assert on the value returned by the next observable action: the value returned by the method, the state visible through the repository interface, or the Discord message sent via the cog.
