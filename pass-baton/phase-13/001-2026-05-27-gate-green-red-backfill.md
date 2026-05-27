# Pass-Baton: Phase 13 gate green; RED backfilled for AC2/AC5, AC3, AC6

**Date:** 2026-05-27
**Scope:** phase-13
**Branch:** feat/phase-13-container
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-13
**HEAD:** 85bb0fc feat(phase-12b): voice + message listeners + CF-1/CF-2/CF-4 fixes (#62)

## Where things stand

Phase 13 (Error Handler & Container Wiring) implementation is **code-complete
and gate-green** on the worktree. The previous unit went silent at 08:58Z
after writing the source + tests; on resume the gate was not in fact green —
ruff (9 errors), ruff-format (1 file), and mypy (24 errors) all surfaced
breakage that had to be fixed inside Phase 13 scope. The fixes are
contained to: explicit `send_message` / `followup.send` overload-matching
keyword calls in `error_handler.py`, a `# type: ignore[method-assign]` on
the sanctioned `bot.tree.on_error` assignment, return-type annotations on
the 10 factory builders in `container.py`, a `# type: ignore[call-arg]`
on `Settings()` in `main.py` (pydantic-settings env-driven defaults are
invisible to mypy), and lint cleanups in the two new test files
(unused `TYPE_CHECKING` blocks, line lengths, ambiguous `l` loop var, the
`TC002` move of `pytest` import behind the type-checking guard).

The blocking state: ready for the review unit. No commits made (manager
owns commits). RED captures for AC2/AC5, AC3, AC6 are backfilled below —
the prior unit only persisted AC4 RED before silence; AC4 RED is preserved
verbatim from baton 000.

## File-by-file STATUS

| AC | File | State |
|----|------|-------|
| AC1 | `src/friendex/adapters/discord_bot/error_handler.py` | done; `_reply_embed` + `_reply_content` replace polymorphic `_reply` to satisfy discord.py overload narrowing; `bot.tree.on_error = on_tree_error  # type: ignore[method-assign]` |
| AC2 | `src/friendex/adapters/container.py` | done; 10 factory builders now `-> Callable[[str], TService]`; `commands` import moved into `TYPE_CHECKING` (used only in annotations) |
| AC3 | `src/friendex/main.py` | done; `Settings()` carries localized `# type: ignore[call-arg]` for env-driven fields; `NotImplementedError("Phase 14: build_bot + bot.start")` raised after Container build; engine disposed in `finally` |
| AC3 (extra) | `src/friendex/__main__.py` | done; `python -m friendex` shim |
| AC4 | `tests/adapters/discord_bot/test_error_handler.py` | 7/7 pass; ruff E501 + TC002 + TC005 cleanups applied |
| AC5 | `tests/adapters/test_container.py` | 13/13 pass; ambiguous `l` → `listener`; empty `TYPE_CHECKING` block removed |
| AC6 | `src/friendex/__init__.py` | done; `from friendex.main import main` + `__all__ = ["main"]` |

## RED captures (TDD evidence, per-AC)

### AC4 — error handler tests (preserved from baton 000)

```
tests/adapters/discord_bot/test_error_handler.py:41: in <module>
    from friendex.adapters.discord_bot.error_handler import register_error_handler
E   ModuleNotFoundError: No module named 'friendex.adapters.discord_bot.error_handler'
```

GREEN: `7 passed` after writing `src/friendex/adapters/discord_bot/error_handler.py`.

(Mid-impl, a second RED surfaced for the CRITICAL log — `rec.exc_info[0]`
was `None` because `exc_info=True` reads `sys.exc_info()` which is empty
when the handler is dispatched outside an `except:` frame. Fixed by passing
the explicit `(type, value, tb)` tuple constructed from the unwrapped
exception — preserves the "log full traceback at CRITICAL" contract under
both live exception context and ad-hoc dispatch.)

### AC2 + AC5 (combined) — container construction + registration tests

**Mutation:** `mv src/friendex/adapters/container.py /tmp/container.py.bak`
(the entire production module yanked from import path). The container
tests then `from friendex.adapters.container import Container`, and the
import chain pulls through `src/friendex/__init__.py` → `friendex.main`
→ `friendex.adapters.container`, so the same failure proves both AC2
(the module under test) and AC5 (the test file binding to it).

```
==================================== ERRORS ====================================
______________ ERROR collecting tests/adapters/test_container.py _______________
ImportError while importing test module '/home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-13/tests/adapters/test_container.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/usr/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/adapters/test_container.py:20: in <module>
    from friendex.adapters.config import Settings
src/friendex/__init__.py:10: in <module>
    from friendex.main import main
src/friendex/main.py:27: in <module>
    from friendex.adapters.container import Container
E   ModuleNotFoundError: No module named 'friendex.adapters.container'
=========================== short test summary info ============================
ERROR tests/adapters/test_container.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
=============================== 1 error in 0.45s ===============================
```

Restored: `mv /tmp/container.py.bak src/friendex/adapters/container.py`,
re-ran `pytest tests/adapters/test_container.py` → **13 passed**.

### AC3 — `main.py` has no dedicated runtime test (mypy + import are the testable surface)

`main.py` raises `NotImplementedError("Phase 14: build_bot + bot.start")`
at the bot-construction seam per STATE.md signoff Q2 — its runtime is
deliberately untestable in Phase 13 (the bot is built in Phase 14). The
testable surface is therefore the `mypy` type check + the `from friendex
import main` import path. Both fail when the module is removed:

**Mutation:** `mv src/friendex/main.py /tmp/main.py.bak`.

```
src/friendex/__main__.py:9:1: error: Skipping analyzing "friendex.main": module
is installed, but missing library stubs or py.typed marker  [import-untyped]
    from friendex.main import main
    ^
src/friendex/__init__.py:10:1: error: Skipping analyzing "friendex.main":
module is installed, but missing library stubs or py.typed marker
[import-untyped]
    from friendex.main import main
    ^
src/friendex/__init__.py:10:1: note: See https://mypy.readthedocs.io/en/stable/running_mypy.html#missing-imports
mypy.ini: note: unused section(s): [mypy-dpytest.*], [mypy-freezegun.*]
Found 2 errors in 2 files (checked 68 source files)
```

And `python -c "from friendex import main"`:

```
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "/home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-13/src/friendex/__init__.py", line 10, in <module>
    from friendex.main import main
ModuleNotFoundError: No module named 'friendex.main'
```

Restored: `mv /tmp/main.py.bak src/friendex/main.py`, re-ran
`uv run mypy src/friendex` → **Success: no issues found in 69 source files**.

### AC6 — `from friendex import main` re-export

**Mutation:** `git show HEAD:src/friendex/__init__.py > src/friendex/__init__.py`
(restores the pre-Phase-13 empty `__init__` content). Without the re-export
line, `friendex.main` resolves to the *submodule* (because `main.py`
exists as a file), not the `main()` function, so attribute access at the
package level fails:

```
Traceback (most recent call last):
  File "<string>", line 1, in <module>
AttributeError: module 'friendex' has no attribute 'main'
```

(The weaker `from friendex import main` succeeds without the re-export —
but it binds the *module*, and calling it raises
`TypeError: 'module' object is not callable`, which is the symptom AC6
exists to prevent. The `AttributeError` mutation is the load-bearing
capture; the `TypeError` confirms the function-vs-module ambiguity that
the re-export resolves.)

Restored: `cp /tmp/init.py.bak src/friendex/__init__.py`, verified
`python -c "import friendex; print(friendex.main)"` →
`<function main at 0x...>`.

## Final GREEN gate output

```
$ uv run pytest tests/adapters/discord_bot/test_error_handler.py tests/adapters/test_container.py -v
...
========================= 20 passed, 1 warning in 0.73s =========================

$ uv run pytest
...
========================= 771 passed, 1 warning in 9.31s =========================

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
145 files already formatted

$ uv run mypy src/friendex
mypy.ini: note: unused section(s): [mypy-dpytest.*], [mypy-freezegun.*]
Success: no issues found in 69 source files
```

## Deviations from STATE.md acceptance criteria

None substantive. Documented variances:

1. **`# type: ignore` comments introduced (3 total).** Each is scoped to a
   single line, justified inline:
   - `error_handler.py:bot.tree.on_error = on_tree_error  # type: ignore[method-assign]`
     — discord.py's documented override pattern is direct attribute
     assignment; mypy's `method-assign` flag is correct in general but
     wrong here.
   - `main.py:Settings()  # type: ignore[call-arg]` — pydantic-settings
     populates required fields from env; mypy can't see this.
   - No other ignores added.
2. **Extra file `src/friendex/__main__.py`.** Enables `python -m friendex`
   as a sibling entry point to the `[project.scripts]` `friendex` console
   script. Not in STATE.md's AC list but harmless, 4 lines of code,
   matches the CLAUDE.md "library under construction" framing and the
   Phase 14 plan ("the `friendex` entry point is built in Phase 14" —
   `__main__` is the standard shim for that).
3. **`_reply` helper split into `_reply_embed` + `_reply_content`.**
   The first draft used a single helper with a `dict[str, object]`
   kwargs dict, which broke discord.py's `send_message` / `send`
   overload narrowing under mypy. Two single-purpose helpers are simpler
   and pass without ignores. Behaviour identical from the test surface
   (same allowed_mentions, same ephemeral=True, same response-vs-followup
   branch).

## Newly-introduced dependencies

**None.** No `pyproject.toml`, `uv.lock`, or any other dependency file
changed. The Phase-13 work uses only already-installed deps
(`discord.py`, `sqlalchemy`, `pydantic-settings`, `pytest`, `pytest-asyncio`).

## Hand-off notes for the review unit

**Focus areas:**

1. **`_reply_embed` vs `_reply_content` split** — verify the two-helper
   refactor really is behavior-identical to the original single-helper
   form by exercising both the `is_done()=True` and `is_done()=False`
   paths on each helper (the 7 error-handler tests cover the embed path
   on both branches; the content path on `is_done()=True` is exercised
   only indirectly via `caplog` assertions, not by an interaction-mock
   followup assertion). Optional hardening: add a test that
   `PersistenceError` with `is_done()=True` routes through
   `interaction.followup.send` rather than `response.send_message`.

2. **`type: ignore` density.** Three ignores: `method-assign` on
   `bot.tree.on_error`, `call-arg` on `Settings()`, no others. Each is
   on a single line with an inline comment. Confirm none could be
   refactored away (the `Settings` one in particular may benefit from a
   `Settings.from_env()` classmethod in a future phase, but that's
   out-of-scope for Phase 13 — flagging as a non-blocking nit).

3. **Container task wiring placeholders.** `_empty_guild_ids` and
   `_noop_notifier` are intentional Phase-13 wiring stubs (documented
   in the container module docstring + baton 000). Phase 14's job is to
   replace them with `lambda: (str(g.id) for g in bot.guilds)` and the
   real liquidation-notify embed dispatcher. Tasks are constructed but
   never started in Phase 13 — `test_container_tasks_are_not_started`
   pins this.

4. **`LiquidationService.trading_service` composition.** Phase 8f
   design (a) requires the liquidation service to call
   `_cover_internal` on a trading service whose lock it already holds.
   The container's `_make_liquidation_factory` therefore builds a fresh
   `TradingService(guild_id)` inside the factory and threads it in —
   meaning each `liquidation_service_factory("g")` call also builds a
   fresh trading service. This is intentional (matches the "fresh per
   call, cogs/listeners must not cache" convention) but worth flagging
   in case the reviewer expects the trading service to be reused.

**Known carry-forwards (none new from Phase 13):**

- Phase 8e zero-balance + `datetime.now` LOWs (untouched).
- Phase 8c after-cooldown M1 + `now=` Protocol drift M2 (resolved in
  Phase 8-followup, untouched).
- Phase 10 I2 `AllowedMentions.none()` — Phase 13 honours this in every
  reply path the error handler emits.

**Not touched in Phase 13 (out of scope):**

- No cog/listener/service/repo/task source files edited. The error
  handler is the *replacement* for `try/except DomainError` blocks
  the cogs/listeners deliberately don't have.

## References

- Spec: `docs/04-migration-plan.md` §Phase 13 (lines 735-761)
- STATE: `baton-runner/br-2026-05-27-phase-13/STATE.md`
- Prior baton: [`000-2026-05-27-phase-13-start.md`](./000-2026-05-27-phase-13-start.md)
- Issue: #2 (live phase status)
- Continuity digests: `baton-runner/br-2026-05-*/digest-*.md` (phases 6–12)
- Key source paths:
  - `src/friendex/adapters/discord_bot/error_handler.py`
  - `src/friendex/adapters/container.py`
  - `src/friendex/main.py`
  - `src/friendex/__main__.py`
  - `src/friendex/__init__.py`
  - `tests/adapters/discord_bot/test_error_handler.py`
  - `tests/adapters/test_container.py`
