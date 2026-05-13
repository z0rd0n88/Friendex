# StockXchange

Discord bot that simulates a stock exchange game. Each server member has their
own "stock" that others can buy, sell, or short. Prices move with real Discord
activity (messages, voice time, reactions) tracked by the bot.

This repository is currently being built out under the phased migration plan
in `docs/04-migration-plan.md` (see also `docs/01-current-state.md` and
`docs/02-target-architecture.md`).

## Development

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest
```
