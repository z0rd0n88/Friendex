"""Phase 16 cutover smoke-test driver.

This module is the **single source of truth** for the production smoke-test
checklist documented in ``docs/runbook-smoke-test.md``.  It defines an
immutable, ordered tuple of :class:`SmokeStep` records and a :func:`main`
entry point that prints a deterministic, numbered checklist for the operator
to walk through against a live staging guild.

The script is intentionally side-effect-free: it neither talks to Discord
nor touches the database.  Running it twice produces byte-identical output,
so the runbook can diff against a captured baseline.

See:

* ``docs/04-migration-plan.md`` §Phase 16 (lines 828-854) — phase contract.
* ``CLAUDE.md`` §Bot Commands — canonical slash command table.
* ``baton-runner/br-2026-05-27-phase-12/`` digests — listener surface.
* ``baton-runner/br-2026-05-25-phase-9/digest-phase-9.md`` — task surface.
* ``baton-runner/br-2026-05-26-phase-11/digest-phase-11c.md`` — ``/fund``
  subcommands; ``/fund invest`` is deferred to Phase 17 and currently
  surfaces ``NotImplementedError`` as an ephemeral user-facing error.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Final, Literal

SmokeCategory = Literal[
    "startup",
    "slash",
    "listener",
    "background",
    "shutdown",
]


@dataclass(frozen=True, slots=True)
class SmokeStep:
    """One row in the Phase 16 cutover smoke-test checklist.

    Attributes:
        id: 1-based monotonically increasing primary key.  Operators refer
            to steps by id in the sign-off table.
        category: Coarse grouping — startup / slash / listener / background
            / shutdown.  Used by the printer to band the output.
        name: Short human-readable label (printed next to the id).
        command: The literal action to perform (slash command string, event
            to trigger, task name to observe, or shell command).
        expected: The observable outcome the operator should record.
    """

    id: int
    category: SmokeCategory
    name: str
    command: str
    expected: str


# ---------------------------------------------------------------------------
# The checklist.  Ordering is contractual: ids are 1-based and strictly
# increasing, and ``main()`` prints in this order.  Do not re-sort.
# ---------------------------------------------------------------------------

STEPS: Final[tuple[SmokeStep, ...]] = (
    # -- startup ------------------------------------------------------------
    SmokeStep(
        id=1,
        category="startup",
        name="Bot login (DISCORD_TOKEN)",
        command="uv run friendex",
        expected=(
            "Process starts and authenticates with Discord using the "
            "DISCORD_TOKEN from .env; no ValidationError is raised at "
            "boot."
        ),
    ),
    SmokeStep(
        id=2,
        category="startup",
        name="Command tree sync (global + optional DEV_GUILD_ID)",
        command="observe setup_hook in logs",
        expected=(
            "Structured log shows global tree.sync() ran; if DEV_GUILD_ID "
            "is set, the global tree is also copied to that guild and "
            "synced for instant availability."
        ),
    ),
    SmokeStep(
        id=3,
        category="startup",
        name="Structured-log 'ready' line",
        command="grep ready in stdout",
        expected=(
            "A single 'ready' log line is emitted after on_ready fires, "
            "with bot user id and guild count; no ERROR-level lines "
            "precede it."
        ),
    ),
    # -- slash commands ----------------------------------------------------
    SmokeStep(
        id=4,
        category="slash",
        name="/balance — cash + portfolio summary",
        command="/balance",
        expected=(
            "Invoke /balance: ephemeral reply shows current cash and a "
            "per-holding row for each long/short position; only the "
            "invoker sees it."
        ),
    ),
    SmokeStep(
        id=5,
        category="slash",
        name="/daily — claim daily $500 reward",
        command="/daily",
        expected=(
            "Invoke /daily: public reply confirms the daily_reward "
            "credit ($500); second invocation the same UTC day raises "
            "AlreadyClaimedToday surfaced as an ephemeral DomainError "
            "via the Phase 13 error handler."
        ),
    ),
    SmokeStep(
        id=6,
        category="slash",
        name="/price — look up a stock price",
        command="/price",
        expected=(
            "Invoke /price [user]: ephemeral reply shows the current "
            "price for the target user (defaults to the invoker when no "
            "user is supplied)."
        ),
    ),
    SmokeStep(
        id=7,
        category="slash",
        name="/mystock — your own stock stats",
        command="/mystock",
        expected=(
            "Invoke /mystock: ephemeral reply shows the invoker's "
            "current price, activity-tick score, and outstanding shares."
        ),
    ),
    SmokeStep(
        id=8,
        category="slash",
        name="/buy — open a long position",
        command="/buy",
        expected=(
            "Invoke /buy <user> <shares>: public reply confirms the "
            "buy; price_impact_k bump applied; InsufficientCash / target "
            "opt-out paths surface as ephemeral DomainError via the "
            "error handler."
        ),
    ),
    SmokeStep(
        id=9,
        category="slash",
        name="/sell — close a long position",
        command="/sell",
        expected=(
            "Invoke /sell <user> <shares>: public reply confirms the "
            "sell; cash credited at current price; price_impact_k "
            "downshift applied."
        ),
    ),
    SmokeStep(
        id=10,
        category="slash",
        name="/short — open a short position",
        command="/short",
        expected=(
            "Invoke /short <user> <shares>: public reply confirms the "
            "short open; 15-min trade_cooldown and 30-min short_freeze "
            "enforced on subsequent attempts."
        ),
    ),
    SmokeStep(
        id=11,
        category="slash",
        name="/cover — close a short position",
        command="/cover",
        expected=(
            "Invoke /cover <user> <shares>: public reply confirms the "
            "cover; P&L equals (entry - current) * shares; "
            "trade_cooldown_seconds applies."
        ),
    ),
    SmokeStep(
        id=12,
        category="slash",
        name="/portfolio — full portfolio view",
        command="/portfolio",
        expected=(
            "Invoke /portfolio [user]: ephemeral reply shows the target "
            "user's full long+short position list with mark-to-market "
            "values; defaults to invoker."
        ),
    ),
    SmokeStep(
        id=13,
        category="slash",
        name="/trending — top movers leaderboard",
        command="/trending",
        expected=(
            "Invoke /trending: public reply shows the top movers "
            "leaderboard for the current guild (per-guild market per "
            "ADR-0001)."
        ),
    ),
    SmokeStep(
        id=14,
        category="slash",
        name="/mystats — personal activity stats",
        command="/mystats",
        expected=(
            "Invoke /mystats: ephemeral reply shows the invoker's "
            "accumulated activity buckets (text, media, reactions, "
            "replies, voice minutes)."
        ),
    ),
    SmokeStep(
        id=15,
        category="slash",
        name="/optin — consent to be a tradeable stock",
        command="/optin",
        expected=(
            "Invoke /optin: ephemeral reply confirms the invoker is now "
            "tradeable; other users can /buy or /short them."
        ),
    ),
    SmokeStep(
        id=16,
        category="slash",
        name="/optout — withdraw consent to be tradeable",
        command="/optout",
        expected=(
            "Invoke /optout: ephemeral reply confirms the invoker is no "
            "longer tradeable; subsequent /buy or /short attempts "
            "against them surface a NotOptedIn ephemeral DomainError."
        ),
    ),
    SmokeStep(
        id=17,
        category="slash",
        name="/fund create — create a hedge fund",
        command="/fund create",
        expected=(
            "Invoke /fund create <name>: public reply confirms the new "
            "fund; manager set to invoker; duplicate names surface as "
            "ephemeral DomainError."
        ),
    ),
    SmokeStep(
        id=18,
        category="slash",
        name="/fund invest — deferred to Phase 17",
        command="/fund invest",
        expected=(
            "Invoke /fund invest <fund> <amount>: the cog surfaces a "
            "NotImplementedError as an ephemeral user-facing error "
            "(deferred to Phase 17 per Phase 8e Open-Q5 + Phase 11c "
            "digest); no state is mutated."
        ),
    ),
    SmokeStep(
        id=19,
        category="slash",
        name="/fund withdraw — withdraw from a hedge fund",
        command="/fund withdraw",
        expected=(
            "Invoke /fund withdraw <fund> <amount>: public reply "
            "confirms the withdrawal; early_withdraw_penalty (5%) "
            "applied when within penalty_duration_days window."
        ),
    ),
    SmokeStep(
        id=20,
        category="slash",
        name="/fund info — show hedge fund stats",
        command="/fund info",
        expected=(
            "Invoke /fund info <fund>: ephemeral reply shows the fund's "
            "AUM, base APY, and investor list."
        ),
    ),
    # -- listener events ---------------------------------------------------
    SmokeStep(
        id=21,
        category="listener",
        name="on_message — text + media activity credit",
        command="post a text message; post a message with an attachment",
        expected=(
            "ActivityService increments the text bucket for plain "
            "messages and the media bucket for messages with attachments "
            "(per Phase 12a M1 / on_message handler)."
        ),
    ),
    SmokeStep(
        id=22,
        category="listener",
        name="on_reaction_add — reaction credit + bot-reactor skip",
        command="react to a user's message (human reactor, then bot)",
        expected=(
            "Reaction from a human reactor credits the message author's "
            "reaction bucket; reactions from bots are skipped per "
            "Phase 12a M1 contract — no bucket movement."
        ),
    ),
    SmokeStep(
        id=23,
        category="listener",
        name="on_voice_state_update — VC join/leave + ping-response timing",
        command="join a voice channel; leave a voice channel",
        expected=(
            "VC join starts a session; leave finalises voice-minutes "
            "bucket; ping responders inside the configured window "
            "earn the first-N join boost / stay boost per Phase 12b "
            "CF-2."
        ),
    ),
    SmokeStep(
        id=24,
        category="listener",
        name="on_member_update — timeout + ban discipline penalty",
        command='timeout a member; ban a member (kind literals "timeout"/"ban")',
        expected=(
            "Discipline penalty applies a 17% price drop on both "
            'kinds (literal strings "timeout" and "ban" per Phase 12a '
            "M3); the event is logged structured."
        ),
    ),
    SmokeStep(
        id=25,
        category="listener",
        name="opt-out blocks tradeability",
        command="/optout then attempt /buy <opted-out user>",
        expected=(
            "After /optout, /buy / /short against the opted-out user "
            "surface NotOptedIn as an ephemeral DomainError; on_message "
            "activity from the opted-out user does not move their "
            "price (Phase 12a opt-out gate)."
        ),
    ),
    # -- background tasks --------------------------------------------------
    SmokeStep(
        id=26,
        category="background",
        name="15-min activity tick",
        command="wait one activity_tick_minutes cycle (default 15 min)",
        expected=(
            "ActivityTickService applies ΔP = K · ln(1 + score) with "
            "activity_tick_k = 0.3 per Phase 8-followup chore B; buckets "
            "are NOT reset by the tick itself."
        ),
    ),
    SmokeStep(
        id=27,
        category="background",
        name="Short liquidation sweep at 1.5x entry",
        command="wait one ShortLiquidationTask cycle",
        expected=(
            "Open shorts whose current price >= liquidation_threshold "
            "(1.5) x entry_price are auto-covered (Phase 8f F1/F2/F3); "
            "the affected short owner sees their position closed at the "
            "mark."
        ),
    ),
    SmokeStep(
        id=28,
        category="background",
        name="Daily streak rollover / month_start net-worth capture",
        command="cross UTC midnight (DailyResetTask + MonthlyRolloverTask)",
        expected=(
            "DailyResetTask advances streak state and resets daily "
            "activity buckets; on day==1 hour==0 the MonthlyRolloverTask "
            "captures month_start net worth THEN accrues fund APY in "
            "that order (Phase 9 digest contract)."
        ),
    ),
    SmokeStep(
        id=29,
        category="background",
        name="Hedge fund APY accrual",
        command="wait one MonthlyRolloverTask cycle on day 1 / hour 0",
        expected=(
            "FundService.accrue_apy applies hedge_fund_base_apy (0.15) "
            "to each fund's AUM; ordering is portfolio capture FIRST, "
            "then accrual (Phase 9 §6)."
        ),
    ),
    SmokeStep(
        id=30,
        category="background",
        name="Early-withdrawal penalty decay",
        command="wait the penalty_duration_days window",
        expected=(
            "Outstanding fund penalties created by /fund withdraw decay "
            "off after penalty_duration_days (default 14); decayed "
            "penalties no longer affect subsequent withdrawals."
        ),
    ),
    SmokeStep(
        id=31,
        category="background",
        name="VC extra-boost (1.03x per cycle)",
        command="stay in voice past the first-N join boost",
        expected=(
            "PriceTickService.vc_boost_tick applies "
            "vc_extra_boost_multiplier = 1.03 per "
            "vc_extra_boost_interval_seconds cycle to retained "
            "responders beyond the one-time first-N join boost."
        ),
    ),
    # -- shutdown ----------------------------------------------------------
    SmokeStep(
        id=32,
        category="shutdown",
        name="Graceful close (bot.close() drains task loops)",
        command="Ctrl-C in the terminal running `uv run friendex`",
        expected=(
            "SIGINT triggers bot.close(); background tasks stop on "
            "their next iteration; no ERROR-level log lines are emitted "
            "during teardown; the process exits 0."
        ),
    ),
)


# Category banner ordering (matches STEPS ordering; pinned here so the
# printer never inverts a section).
_CATEGORY_ORDER: Final[tuple[SmokeCategory, ...]] = (
    "startup",
    "slash",
    "listener",
    "background",
    "shutdown",
)

_CATEGORY_TITLES: Final[dict[SmokeCategory, str]] = {
    "startup": "Startup",
    "slash": "Slash commands",
    "listener": "Listener events",
    "background": "Background tasks",
    "shutdown": "Shutdown",
}


def _format_step(step: SmokeStep) -> str:
    """Render one step as a numbered block.

    The leading ``"Step <id>."`` header is load-bearing — the test
    ``test_main_prints_steps_in_strict_id_order`` parses it to recover
    the printed id sequence.  Do not change the format without updating
    the test.
    """
    return (
        f"Step {step.id}. {step.name}  ({step.category})\n"
        f"    command:  {step.command}\n"
        f"    expected: {step.expected}\n"
    )


def main() -> int:
    """Print the deterministic, numbered Phase 16 smoke-test checklist.

    Returns:
        ``0`` always — the script has no failure mode; it is a printer.
    """
    print("Friendex Phase 16 — Production Smoke Test Checklist")
    print("=" * 60)
    print(
        "Run each step against the staging guild and record pass/fail in\n"
        "the sign-off table in docs/runbook-smoke-test.md."
    )
    print()

    for category in _CATEGORY_ORDER:
        category_steps = [s for s in STEPS if s.category == category]
        if not category_steps:
            continue
        print(f"-- {_CATEGORY_TITLES[category]} --")
        print()
        for step in category_steps:
            print(_format_step(step))
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI shim
    sys.exit(main())
