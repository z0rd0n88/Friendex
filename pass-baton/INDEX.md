# Pass-Baton Index

Latest entry per scope. Read the linked file for full context. **New sessions should start here.**

| Scope | Latest | Date | Topic |
|---|---|---|---|
| phase-3-domain | [001](./phase-3-domain/001-2026-05-15-phase-3-pr11-ready.md) | 2026-05-15 | Phase 3 code-complete; PR #11 open, awaiting review |
| phase-4-domain-funcs | [003](./phase-4-domain-funcs/003-2026-05-23-phase-4-review.md) | 2026-05-23 | Independent review — VERDICT CLEAN (gate green, mutation-verified); 2 MEDIUM + 2 LOW non-blocking findings; digest written |
| phase-5-orm | [003](./phase-5-orm/003-2026-05-24-fk-decision-closed.md) | 2026-05-24 | FK enforcement decision closed — PRAGMA foreign_keys=ON chosen (ADR-0002); PR #36 open |
| phase-6-repos | [015](./phase-6-repos/015-2026-05-25-hardening-h1-h2-review.md) | 2026-05-25 | Independent review of hardening fixes H1 (N+1 batching) + H2 (MigrationError) — VERDICT CLEAN; gate green, both RED-verified under reversion; 1 LOW (weak ordering test) + 1 INFO, no CRITICAL/HIGH/MEDIUM |
| phase-7-locks | [000](./phase-7-locks/000-2026-05-25-phase-7-merged-carryforward.md) | 2026-05-25 | Phase 7 LockManager merged (PR #38, `97b505e`); VERDICT CLEAN, 100% cov. Carry-forward: 1 MEDIUM (cancel-mid-acquire lock leak) to fix in Phase 8a + 1 LOW; conventions for service lock usage |
| phase-8-fakes | [002](./phase-8-fakes/002-2026-05-25-review-clean.md) | 2026-05-25 | Independent review — VERDICT CLEAN; gate green, boundary tests RED-verified (non-tautological), semantics match real adapters + Phase 6 digests; 1 LOW (get returns mutable ref) + 1 INFO, no CRITICAL/HIGH/MEDIUM; digest written |
| phase-8a | [005](./phase-8a/005-2026-05-25-phase-8a-review-iter2-clean.md) | 2026-05-25 | Re-review iter-2 — VERDICT CLEAN; gate green (430 pytest, ruff/format/mypy), iter-1 HIGH (composite lock key) resolved by load-bearing two-guild isolation test + surgical 6-call-site fix, both deferred LOWs documented + untouched, no new deps. Digest written |
| phase-8b | [003](./phase-8b/003-2026-05-25-phase-8b-review-fixes.md) | 2026-05-25 | Review iter-1 fixes applied — H1 (RMW atomicity: lock-first, in-lock re-read, single `_rmw_price` helper) + M2 (history append + ATH ratchet on every tick write) + L1 (imports hoisted) + L2 (floor-stall divergence documented) FIXED; M1 (`activity_tick_k=0.5`) deferred per contract — value unchanged, docstring now tags it as TBD/placeholder pointing at the activity-K carry-forward note. Gate green: 94 application tests, 445 repo-wide (+6 from baseline 439), ruff/format/mypy all pass. H1 verified RED-first via barrier-instrumented `_BarrierPriceRepo` test (captured `101.91 < 200.00` clobber against pre-fix code) |

*Top-level seed: [`000-2026-05-15-start-pass-baton.md`](./000-2026-05-15-start-pass-baton.md) (empty placeholder; establishes the sequence head).*

---

## How to read this

- Each row points at the **highest-numbered** pass-baton in that scope's subdirectory.
- Older pass-batons in the same scope are reachable by following the `## References` section of the latest one (pass-batons link backward as they evolve).
- The index is overwritten by the `pass-baton` skill on every write. Do not hand-edit.
