# IKARS build log

One section per milestone and one subsection per slice, each with **Built**, **Verified** (local, live),
**Review** (the per-commit and per-merge mini reviews' findings and what was done about each; R14),
**Problems and fixes**, and any decisions taken. The slice table in [PLAN.md](PLAN.md) marks a slice
"done (BUILD-LOG x.y)".

## M0 Foundation

### 0.1 Survey and proposal (2026-10-26)

**Built:** `CLAUDE.md`, `docs/PLAN.md` (M0 decisions D1 to D12, proposed), this log. No code, no repository yet.

**Surveyed:**

- **KAPS** (`main` at `d8eda3b`): Python `uv` workspace. `kaps-core` (signing, REST, WebSocket, fixed-point money,
  fees) depends only on `cryptography`, `httpx`, `pydantic`, `websockets` and is portable to Windows (one
  POSIX-only file-mode check in `auth.py`, guarded by `os.name`). `kaps-server` and `kaps-analytics` are
  Linux/macOS only by design. KAPS is frozen; its ingest keeps recording on the server host.
- **KalTerm3** (`main` at `4095516`): .NET 10, WPF terminal (`net10.0-windows`, ScottPlot.WPF, Windows toasts),
  reconnecting stream client, contracts, fixed-point money and fees in C#, a DPAPI key vault, trading client with a
  risk layer. Its terminal talks to its own SQL Server–backed API, so IKARS's engine must serve a compatible
  (or adapted) contract.
- **SPARQ** (`main` at `8d82def`): forked from KAPS; holds the history and live-book studies (Parquet on the
  analytics node; read-only role on the KAPS database).

**Decisions taken (owner, 2026-10-26):**

- Direction: beat human traders, not professional bots; sniping, momentum and perpetuals are in scope (PLAN §1.1).
- **D1** approved: Python engine + .NET WPF terminal. Added **R9**: an Overview main screen with drill-down and
  cross-reference views (PLAN D1, "Terminal navigation model").
- **D2** confirmed: single box on the workstation; read-only Kalshi key there.
- **D6** approved as **recommend-only**; one-click and auto-snipe parked.
- **D13** approved as recommended: news + momentum, then sniping, then perpetuals.
- D3 to D5 and D7 to D12 still await the owner.
- Owner additions R10 to R13 (custom order types, GPU, live order-book analysis, SPARQ reuse) became proposals D14
  to D17. Checked for them: KAPS's plan already listed synthetic orders (stop, take-profit, bracket/OCO, trailing,
  condition-triggered) with the warning that they protect nothing while the app is down, but KalshiTerm2 has no
  implementation of them. SPARQ's notes on the `orderbook_delta` channel, its S1.2a live-book measures and its
  S1.2b recorder plan cover much of D16. SPARQ's forward test runs to 2026-11-23, so D17 adds a guard against
  contaminating it.
- Owner's answers later the same day:
  - Custom order types are to become **live trade functions** eventually, so D6 is amended to recommend-only
    through M6 and live trading in a new M7, behind one executor interface.
  - D3, D5, D7, D8 and D11 approved.
  - D9 approved, with a local-model option added.
  - D10: **public** (unlimited Actions minutes).
  - D12: a **portable zip plus a user-space installer**.
  - D14 to D17 approved.
  - D4: the owner asked for a comparison. Five options were compared in PLAN D4, and C is recommended (SQLite for
    state, Parquet for raw feeds, DuckDB for queries).
  - The installer tool was compared in PLAN D12; Velopack is recommended.
  - The M1 slices were proposed (PLAN §4.1).
- Owner, the same day: **D4 option C approved; D12 Velopack approved.** New requirement R15: an audit log fit to
  keep with a 1040. Designed as D18 (PLAN): an append-only, hash-chained ledger from Kalshi's account records with
  reconciliation, and a tax-year package (PDF/A summary, Form 8949 and Form 6781 CSVs, JSONL, SHA-256 manifest).
  The store becomes slice M1.5 and the export goes in M5. The M1 list was renumbered to M1.1 to M1.10 and
  re-presented.
- Owner, the same day: the ledger's backups go to **the same NAS share as the KAPS database backups**, in a subfolder
  tree of their own (PLAN D18): `ikars\` with its own marker, a path guard so KAPS's pgBackRest files are never
  touched, and retention per folder.
- Owner, the same day: **D18 approved.** Asked "what am I missing"; a gap review of 21 items was answered and
  recorded as **D19**:
  - Tier 1 and Tier 2 approved as shown.
  - Compliance review approved.
  - Fault and soak testing dropped (proved in SPARQ).
  - Automatic demotion approved, with its own Strategy review page.
  - Remote view parked.
  - Social sentiment parked, with a plugin architecture so that adding it needs no restructuring.
  - The M1 slices were revised to M1.1 to M1.11 and re-presented.
- Owner, the same day: a second gap review (A to J) was approved as **D20**:
  - point-in-time correctness
  - LLM safety and reproducibility
  - fill probability and adverse selection
  - return per day of capital
  - tradability filters
  - provenance
  - safe upgrades
  - alert hygiene
  - diagnostics and the rebuild runbook
  - performance targets

  M1 was refined (M1.4 split into M1.4a engine core and M1.4b Kalshi feeds) and re-presented.
- Owner, the same day: a third gap review was approved as **D21**:
  - milestone gates and pre-registered stop rules
  - the end-to-end momentum slice M1.12
  - IKARS's own read-only key and a Kalshi request budget that protects KAPS's ingest
  - the test-data policy
  - a monthly cost ceiling

  The **scope is frozen** at D1 to D21. The final M1 list (M1.1 to M1.12) and the gates were presented.

## M1 Skeleton

### 1.1 Repository, CI and security (plan APPROVED 2026-10-26 with open questions 1 to 3 as recommended; branch `m1/1-repo-ci`)

**Builds.**
- **Repository:**
  - `git init` in this folder.
  - Commit identity is repository-local: `Steve Walsh` and the GitHub no-reply address, as in KAPS. The personal
    address is never used.
  - A first commit on `main` holds only the M0 documents: `CLAUDE.md`, `docs/PLAN.md`, `docs/BUILD-LOG.md`,
    `README.md` (what IKARS is, its status, and a not-affiliated-with-Kalshi note), `LICENSE` (MIT, Steven Walsh),
    `SECURITY.md`.
  - Everything else goes on the branch and reaches `main` through PR #1.
- **Repository hygiene files:**
  - `.gitattributes`: LF everywhere.
  - `.editorconfig`: SPARQ's rules plus KalTerm3's C# rules, with LF instead of KalTerm3's CRLF.
  - `.gitignore`: SPARQ's list plus `*.sqlite*`, `*.db`, `*.arrow`, ledger exports (`*.jsonl`, `IKARS-*.pdf`),
    `logs/`, `data/`, `bin/`, `obj/`, `TestResults/`, `.vs/`.
- **Python workspace (`uv`):**
  - The root `pyproject.toml` takes SPARQ's ruff, mypy strict, pytest and coverage settings (90% gate; the 95%
    money and fee gate is wired here and takes effect in M1.2).
  - `.python-version` 3.13: library support, such as CUDA and ONNX wheels, lags on 3.14. It is installed by `uv`
    into its own cache, leaving the system Python alone.
  - `packages/ikars-core` and `packages/ikars-engine`, each with only a version module and one test. M1.2 seeds the
    core from `sparq_core`.
  - `uv.lock`, with hashes.
- **.NET solution:**
  - `Ikars.slnx` (the .NET 10 solution format), `global.json` (SDK 10.0.402), and `Directory.Build.props` from
    KalTerm3 (nullable, warnings as errors, analysers, Windows-only attribute).
  - `Directory.Packages.props` (central versions) and NuGet lock files restored in locked mode in CI.
  - `src/Ikars.Core` (`BuildInfo` only) and `tests/Ikars.Core.Tests` (xUnit v3 on Microsoft Testing Platform), so
    CI exercises the whole toolchain.
  - `tools/ci/check-coverage.ps1` from KalTerm3.
- **CI (`.github/workflows/ci.yml`, `windows-latest`):** every action is pinned by commit SHA, the token is
  read-only, and runs of the same branch cancel each other.
  - `python` job: uv pinned, `sync --locked`, ruff format and lint, mypy, pytest with coverage, `pip-audit` on the
    locked requirements.
  - `dotnet` job: locked restore, `dotnet format --verify-no-changes`, build, vulnerable-package check, tests, the
    coverage gate.
  - `hygiene` job:
    - **gitleaks** (a pinned release binary, checksum-verified, over the full history);
    - a **data guard** that fails on any committed data file (`*.parquet`, `*.duckdb`, `*.sqlite*`, `*.arrow`,
      `*.pem`, `*.pfx`) or any file over 1 MB under `tests/`.
- **`.github/dependabot.yml`:** weekly updates for `github-actions`, `nuget` and `uv`.
- **`.github/pull_request_template.md`:** the R14 mini-review checklist, both the per-commit and the per-merge parts.
- **`docs/TESTING.md`, the test-data policy (D21-4):**
  - test kinds and markers: `integration` (live Kalshi, opt-in) and `localdata` (needs the workstation's recordings,
    skipped in CI);
  - synthetic fixture generators live in each package's `tests/`; the first real ones arrive in M1.2;
  - only tiny hand-written samples of live data are committed;
  - never committed: real feeds, articles, account data.
- `CLAUDE.md` Dev commands filled in.

**Changes on this machine:**
- A `.git` folder in `IKARS`.
- `uv` downloads Python 3.13 into its cache; packages go to the `uv` and NuGet caches.
- Optional (open question 1): gitleaks installed with `winget` for a local pre-commit hook.

**Changes on GitHub (outward-facing):**
- Create public `swalsh76/IKARS` (MIT), with a description and topics. Wiki and projects off.
- Turn on secret scanning with **push protection**, Dependabot alerts and security updates, and private
  vulnerability reporting.
- Actions:
  - the default token is read-only;
  - workflows from fork pull requests need approval.
- A **ruleset on `main`:**
  - a pull request is required;
  - the `python`, `dotnet` and `hygiene` checks must pass;
  - no force-push or deletion.
  - No review requirement, since a sole owner cannot approve their own pull request. The owner merges.
- Push `main` (docs only), push the branch, open PR #1.

**New packages and licences:**

| Package | Licence |
|---|---|
| ruff | MIT |
| mypy | MIT |
| pytest | MIT |
| pytest-asyncio | Apache-2.0 |
| pytest-cov | MIT |
| coverage | Apache-2.0 |
| hypothesis | MPL-2.0 |
| pip-audit | Apache-2.0 |
| xunit.v3 | Apache-2.0 |
| coverlet.MTP | MIT |
| Microsoft.Testing.Extensions.TrxReport | MIT |
| gitleaks (binary, CI) | MIT |

**Checks:**
- Everything green locally, then in CI on GitHub's Windows runner.
- **Planted failures, proved locally and then discarded, never pushed:**
  - a fake key that gitleaks must catch;
  - a `*.parquet` file the data guard must catch;
  - a failing test, a format error and a type error, each of which must turn its job red.
- The ruleset is shown to block a direct push to `main`.
- No live Kalshi calls in this slice.

**Open questions:**
1. Install gitleaks locally (`winget install Gitleaks.Gitleaks`) for a pre-commit hook? Recommended: yes, so a
   secret is stopped before it is committed rather than only in CI.
2. .NET naming: the `Ikars.*` prefix and `.slnx`. Recommended: yes.
3. Repository description. Proposed: "IKARS: real-time Kalshi trade recommendations from news, data and live order
   books, for Windows 11. Not affiliated with Kalshi."

#### 1.1 progress (2026-10-26): built and verified locally; GitHub steps waiting for the owner's go-ahead

**Built.**
- Commits:
  - `main` holds the M0 docs (`8f0bcca`);
  - branch `m1/1-repo-ci` holds the slice (`e0374c6`) and its review fixes (`d7cbc53`).
- Repository-local identity: Steve Walsh and the GitHub no-reply address.
- gitleaks 8.30.1 installed with `winget`.
- The pre-commit hook is enabled through `core.hooksPath tools/hooks`.

**Verified locally.**
- Python 3.13.16 (uv-managed):
  - ruff format and lint clean; mypy strict clean;
  - pytest 5 passed, 100% coverage;
  - pip-audit finds no known vulnerabilities.
- .NET 10.0.402:
  - locked restore;
  - `dotnet format` clean;
  - build with 0 warnings;
  - 1 test passed; the coverage gate passes (`Ikars.Core` 100%);
  - no vulnerable packages.
- Hygiene: the data guard passes (39 tracked files); gitleaks finds nothing in the history or the staged changes.
- **Planted failures, all caught and then removed** (none committed):
  - a fake private key, refused by the hook (gitleaks);
  - a force-added `.parquet`, refused by the data guard;
  - a Python failing test, format error and type error;
  - a C# failing test and format error.
- **Review fix checked:** a 1 MB file staged and then shrunk on disk is still refused, and the guard gives the same
  result from a subfolder.

**Review (R14).**
- The M0 commit is docs only. A scan for real host names, the share path and local paths found nothing; gitleaks
  is clean.
- The slice commit had `/code-review` run on it at low effort. Two findings, both **fixed** in `d7cbc53`:
  1. `check-coverage.ps1`, inherited from KalTerm3, judged an assembly with no instrumented lines as 0% and failed
     CI. It now skips the assembly and lists it.
  2. `data_guard.py` read sizes from the working tree, so a large staged file could pass once changed on disk, and
     it only worked from the repository root. It now reads sizes from the index (`git ls-files --format`) at the
     repository's top level.
- The fix commit was reviewed by hand: a small diff, and the behaviour was proved by the staged-then-shrunk test.

**Kept, changed and why.**
- *Kept:*
  - SPARQ's ruff, mypy, pytest and coverage settings, and its `.gitignore`, extended for databases, ledger exports
    and logs;
  - KalTerm3's `Directory.Build.props`, the central package versions, the test project shape and the coverage
    script.
- *Changed:*
  - LF instead of KalTerm3's CRLF;
  - NuGet lock files restored in locked mode (KalTerm3 had none);
  - Python 3.13 floor (SPARQ allows 3.12);
  - the opt-in test markers live in one root `conftest.py` instead of per package;
  - gitleaks and the data guard are new.

**Problems and fixes.**
- `gitleaks dir .` reported 200 findings, all inside the ignored `.venv`: third-party package test data. The hook
  and CI scan only what git tracks, so the documented command is `gitleaks git`.
- Files written by Python on Windows get CRLF in the working tree. git normalises them to LF on commit
  (`.gitattributes`), so this only matters for exact-text edits.

#### 1.1 GitHub (2026-10-26, owner's go-ahead)

**Done.**
- Created public `swalsh76/IKARS` with the approved description; wiki, projects and discussions off; branches
  deleted after merge.
- Secret scanning **with push protection**, Dependabot alerts and security fixes, and private vulnerability
  reporting turned on.
- Workflow token read-only by default; workflows from fork pull requests need approval for all outside
  contributors.
- `main` pushed (`8f0bcca`).
- Ruleset **"main protection"** (id 25919620), active with no bypass:
  - deletion and non-fast-forward blocked;
  - a pull request is required (no review count: a sole owner cannot approve their own pull request);
  - the checks `python`, `dotnet` and `hygiene` must pass (GitHub Actions).
- PR #1 opened from `m1/1-repo-ci`.

**Verified.**
- **A direct push to `main` was refused:** `GH013: Repository rule violations`. Remote `main` stayed at `8f0bcca`.
- CI on PR #1, first run, all green on `windows-latest`:
  - `python` 39 s;
  - `dotnet` 1 m 0 s;
  - `hygiene` 15 s.

**Review (R14), per merge.**

`/code-review` at medium effort over the branch against `main` found 10 issues. All were checked against the code
and all **fixed** in `f9db0d9`:

1. `KALSHI_INTEGRATION=0` turned live tests *on* (any non-empty value counted), and the variable is shared with
   KAPS and SPARQ. Now IKARS's own `IKARS_LIVE`, on only for exactly `1`; `IKARS_LOCAL_DATA` must name an
   existing folder.
2. `marker in item.keywords` also matched folder, module and test names. Now `get_closest_marker`. Both behaviours
   are tested with `pytester` (`tools/ci/test_optin.py`, 9 cases).
3. The data guard missed `.sqlite3`, `-wal`/`-shm`/`-journal`, `.duckdb.wal`, `.feather`, `.p12` and the ledger's
   exports. All are now refused (every suffix of a name is checked; the exports match `IKARS-<year>-*`), and
   `.gitignore` is widened to match.
4. The coverage merge keyed lines on each report's relative filename, so different `<sources>` roots could
   double-count. Lines are now keyed on full paths; a duplicated report was shown to leave the totals unchanged.
5. An unmeasured or uninstrumented production assembly passed the gate. Every `src` project must now be measured,
   or be listed in `-AllowEmpty`; a required project missing from the reports was shown to fail (exit 1).
6. The hook was committed as 100644, so other platforms would ignore it. Now 100755.
7. mypy did not check `tools/` or `conftest.py`. It does now, in CI and in the dev commands.
8. A newer push to `main` cancelled the previous merge's CI. Cancellation now applies to pull requests only.
9. The hand-made vulnerable-package step duplicated NuGetAudit with a fragile regex. NuGetAudit is now set in
   `Directory.Build.props` (all packages, level low, errors through warnings-as-errors). Proved in a scratch copy:
   System.Text.Json 8.0.0 failed the restore with **NU1903**.
10. No caching on the Windows runners. Now `astral-sh/setup-uv` v10.3.0 (pinned by SHA; a release over two weeks
    old) with its cache, and `setup-dotnet`'s NuGet cache keyed on the lock files.

Per-commit `/code-review` (low) on `f9db0d9`:
- "Locked mode no longer set": **false positive**. `RestoreLockedMode` is in `Directory.Build.props` under
  `GITHUB_ACTIONS`; it was outside the reviewed hunk.
- A report with no `<sources>` crashed the coverage gate: **fixed**, and tested with a hand-made report.
- The ledger-export rule blocked any `ikars-*.csv/.pdf/.txt`: **fixed**. It now matches only `IKARS-<year>-*`, and
  `ikars-notes.csv` is tested as allowed.

**Problems and fixes.** A PowerShell test command was blocked by the assistant's safety check (a `Remove-Item` on a
temp wildcard was misread); nothing had run. The vulnerable-package test was redone in a scratch copy of the .NET
files instead, which left the working tree untouched.

**CI after the fixes** (`f9db0d9`), all green:
- `python` 36 s
- `dotnet` 1 m 16 s
- `hygiene` 14 s

The caches fill on this first run, so later runs should be quicker. **Ready for the owner to merge PR #1.**

### 1.2 `ikars-core` (plan APPROVED 2026-10-26; branch `m1/2-core`)

**Builds.**

1. **Seed `ikars_core` from SPARQ's `sparq_core`.**
   - The source is about 2,260 lines in 11 modules: auth, clock, config, endpoints, fees, fixedpoint, models,
     ratelimit, rest, ws, ws_models.
   - Its tests come too (about 3,900 lines), with the 12 fixtures (already made-up: `KXDEMO-…`) and the golden
     vectors. `fees.json` is 970,569 bytes, under the data guard's 1 MB limit.
   - The exact SPARQ commit is recorded (open question 1).
   - Renames:
     - `sparq` → `ikars`;
     - `SPARQ_*` settings → `IKARS_*`;
     - the live switch → `IKARS_LIVE`;
     - the key → `IKARS_KALSHI_KEY_ID` and `IKARS_KALSHI_PRIVATE_KEY_PATH`.
   - **`test_live_ratelimit.py` is dropped entirely**: the rate-limit ramp stays banned.
   - The 95% coverage gate on `fixedpoint.py` and `fees.py` goes into CI.
2. **A key-file permission check on Windows.** SPARQ's check is POSIX-only, so on Windows it checks nothing.
   - The new check reads the file's ACL through the Windows API (`ctypes`, no new package).
   - It refuses to load a key that any principal other than the owner, SYSTEM or Administrators can read, and
     gives the `icacls` command that fixes it.
   - Tested on temporary files whose ACLs the tests set.
3. **A signed read-only account client** for D18's ledger import and D21-3's limits:
   - GET only, through the same guard as the public client;
   - its own allow-list (`/portfolio/...` reads and `/account/limits`), signed with the existing `DataSigner`;
   - account responses are never logged;
   - models for balance, positions, fills, settlements and account limits. Transfers and interest come too *if*
     the API has endpoints for them; checked against Kalshi's docs in the slice, and recorded as a D18 gap if not.
4. **Order-book models:**
   - `orderbook_snapshot` and `orderbook_delta` messages (`seq`, `clock`, fixed-point levels);
   - a pure in-memory book that applies deltas and reports a gap.

   The keeper and resync logic belong to M1.4b. SPARQ's unmerged branches are checked first, and anything already
   built there is reused.
5. **Native order options re-checked** against Kalshi's API reference (for D14). Written up in
   `docs/research/kalshi-order-options.md`: types, time-in-force, post-only, reduce-only, expiry, self-trade
   prevention, order groups if any.
6. **Account-level limits (D21-3):** read once from `GET /account/limits` and recorded (open question 2).
7. **Contract tests:** hand-written fixtures with made-up values for every new model.

**Changes on this machine:** none beyond the repository. The slice reads the key file's ACL (not its contents) to
check it. If the ACL is too open, the owner is given the command; the assistant does not change it (open
question 3).

**New packages and licences** (carried from SPARQ):

| Package | Licence |
|---|---|
| cryptography | Apache-2.0 or BSD-3 |
| httpx | BSD-3 |
| pydantic | MIT |
| websockets | BSD-3 |

Dev only: `hypothesis` (MPL-2.0), already present. The engine–terminal contracts use `msgspec` from M1.7; the core
keeps SPARQ's pydantic models.

**Live checks** (GET only, IKARS's own key, about 100 requests in all, nothing printed but public data and status
codes):
- `test_live_public` (about 15 GETs);
- `test_live_bounds` (about 40);
- `test_live_ws` (subscribe commands on public channels);
- new `test_live_account`: signed GETs for balance, positions, fills, settlements and limits. It asserts shapes
  only and **never reports an account value**.

**Open questions.**
1. **Seed from SPARQ `main` (`15669a4`) or its review clean-up branch `s1/2q-cleanup` (`c017add`)?** The clean-up
   was approved in SPARQ but is not merged yet. It touches 12 files in `sparq-core`: one rate-limited client
   config, generic endpoint readers, one model base. *Recommended: `c017add`*, so the refactor is not ported twice.
   The commit is recorded in this log.
2. **WebSocket market-subscription limit:** read the account limits only now, and learn the WebSocket limit
   passively in M1.4b (by recording any "limit reached" error), never by probing? *Recommended: yes.* Probing
   would be close to the benchmarking the Developer Agreement forbids.
3. If the key file's ACL lets other users read it, the owner runs the `icacls` command the assistant gives; the
   assistant never changes the key file. *Recommended: yes.*

**Owner's answers (2026-10-26):**
1. Seed from SPARQ's `s1/2q-cleanup` at **`c017add`**.
2. The WebSocket subscription limit is learned passively in M1.4b, never probed.
3. If the key file's ACL is too open, the owner runs the `icacls` command; the assistant never changes it.

PR #1 (M1.1) was merged by the owner as `2f19222`, and CI on `main` is green.

#### 1.2 progress (2026-10-26)

**Built (commits on `m1/2-core`).**
1. **Seed** (`344bb3c`): `sparq-core` at SPARQ `c017add`, copied with its tests, made-up fixtures and golden
   vectors.
   - Renamed throughout. IKARS's own variables: `IKARS_LIVE`, `IKARS_KALSHI_ENV`, `IKARS_KALSHI_KEY_ID`,
     `IKARS_KALSHI_PRIVATE_KEY_PATH`; KAPS's `KALSHI_ENV` can no longer switch IKARS to production.
   - The rate-limit ramp test is dropped.
   - The package conftest's own opt-in hook is removed, since it used SPARQ's fragile `item.keywords` check.
   - mypy runs in two CI steps (two modules named `conftest`).
   - The 95% coverage gate for money and fee code is added to CI.
   - 923 tests pass on Windows.
2. **Key files private on Windows** (`046a396`): `winacl` reads the DACL through `ctypes` and refuses a key that
   anyone but the owner (or OWNER RIGHTS), SYSTEM or Administrators can read, giving the `icacls` fix. SPARQ's
   check was POSIX-only. **The owner's IKARS key file passes**; only its ACL was read.
3. **Account reader** (`3d2b23d`):
   - `AccountSigner` signs exactly the account reads D18 needs, plus the account limits.
   - `KalshiAccountClient` is the one GET-only client class, preset for those reads, with every attempt signed
     afresh.
   - **A hole closed:** SPARQ's data signer and public client allowed every `/historical` route, but Kalshi's
     reference shows `/historical/fills`, `/orders` and `/positions` are account data. Both now allow only cutoff,
     markets and trades there.
4. **Account models and reader** (`0f1769c`): balance, positions (live and archived), fills (live and historical),
   settlements, deposits, withdrawals, instance transfers and limits. Kalshi's cent fields are converted to dollar
   millionths. **Kalshi has no interest endpoint (D18 gap):** interest reaches the ledger only as a balance change
   it cannot explain, so reconciliation must show it and the tax package must name it.
5. **Order book** (`4d3801c`): the `orderbook_snapshot` and `orderbook_delta` models and a pure in-memory
   `OrderBook` (stale-clock, no-snapshot and broken-on-negative rules; best ask is $1 minus the other side's best
   bid; crossed books reported). SPARQ had no book code on any branch.
6. **Native order options** (`8a0726f`): `docs/research/kalshi-order-options.md`. Every order is a priced limit on
   the YES book. There is no market, stop, take-profit, trailing, OCO or bracket order. Order groups (cancel all
   when a contracts cap is hit within a rolling 15 s), `expiration_time`, `cancel_order_on_pause`, `post_only` and
   `reduce_only` are the native building blocks. Each D14 type is mapped onto them.
7. **Key-id guard and live checks** (`96a5df4`): see Problems.

**Verified.**
- Locally: about 1,020 tests pass, coverage 97.5%, money and fees 100%; ruff, mypy and pip-audit clean.
- **Live (public, no key; about 55 GETs), all passed:**
  - trades `min_ts` inclusive and `max_ts` exclusive, live and historical;
  - the largest page is 1,000 (1,001 gives HTTP 400);
  - cursors are stable, with no duplicates, newest first;
  - one 429 on each paged walk, retried.
- **Live with the key: blocked** (see Problems).

**Review (R14).** `/code-review` at low effort on every commit:
- seed, ACL check, signers, account models, book: no findings;
- key-id guard: one finding, **fixed** before the commit (the error reason was judged on the untrimmed value).

**Problems and fixes.**
- The first signed live run got **401 `INVALID_PARAMETER`** on every host. The stored `IKARS_KALSHI_KEY_ID`
  contains quotes or spaces, so it is not the bare 36-character id; the key file itself loads. **The owner is
  re-setting the variable.**
- The signer now refuses such an id with a clear reason that never shows the value.
- After the variable is fixed: run `test_live_account` and `test_live_ws` (including the order-book check), then
  close the slice.
