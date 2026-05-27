# Phase 13 exit digest — Error Handler & Container Wiring

Approved 2026-05-27 (baton 002). Branch `feat/phase-13-container`, HEAD `e1f86f1`.

## Public surface added

- `adapters/discord_bot/error_handler.py::register_error_handler(bot, settings) -> None` —
  installs on `bot.tree.on_error`. Unwraps `CommandInvokeError` recursively.
  DomainError → red embed via `build_error_embed`; PersistenceError → log ERROR
  (`extra={operation, detail}`) + ephemeral `"Internal error, please try again"`;
  fallthrough → log CRITICAL with explicit `exc_info=(type, value, tb)` + ephemeral
  `"Unexpected error"`. Every send passes `allowed_mentions=AllowedMentions.none()`.
- `adapters/container.py::Container(settings, sessionmaker)` — owns 6 repos,
  1 `LockManager`, lazy per-guild voice/ping store dicts, 10 per-guild service
  factories (`Callable[[str], TService]`), 8 tasks (not started), 7 cogs, 4 listeners.
  Exposes `.cogs`, `.listeners`, `.tasks`, the `*_service_factory` callables,
  `voice_session_store_factory`, and `async register_with(bot)` (calls add_cog × 11
  then `register_error_handler`).
- `main.py::amain() -> None` — loads `Settings()`, configures logging, builds engine +
  `async_sessionmaker(expire_on_commit=False)`, constructs Container, raises
  `NotImplementedError("Phase 14: build_bot + bot.start")`. `finally`: `engine.dispose()`.
- `main.py::main() -> None` — `asyncio.run(amain())`. Re-exported from
  `friendex.__init__`; `friendex.__main__` is the `python -m friendex` shim.

## Phase 14 MUST honour

1. Fill the `NotImplementedError` seam: build bot, `await container.register_with(bot)`,
   start the 8 tasks, then `await bot.start(settings.discord_token)`. Remove the `raise`
   line; `engine.dispose()` in `finally` already covers shutdown.
2. Replace `_empty_guild_ids` with `lambda: (str(g.id) for g in bot.guilds)` (or async
   equivalent) — either widen `Container.__init__` to accept it and rebuild tasks, or
   add a `bind_runtime(bot)` method that swaps each task's `iter_guild_ids`
   post-construction. Test pins "8 built, none started" — both are compatible.
3. Replace `_noop_notifier` with the real Discord-embed dispatcher for
   `LiquidationTask`. Signature: `Callable[[LiquidationEvent], Awaitable[None]]`.
4. Keep direct attribute assignment on `bot.tree.on_error` (the `method-assign`
   ignore) — do not switch to `@bot.tree.error`; tests pin the attribute form.
5. `AllowedMentions.none()` on every reply path remains the rule.
6. Type-ignore budget: 3 (method-assign, call-arg, attr-defined in tests). No more.
7. `_make_liquidation_factory` builds a fresh `TradingService` per call; the shared
   `LockManager` (keyed `f"{guild}:{user}"`) still serialises `_cover_internal`
   correctly — asymmetry to know, not fix.

Tests pinned: 7 in `test_error_handler.py`, 13 in `test_container.py`; suite 771 passed.
Carry-forward: 1 new LOW (M2 vacuous-mutation hazard in `test_error_handler.py:119`,
fix recipe in baton 002); all prior carry-forwards untouched.
