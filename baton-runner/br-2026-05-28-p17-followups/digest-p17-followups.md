# Phase 17 follow-ups — digest

**Scope:** three Phase-17 review carry-forwards bundled (F1 dict-identity
pin, F2 consume→ack ordering pin, F3 ``discord.Forbidden`` DM-fallback log).

## Public surface added

**Tests (3):**

- ``tests/application/test_fund_service.py::test_invest_does_not_mutate_input_investors_dict``
  — pins ``FundService.invest`` clones ``fund.investors`` before mutating.
- ``tests/adapters/discord_bot/cogs/test_account_cog.py::test_optin_consumes_intro_before_acking``
  — pins ``activity_service.opt_in_and_consume_intro`` runs BEFORE any
  Discord send in ``AccountCog.optin`` (via ``parent.attach_mock``).
- ``tests/adapters/discord_bot/cogs/test_account_cog.py::test_optin_logs_when_intro_dm_is_forbidden``
  — pins the F3 log fires (count=1, level=INFO, keys=user_id+guild_id).

**Product surface (1):**

- ``account_cog`` module logger ``logger = logging.getLogger(__name__)``.
- One ``logger.info("account.optin_intro_dm_forbidden", extra={"user_id":
  str(interaction.user.id), "guild_id": guild_id_of(interaction)})`` call
  inside the pre-existing ``except discord.Forbidden`` branch of
  ``AccountCog.optin``, BEFORE the fallback ``send_message``.
- Event name: ``account.optin_intro_dm_forbidden`` (stable).
- Embed contents deliberately NOT in payload.

## Verification

- Gate green (pytest 831 passed + ruff + format + mypy).
- Coverage: ``account_cog.py`` 100%, ``fund_service.py`` 93% (both ≥85%).
- M1/M2/M3 each failed their matching test under revert; M4 (level
  downgrade) also caught.
- No new deps, no ``application/`` diff, no new ``try/except``.

## Follow-ups

None. This phase WAS the follow-up phase for Phase 17 review carry-forwards.
