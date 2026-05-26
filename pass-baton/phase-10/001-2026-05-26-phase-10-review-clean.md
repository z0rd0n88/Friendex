# Pass-Baton: Phase 10 review — VERDICT CLEAN

**Date:** 2026-05-26
**Scope:** phase-10
**Branch:** feat/phase-10-embeds
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-26-phase-10
**HEAD:** e04f6c8 chore(arch): regenerate ARCH.md for phase-10 embed builders

## Where things stand

Independent review of the phase-10 implement-unit (2 commits: `5f9c77c` embed
builders + `e04f6c8` ARCH refresh). **Gate green** via
`scripts/gate.sh baton-runner/br-2026-05-26-phase-10/gate-phase-10-iter-1/`
(pytest 616 passed, ruff-check, ruff-format, mypy all PASS). All 8 ACs from
`docs/04-migration-plan.md` §Phase 10 are met and tests are mutation-load-bearing
on the load-bearing claims. 100% branch coverage on the embeds module
(128 stmts / 10 branches, all hit). **No new dependencies** — `pyproject.toml`
and `uv.lock` untouched. Phase 9 invariant preserved: `adapters/tasks/`
contains zero `import discord` (verified); `adapters/discord_bot/embeds.py` is
the only `discord`-importing source file. **VERDICT: CLEAN.**

## Findings by severity

### CRITICAL — none

### HIGH — none

### MEDIUM — none

### LOW

- **L1 — AC8 contract not tight enough to catch `str(error)` mutation.**
  `tests/adapters/discord_bot/test_embeds.py:708` asserts
  `data["description"] == err.user_facing_message`, which IS the verbatim
  contract AC8 calls for. However, because `DomainError.__init__` does
  `super().__init__(user_facing_message)`, today `str(err) ==
  err.user_facing_message` for every existing subclass — so a mutation that
  swaps `description=error.user_facing_message` for `description=str(error)`
  would still pass the test. The work baton already flags this. A
  load-bearing tightening is a custom `DomainError` subclass whose
  `__init__` deliberately calls `super().__init__("DIFFERENT")` and sets
  `self.user_facing_message = "USER FACING"`; the embed must render the
  `user_facing_message`, not the `Exception.args[0]`. **Fix:** add one such
  test in `test_embeds.py::test_build_error_embed_*` group; non-blocking
  but cheap.

- **L2 — `_money` renders negative Decimal as `$-50.00` (sign after `$`).**
  `src/friendex/adapters/discord_bot/embeds.py:85`. Unreachable in current
  product code — every signed value goes through `_signed_money` (which
  yields `-$50.00`); `_money` only sees non-negative balances/prices/costs.
  Leaving it as-is is fine, but worth either (a) asserting `value >= 0` in
  `_money` or (b) routing all currency through `_signed_money`. **Fix:**
  consider in Phase 11 cogs if any new code path passes a signed value
  through `_money`.

### INFO

- **I1 — `build_fund_info_embed` description double-bolds the fund name.**
  `embeds.py:511` reads `f"**{fund.name}**\nManager: …"` while the embed
  title already says `f"Hedge Fund — {fund.name}"`. Repeating the name in
  the body is fine, but the `**…**` wrap means a fund named `**foo**`
  renders as `****foo****` (verified). Cosmetic; cogs will not care, but a
  future fund-name validator (Phase 11) should treat `*`/`_`/`~` as
  reserved.

- **I2 — Fund name is interpolated into title/description unsanitised.**
  `embeds.py:509,511` — a fund named `"<@everyone>"` will render verbatim
  in the embed body. `discord.Embed` does not expose `allowed_mentions`;
  mention suppression happens on the `interaction.response.send_message`
  call. **Action for Phase 11:** every `FundCog.send_*` that echoes a
  user-supplied fund name MUST pass
  `allowed_mentions=discord.AllowedMentions.none()` (and ideally apply a
  length/charset validator at `FundService.create_or_rename`). Not a
  phase-10 finding — embed-layer rendering is correct; the seam belongs
  one layer up.

- **I3 — Empty discord_bot package `__init__.py`.**
  `src/friendex/adapters/discord_bot/__init__.py` is a 1-line empty file;
  the embed builders are imported directly from
  `friendex.adapters.discord_bot.embeds` (tests already do this). No
  re-export needed — keeps the package surface small. Confirming this is
  intentional, not an oversight.

## Mutation-think evidence (per the review checklist)

- **AC3 palette is distinct values, not aliases.** Verified at runtime:
  `[COLOR_SUCCESS.value, COLOR_ERROR.value, COLOR_WARNING.value,
  COLOR_INFO.value, COLOR_NEUTRAL.value] == [3066993, 15158332, 15105570,
  5793266, 3447003]` — 5 distinct ints. The `len({…}) == 5` test would
  fail under a mutation that aliased any two (`discord.Color` hashes by
  `.value`, so equal values collapse in a set). Test is load-bearing.

- **AC7 money formatting is mutation-load-bearing.** `_money` uses
  `f"${value:,.2f}"`. Three sampled tests pin verbatim strings:
  `tests/.../test_embeds.py:125` (`"$1,234.50"`),
  `tests/.../test_embeds.py:137` (`"$11,111.11"`),
  `tests/.../test_embeds.py:285` (`"$1,000.00"`). Dropping `,` → renders
  `"$1234.50"` (fails). Dropping `.2f` → renders `"$1234.5"` (fails).

- **AC8 error description is verbatim, but see L1 above** — the
  `description == user_facing_message` assertion is the right contract;
  it just happens to coincide with `str(error)` today.

- **`build_balance_embed(snapshot)` signature consistent with
  `PortfolioSnapshot`.** All six fields (`cash_balance`, `net_worth`,
  `month_start_net_worth`, `fund_balance`, `long_positions`,
  `short_positions`) are used by the embed; no field the spec implied
  ("UserAccount + prices + HedgeFund" alternative would have provided) is
  lost. Verified against `src/friendex/application/snapshot_models.py:32-50`.

- **`build_fund_info_embed` kw-only is enforced.** `inspect.signature`
  reports all 4 params as `KEYWORD_ONLY`; positional call raises
  `TypeError: build_fund_info_embed() takes 0 positional arguments but 4
  were given`. Defensible — no behavioural gap vs a snapshot DTO since
  `base_apy`/`effective_apy`/`has_penalty` are typed primitives the
  Phase-11 cog computes from `Settings.hedge_fund_base_apy` +
  `compute_effective_apy(...)`.

- **Phase 9 invariant — `adapters/tasks/` has zero `import discord`.**
  `grep -rn "^import discord\|^from discord" src/friendex/adapters/tasks/`
  returns nothing. The only product file importing `discord` is
  `src/friendex/adapters/discord_bot/embeds.py:41`.

- **100% branch coverage genuine.** `pytest --cov-branch` reports 10/10
  branches hit. `_user_mention` numeric vs non-numeric: covered by
  `test_build_price_embed_renders_snowflake_id_as_discord_mention` (numeric)
  and the `"target-1"`/`"user-42"` paths (non-numeric).
  `build_fund_info_embed` `has_penalty` true/false: covered by
  `test_build_fund_info_embed_indicates_active_penalty`. Sell/cover
  `position_after is None` vs non-None: covered.

## Process note (not a finding)

The implement-unit's transcript stalled before it emitted a STATUS line.
This review verifies the **artifact** (commits, tests, gate output), not the
work-unit transcript. The artifact is sound — STATUS-line stall is a
harness/UX concern, not a code defect.

## Next steps

1. **Manager:** mark phase-10 complete on issue #2 and open the PR.
   Two-commit boundary is already split per spec
   (`feat(phase-10): discord embed builders` + `chore(arch):
   regenerate ARCH.md for phase-10 embed builders`) — note the title
   differs slightly from the spec's `feat(discord): embed builders` /
   `test(discord): embed structure` because impl and tests are in one
   commit; cosmetic, not blocking.
2. **Phase 11 (cogs):** consume `COLOR_*` palette + 15 builders. Apply
   `allowed_mentions=discord.AllowedMentions.none()` on every send that
   echoes user-supplied fund names back (see I2). Consider tightening
   L1 with a custom-subclass test if cheap.
3. **Phase-exit digest written** to
   `baton-runner/br-2026-05-26-phase-10/digest-phase-10.md`.

## References

- Spec: `docs/04-migration-plan.md` §Phase 10 (lines 635-658)
- Issue: #2
- Work baton: `pass-baton/phase-10/000-2026-05-26-phase-10-embeds-complete.md`
- Commits: `5f9c77c feat(phase-10): discord embed builders (15 builders + 35 tests)`,
  `e04f6c8 chore(arch): regenerate ARCH.md for phase-10 embed builders`
- Code reviewed:
  - `src/friendex/adapters/discord_bot/embeds.py:1-697`
  - `tests/adapters/discord_bot/test_embeds.py:1-737`
  - `tests/adapters/discord_bot/__init__.py`
- Gate logs:
  `baton-runner/br-2026-05-26-phase-10/gate-phase-10-iter-1/*.log`
- Phase-exit digest:
  `baton-runner/br-2026-05-26-phase-10/digest-phase-10.md`
