# Pass-Baton: Phase 12a listeners foundation — complete

**Date:** 2026-05-27
**Scope:** phase-12a
**Branch:** feat/phase-12a-listeners-simple
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-12
**HEAD:** ea4b7b2 chore(phase-11): apply review follow-ups (LOWs, non-blocking) (#59)

## Where things stand

All eight phase-12a acceptance criteria (A1–A8) are GREEN. Listener
package foundation is in: `ReactionListener` + `MemberListener` cogs
wired with per-guild service factories (Phase 9 convention), shared
listener test conftest exposing `fake_message` / `fake_member` /
`fake_voice_state` event factories plus AsyncMock stand-ins for every
Phase 12 service, and TDD-built mutation-hardened tests. 21 new tests
total (7 reaction, 14 member). Working tree contains only new files +
`baton-runner/br-2026-05-27-phase-12/` and `baton-pass/phase-12a/`
untracked; nothing modified outside the slice. Ready for review or
commit + PR.

## Verification gates (all green)

- `uv run pytest tests/adapters/discord_bot/listeners/ --cov=src/friendex/adapters/discord_bot/listeners --cov-fail-under=80` → **21 passed**, 100% line+branch coverage on the two listener files + `__init__.py`.
- `uv run pytest` (full suite) → **724 passed**, no regressions.
- `uv run ruff check .` → clean.
- `uv run ruff format --check .` → 135 files already formatted.
- `uv run mypy src/friendex` → no issues found in 63 source files.

## Files created

- `src/friendex/adapters/discord_bot/listeners/reaction_listener.py` (A4)
- `src/friendex/adapters/discord_bot/listeners/member_listener.py` (A5)
- `tests/adapters/discord_bot/listeners/__init__.py` (A2)
- `tests/adapters/discord_bot/listeners/conftest.py` (A3 — also pre-seeds 12b's voice/ping/trading/fund/daily/portfolio/stats service fixtures)
- `tests/adapters/discord_bot/listeners/test_reaction_listener.py`
- `tests/adapters/discord_bot/listeners/test_member_listener.py`

`src/friendex/adapters/discord_bot/listeners/__init__.py` (A1) already
existed empty pre-slice; left untouched.

## Acceptance criteria coverage

- **A1/A2** — package `__init__.py`s present.
- **A3** — conftest exposes `fake_message`, `fake_member`,
  `fake_voice_state` matching the signatures in STATE.md, plus all
  `{activity,voice_ping,discipline,trading,fund,daily,portfolio,stats}_service`
  fixtures and matching `_factory` fixtures.
- **A4** — `ReactionListener(commands.Cog)` registers `on_reaction_add`;
  delegates to `ActivityService.record_reaction(user_id=str(user.id))`;
  self-reaction silently dropped (`user.id == reaction.message.author.id`);
  bot reactor silently dropped (`user.bot`); per-guild
  `activity_service_factory: Callable[[str], ActivityService]` ctor;
  DM-narrowing returns silently when `reaction.message.guild is None`
  (Phase 11 `guild_id_of` rule restated for listeners — see note below).
- **A5** — `MemberListener(commands.Cog)` registers both
  `on_member_update` and `on_member_ban`; timeout penalty fires ONLY on
  `before.timed_out_until is None and after.timed_out_until is not None`;
  extensions, un-timeouts, and `None → None` all no-op; ban always fires
  `"ban"`; per-guild
  `discipline_service_factory: Callable[[str], DisciplineService]` ctor.
- **A6** mutation-hardening:
  - Bot-skip drop → `test_on_reaction_add_skips_bot_reactor` fails.
  - None→set guard drop → `test_on_member_update_does_not_fire_on_extension`,
    `test_on_member_update_does_not_fire_on_un_timeout`,
    `test_on_member_update_does_not_fire_on_none_to_none` all fail
    (positional coverage of every disallowed transition).
  - `"timeout"`↔`"ban"` flip → `test_on_member_update_passes_kind_timeout_literal`
    and `test_on_member_ban_passes_kind_ban_literal` each pin the literal
    via positional-arg unpack of `mock_calls`; exactly one breaks under a
    flip.
- **A7** — no `try/except` in either listener;
  `test_on_reaction_add_propagates_domain_error`,
  `test_on_member_update_propagates_domain_error`,
  `test_on_member_ban_propagates_domain_error` use `pytest.raises` to pin
  the policy. `OptedOut` chosen for reaction (concrete subclass);
  `DomainError` base used for member tests.
- **A8** — see "Verification gates" above; ≥80% bar met (100% achieved).

## Notes for the reviewer / Phase 12b

- **DM-narrowing in `reaction_listener.py`:** `discord.Message.guild` is
  typed `Guild | None`, so `str(reaction.message.guild.id)` fails mypy.
  Resolved with an early-return guard mirroring the cogs `guild_id_of`
  assert (`DM reactions are dropped here — the bot has no economy
  outside guilds`). Documented inline. Phase 12b will face the same
  shape on `message.guild`; reuse the same pattern (or factor into a
  shared `_guild_id_of_event` helper if the duplication grates).
- **`Member.guild`** is `Guild`, not `Guild | None`, so
  `member_listener.py` needs no narrowing.
- **`discipline_service.apply_discipline_penalty(user_id, kind)`** is
  called positionally (matches the
  `DisciplineService.apply_discipline_penalty(self, user_id, reason)`
  signature from Phase 8f digest). Tests assert via `mock_calls[0]`
  positional unpack rather than `.assert_awaited_once_with(kwargs)`.
- **Conftest pre-seeds 12b fixtures** per the brief — voice_ping,
  trading, fund, daily, portfolio, stats AsyncMocks + factories all
  present and untouched by 12a tests.
- **`commands.Cog.get_listeners` is an instance method** in our
  installed `discord.py` — `ReactionListener.get_listeners()` raises
  `TypeError`; tests instantiate the listener first. Same in
  `test_member_listener`.

## Next steps

1. Reviewer (or you) runs the verification gates from the worktree:
   ```bash
   cd /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-12
   uv run pytest tests/adapters/discord_bot/listeners/ --cov=src/friendex/adapters/discord_bot/listeners --cov-fail-under=80
   uv run ruff check . && uv run ruff format --check .
   uv run mypy src/friendex
   ```
2. Manager owns git: stage the listed files + the
   `baton-runner/br-2026-05-27-phase-12/` and `baton-pass/phase-12a/`
   subdirs, commit on `feat/phase-12a-listeners-simple`, push and open
   the PR per `.github/pull_request_template.md` (Refs #2).
3. After 12a merges, kick 12b on `feat/phase-12b-listeners-voice` (base
   `feat/phase-12a-listeners-simple`); acceptance criteria B1–B8 already
   in STATE.md.

## Open questions / risks

- None blocking. The DM-narrowing decision is a forced move (mypy) and
  matches the cogs `guild_id_of` policy — flagged here so 12b adopts the
  same shape.

## References

- Spec: `docs/04-migration-plan.md` §Phase 12 (lines 702–732)
- STATE: `baton-runner/br-2026-05-27-phase-12/STATE.md` (phase-12a slice)
- Cog conventions: `baton-runner/br-2026-05-26-phase-11/digest-phase-11a.md`
- DisciplineService contract: `baton-runner/br-2026-05-25-phase-8/digest-phase-8f.md`
- Composite lock keys: `baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md`
- Predecessor: `baton-pass/phase-12a/000-2026-05-27-phase-12a-kickoff.md`
- Issue #2 (phase tracker)
