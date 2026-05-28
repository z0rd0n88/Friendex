"""Discord bot factory and ``setup_hook`` (Phase 14).

This module exposes a single function, :func:`build_bot`, that constructs the
process-wide :class:`discord.ext.commands.Bot` instance and installs the
Phase 14 ``setup_hook`` lifecycle entry point. It is the **last** seam in the
hexagonal graph — every layer below depends on the bot through narrowly typed
callables (``Callable[[str], TService]`` factories, ``Callable[[], Awaitable
[Iterable[str]]]`` for iter_guild_ids, the notifier callback), so the bot is
materialised exactly once here.

**Why the prefix is :func:`commands.when_mentioned`.** Friendex is a slash-only
bot — the entire command surface is :mod:`discord.app_commands`. The
``command_prefix`` argument is API-required on :class:`commands.Bot` even for
slash-only deployments; :func:`commands.when_mentioned` is the
documentation-sanctioned inert value (the bot only treats a literal mention as
a prefix, which it ignores because no prefix commands exist).

**Why ``Intents.all()``.** Phase 12 listeners need ``message_content``,
``members``, ``voice_states``, and ``reactions``; Phase 12b additionally needs
``guilds`` and ``presences``. Opting into every privileged intent up front
keeps this list short and matches the Phase-14 STATE.md signoff.

**setup_hook contract.** discord.py invokes :meth:`Bot.setup_hook` once, after
login but before the gateway connects, on the bot's own event loop. Phase 14
uses it as the **single** place where:

1. :meth:`Container.build_runners` swaps the Phase-13 placeholders
   (``_empty_guild_ids`` + ``_noop_notifier``) for live ``bot``-backed
   callables, then wraps each task in a
   :class:`~friendex.adapters.tasks.task_runner.TaskRunner`.
2. Every cog and listener is added to the bot
   (via :meth:`Container.register_with`).
3. Every background task is started.
4. The slash-command tree is synced **globally** (so the bot works in every
   server it has been added to). If ``settings.dev_guild_id`` is set, the
   tree is *also* copy-and-synced to that guild for instant propagation —
   global Discord propagation can take up to ~1 hour, which is too slow for
   dev iteration.

**setup_hook attribute assignment.** discord.py sanctions overriding
:meth:`setup_hook` either by subclassing :class:`Bot` or by attribute
assignment. Phase 13 chose attribute assignment for ``bot.tree.on_error``
(mirrors the discord.py docs idiom); Phase 14 follows the same pattern for
``bot.setup_hook`` to keep the seam consistent. mypy flags the assignment as
``method-assign``; the local ignore is the documented form for this pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import structlog
from discord.ext import commands

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from friendex.adapters.container import Container

_log = structlog.get_logger(__name__)


def build_bot(settings: Settings, container: Container) -> commands.Bot:
    """Construct the bot and install the Phase-14 ``setup_hook``.

    Parameters
    ----------
    settings :
        Validated :class:`~friendex.adapters.config.Settings`. Used for
        ``dev_guild_id`` (optional, dev-only) — the discord token itself is
        passed to :meth:`Bot.start` by the entry point, not by this factory.
    container :
        The Phase-13 :class:`~friendex.adapters.container.Container` whose
        cogs/listeners are registered and whose tasks are started.

    Returns
    -------
    commands.Bot
        A ready-to-start bot. The caller (``main.amain``) is responsible for
        :meth:`Bot.start` and engine disposal.
    """
    bot = commands.Bot(
        command_prefix=commands.when_mentioned,
        intents=discord.Intents.all(),
    )

    async def setup_hook() -> None:
        """One-shot Phase-14 lifecycle.

        The order matters:

        1. ``register_with`` adds cogs/listeners so the slash-command tree is
           fully populated before the global sync call.
        2. ``build_runners`` injects the live ``iter_guild_ids`` closure and
           the liquidation notifier, then wraps each task in a
           :class:`~friendex.adapters.tasks.task_runner.TaskRunner` (building
           its ``discord.ext.tasks.Loop`` at that point).
        3. Each runner's ``start()`` is called — runners are valid immediately,
           no dead zone between construction and start.
        4. Global ``bot.tree.sync()``. Then, if ``settings.dev_guild_id`` is
           set, ``copy_global_to`` + per-guild ``sync`` for instant dev
           propagation.
        """
        await container.register_with(bot)
        failed_runners: list[str] = []
        for runner in container.build_runners(bot):
            try:
                runner.start()
            except Exception as exc:
                task_name = type(runner._task).__name__
                _log.error(
                    "task_runner_start_failed",
                    task=task_name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                failed_runners.append(task_name)
        if failed_runners:
            raise RuntimeError(f"Failed to start runners: {', '.join(failed_runners)}")
        await bot.tree.sync()
        if settings.dev_guild_id is not None:
            dev_guild = discord.Object(id=settings.dev_guild_id)
            bot.tree.copy_global_to(guild=dev_guild)
            await bot.tree.sync(guild=dev_guild)

    # discord.py sanctions overriding ``setup_hook`` by direct attribute
    # assignment (mirrors Phase 13's ``bot.tree.on_error`` pattern). mypy
    # flags ``method-assign`` for this — silence it locally; the assignment
    # is the intended customization seam.
    bot.setup_hook = setup_hook  # type: ignore[method-assign]
    return bot
