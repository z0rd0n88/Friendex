# Pass-Baton: Phase 8a review iter-2 — VERDICT CLEAN

**Date:** 2026-05-25
**Scope:** phase-8a
**Branch:** feat/phase-8a-activity
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** b56bca9 fix(phase-8a): address review findings (iter 1)

## Where things stand

Re-reviewed the iter-2 fix against the iter-1 HIGH (ADR-0001 composite lock key).
**Gate is GREEN** (`baton-runner/br-2026-05-25-phase-8/gate-phase-8a-iter-2/`:
pytest 430 passed, ruff-check, ruff-format, mypy all PASS). The HIGH is
genuinely resolved by a load-bearing test + surgical six-call-site change. Both
iter-1 LOWs are explicitly deferred to Phase 12 per the fix baton's documented
rationale and were NOT silently touched. No new dependencies. **Verdict: CLEAN.**

Phase-exit digest written to
`baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md` with the composite-key
convention as a hard rule for 8b–8f.

## Findings by severity

### CRITICAL — none

### HIGH — none

The iter-1 HIGH (lock key omits `guild_id`) is **resolved**:

- Both `ActivityService` and `VoicePingService` now define
  `_lock_key(self, user_id) -> str` returning `f"{self._guild_id}:{user_id}"`,
  with module-docstring updates that document the composite-key contract
  (`activity_service.py:80-89`, `voice_ping_service.py:186-195`).
- All six `locked(...)` call sites route through it
  (`activity_service.py:126, 272, 298, 311`;
  `voice_ping_service.py:201, 215`). Grep confirms zero bare-`user_id`
  `locked(` calls remain in either service.
- The new isolation test
  `test_same_user_in_two_guilds_does_not_serialise_on_shared_lock_manager`
  (`tests/application/test_activity_service.py:489-560`) is genuinely
  load-bearing: it constructs ONE `shared_locks = LockManager()` and injects
  it into TWO `ActivityService`s with distinct `guild_id`s (guild A vs B), then
  runs `record_message(USER, ...)` on both concurrently. Both upserts park on
  a shared `asyncio.Barrier(2)` inside their critical sections; a bare-`user_id`
  key would serialise guild B behind guild A's held lock and the
  `asyncio.wait_for(..., timeout=1.0)` would trip. The fix baton's captured RED
  output (`asyncio.exceptions.CancelledError` + `TimeoutError` against the
  pre-fix commit `c104f3b`) confirms this is not a false positive — the test
  was authored TDD-first and reverting `_lock_key` puts it back into timeout.
- Spot-checked the original 11 ACs: the iter-2 diff against
  `tests/application/test_activity_service.py` is **additions only** — no
  AC test was deleted, renamed, or loosened. `test_voice_ping_service.py` is
  untouched.

### MEDIUM — none

### LOW — none (re-raising the two iter-1 LOWs is explicitly out of scope)

The two iter-1 LOW findings — `VoiceSessionStore.link_ping` in-place set
mutation, and `reward_voice_ping_response` RMW non-atomicity — are deferred
to Phase 12 (no caller until listeners drive these paths). Confirmed:

- `git diff c104f3b..HEAD -- src/friendex/application/voice_session_store.py`
  is **empty** — LOW #1 not silently touched.
- The `reward_voice_ping_response` body (`voice_ping_service.py:131-146`) is
  unchanged in iter-2 — LOW #2 not silently touched.
- The fix baton (`004-2026-05-25-guild-composite-lock-key.md:100-121`)
  documents both deferrals with rationale and explicitly hands them to the
  8a digest for propagation into 8b–8f. They are carried into
  `digest-phase-8a.md` as Phase 12 follow-ups.

## What is verified GOOD (do not regress)

- **Composite key discipline at every mutation lock site.** Six call sites
  in two services, one helper per service, no bare-`user_id` leaks.
- **Single shared `LockManager` topology proven safe by test.** The isolation
  test models the exact Phase 14 wiring (one process-local `LockManager`,
  many per-guild service scopes) and proves cross-guild non-contention.
- **Surgical diff since iter-1:** +28/-7 in `activity_service.py`,
  +21/-3 in `voice_ping_service.py`, +109/-0 in `test_activity_service.py`
  (test wrapper + new test); plus docstring updates and baton/digest/STATE/log.
  No behavioural change to any non-lock-key code path.
- **No new dependencies:** `git diff c104f3b..HEAD -- pyproject.toml uv.lock`
  is empty.
- **Code-review pass on the iter-2 delta:** the `_lock_key` helper is a
  trivial typed pure function; `_BarrierUserRepo` is a properly scoped in-test
  duck-typed wrapper (mypy clean via two `# type: ignore[arg-type]` on the
  service-injection sites — acceptable for a test double mirroring the
  Protocol shape).
- **Security pass on the iter-2 delta:** `f"{self._guild_id}:{user_id}"` keys
  a `dict[str, asyncio.Lock]` inside the process; both fields are internal
  Discord snowflake strings, no trust boundary crossed, no injection surface.
  No secret handling, no new I/O, no new dependencies.

## Next steps

1. Squash-and-merge the iter-2 work to close Phase 8a on issue #2; rely on
   `deleteBranchOnMerge` to remove `feat/phase-8a-activity`, then clean up
   the worktree (`git worktree remove` → `git branch -D` → `git fetch --prune`).
2. Phase 8b onward: every new application service MUST honour the composite
   lock-key discipline captured in `digest-phase-8a.md` — that file is the
   binding spec for 8b–8f.
3. Phase 12: address the two deferred LOWs at the same time the listeners
   wire `link_ping` and `reward_voice_ping_response` into real concurrent
   call paths.

## References

- Issue: #2 (phase status)
- Gate logs: `baton-runner/br-2026-05-25-phase-8/gate-phase-8a-iter-2/`
- Phase-exit digest: `baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md`
- Iter-1 review (HIGH source): `pass-baton/phase-8a/003-2026-05-25-phase-8a-review.md`
- Fix baton under re-review: `pass-baton/phase-8a/004-2026-05-25-guild-composite-lock-key.md`
- ADR mandating the key: `docs/adr/0001-per-guild-markets.md:72`
- Phase-7 digest rule 5: `baton-runner/br-2026-05-25-phase-7/digest-phase-7.md`
- Code: `src/friendex/application/activity_service.py:80-89, 126, 272, 298, 311`,
  `src/friendex/application/voice_ping_service.py:186-195, 201, 215`
- Test: `tests/application/test_activity_service.py:455-560`
