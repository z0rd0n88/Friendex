# Pass-Baton: 6f JSON->SQLite migrator review — VERDICT CLEAN

**Date:** 2026-05-24
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** 820a517 feat(phase-6): JSON-to-SQLite migrator + fixtures

## Where things stand

Independent review of sub-unit 6f (the final Phase 6 unit) over the diff
`843cf2d..HEAD`. **VERDICT: CLEAN.** Gate green, all four ACs met and
non-vacuously tested, the declared `--guild-id` deviation is sound per ADR-0001,
no new dependencies, no security findings. Two non-blocking findings (1 MEDIUM,
1 LOW) and one NOTE — none gate the merge. Phase 6 (6a-6f) is complete and the
single Phase 6 draft PR can open.

### Gate (step 1) — PASS

`scripts/gate.sh baton-runner/br-2026-05-24-phase-6/gate-phase-6f-iter-1/` →
`GATE: PASS`, exit 0:

```
PASS pytest        (362 passed)
PASS ruff-check    (src tests alembic)
PASS ruff-format
PASS mypy          (src/friendex)
```

### AC verification — all non-vacuous (mutation-tested)

Each AC's tests were proven to FAIL under a targeted reversion/mutation of the
product code (run in a clean, isolated venv — see the harness caveat below):

- **AC1 row counts + AC2 round-trip** — skipping one user record in
  `_migrate_users` → 3 tests fail (`test_migrate_produces_expected_row_counts`,
  `test_user_record_round_trips`, `test_second_migration_is_idempotent`).
- **AC3 idempotency** — neutering `_clear_price_history` to a no-op →
  `test_second_migration_is_idempotent` fails (price_history doubles 6->12).
  The claim "idempotency proven non-vacuously" is CONFIRMED: price_history is an
  append-only, surrogate-PK table (`session.add`, price_repo.py:115-121), so the
  custom bulk-DELETE-before-re-append is genuinely required and genuinely tested.
- **AC2 Decimal/UTC** — disabling UTC localisation in `_to_utc` → 8 failures
  (`UtcDateTime` rejects naive at bind). `parse_float=Decimal` keeps money exact;
  tests assert `as_tuple().exponent` scale.
- **FK-safe order** — appending price history before upserting the parent stock →
  8 failures (FK violation under ADR-0002 PRAGMA ON).

> **Harness caveat for the next reviewer:** mutation-testing must run with a
> venv whose editable install points at the worktree under test. A leaked
> `VIRTUAL_ENV=/home/alex/Friendex/.venv` makes `uv run pytest` import the
> *original* worktree's source, so mutations to a copied tree silently no-op and
> tests look vacuous. Recreate the venv (`rm -rf .venv && uv venv && uv sync`)
> with `VIRTUAL_ENV` unset before drawing conclusions.

### Declared deviation — required `--guild-id` — ACCEPTABLE

The plan's CLI snippet (docs/04-migration-plan.md:360) shows only
`--source/--target`. 6f adds a REQUIRED `--guild-id`.

- **(a) Necessity — yes.** ADR-0001 (per-guild markets, dated 2026-05-22,
  supersedes decision #12) keys every per-guild table by `(guild_id, user_id)`
  with guild-first composite PKs; the 6c-6e repos take `upsert(guild_id, ...)`.
  The original JSON is guild-less, so a target guild is structurally mandatory.
  The plan snippet is simply stale w.r.t. ADR-0001, not authoritative.
- **(b) Design — correct.** "Required, no default" is the safe choice: a default
  would silently misplace whole economies under a bogus guild; per-file guild is
  meaningless for a single-guild source snapshot. Fail-fast is right.
- **(c) Correctness — sound.** All rows land under the one supplied guild,
  matching the single-guild origin.

Counts as intent met.

## Findings by severity

### MEDIUM — corrupt source data crashes instead of clean non-zero exit
`src/friendex/adapters/persistence/migrate_json_to_sqlite.py:477-481`.
`main()` catches only `(OSError, ValueError)`. A bad numeric value raises
`decimal.InvalidOperation` (an `ArithmeticError`, NOT a `ValueError`); a missing
required key raises `KeyError`. Both escape as an uncaught traceback rather than
`return 1`. Reproduced: a `users.json` with `cash_balance: "not-a-number"` →
`UNCAUGHT InvalidOperation: ConversionSyntax`. (Malformed JSON syntax is fine —
`JSONDecodeError` is a `ValueError`.) Low real-world risk (operator-run one-shot
tool on the bot's own files), hence MEDIUM not HIGH.
**Fix:** broaden to `except (OSError, ValueError, ArithmeticError, KeyError)`,
or wrap record mapping in a domain-specific `MigrationError`.

### LOW — no structural validation of decoded JSON
`migrate_json_to_sqlite.py:105-117`. `_load_json_object` trusts the decoded
shape (`dict[str, Any]` is annotation-only). A top-level array/scalar would fail
later with a confusing error. Acceptable for trusted input.
**Fix (optional):** `if not isinstance(data, dict): raise ValueError(...)`.

### NOTE — plan CLI snippet drift (out of 6f scope)
docs/04-migration-plan.md:360 (and the Phase 15 example block ~808-818) omit
`--guild-id`. Worth a docs touch-up when Phase 15 adds `--dry-run`/`--report` to
this same module. Already flagged in work baton 011's open questions.

## Security review — no findings

Offline operator CLI; no network/auth/web surface. SQL injection: none (all
writes via SQLAlchemy ORM/Core with bound column expressions). Deserialization:
`json.load` only — no unsafe object deserialization, no `eval`/`exec`, no YAML.
Path traversal: `--source` joins fixed literal filenames; operator-controlled
dir; no attacker-supplied path component. Secrets: none hardcoded/logged
(row-count aggregates only). No new dependencies (`pyproject.toml`/`uv.lock`
unchanged).

## Next steps

1. Optional, non-blocking: address the MEDIUM (broaden `main()`'s `except`) and
   LOW before the PR, or carry them as follow-ups.
2. Open the single Phase 6 draft PR (base `main`, head `feat/phase-6-repos`),
   body `Refs #2`, per `.github/pull_request_template.md`.
3. Set `baton-runner/br-2026-05-24-phase-6/STATE.md` -> DONE.

## References

- PRs: none open yet (Phase 6 draft PR to open)
- Issues: #2 (master tracking — Phase 6 box)
- Work baton under review: `baton-pass/phase-6-repos/011-2026-05-24-6f-json-sqlite-migrator.md`
- Code: `src/friendex/adapters/persistence/migrate_json_to_sqlite.py`; `tests/adapters/persistence/test_migrate_json.py`
- Docs: `docs/adr/0001-per-guild-markets.md`; `docs/04-migration-plan.md` §"Phase 6"; `docs/spec/original-skeleton.md`
- Gate logs: `baton-runner/br-2026-05-24-phase-6/gate-phase-6f-iter-1/`
- Digest: `baton-runner/br-2026-05-24-phase-6/digest-phase-6f.md`
