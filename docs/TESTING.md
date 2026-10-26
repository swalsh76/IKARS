# Testing and test data

The repository is public, so what tests may use is limited by Kalshi's Developer Agreement (storing and sharing
API data), news sources' terms of use, and the owner's privacy (PLAN D10, D21-4).

## Test kinds

| Kind | Marker | Runs | Data |
|---|---|---|---|
| **Unit and property tests** | none | always, locally and in CI | synthetic data from generators, or tiny hand-written fixtures |
| **Live Kalshi tests** | `integration` | only with `KALSHI_INTEGRATION=1`; never in CI | the real API, GET only, with IKARS's own read-only key |
| **Local-recording tests** | `localdata` | only with `IKARS_LOCAL_DATA` set to the recordings' folder; never in CI | the workstation's own recorded feeds, ledger or news, read in place, never copied into the repository |

The root `conftest.py` skips the opt-in kinds unless asked for.

## What may be committed

- **Synthetic fixture generators.** Each package keeps the generators for its data in its own `tests/`: order
  books, trades, news items, account histories for the ledger. Generators are seeded, so the data is the same on
  every run.
- **Tiny hand-written samples** of a live response's *shape* (a single market, a single fill), with made-up
  values. They are used for contract tests.
- Nothing over 1 MB under any `tests/` folder.

## What is never committed

- Recorded feeds, order books or trades (`*.parquet`, `*.arrow`, `*.jsonl`).
- Databases (`*.sqlite*`, `*.db`, `*.duckdb`) and ledger or tax exports.
- Fetched news articles or their text.
- Account data of any kind: balances, fills, positions, statements.
- Keys, key ids, key paths, certificates, real host names, share paths.

`tools/ci/data_guard.py` enforces the file rules in CI and in the pre-commit hook. gitleaks scans for secrets in
both places.

## Live tests

- Live tests are **GET only, never an order**.
- The key comes from the User environment variables `IKARS_KALSHI_KEY_ID` and `IKARS_KALSHI_PRIVATE_KEY_PATH`,
  loaded into the command's own process. They are never printed or read: the file is checked with `Test-Path` only.
- What a run read is repeated in the reply as public market data and status codes, never an account value.
- The rate-limit ramp is never run.
