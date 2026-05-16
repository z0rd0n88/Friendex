# PR #11 Team Review — Phase 3 Domain Models & Error Taxonomy

**Status:** Findings captured. Phase 3.1 (A1+A2+A7) **implemented** on this same branch and PR (`docs/pr-11-team-review` / #13). Phase 3.2 (A3–A6) remains open.

**Subject PR:** [#11 — feat(phase-3): domain models & error taxonomy](https://github.com/z0rd0n88/Friendex/pull/11) (merged)

**Review date:** 2026-05-15

**Reviewers:**
- `code-reviewer` agent — general code quality
- `security-reviewer` agent — security & risk
- `python-reviewer` agent — Python idiom & PEP 8

**Synthesised by:** manager agent (general-purpose subagent)

---

## Table of contents

1. [Executive summary](#executive-summary)
2. [Action checklist](#action-checklist)
3. [Manager's unified verdict](#managers-unified-verdict)
4. [Individual reviewer reports](#individual-reviewer-reports)
   - [Code-quality review](#code-quality-review)
   - [Security review](#security-review)
   - [Python-patterns review](#python-patterns-review)
5. [Notes for the implementing session](#notes-for-the-implementing-session)

---

## Executive summary

PR #11 (Phase 3) shipped a clean, idiomatic, well-tested domain layer with 91 tests, 100% coverage, zero ruff/mypy diagnostics, and architecturally sound error taxonomy. The merge was correct.

A multi-agent retrospective surfaced **seven follow-up items**. None are CRITICAL; two are HIGH-severity and **block Phase 4 (persistence/ORM)** — the rest are polish that can land in parallel with Phase 4.

The **blocking** items are:
1. Migrate every monetary/price field from `float` to `decimal.Decimal` (avoids IEEE-754 accounting drift in trade math).
2. Replace `datetime.utcnow` defaults with `datetime.now(tz=UTC)` (utcnow is deprecated in 3.12, returns naive datetimes, and currently forces a workaround in tests).

Both are materially cheaper to fix *before* Phase 4 commits to column types and serialisation formats.

---

## Action checklist

Tracked as a single ordered list so a future session can implement them in one or two PRs. Severity is the manager-escalated value (multi-reviewer consensus may exceed any single reviewer's rating).

### Phase 3.1 — blocking PR (before Phase 4 starts) — ✅ implemented in this PR

- [x] **A1 (HIGH, effort M)** — Migrate money/price fields from `float` to `decimal.Decimal`
  - Fields migrated: `UserAccount.cash_balance`, `UserAccount.net_worth`, `UserAccount.month_start_net_worth`, `LongPosition.avg_entry`, `ShortPosition.entry_price`, `ShortPosition.locked_cash`, `ShortPosition.locked_fund`, `PricePoint.price`, `Stock.current`, `Stock.high_24h`, `Stock.low_24h`, `Stock.all_time_high`, `HedgeFund.cash_balance`, `HedgeFund.investors` (values), `FundPenalty.penalty_apr`.
  - Exception signatures updated: `InsufficientFunds(need: Decimal, have: Decimal)` and `FundInsufficientBalance(need: Decimal, have: Decimal)`. `Decimal` supports `:,.2f`, so user-facing message templates are unchanged.
  - Tests in `tests/domain/test_models.py` and `tests/domain/test_errors.py` now construct with `Decimal("…")` literals.
  - Quantisation policy documented in `src/friendex/domain/models.py` module docstring: currency → `Decimal('0.01')`, rates → `Decimal('0.0001')`. Invariants do not auto-quantise; callers supply quantised values.
  - `voice_minutes`, `role_ping_joins`, `role_ping_join_minutes` left as `float` (duration/count, not money).

- [x] **A2 (HIGH, effort S)** — Replace `datetime.utcnow` with `datetime.now(tz=UTC)`
  - `models.py`: `ActivityBucket.bucket_start = field(default_factory=lambda: datetime.now(tz=UTC))`.
  - Imports updated to `from datetime import UTC, datetime`.
  - Tests: `NOW` constant is now `datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)` (no `.replace(tzinfo=None)` workaround). Defaults test asserts `bucket.bucket_start.tzinfo is UTC`.

- [x] **A7 (LOW, effort S)** — Bundled with A2: deleted the `-O`-mode test cruft
  - `tests/domain/test_models.py`: redundant second `try/except AssertionError` block removed (`pytest.raises(ValueError)` already proves `AssertionError` was not raised). The dedicated `test_invariant_holds_under_python_optimised_mode_semantics` function went with it.
  - `_TIME_SENTINEL = time(0, 0)` workaround removed and `time` dropped from the test imports.

### Phase 3.2 — polish PR (in parallel with Phase 4)

- [ ] **A3 (MEDIUM, effort S)** — Add `frozen=True` to all domain dataclasses
  - Targets: `DailyProgress`, `LongPosition`, `ShortPosition`, `UserAccount`, `Stock`, `HedgeFund`, `PricePoint`, `FundPenalty`, `VoiceSession`, `VoicePingSession`, `VcExtraBoost`.
  - **Exception:** `ActivityBucket` cannot be frozen because `__post_init__` reassigns `voice_unique_channels`. Either keep mutable (document why) or rebind via `object.__setattr__` inside `__post_init__`.
  - **Exception:** `ShortPosition.frozen: bool` flag — if mutation is required at runtime, this dataclass must stay mutable; otherwise the flag transition becomes "construct a new instance with the updated flag" at the service layer.

- [ ] **A4 (MEDIUM, effort S)** — Redact balance from user-facing messages
  - `errors.py:53` (`InsufficientFunds`): change message to `f"Insufficient funds: need ${need:,.2f}."`  — drop the `have` portion.
  - `errors.py:115` (`FundInsufficientBalance`): same shape.
  - Keep `self.have` on the exception object for operator-side logging; only the user-facing string is redacted.
  - Update the two affected test assertions in `tests/domain/test_errors.py`.

- [ ] **A5 (LOW, effort S)** — Tighten `Stock.current` zero-price guard
  - `models.py:109`: change `if self.current < 0` to `if self.current <= 0`. Match `LongPosition.avg_entry` and `ShortPosition.entry_price` which already use strict positivity.
  - Update `test_stock_zero_price_allowed` → `test_stock_rejects_zero_price` (it should now raise).

- [ ] **A6 (MEDIUM, effort S)** — Docstrings for `DomainError` subclasses
  - `errors.py`: add one-line docstrings to `InsufficientFunds`, `MarketClosed`, `PositionFrozen`, `OnCooldown`, `OptedOut`, `NoPosition`, `InsufficientShares`, `SelfTrade`, `InvalidAmount`, `FundInsufficientBalance`, `AlreadyOptedIn`, `AlreadyOptedOut`. `DomainError`, `FriendexError`, and `PersistenceError` already have them.

---

## Manager's unified verdict

### Convergent findings (multiple reviewers agreed)

| # | Finding | Location | Flagged by | Escalated severity |
|---|---------|----------|------------|--------------------|
| C1 | `datetime.utcnow` used as default factory — deprecated in 3.12, returns naive datetime, forces tests to strip tzinfo | `models.py:25`; test workaround at `test_models.py:32` | code-reviewer (MEDIUM), python-reviewer (HIGH) | **HIGH** |
| C2 | Monetary fields use `float`, not `Decimal` — IEEE-754 drift will cause false insufficient-funds errors and short-collateral math errors | `models.py:45, 58–60, 76–78, 95, 102, 104–106, 118–119, 129` | security-reviewer (MEDIUM); code-reviewer implicitly via "frozen-ish" concern | **HIGH** |
| C3 | Test/typing smells stemming from naive-datetime workarounds | `test_models.py:32, 519`; `errors.py:19` (`time` import) | code-reviewer (LOW), python-reviewer (MEDIUM, LOW) | **MEDIUM** (auto-resolves with C1) |

### Unique findings (one reviewer)

| Sev | Location | Source | Description |
|-----|----------|--------|-------------|
| MEDIUM | `models.py` (all dataclasses) | code-reviewer | No `frozen=True` despite "frozen-ish" intent — silent post-construction mutation possible. |
| MEDIUM | `errors.py:53, 115` | security-reviewer | `InsufficientFunds` / `FundInsufficientBalance` leak exact cash balance into user-facing Discord messages. |
| MEDIUM | `errors.py` (10 of 12 subclasses) | python-reviewer | Missing docstrings; `help()` is empty for most exceptions. |
| LOW | `models.py:28` | security-reviewer | `voice_unique_channels` has no size/element-length cap — risk only on untrusted-JSON load. Flag for persistence adapter. |
| LOW | `models.py:109` | security-reviewer | `Stock.current` allows zero price (`< 0` should be `<= 0`) — div-by-zero downstream. |
| LOW | `test_models.py:499–514` | code-reviewer | Redundant `try/except`; cannot actually test `-O` behavior without subprocess. |

### Severity disagreements & manager's adjudication

- **`datetime.utcnow`** — code-reviewer rated MEDIUM, python-reviewer rated HIGH. Manager adopts **HIGH**: deprecated stdlib API already leaking into tests as a known-bad workaround; cost only grows as Phase 4 commits to a serialisation format.
- **`float` for money** — only security-reviewer flagged at MEDIUM; code-reviewer circled the same hazard via the "frozen-ish" concern. Manager escalates to **HIGH**: retrofitting `Decimal` after Phase 4 schemas exist requires a data migration on every JSON file plus a touch of every trade-math site. Cheaper by an order of magnitude now.

### Strengths (consensus across all three reviewers)

- Parallel `DomainError` / `FriendexError` exception taxonomy — type-system seam between user-facing and operator-facing errors, enforced by a dedicated test (`test_persistence_error_is_not_a_domain_error`).
- `raise ValueError` over `assert` in `__post_init__`, with a dedicated test that survives `python -O`.
- Zero ruff / zero mypy diagnostics; modules under 160 lines; no application-layer imports from domain.
- Test ergonomics: helper factory functions (`_short`, `_account`, `_stock`, `_fund`) with override-dict pattern; `pytest.raises(..., match=...)` pins both type and message contract.
- No hardcoded secrets, tokens, or credentials anywhere in the diff.

### Risk to downstream phases

The domain layer is structurally ready for Phase 4, but two issues are materially cheaper to fix **before** persistence schemas exist: monetary `Decimal` migration and timezone-aware datetimes. Both will become column-type / serialisation-format decisions in Phase 4, and reversing them later means a data migration over every `users.json` / `prices.json` / `funds.json` plus rewrites at every trade-math site. Everything else (immutability via `frozen=True`, balance redaction in error messages, the `Stock.current` zero guard, missing docstrings, and the `-O`-test cleanup) can be tracked as Phase 3.2 polish without blocking Phase 4 work.

**Recommended sequencing:** A1 + A2 (+ A7 bundled) land as the Phase 3.1 PR before Phase 4 kicks off. A3–A6 batched as Phase 3.2 polish PR in parallel with Phase 4.

---

## Individual reviewer reports

### Code-quality review

**Reviewer:** `code-reviewer` agent.

**Summary:** Clean, well-scoped domain layer. Twelve dataclass models with construction-time invariants and a two-root exception taxonomy. The code is readable and deliberately minimal. Three issues — one structural gap in the models, one test smell, one silent-mutation risk — none blocking.

**Findings:**

- **[MEDIUM]** `src/friendex/domain/models.py:15` — Dataclasses declared mutable despite "frozen-ish" label.
  - **Problem:** None of the dataclasses use `@dataclass(frozen=True)`. The coding-style rule prefers immutable data. A caller can mutate any field after construction, bypassing all invariants silently — the invariants only run at construction time.
  - **Recommendation:** Add `frozen=True` to all dataclasses that have invariants. Document explicit exceptions where mutation is intentional (e.g., `ShortPosition.frozen` flag).

- **[MEDIUM]** `src/friendex/domain/models.py:25` — `datetime.utcnow` is a deprecated, timezone-naive default.
  - **Problem:** `ActivityBucket.bucket_start` defaults to `field(default_factory=datetime.utcnow)`. Deprecated since Python 3.12, returns a naive datetime. The test fixture constructs `NOW` with `tzinfo=UTC` then strips it via `.replace(tzinfo=None)`, which papers over the inconsistency rather than resolving it.
  - **Recommendation:** Change the default to `field(default_factory=lambda: datetime.now(UTC))` and remove the `.replace(tzinfo=None)` workaround from the test fixture.

- **[LOW]** `tests/domain/test_models.py:499–514` — `test_invariant_holds_under_python_optimised_mode_semantics` is logically redundant.
  - **Problem:** Raises `DailyProgress(streak=-1)` twice; the second `try/except` block catches `ValueError` and passes — identical to what `pytest.raises(ValueError)` already asserted. The `except AssertionError` branch is `# pragma: no cover`, meaning never exercised. Cannot actually verify `-O` behavior without a subprocess invocation.
  - **Recommendation:** Remove the second `try/except` block and add a comment explaining a subprocess approach is not used.

**Strengths:**
- Exception taxonomy architecturally sound — `DomainError` / `FriendexError` parallel roots enforce the user-facing vs. operator-facing seam at the type level.
- `ValueError` over `assert` is the right decision and is tested.
- Helper factory pattern in tests eliminates fixture boilerplate.
- Error message contracts are pinned via exact string assertions — treats them as a tested public contract.
- Clean separation of concerns; modules under 160 lines; no application-layer imports.

**Verdict:** APPROVE — no CRITICAL or HIGH. Two MEDIUM items worth addressing before the application layer starts reading these fields.

---

### Security review

**Reviewer:** `security-reviewer` agent.

**Summary:** Sound posture for a pure-domain layer. Two findings warrant attention before persistence/command layers build on top: pervasive use of `float` for monetary fields, and information disclosure in `InsufficientFunds` / `FundInsufficientBalance` surfacing internal balance to other users.

**Findings:**

- **[MEDIUM]** `src/friendex/domain/errors.py:53` — `InsufficientFunds` leaks the caller's exact cash balance.
  - **Threat:** The message `"Insufficient funds: need $X, have $Y"` is routed verbatim to the Discord channel via `user_facing_message`. In a public channel, a watching user learns the exact cash balance of the person who triggered the error.
  - **Impact:** Balance disclosure to uninvolved parties; in a game economy this provides unfair information advantage. `FundInsufficientBalance` at line 115 has the same shape.
  - **Recommendation:** Omit the `have` amount: `"Insufficient funds: need $1,234.50."` Retain the full detail on the exception attribute for operator logs.

- **[MEDIUM]** `src/friendex/domain/models.py:45, 58–60, 76–78, 95, 102, 104–106, 118–119, 129` — All monetary and price fields are `float`, not `Decimal`.
  - **Threat:** IEEE-754 floating-point arithmetic on cash balances introduces representational error. With `INITIAL_CASH = $10,000` and `PRICE_IMPACT_K = 0.5`, repeated additions and multiplications accumulate drift. Two independent calculations of the same balance may not compare equal, causing a user to pass or fail a sufficiency check by a rounding artifact.
  - **Impact:** A user could be erroneously blocked from a valid trade (false "insufficient funds") or allowed a trade that should have been blocked because a rounding direction went their way. `ShortPosition` collateral math is the highest-risk site.
  - **Recommendation:** Replace all monetary fields with `decimal.Decimal`. Foundational change — safer to make now before the application layer cements `float` arithmetic everywhere.

- **[LOW]** `src/friendex/domain/models.py:28` — `ActivityBucket.__post_init__` coerces all elements via `str()` without bounding the list or element size.
  - **Threat:** A JSON-deserialized payload with a large `voice_unique_channels` list passes coercion silently.
  - **Impact:** Bounded in this layer alone, but materialises when the persistence adapter loads from untrusted JSON without a schema validation step.
  - **Recommendation:** Add a size guard: `if len(self.voice_unique_channels) > MAX_CHANNELS: raise ValueError(...)`. Define `MAX_CHANNELS` as a module-level constant.

- **[LOW]** `src/friendex/domain/models.py:109` — `Stock.current` allows zero price.
  - **Threat:** The check is `if self.current < 0` (strict). A zero-price stock passes invariant validation. CLAUDE.md notes `MIN_PRICE = $70` as a hard floor enforced by the price engine. If a deserialized payload sets `current=0.0`, the domain model accepts it silently, bypassing the engine's floor.
  - **Impact:** A zero-price stock causes division-by-zero in any percentage-change or liquidation-threshold calculation.
  - **Recommendation:** Change the guard to `<= 0` to match `LongPosition.avg_entry` and `ShortPosition.entry_price` which already use strict positivity.

**Defense-in-depth observations:**
- The `PersistenceError` / `DomainError` seam is correctly enforced at the type-system level and verified by a dedicated test.
- `raise ValueError` over `assert` survives `python -O`.
- The parallel hierarchy (not a single tree) prevents accidentally catching infrastructure errors inside a game-rule handler.
- No hardcoded credentials, tokens, or secrets anywhere in the diff.

---

### Python-patterns review

**Reviewer:** `python-reviewer` agent.

**Summary:** High idiomatic standard — ruff and mypy both pass with zero diagnostics, `raise ValueError` vs `assert` tradeoff correctly applied and explicitly documented, exception hierarchy cleanly mirrors stdlib conventions. Three narrower idiom gaps.

**Findings:**

- **[HIGH]** `src/friendex/domain/models.py:25` — `datetime.utcnow` used as a `field(default_factory)`.
  - **Issue:** Deprecated in Python 3.12 and will eventually be removed. Returns a naive datetime, silently mixing naive and aware objects elsewhere (the test file explicitly strips tzinfo at line 32).
  - **Idiomatic alternative:** `field(default_factory=lambda: datetime.now(tz=UTC))` — always timezone-aware, not deprecated.

- **[MEDIUM]** `src/friendex/domain/errors.py:19` — `from datetime import time` import looks accidental.
  - **Issue:** `time` is imported solely because `MarketClosed.__init__` annotates parameters with it. Without an explicit comment or `from __future__ import annotations`, the import looks unused to reviewers.
  - **Idiomatic alternative:** Add `from __future__ import annotations` at the top of both `models.py` and `errors.py`. PEP 563 makes annotations strings; the runtime import becomes unnecessary.

- **[MEDIUM]** `tests/domain/test_models.py:32` — naive datetime created by stripping tzinfo after construction.
  - **Issue:** `NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)` is semantically contradictory. Workaround for the model defect above.
  - **Idiomatic alternative:** Fix the factory in `models.py`, then simplify the fixture to `NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)` throughout.

- **[MEDIUM]** `src/friendex/domain/errors.py` — `DomainError` subclasses missing docstrings.
  - **Issue:** Ten of twelve subclasses (`InsufficientFunds`, `MarketClosed`, `PositionFrozen`, etc.) have no class-level docstring. `FriendexError`, `DomainError`, and `PersistenceError` do. `help(InsufficientFunds)` is empty.
  - **Idiomatic alternative:** One-line docstring per subclass describing when it is raised.

- **[LOW]** `tests/domain/test_models.py:519` — sentinel import workaround is a code smell.
  - **Issue:** `_TIME_SENTINEL = time(0, 0)` exists only to suppress a misdiagnosed static-analysis false positive. The `time` import is not actually needed in the test body.
  - **Idiomatic alternative:** Remove `time` from the import line: `from datetime import UTC, datetime`. Delete the sentinel and its comment.

**Notable idioms used well:**
- `raise ValueError` over `assert` in `__post_init__` — explicitly documented and validated by a parametrised test.
- `field(default_factory=list)` and `field(default_factory=set)` everywhere; no mutable class-attribute trap.
- Helper factory functions with override-dict pattern in tests.
- `pytest.raises(ValueError, match=...)` with exact message strings — catches both type and message contract.
- Parallel exception hierarchy with no shared base — `isinstance(exc, DomainError)` correctly excludes infrastructure failures by construction.

---

## Notes for the implementing session

1. **Branch from `main`, not from `feat/phase-3.1-decimal-utc`.** This PR is documentation only; the implementation should land on a fresh branch.
2. **Bundle A1 + A2 + A7 in one PR** (the Phase 3.1 blocking PR). Bundle A3–A6 in a follow-up PR (Phase 3.2). The manager's risk analysis supports this split.
3. **Run the full verification gate** before opening the PR:
   ```bash
   uv run ruff format --check src/friendex/domain/ tests/domain/
   uv run ruff check src/friendex/domain/ tests/domain/
   uv run mypy src/friendex/domain/
   uv run pytest tests/domain/ -v --cov=src/friendex/domain --cov-fail-under=95
   ```
4. **Decimal quantisation policy** — decide and document in the model docstring. Recommended defaults: `Decimal('0.01')` for currency-denominated fields, `Decimal('0.0001')` for rate fields like `penalty_apr`.
5. **Test value updates** — every numeric literal in the money-related tests must change from `100.0` → `Decimal("100.00")`. The `_short`, `_account`, `_stock`, `_fund` helpers each need their `base` dict updated. The `InsufficientFunds` / `FundInsufficientBalance` message tests do **not** need to change strings — `Decimal` supports `:,.2f` formatting natively.
6. **Coverage gate** — current bar is 95%. After A7 removes the redundant test, total lines drop slightly; verify the coverage gate still passes.
