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

#### 1.2 live checks and merge review (2026-10-26, after the owner re-set the key id)

**Verified live with IKARS's own key** (GET and subscribe only):
- Production accepts the signature on both hosts; the demo answers "not found", as expected for a production key.
- **Account (4 checks):** every one of the owner's real records parsed, under the final, stricter models: positions
  (live, settled and archived), fills, historical fills, settlements, deposits, withdrawals and instance transfers.
  Fills carry a direction and dollar fields arrive as text.
  - Kalshi's undocumented `updated_ts` and `created_ts` are **Unix seconds**.
  - The API tier is **basic**: read bucket 200 tokens/s with a burst of 600, write 100/s with a burst of 100 (each
    request costs tokens). This is the D21-3 budget input.
- **WebSocket (5 checks):**
  - the signed handshake succeeds;
  - 1,563 frames parsed with no errors and no unknown fields;
  - no `seq` gaps;
  - a forced disconnect reconnects;
  - adding and removing markets is not a gap;
  - the receive delay after `sending_ts_ms` is about 46 ms.
- **Order book:**
  - Production book messages carry **no `clock`** (Kalshi's reference has it; the changelog dates it
    2026-10-29). The first run therefore rejected 17,772 deltas in 20 s, and the model was fixed (`ef0a714`).
  - After the fix: 33,464 book messages applied in 30 s on the three busiest markets, **none broken**.
  - The book at each ticker frame's own `ts_ms` matched the ticker **86 of 87 times**, which confirms that both
    sides are bids and a YES ask is $1 minus the best NO bid.
  - Delta rates were **783, 234 and 98 a second** (the 15-minute BTC, ETH and XRP contracts). This sizes M1.4b's
    watched set.

**Review (R14), per merge.** `/code-review` at medium effort over `main...m1/2-core` found 10 issues. All were
checked against the code, and all **fixed** in `ff5a13d`:

1. The data key's WebSocket would subscribe to *any* channel, private account channels included. It now accepts
   market-data channels only (`MARKET_DATA_CHANNELS`).
2. The data key could sign `/account/limits` (account data), and the account client did not check its signer at
   run time. Limits are now the account reader's only, and the client refuses any other signer. The reference
   signing vectors for that route pass through the account reader with the identical signature.
3. A bad side in a snapshot left a half-applied book reporting ready. Both sides are now built before either is
   replaced, and the parser refuses negative sizes and non-positive prices.
4. Key-loading errors contained the key file's path (CLAUDE.md forbids printing key paths). They now say "the
   private key file", and the fix commands use the environment variable.
5. A fill could parse with no direction, which would silently corrupt the ledger's cost basis. The direction is
   now required: taken from `outcome_side` or `book_side`, or else from the legacy `side` and `action`.
   Disagreeing fields are refused.
6. The ACL check missed conditional (callback) allow entries and `WRITE_DAC`/`WRITE_OWNER`. They are now counted;
   the rights are tested with icacls.
7. `positions` duplicated the client's paging loop with weaker checks. There is now one `payloads()` loop.
8. The public route rules existed twice with different regexes. There is now one table (`auth.public_route`).
9. An integer in a dollar field would have been read as whole dollars, a 100-fold error if Kalshi ever sent cents
   there. Account dollar fields now take text only.
10. A 36-character malformed key id got a misleading reason. It now gets the right one.

**Problems and fixes (this part).**
- The missing `clock`: see above.
- The first live book comparison was too naive for markets this fast (about 800 deltas a second against a ticker
  about once a second). It now compares at matching times.

**Close-out of M1.2.**
- Offline: 1,025 tests pass; coverage about 97%, money and fees 100%; ruff, mypy and pip-audit clean.
- Live: 8 public checks and 9 signed checks pass.
- Left for later slices: the order-book keeper and resync, the watched set and the request budget (M1.4b); the
  ledger store and import (M1.5).
- Interest is not exposed by Kalshi's API (a D18 gap): the ledger's reconciliation must show unexplained balance
  changes and the tax package must name them.

**CI on PR #3 (first run): `python` failed, a real Windows difference.**
- On GitHub's runner, files that `runneradmin` creates are owned by the Administrators group, so the user's own
  access to its key was reported as another reader.
- Fixed: the account running the process is an expected reader too, read from its access token. An undeclared
  `GetCurrentProcess` handle was truncated to 32 bits at first; pointer-sized `ctypes` declarations fixed that.
- The deny test now denies read-data only (`(RD)`): denying `(R)` also denies `READ_CONTROL`, and with it reading
  the ACL.
- The owner's own key file still passes.

**M1.2 done:** PR #3 merged by the owner's instruction as `3485b16` (2026-10-27 00:18 UTC). The owner added a
local permission rule so the assistant can run `gh pr merge` when asked; `.claude/settings.local.json` is
git-ignored.

### 1.3 UI mock-ups (plan APPROVED 2026-10-27: an HTML Artifact; branch `m1/3-mockups`)

**Builds.** Clickable mock-ups of the terminal's navigation model (R9), with made-up data (`KXDEMO-…`), for the
owner's approval **before M1.8 builds any screen**. They follow the Windows 11 Fluent look of KalTerm3's WPF
terminal, and its existing views (Markets, MarketDetail, OrderBook, CandleChart, Alerts, Status) are the starting
point.

1. **Overview (main screen):**
   - top recommendations (expected value after costs, return per day, size, a "why" line);
   - live movers;
   - latest source items with their linked markets;
   - portfolio and exposure (G1);
   - perp funding;
   - the journal's scorecard;
   - a health strip (feeds, latency, backups, storage, cost).

   Every element links to a drill-down.
2. **Drill-down, markets path:** category → series → event → market detail (price chart with news markers, order
   book depth, the entry and exit P&L scenarios of D7) → recommendation detail (evidence, provenance, lifecycle).
   Breadcrumbs, back and forward.
3. **Cross-reference panel:** a docked panel for whatever is selected: source items that moved it, related
   markets, a perp hedge, past recommendations and outcomes; selection kept in sync.
4. **Health view** (G10, G11, J, D21-5) and the **alert** styles by priority (H).
5. **Hotkeys overlay, dark and light, a multi-monitor layout sketch** (G16).

**Medium (open question 1).** *Recommended:* one self-contained HTML mock-up, published as a **private claude.ai
Artifact** for the owner to click through and comment on. Its source is kept in `docs/design/mockups/` (made-up
data only). It is fast to change between rounds; the WPF build in M1.8 then follows the approved version.
*Alternative:* a throwaway WPF/XAML prototype, closer to the final look but slower to iterate.

**Changes on any machine:** none (a private artifact on the owner's claude.ai account).

**New packages:** none in the product. The mock-up may load a charting library from a CDN, pinned and only in the
mock-up.

**Live checks:** none.

**Open questions.**
1. HTML Artifact (recommended) or a WPF prototype?
2. Rounds: the owner comments; the assistant revises; **approval is recorded in this log** before M1.8.

### 1.4a Engine core (plan APPROVED 2026-10-27: TOML settings, drop-oldest with a recorded gap, JSON-lines logs; branch `m1/4a-engine-core`)

**Builds** (in `ikars-engine`; no network in this slice):

1. **Configuration and paths.**
   - Settings in a versioned TOML file (G15, G): read with the standard library's `tomllib`, written with
     `tomli-w` (already in the lock).
   - Data, logs and settings under `%LOCALAPPDATA%\IKARS`, or beside the program when `portable.marker` exists
     (D12).
   - Secrets are never stored in the settings, only the names of the environment variables (D8).
2. **Logging (I).** The standard library's `logging`, as JSON lines, rotated by size, with a **redaction filter**:
   - the key id and key path (from the environment), PEM blocks and bearer tokens are masked;
   - account models are never passed to a logger (enforced by a test).
3. **Item, reference and link model (D1, D19, A).**
   - Typed references (`Ref(kind, id)`).
   - Items with **publisher time and received time**.
   - Links between references.
   - All as `msgspec` structs, which the loopback API (M1.7) reuses as its contracts.
4. **Plugin registry (D19):** plugins found through Python entry points (group `ikars.plugins`); kinds source,
   signal, order type and venue, each a small typed interface that declares its settings, secrets, rate budget
   and item kinds. Each plugin runs in **its own task** with:
   - a bounded queue (open question 2);
   - a timeout on every call;
   - a crash boundary with restart backoff.

   One failing plugin never stalls the others.
5. **One input interface, live or replay (G3, A).**
   - An `EventClock` (the wall clock, or a virtual clock in replay) and an event stream ordered by **received
     time**.
   - The replay source reads recorded Parquet (the schema is defined here and written by M1.4b's recorder) and
     releases each item **only at its received time**, so nothing downstream can see the future.
   - The same engine code runs on either source.
6. **Clock skew (G10):** `ikars_core.clock`'s estimator wired in; the offset is used by the signers and reported
   in health.
7. **Metrics (J):**
   - counters;
   - latency histograms (fixed log-scale buckets, cheap enough for the hot path);
   - **feed-to-alert latency** stages stamped with monotonic times;
   - a hook for the terminal's frame time (M1.8).
8. **Cost tracking (D21-5):** per-plugin spend against a monthly budget; past a threshold a plugin is told to
   degrade (cheaper model, slower polling), and at the ceiling it is paused.
9. `python -m ikars_engine` runs the core with no plugins and a replay of a synthetic recording, as a smoke test.

**Changes on this machine:** none beyond the repository and package caches.

**New packages:**

| Package | Version | Licence |
|---|---|---|
| `msgspec` | 0.22.0 (2026-09-29) | BSD-3 |
| `pyarrow` | 26.0.0 (2026-10-09) | Apache-2.0 |

Both are over two weeks old; `pyarrow` is also what M1.4b writes with.

**Tests.**
- **Determinism:** the same recording replayed twice gives byte-identical outputs.
- **Point-in-time:** an item is never released before its received time.
- **Isolation:** a plugin that raises, hangs or floods is contained, restarted with backoff, and counted.
- **Redaction:** planted secrets never reach a log file.
- **Settings:** migration from an older version.
- Property tests on the clock and the histogram.

**Live checks:** none (offline slice).

**Open questions.**
1. **Settings format:** TOML (recommended: human-editable, `tomllib` in the standard library) or JSON.
2. **When a plugin's queue is full:** *recommended* drop the oldest item and **record a gap** (counted, shown in
   health, kept in the gap log), never a silent drop and never blocking the feed. *Alternative:* block the
   producer with a timeout.
3. **Logs:** JSON lines (recommended: machine-readable for the support bundle) or plain text.

#### 1.3 round 1 (2026-10-27)

**Built.** A private Design canvas on the owner's claude.ai account (the link is kept out of this public
repository), with six screens, all with made-up data:
1. **Overview**, with a light/dark toggle;
2. **Market drill-down**, with the cross-reference panel docked;
3. **Recommendation detail**: evidence, size and limits, provenance, taken/ignored/dismissed, and a bracket as a
   watch order;
4. **Health**: feeds, books, latency, clock, ledger, backups, storage, cost, plugins, gap log;
5. **Alerts** by priority, with the hotkeys list;
6. a **two-monitor layout** sketch.

The screens link to each other for clicking through.

**Problems and fixes.** The first draft's sample P&L added the spread to the cost basis, which double-counts it
when the ask is paid. It now shows the spread separately and the figures follow D7: cost basis $17.49, YES
+$22.51, NO −$17.49, maker exit at $0.55 +$4.33, stop at $0.41 −$1.77, EV +$3.71, break-even 0.437.

**Waiting on the owner:** comments or approval. The approved source goes into `docs/design/mockups/` on branch
`m1/3-mockups`.

#### 1.4a built (2026-10-27)

**Built** (`ikars-engine`, offline; commits `7ab6439` and `49468ba`):

| Area | What |
|---|---|
| Paths | `%LOCALAPPDATA%\IKARS`, or `data-portable\` beside the program when `portable.marker` exists. |
| Settings | Versioned TOML (`tomllib` / `tomli-w`); step-by-step migrations with a backup first (names never collide); atomic, fsynced writes; secrets refused (by whole words of key names, and PEM blocks in values); only environment-variable names allowed. |
| Logs | JSON lines, rotated; the redaction filter on every handler (key id, key path in both slash forms and its file name, case-insensitive; PEM blocks; bearer tokens; in messages, arguments, extra fields and exceptions). |
| Model | `Ref`, `Item` (publisher time, **received time**, `hops`), `Link` as `msgspec` structs, reused by M1.7. |
| Time and input | `WallClock` and `VirtualClock`; Parquet recordings (`RECORDING_SCHEMA`); a `ReplaySource` that merges in received order and moves the clock to each item **before** releasing it; a `LiveSource`. |
| Plugins | Entry points `ikars.plugins`. Live: a task and a bounded inbox each, **drop-oldest with a recorded gap**, gaps also for items left at stop. Replay: deterministic, in name order. Everywhere: a timeout on every call; crash boundary with backoff; paused after repeated crashes (a stable minute resets the count); pause cancels a producer; resume restarts it unless it had finished. |
| Engine | Routing with a hop limit carried on items (holds live and in replay). Replay emits are delivered breadth-first after the emitting call returns (each plugin's time is its own), strictly one delivery at a time. Listeners are synchronous and contained. Budget: degrade, pause and resume of paid plugins. A health summary. |
| Measures | Log-scale latency histograms (percentiles never optimistic), stage timings, a monthly cost tracker in dollar millionths, a skew monitor over `ikars_core.clock`. |
| Smoke | `python -m ikars_engine smoke`: an offline replay of 100 synthetic items through a test plugin. |

**Verified.**
- 1,118 tests pass; coverage 97.5%; ruff, mypy (64 files) and pip-audit clean.
- Covered by the tests:
  - byte-identical replays;
  - nothing released before its received time (also a property test);
  - containment of raising, hanging, flooding and crashing plugins;
  - planted secrets never in the log file or another handler;
  - settings migration, backup and refusal;
  - live and replay hop limits;
  - budget degrade, pause and resume across a month;
  - listener faults contained.

**Review (R14).**
- **Per commit:** three rounds on the core found 9 issues, all fixed before `7ab6439`:
  1. the hop limit did nothing live;
  2. a pause could be undone by a backoff;
  3. pause did not stop a running producer;
  4. `seconds=0` meant the default;
  5. lifetime crash counting;
  6. a slow listener timed out its plugin;
  7. budget pauses were never lifted;
  8. a resume right after a pause restarted nothing;
  9. a finished one-shot source re-ran on resume.
- **Per merge:** the medium review over the branch found 10 issues, and its follow-up found 3 more; all fixed in
  `49468ba`. They are listed in that commit: silent drops at stop; narrow secret detection; case-sensitive
  redaction; redaction on one handler only; a truncating money migration; raw version errors; downstream time
  charged upstream; no fsync and colliding backup names; a stale month total; listener faults; plus routing an
  emit made outside a delivery, serialising deliveries, and counting leftovers on cancellation.

**Kept, changed and why.**
- *Kept:* SPARQ's clock estimator (`ikars_core.clock`), wrapped and not copied; KAPS's "log what was skipped" rule,
  now the gap record.
- *New:* everything else; neither KAPS nor SPARQ had a plugin host, a replay engine or a cost ceiling.
- *Moved to M1.5:* the test that account models are never passed to a logger. No engine code touches account data
  until the ledger, so there is nothing yet to enforce; the redaction layer it backs up is in place and tested.

**Problems and fixes.**
- `pyarrow` ships no type stubs: a mypy override, as in SPARQ.
- Python on Windows writes CRLF and the shell mangled two large inline edit scripts; edits now go through script
  files or exact `Edit` calls.

**M1.4a done:** PR #4 merged by the owner's instruction as `b29a86e` (2026-10-27).

**M1.3 approved for now** by the owner (2026-10-27: "looks good for now"). The source is in
`docs/design/mockups/` (made-up data; a privacy scan's only match was the variable name `navItems`), and stays
revisable before M1.8.

### 1.4b Kalshi feeds and recording (plan APPROVED 2026-10-27; branch `m1/4b-feeds`)

**Owner's answers:**
1. Record the watched set plus all-market trades.
2. Typed tables per channel.
3. **A watched set of up to 100 markets** (raised from 50), with the 3,000 deltas a second budget.
4. 25% of the read bucket.
5. A 200 GB storage budget.

**Builds** (in `ikars-engine`; uses IKARS's own read-only key). The Kalshi feed is a built-in source on the
M1.4a core. KAPS's production ingest is the design guide (read critically; its storage, PostgreSQL on the server
host, does not carry over):

1. **Discovery** (from KAPS `discovery.py`):
   - a full cycle at start and daily, and an incremental one every minute (`min_updated_ts`);
   - the bookmark advances only after a whole cycle succeeds, set two minutes back;
   - combo markets are skipped and counted;
   - a market that does not parse is set aside and counted, never failing the cycle.

   It keeps an in-memory market index (series, event, category, status, close time, rules) with a daily Parquet
   snapshot. It feeds the watched set, the drill-down tree and tradability (E).
2. **Exchange status and schedule** (from KAPS `exchange.py`): status polled every minute and stored only on
   change; the schedule daily. Used for tradability (E) and so that a pause is not mistaken for an outage.
3. **The live feed:**
   - ticker, trade and lifecycle for **all** markets;
   - `orderbook_delta` for a **watched set** (open question 3), each with an `OrderBook` (M1.2): a `seq` gap or a
     broken book asks for a fresh snapshot (`get_snapshot`);
   - every frame stamped with its received time and handed to the engine as an `Item`.
4. **The gap log** (from KAPS `gaps.py` and `pump.py`):
   - a reconnect, restart, `seq` gap, queue overflow or drop-oldest is written at once, from the **last frame
     actually received**, with a heartbeat saved every few seconds;
   - what is recoverable: trades by backfill, lifecycle by discovery;
   - what is not: ticker and books, marked `unrecoverable`.
5. **Trade backfill** (from KAPS `backfill.py`): the missed window from REST (live or historical by the cutoff), in
   one-hour slices, safe to repeat (de-duplicated by `trade_id`), never truncated; unfinished gaps are re-run at
   start.
6. **Recording** (D4 C):
   - typed tables per channel (open question 2);
   - the open hour in Arrow IPC, flushed every few seconds, then compacted to Parquet (zstd) at the hour;
   - a hard crash loses at most the last unflushed seconds, and that loss is in the gap log.
   - The replay source (M1.4a) gains readers for these tables, so a recorded hour replays through the same engine.
7. **The operational state store:** a `state.sqlite` (WAL, `synchronous=FULL`) holding the gap log, the discovery
   bookmark and the heartbeat, with the **versioned migration helper (backup first)** that M1.5's ledger reuses.
8. **The storage governor (G11):** a budget (open question 5); a warning at 80%; at 95% the oldest raw recordings
   are pruned first. The ledger and state are never pruned. It reports to health.
9. **The request budget (D21-3):** one token bucket for all of IKARS's REST calls, sized as a share of the
   account's read tier (open question 4), so IKARS backs off before it can crowd KAPS's ingest; 429s counted.
   The WebSocket subscription limit is learned passively (M1.2 decision): a "limit reached" error is logged and
   shrinks the watched set.
10. **Clock and latency (G10, J):** HTTP `Date` samples from REST replies and `sending_ts_ms` from frames feed the
    skew monitor; the signers use its offset. **Feed-to-alert latency** is stamped from frame receipt to the
    engine's listeners, against the 250 ms target.
11. `python -m ikars_engine run`: the live engine in the foreground, until Ctrl+C, printing health every minute.
    The background run mode is M1.6.

**Changes on this machine:**
- Recordings, `state.sqlite` and logs under `%LOCALAPPDATA%\IKARS` (or the portable folder).
- No other change; nothing installed, and no task scheduled (that is M1.6).

**New packages:** none (the standard library's `sqlite3`; `pyarrow` and `msgspec` are already in).

**Tests:** fakes for REST and the WebSocket; discovery's bookmark rules; changes-only status; gap-log rules for
each kind of loss; backfill idempotence and slicing; book resync on a `seq` gap; recording, compaction and replay
of a recorded hour; the governor's pruning order; the budget's share; migration with backup.

**Live checks** (GET and subscribe only):
- a **30-minute live run** on the watched set, measuring CPU, deltas a second, feed-to-alert p50/p99 against
  250 ms, recording size per hour and REST tokens used;
- a **forced reconnect**, giving gap rows and a backfill that matches Kalshi's trade count for the window;
- a **restart in the middle of an hour**, with nothing lost beyond the logged gap.

**Open questions.**
1. **What to record locally?**
   - *Recommended:* the **watched set only** (its books, trades and ticker), plus all-market trades. About a few
     hundred MB a day. The full all-market history already lives in KAPS's database and SPARQ's Parquet copies.
   - *Alternative:* everything KAPS records (all-market ticker and trades, about 3 GB a day) plus the books.
2. **Recording format:** *recommended:* typed tables per channel (ticker, trade, lifecycle, book snapshot, book
   delta), with real columns: smaller, faster to replay, and the same shape as SPARQ's live copies. *Alternative:*
   the generic item table from M1.4a (JSON payloads), simpler but several times larger.
3. **The watched set's size.** *Recommended:* up to **50 markets** and a budget of **3,000 deltas a second** in
   total, chosen in this order: open positions, live recommendations, markets open in the terminal, then the most
   active markets inside the human-paced filter (D17).
   - The hyperactive 15-minute crypto markets (around 800 a second each) are included only when a position, a
     recommendation or the owner asks for them.
   - The slice measures the CPU cost per delta and reports whether the budget should change.
4. **Request budget:** *recommended:* IKARS takes at most **25% of the account's read bucket** (50 of 200 tokens a
   second).
5. **Storage budget:** *recommended:* **200 GB** of the workstation's 2 TB for raw recordings (warn at 80%, prune
   the oldest at 95%).

#### 1.4b built (2026-10-27)

**Built** (`ikars-engine`, plus small additions to `ikars-core`; commits `b2cfe8c`, `71489fc`, `f2d57fb`):

| Area | What |
|---|---|
| State store | `state.sqlite` (WAL, `synchronous=FULL`) through a versioned migrator that backs the file up first and refuses a newer schema (M1.5's ledger reuses it): the gap log, bookmarks, heartbeats, the clean-shutdown mark, exchange-status changes. |
| Discovery | A full cycle at start and daily, incremental (`min_updated_ts`) every minute; the bookmark moves only after a whole cycle, two minutes back; combos skipped and counted; an in-memory market index with 24-hour volume and a Parquet snapshot. |
| Exchange | Status polled every minute, stored only on change; the schedule daily. |
| Live feed | Ticker, trades and lifecycle for every market; order books for the watched set (up to 100 markets, 3,000 deltas a second; pinned first, then by volume inside the 20 to 80 cent band; the hottest held out an hour when over budget); rebalanced every five minutes, removals before additions, keeping the set the exchange confirms. |
| Gaps | Every loss written at once from the channel's last frame (or from when the subscriptions went live): reconnects, `seq` gaps, restarts after a crash, downtime after a clean stop, refused resubscriptions (sent again with backoff until they hold), and frames that fail to process (counted; at most one gap a minute per channel). Trades are recovered by backfill, lifecycle by the next discovery cycle; ticker and books are marked unrecoverable. |
| Backfill | Live or historical trades by the cutoff, split at it, in one-hour slices, padded five seconds; only trades not already recorded are stored; five attempts, then `failed`. |
| Books | `OrderBook` per watched market; a broken book or a `seq` gap asks for snapshots off the pump, once per market until the snapshot comes, to the subscription's current id. |
| Recording | Typed tables per channel (trade, ticker, lifecycle, book snapshot, book delta) with fixed-point integer columns; the open hour in Arrow IPC, flushed every five seconds; closed two minutes after the hour and compacted to Parquet (zstd) in a worker thread; a two-step commit that `recover()` finishes after a crash; a closed hour is never reopened. |
| Governor | A 200 GB budget; a warning at 80%; the oldest compacted hours pruned at 95%; open hours, state and the ledger never pruned. |
| Request budget | One token bucket for every REST call, 25% of the account's read bucket (read at start); health shows tokens spent, waits, requests and 429s. |
| Clock and latency | HTTP `Date` samples (at most one a minute) and `sending_ts_ms` feed the skew monitor; the signer gets the offset; feed-to-route p50 and p99 in health. |
| Runner | `python -m ikars_engine run [--minutes N] [--root DIR]`: the live engine in the foreground, a one-line health summary every minute. |

**Verified.**
- 1,165 tests pass; coverage 95.2%; ruff, mypy and the data guard clean; gitleaks clean on the whole history.
- **30-minute live run** (01:43 to 02:14 UTC, across an hour boundary):

  | Measure | Result |
  |---|---|
  | Watched books | 100 of 100 ready; none held out |
  | Deltas a second | 956 to 2,044 (budget 3,000) |
  | Losses | no gaps, no `seq` gaps, no overflows, no bad frames (queue high-water 1,578 of 200,000) |
  | CPU and memory | 14 to 19% of one core (about 2% of the machine); 230 to 280 MB |
  | Feed to route | p99 at most 50 ms every minute (target 250 ms); p50 0.5 ms (bucket bounds, so conservative) |
  | Recording | about 44 MB compacted for 30 minutes, about 2.1 GB a day (57% book deltas, 39% all-market trades); hour 01 compacted live at 02:02 |
  | REST tokens | about 2,900 at start (full discovery of about 227,000 markets plus a backfill), then about 20 a minute, against 50 a second |

- **Forced reconnect** (the WebSocket closed from our side): back in 0.5 s; one gap per channel; the trade backfill fetched 71 trades. Kalshi lists 2,647 trades in the padded window and all 2,647 are recorded, none twice. The lifecycle gap closes at the next discovery cycle (seen live in the restart check).
- **Hard kill in the middle of an hour, then a restart:** five open hours left behind, all five recovered and compacted at start; "restart" gaps of 83 s with 17,668 trades backfilled. The earlier clean stop gave "stopped" gaps of 315 s with 83,044 trades backfilled. Trades recorded: 715,311 rows, no duplicates.

**Review (R14).**
- **Per commit (`b2cfe8c`):** findings fixed before the commit, including an hour-boundary row loss in the recorder.
- **Per merge (medium):** 10 findings, all fixed in `71489fc` with a regression test each:
  1. a stale book sid after a reconnect;
  2. the pump died silently on one exception, and a failed compaction stopped recording;
  3. downtime after a clean stop was not logged, and a crash during start looked clean;
  4. snapshot requests stalled the pump and were repeated;
  5. a repeated backfill could store a trade twice across hours;
  6. a quiet channel's loss was an empty gap;
  7. a refused resubscription was never sent again;
  8. the rebalance could exceed the cap and keep a set the exchange did not hold;
  9. compaction blocked the event loop;
  10. a crash during compaction could duplicate rows.
- **Per commit (`71489fc`):** one more: a row later than the grace period could reopen a closed hour while a worker compacted it. Fixed and tested.
- **Per commit (`f2d57fb`):** none.

**Kept, changed and why.**
- *Kept from KAPS:* the discovery bookmark rule, "log every loss from the last frame", backfill by window, the exchange watch.
- *Changed:* KAPS writes PostgreSQL on the server host; IKARS records typed Parquet hours on the workstation. KAPS's backfill trusted the database's unique key; IKARS checks recorded trade ids before writing, since Parquet has none. Losses during a clean stop are logged too (KAPS logged only crashes).

**Problems and fixes.**
- Production order-book messages carry no `clock`; the book treats it as optional (M1.2 finding, confirmed again).
- gitleaks is on the user PATH (WinGet links) but not in a session started before it was installed; the commit adds that folder for the hook.
- A known-ids window that ended at "now" missed a later gap in a test with a virtual clock; it now ends at the later of the two.

**Open for the owner.**
- **GPU detection** is in PLAN §4.1's line for M1.4b but was not in the approved slice plan, so it was not built. *Recommended:* move it to M1.6 (background run mode, which already reports the machine) or to M2's local-model slice, where it is first used.
- **Recording volume:** about 2.1 GB a day, above the plan's "few hundred MB" estimate, because all-market trades are busy (about 300 a second). The 200 GB budget holds about three months before the governor prunes.

**Owner's decisions (2026-10-27):** merge (PR #5 merged as `58abbe5`); GPU detection moves to **M1.6**; the
recording volume stays as is. A dependencies register, [DEPENDENCIES.md](DEPENDENCIES.md), now lists what IKARS
needs installed now and later, for the install instructions; each slice keeps it current.

**M1.4b done.**

### 1.5 Audit and tax ledger, portfolio (plan APPROVED 2026-10-27, all six answers as recommended; branches `m1/5a-ledger`, then M1.5b's)

**Proposed split** (like M1.4): **M1.5a** the ledger (store, chain, import, reconciliation, `verify`); **M1.5b**
the share (backups, restore drill) and the portfolio (positions and exposure, G1). Each is reviewed and merged on its
own. The tax-year package stays in M5 (D18).

**M1.5a builds** (a new `ledger` module in `ikars-engine`, on M1.2's `Account` reader and M1.4b's migrator):
1. **The ledger store:** `ledger.sqlite`, separate from `state.sqlite` (WAL, `synchronous=FULL`), versioned migrations
   with a backup first (G). Append-only: SQLite triggers refuse `UPDATE` and `DELETE`; corrections are adjustment
   records pointing to the record they correct.
2. **The record** (D18): sequence; kind (fill, settlement, deposit, withdrawal, transfer, fee, funding, interest,
   adjustment, app action); UTC time and the tax-zone time; Kalshi ids; tickers and the market title at the time;
   side, action, contracts (10^-2); price, fee, cash amount and balance after where given (dollars at 10^-6);
   Kalshi's raw JSON verbatim; IKARS context ids; `prev_hash` and `hash`.
3. **The hash chain:** SHA-256 over a defined canonical encoding (sorted keys, no whitespace, UTF-8, integers only,
   no floats), checked at every start and by `python -m ikars_engine ledger verify`; a daily checkpoint file with the
   day's last hash.
4. **The import:** the account's whole history on first run (fills live and historical, settlements, deposits,
   withdrawals, instance transfers), then new records every minute while the engine runs. Idempotent: a Kalshi id is
   never imported twice; records are added in Kalshi's time order within each import. Manual trades are included.
5. **Reconciliation** after each import: the cash the ledger explains against Kalshi's balance, and the contracts it
   explains against Kalshi's positions. A difference is shown in health and logged as a **reconciliation
   record**; it is never fixed silently. Interest has no API endpoint (M1.2): an unexplained balance change stays
   unexplained until the owner labels it (`ledger annotate`, which appends an adjustment record of the chosen kind).
6. **App actions** in the same chain: engine start and stop, settings changed, key loaded (its fingerprint only,
   never the id or path).
7. **The paper stream:** its own table and chain, clearly marked, never read by the tax package. Empty until the
   paper executor (M3/M4).
8. **Privacy:** the test deferred from M1.4a: account models and ledger rows never reach a log; replies, health and
   logs show counts and status, never amounts.

**M1.5b builds:**
1. **Backups to the share** (D18 tree under `ikars\`): nightly while the engine runs, and at start if the last one is
   older than a day. SQLite online backup to a temporary name, hash-checked, then renamed; `SHA256SUMS` and the
   checkpoint beside it. Every path is resolved under `<share>\ikars\` and anything outside is refused; nothing is
   written without `.ikars-backup-marker`. Retention as D18 (ledger dailies 90 days, monthlies and checkpoints forever,
   state dailies 30 days). The ledger is also backed up before every ledger migration (G).
2. **`ledger restore-drill`** (G14): restore the newest copy from the share to a scratch file, verify its chain, compare
   its head with the local ledger's.
3. **Portfolio (G1, model only; the view is M1.8):** positions from the ledger, cross-checked with Kalshi's positions;
   FIFO lots for cost basis; marked to market from the live feed's last prices; unrealised P&L **after exit costs**
   (D7 fees on exit); exposure by event, series and category (from the discovery index); balance.

**Changes on this machine and the network:**
- `ledger.sqlite`, checkpoints and the paper ledger under `%LOCALAPPDATA%\IKARS\data`.
- **On the NAS share: creates `ikars\` and `.ikars-backup-marker`** (owner's go-ahead needed), then the D18 tree inside
  it. Nothing outside `ikars\` is read, listed or written. Access uses the owner's saved Windows credential.
- The share's real path goes only in the local settings file, never in the repository.
- No scheduled task (M1.6 adds the logon task, so backups run while the engine runs).

**New packages:** `tzdata` (Apache-2.0): Windows Python has no time-zone database, so `zoneinfo` needs it for the tax
time zone. Added to DEPENDENCIES.md.

**Tests:** canonical encoding and hash vectors; a broken, reordered or edited chain is caught; the triggers refuse edits;
idempotent import against a fake account (made-up records, per docs/TESTING.md); reconciliation exact, short and
over; annotation; migration with backup; the share guard (outside paths, a missing marker,
`\127.0.0.1\ikars-no-such-share`); interrupted backup copies; the restore drill; FIFO lots and unrealised P&L in
integers; nothing from the account in logs.

**Live checks** (GET only; results reported as counts and pass or fail, never amounts):
- the full history imports; a second import adds nothing; `verify` passes;
- reconciliation is **exact** against Kalshi's balance and positions (the M1 gate), or every difference is shown;
- M1.5b: a backup lands on the share inside `ikars\`, a second run is idempotent, the restore drill passes; the
  portfolio's positions match Kalshi's.

**Open questions.**
1. **Split into M1.5a and M1.5b?** *Recommended:* yes, as above; each about the size of M1.4a.
2. **Go-ahead to create `ikars\` and its marker on the NAS share** (the one in memory), in M1.5b. *Recommended:* yes.
3. **The tax time zone** (year boundaries and the local time on each record). *Recommended:* the workstation's zone
   as the default setting; please confirm which zone you file in.
4. **Import cadence:** *recommended:* REST every minute while the engine runs (portfolio reads are allowed for the
   read-only key; M1.2 confirmed them). *Alternative:* Kalshi's private fill and position WebSocket channels for
   real time; that relaxes M1.2's market-data-only guard, and is better left for M7.
5. **Unexplained balance changes (interest, incentives):** *recommended:* shown and recorded as differences, and
   classified only by you (`ledger annotate`), never guessed.
6. **Lot method for the portfolio's cost basis:** *recommended:* FIFO, the same default as D18's tax package.

**Owner's answers (2026-10-27):** all six as recommended: split into M1.5a and M1.5b; go-ahead to create `ikars\`
and its marker on the share (in M1.5b); the tax zone is the workstation's (`America/Denver`); REST import every
minute; unexplained money labelled by the owner; FIFO.

#### 1.5a built (2026-10-27)

**Built** (`ikars-engine`; commits `3b272fb`, `8314c9e`, `1072db0`):

| Area | What |
|---|---|
| Store | `ledger.sqlite` through the versioned migrator (backup first); SQLite triggers refuse `UPDATE` and `DELETE`; real and paper chains; a unique key per Kalshi record, so imports are idempotent. The migrator now runs trigger bodies (statements split with `sqlite3.complete_statement`). |
| Chain | Canonical JSON bodies (sorted keys, no spaces, no floats; Kalshi's raw JSON kept verbatim as one text field), SHA-256 linked by `prev_hash`; `verify` walks sequence, hash, link and indexed columns, and compares the newest daily checkpoint (so cut-off records or an older copy are caught). |
| Import | The whole history first (live and archived fills, settlements, deposits, withdrawals, instance transfers), then every minute from a day before each source's newest record; live and archived fills every time. Market titles from the live index or, failing that, market data (once per market). |
| Cash | Kalshi's netting in exact millionths: a fill closes the opposite side at its price before opening; fees on top; a settlement pays its revenue; deposits, withdrawals (from request, reversed if failed or returned) and event-contract transfers. |
| Archived settlements | Kalshi stops listing settlements once positions are archived and has no archived equivalent. The ledger derives each from the market's result, only when its realised result then equals Kalshi's archived realised P&L exactly; marked `derived`, with Kalshi's archived row and the result as evidence. A listed settlement for a market already derived is skipped. |
| Reconciliation | Cash against Kalshi's balance, positions against Kalshi's; a reconciliation record when the result changes; health shows counts and booleans only. |
| App actions | Engine start and stop, key loaded (public-key fingerprint only), settings changed (a hash), in the same chain. |
| Service | Checks the chain before the feed starts; a broken chain stops every append; the import each minute; a checkpoint after each. |
| Commands | `python -m ikars_engine ledger verify | status | import | annotate`; `import` and `annotate` refuse a broken chain. |
| Tax zone | `taxzone.py`: the setting, else the workstation's zone (Windows name mapped by CLDR's table), else UTC with a health flag; settings v3. New package `tzdata` (Apache-2.0). |

**Verified.**
- 1,198 tests pass; coverage 94.9%; ruff, mypy, data guard and gitleaks clean. Made-up records only.
- Tests cover: canonical text and float refusal; tampering (edited, deleted, reordered, rebuilt with a valid hash,
  a float slipped in, cut back behind the checkpoint); the triggers; the netting cash rule; idempotent imports;
  reconciliation exact and not; reversals; derived settlements confirmed and not (a cent off, scalar, no market
  data); the double-settlement guard; app actions; a broken chain; the commands; the tax zone and the settings
  migration; **account values never in a log** (the test deferred from M1.4a).
- **Live** (GET only; results as counts):

  | Check | Result |
  |---|---|
  | First import | 4 deposits, 147 fills, 25 settlements (7 listed, 18 derived from the archive), 1 withdrawal, 6 transfers |
  | Reconciliation | **exact**: cash to the millionth and every position |
  | Second import | adds nothing |
  | `verify` | passes, with the checkpoint; 188 records after the engine's app actions |
  | Engine, 2 minutes | ledger checked before the feed, imports each minute, still exact, clean shutdown |
  | Titles | every fill and settlement record (172 of 172) |

  The first live import was **not** exact. A diagnosis that printed only counts compared each market with Kalshi's
  position records:
  - fees matched on all 41 markets;
  - the netting rule matched all 16 markets closed before settlement;
  - all 18 differences were finalized markets that had moved to Kalshi's archive, with no settlement record;
  - the 7 listed settlements were off because their `fee_cost` repeats the fills' fees.

  Both were fixed (`8314c9e`) and the import has reconciled exactly since.

**Review (R14).**
- **Per commit (`3b272fb`):** 3 fixed: the commands appended on a broken chain; a float in a tampered body crashed
  `verify`; a client could be opened before a bad tax-zone setting failed. gitleaks flagged a made-up 16-hex
  fingerprint in a test; the value was changed, not allow-listed.
- **Per commit (`8314c9e`):** one candidate (a flat market as an archive candidate); no change: flat markets are
  left out of the replay and the derived record is keyed by ticker.
- **Per merge (medium):** 10 candidates; 9 fixed in `1072db0`:
  1. later imports never read archived fills;
  2. a failure after the history kept every run in first-import mode;
  3. one start time for fills and settlements;
  4. three replays per import, and the chain walk after the feed started;
  5. a fragile shutdown;
  6. a possible double settlement;
  7. records without titles;
  8. truncation undetected by `verify`;
  9. a stale resume point.

  The tenth (the v2 settings migration) needs no change: no v2 file was ever written, and the old value was only
  the default.
- **Per commit (`1072db0`):** none. That review ran just after the commit, not before it: a slip in the order,
  noted here.

**Kept, changed and why.**
- *New:* neither KAPS nor SPARQ has a money ledger (SPARQ's `ledger.py` is a database-durability log).
- *Kept:* M1.2's account reader and its cents-to-millionths rules; M1.4b's migrator.

**Problems and fixes.**
- Kalshi's settlement `fee_cost` is not a separate charge, and archived settlements have no records; both found
  live, confirmed against Kalshi's realised P&L, and recorded in the module's notes.
- A long inline shell script failed to parse twice; long edits now go through script files or whole-file writes.

**M1.5a done**, subject to the owner's merge. Next: M1.5b (backups to the share, the restore drill, the portfolio).

#### 1.5b built (2026-10-27)

**Built** (`ikars-engine`; commits `682b581`, `9ff0be2`):

| Area | What |
|---|---|
| Share guard | Every path is resolved under `<share>\ikars\`; anything outside is refused. Nothing is written without `ikars\.ikars-backup-marker`. `backup init` creates only that folder and marker, and saves the share in the local settings only after the share checks out. The real path is never printed or committed. |
| Copies | Each copy is a consistent SQLite snapshot (online backup) staged in a folder of the run's own, written under a unique temporary name, read back and hash-checked, then renamed. A dated folder is built whole and swapped in, and leftovers of a dead run are swept after an hour. |
| Tree | Per D18: ledger daily, monthly and checkpoints; the state daily; the settings; the pre-migration copy (G). Each folder has a `SHA256SUMS`. Only missing checkpoints and the newest two are sent. Retention: ledger dailies 90 days, state 30; the rest kept forever. |
| Service | Backs up daily while the engine runs, and at start when the last backup is over a day old. It runs in a daemon thread (a stuck NAS never holds up the exit), and health never touches the share. |
| Restore drill | `ledger restore-drill`: the newest daily copy, its hashes, its chain, its checkpoint, and its head in the local ledger. An unreachable share or a read error is a result, not a traceback. |
| Portfolio (G1) | FIFO lots with exact fee splits; marked at the YES or NO bid after the taker fee, rounded up to the cent; unpriced rather than guessed; no bid means worth nothing; exposure by event, series and category. Its cash comes from the same `Book` the reconciliation uses. In health as counts, with the lots cached by the ledger's head; `ledger portfolio [--summary]` for the owner. |
| Supporting | Discovery keeps each series' fee terms; the source keeps each market's YES bid and ask; `open_store` hands a migration's backup to a callback. |

**Verified.**
- 1,221 tests pass; coverage 94.5%; ruff, mypy, data guard and gitleaks clean. The "share" in tests is a folder of
  the test's own; account records are made up.
- **Live** (on the owner's go-ahead):
  - the share is reachable;
  - `ikars\` and its marker were created, and nothing else;
  - the real ledger was started in `%LOCALAPPDATA%\IKARS` (183 records from Kalshi, reconciled exactly);
  - backups ran twice the same day;
  - status is clean;
  - **the restore drill passes** (188 records);
  - the engine runs with backups configured, and its only warnings are the expected "stopped" gaps of the four
    feed channels.
- The portfolio holds **0 positions**, matching Kalshi's 0 open positions. So the pricing path is so far tested
  offline only; it is checked live the first time a position is held.

**Review (R14).**
- **Per commit (`682b581`):** 3 fixed:
  1. no share set, or an unreachable one, crashed the commands;
  2. one unreadable series failed the whole portfolio;
  3. `backup init` saved a share before checking it.
- **Per merge (medium), all 10 fixed in `9ff0be2`:**

  | # | Finding | Fix |
  |---|---|---|
  | 1 | The engine's and the command's backups shared a staging folder and temporary names | Each run has its own staging folder and unique names |
  | 2 | A day folder was not replaced as a whole, so a failure part-way left mixed files | The folder is built whole, then swapped in; stray leftovers swept after an hour |
  | 3 | Ledger commands never sent the pre-migration copy to the share | `migration_copier` (now in `share.py`) is used by the commands too |
  | 4 | Portfolio health replayed the whole ledger on every call | The lots are cached by the ledger's head sequence |
  | 5 | A NO position with a YES ask of 0 was valued at $1.00 | No YES offered means no NO bid: worth nothing |
  | 6 | A hung NAS could block the engine's exit (the default executor) | Backups run in a daemon thread |
  | 7 | The drill reported an unreachable share as "no backup", and a read error as a traceback | Checks the share first; an I/O error becomes a result |
  | 8 | Every backup re-read every checkpoint on the share, a list that only grows | Only missing checkpoints and the newest two are sent |
  | 9 | The portfolio's replay duplicated the import's | One pass that feeds the import's `Book` |
  | 10 | `ledger portfolio` replayed the ledger twice | The holdings are computed once and passed in |
- **Per commit (`9ff0be2`):** none.

**Kept, changed and why.**
- *Kept from KAPS:* the marker-file rule for the share (KAPS's `.kaps-backup-marker`), applied to IKARS's own
  subtree.
- *New:* everything else. KAPS backs up PostgreSQL with pgBackRest, which IKARS never touches.

**Problems and fixes.**
- Long inline shell scripts with backslashes or nested quotes failed to parse; edits now go through script files
  written with the file tool, or direct edits.

**M1.5b done**, subject to the owner's merge. **M1.5 complete** with it.

**M1.5 done:** PR #8 merged as `5b6f702` (2026-10-27).

### 1.6 Run mode and alerts (plan APPROVED 2026-10-27, all four answers as recommended; branch `m1/6-run-mode`)

**Builds** (in `ikars-engine`; no new packages):
1. **Background run mode (G5):**
   - `python -m ikars_engine serve`: the engine without a console (`pythonw`), until asked to stop.
   - **One instance only:** a lock file in the data folder, so the auto-start and the watchdog never run two
     engines.
   - **Stop:** `python -m ikars_engine stop` writes a stop request; the engine shuts down cleanly within seconds.
   - `python -m ikars_engine status` reads the engine's heartbeat and health snapshot (counts only).
   - The engine writes a small health snapshot file every minute, for the watchdog and `status`. M1.7's API
     replaces this.
2. **Auto-start at login:** a per-user entry under `HKCU\...\Run` (no admin rights). This shell has no admin
   token, and a logon-triggered scheduled task usually needs one. `install-autostart` and
   `remove-autostart` commands manage it.
3. **Keep awake while running:** Windows' `SetThreadExecutionState`, so the system doesn't sleep while the
   engine runs. The display may still turn off. It's released when the engine stops.
4. **The watchdog (from KAPS's `watchdog.py`, adapted to Windows):** a separate short-lived process, run every
   5 minutes by a per-user scheduled task (time-triggered, no admin rights needed; checked live).
   - It reads the heartbeat and health snapshot.
   - If the engine has died and wasn't asked to stop, it restarts it, at most 3 times an hour (more is reported,
     not retried).
   - It alerts on: engine down; feed stale (no frame for 10 minutes while the exchange is open); backup failed or
     over 36 hours old; ledger chain broken; reconciliation not exact; storage over its warning level.
   - **Kept from KAPS:** a problem must hold for 2 checks in a row before it alerts; a reminder every 6 hours
     while it lasts; one message when it clears; its own small state file, so it never repeats itself; a
     notification that fails to show is retried at the next check.
5. **The alert framework (H):**
   - priority levels: urgent, high, normal, low;
   - de-duplication by key, and a minimum interval per key (throttling);
   - **quiet hours** (a setting), during which only urgent alerts pop up;
   - lower priorities held for an **hourly digest**.
   - It is one module, used by the watchdog now and by the engine's recommendations in M4.
6. **Workstation notifications (G6, as amended):** **no phone pushes** (owner, 2026-10-27).
   - Alerts appear as **Windows notifications (toasts)** on the workstation. They use Windows' own notification
     classes through Windows PowerShell: no package, nothing sent off the machine.
   - IKARS registers its own notification name under `HKCU` (user space), so a toast says "IKARS", not
     "Windows PowerShell", and Windows' own settings (Do Not Disturb, per-app mute) apply to it.
   - Every alert is also written to an alert log, `alerts.jsonl` in the data folder, which the terminal shows from
     M1.8, so an alert dismissed or missed is never lost.
   - `alerts test` shows one test notification. Alerts carry status text only, no account values.
7. **GPU detection (D15, moved here):** at start, `nvidia-smi` (shipped with the driver) reports the GPU's name,
   memory, driver and CUDA version into health; without an NVIDIA GPU, health says "CPU only". There is no new
   package. NVML through `nvidia-ml-py` is the alternative, if more is needed later.

**Changes on this machine:**
- A `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value, `IKARS Engine`.
- A per-user scheduled task, `IKARS Watchdog`, every 5 minutes.
- The notification name `IKARS` under `HKCU\Software\Classes\AppUserModelId`.
- Test notifications on the workstation.
- All of it is user space, no admin rights; the `remove-autostart` command undoes all three.

**Tests:**
- the watchdog's rules: confirmation, reminders, clearing, restart limits, a failed notification retried;
- the alert framework: priority, de-duplication, throttling, quiet hours, the digest;
- the alert log; the toast's text and escaping, with a fake notifier;
- the lock file, and the stop request;
- GPU parsing, from captured `nvidia-smi` text;
- the Run-key, task and notification-name commands, with fakes.

**Live checks:**
- `serve` starts in the background and `stop` stops it cleanly;
- a second `serve` is refused;
- after a killed engine, the watchdog restarts it within one check and shows one "engine down" then one
  "recovered" notification;
- auto-start works after you sign out and back in (you do that step);
- `alerts test` shows a notification named IKARS;
- GPU detection reports the RTX 5070 Ti.

**Open questions.** (The ntfy topic question was withdrawn: phone pushes are out.)
1. **How notifications are shown:**
   - *Recommended:* Windows' own notification classes through Windows PowerShell, with no new package.
   - *Alternative:* the `windows-toasts` Python package (Apache-2.0), which is tidier code but a new dependency.
2. **Quiet hours:** *recommended:* **22:00 to 07:00** workstation time, during which only urgent alerts pop up
   (engine down, ledger chain broken or backup failed). The rest wait for the digest and the alert log. Is that
   the window you want?
3. **Keep awake:** *recommended:* keep the **system** awake while the engine runs, and let the display sleep.
   *Alternative:* no keep-awake, so the PC sleeps as usual and the engine records the gap.
4. **Watchdog interval:** *recommended:* **every 5 minutes**, with the 2-check confirmation, so an alert comes
   about 10 minutes after a failure. *Alternative:* every minute, like KAPS, for faster alerts at a little more
   noise.

**Owner's change (2026-10-27):** phone pushes (item 6, ntfy) removed; notifications stay on the workstation.
PLAN G6, the M1 and M4 outlines, the M1.6 row and DEPENDENCIES were amended to match.

**Owner's answers (2026-10-27):** all four as recommended:
1. notifications through Windows' own classes via Windows PowerShell, with no new package;
2. quiet hours from 22:00 to 07:00, when only urgent alerts pop up;
3. the system kept awake while the engine runs, the display free to sleep;
4. the watchdog every 5 minutes, with the 2-check confirmation.

#### 1.6 built (2026-10-27)

**Built** (`ikars-engine`; commits `ad60325`, `532a3f2`, `fd4fd1a`; no new packages):

| Area | What |
|---|---|
| Run mode | `serve`, `stop`, `status`.<br>**Lock:** a single-instance lock that Windows releases however the engine ends. `serve` waits up to 3 s for a probe to let go, and the probe writes nothing.<br>**Stop:** a stop request (checked every 2 s), and a stopped-by-owner mark the watchdog respects.<br>**Snapshot:** a "starting" snapshot at once, then a minute health snapshot with counts, ages and booleans only, written whole with `fsync`.<br>**Keep-awake:** the system stays awake while the engine runs.<br>**Failures:** a start-up failure is written to the log, and so is an engine task that ends by itself, which also makes `serve` exit. |
| Watchdog | KAPS's rules: a problem must show on 2 checks; a reminder every 6 hours; one message when it clears; its own state file; a notification that fails, or is throttled, is tried again.<br>**Restarts:** at most 3 an hour, never an engine you stopped, said at once.<br>**Alerts on:** engine down or silent (with a 10-minute start-up grace), feed stale while trading, the ledger chain broken or not reconciling, backups failing or over 36 hours old, storage near its budget.<br>**Log:** its own `watchdog.jsonl`. |
| Alerts | Priorities, de-duplication, throttling, quiet hours 22:00 to 07:00 (a bad setting falls back to that, with a warning), an hourly digest, and the alert log `alerts.jsonl` (failed notifications included).<br>**Windows notifications** come from Windows PowerShell's WinRT classes, with the text passed through environment variables and XML-escaped. **No phone pushes** (your decision, 2026-10-27). |
| Auto-start | Three entries, installed and removed together, all user space, with `--root` resolved: the HKCU Run entry `IKARS Engine`, the scheduled task `IKARS Watchdog` (every 5 minutes), and the notification name `IKARS`. |
| GPU | `nvidia-smi` gives the name, memory, driver and CUDA version ("CUDA Version" or the newer "CUDA UMD Version"). Detection runs in the background, and with no NVIDIA card health says "CPU only". |
| Shared | `files.write_text_atomic` (with `fsync`); `threads.in_daemon_thread` (for a NAS or a driver that hangs); `logs.configure(filename=...)`. |

**Verified.**
- 1,255 tests pass; coverage 93.7%; ruff, mypy, the data guard and gitleaks are clean. Windows is faked in the
  tests, except for one real round trip on a throwaway HKCU key.
- **Live**, on this workstation:

  | Check | Result |
  |---|---|
  | `install-autostart` | all three entries installed with no admin rights |
  | `alerts test` | the notification was shown (the owner to confirm they saw it) |
  | `serve` in the background | running; the snapshot fresh; GPU **NVIDIA GeForce RTX 5070 Ti**; the ledger verified and reconciling exactly; backups configured |
  | A second `serve` | refused |
  | Hard kill, then a watchdog pass | the watchdog restarted the engine (a new process reporting within 10 s). The "engine restarted" notice was **held for the morning digest**, as quiet hours require (it was after 22:00). The scheduled task had already restarted the engine once on its own, when a start failed for lack of the key variables in this editor's session. |
  | `stop` | a clean stop; the watchdog then left the engine alone; no errors in the log |
  | The fixed code | restarted on it: the "starting" snapshot, then the real one, GPU in the background, `watchdog.jsonl` written, no errors |
- **Left for the owner:** sign out and back in, to see the engine start from the Run entry. Until then the engine
  keeps running in the background from this session.

**Review (R14).**
- **Per commit (`ad60325`), 4 fixed:**

  | # | Finding | Fix |
  |---|---|---|
  | 1 | A throttled alert was recorded as told, so a recurring problem went quiet | Not recorded; due again at the next check |
  | 2 | The `shown` count included throttled and held alerts | Counts only notifications shown |
  | 3 | `install-autostart` ignored `--root` | Both the engine and the watchdog get the folder |
  | 4 | A run under a minute printed no summary | A last summary on exit |
- **Live finding (`532a3f2`):** a background engine that failed to start left no trace. Now it is logged.
- **Per merge (medium), all 10 fixed in `fd4fd1a`:**

  | # | Finding | Fix |
  |---|---|---|
  | 1 | A snapshot write blocked by a reader could stop the engine | Logged; the next minute writes it |
  | 2 | An engine task that ended by itself kept the lock and fresh snapshots | `EngineStopped`; `serve` exits; the watchdog restarts it |
  | 3 | Bad quiet-hours settings crashed every watchdog pass | Fall back to 22:00 to 07:00, with a warning |
  | 4 | A lock probe could turn a starting engine away, and wrote its own pid | The probe writes nothing; `serve` waits up to 3 s |
  | 5 | A new engine was judged by the last run's snapshot | A "starting" snapshot and a 10-minute grace |
  | 6 | A relative `--root` resolved elsewhere later | Resolved before it is stored |
  | 7 | A failed notification never reached the alert log | Logged as "failed" |
  | 8 | The watchdog had no log (and `pythonw` has no stderr) | Its own `watchdog.jsonl` |
  | 9 | Duplicated `pythonw` lookup, and three atomic writes without `fsync` | Shared helpers |
  | 10 | GPU detection could hold up the start by up to 20 s | Runs in the background |
- **Per commit (`fd4fd1a`):** 1 fixed: an unexpected error in background GPU detection now reads "detection
  failed" instead of "detecting" forever.

**Kept, changed and why.**
- *Kept from KAPS's watchdog:* the confirm, remind and clear rules, the state file, and a retry for an alert that
  wasn't delivered.
- *Changed:* delivery is Windows notifications (no ntfy); the observations are a lock and a snapshot, not `systemd`
  and PostgreSQL; the restart, which `systemd` did for KAPS, is now the watchdog's job.

**Problems and fixes.**
- This editor's shell predates the key variables. Live runs load them into the process, and at sign-in Windows
  gives them to the Run entry and the task.
- The engine's lock file can't be read while it's held, so the process ID comes from the snapshot.

**M1.6 done**, subject to your merge and your sign-out check.

**Owner (2026-10-27):** saw the test notification. **M1.6 done:** PR #9 merged as `2efc95f`. The sign-out check of
auto-start is still the owner's to do.

### 1.7 Loopback API and contracts (plan APPROVED 2026-10-27, all four answers as recommended; branch `m1/7-api`)

**Builds** (in `ikars-engine`, plus a C# contracts project for the terminal):
1. **The API server in the engine's own process:** Starlette on uvicorn, started inside the engine's event loop,
   listening on `127.0.0.1` only.
2. **Access guard:**
   - a **per-session token**: random, made at each start, and required on every request
     (`Authorization: Bearer`);
   - it is written with the port to `api.json` in the data folder, readable by the owner only (the ACL check from
     `ikars-core`), and never logged;
   - the `Host` header must be loopback, which guards against DNS rebinding;
   - no CORS, and any browser `Origin` is refused.
3. **Version handshake (G):** `GET /v1/hello` gives the API version, the engine version and a hash of the
   contracts schema. The terminal refuses a different major version and says why.
4. **Snapshots over HTTP** (read-only; there are no trading commands before M7):
   - health, the market index (search and paging), one market (its quote and the book's top levels), a full
     book;
   - the portfolio, ledger status and ledger records (paged; your own data, local only);
   - the alert log, and recommendations (empty until M1.12).
5. **Deltas on one stream:**
   - topics: market quotes and books for the markets the terminal subscribes to, portfolio changes, new
     alerts, and health every 5 seconds;
   - subscriptions are changed over HTTP;
   - updates are **coalesced per topic at about 10 Hz** on the engine side, so a burst of 800 deltas a second never
     floods the terminal (D3, J);
   - each event carries an id, so a reconnect can resume.
6. **Contracts:**
   - every payload is a `msgspec` struct in one `contracts` module, exported as **JSON Schema** to
     `contracts/ikars-api.schema.json`;
   - CI fails if the committed schema differs from the generated one;
   - the engine also writes a **golden example** of each payload, and the C# test deserialises every one into its
     C# record with unknown fields refused, so the two sides cannot drift silently.
7. **Latency (J):** the time from a feed frame arriving to its event leaving the stream goes into health, with p50
   and p99.
8. **Commands:** `serve` and `run` start the API too; `status` shows its port, never the token.

**Changes on this machine:** an `api.json` file (port and token, owner-only) in the data folder while the engine
runs, and a loopback port while it listens. Nothing else, and no firewall change: loopback only.

**New packages:**
- `starlette` (BSD-3-Clause) and `uvicorn` (BSD-3-Clause), with their dependencies `anyio` (MIT), `h11` (MIT),
  `click` (BSD-3-Clause) and `sniffio` (MIT or Apache-2.0);
- no new NuGet packages: the C# side uses `System.Text.Json`.

**Tests:**
- the guard: no token, a wrong token, a foreign `Host`, a browser `Origin`;
- the handshake;
- each snapshot route against made-up state;
- the stream: subscribe, coalescing, resume after a reconnect, a slow reader;
- the schema-drift check;
- the C# golden-example test.

**Live checks:**
- the API answers on loopback only, and is refused from another address;
- the handshake;
- snapshots of live markets, and the portfolio;
- the stream carries live quotes for subscribed markets, at no more than the coalescing rate;
- feed-to-stream latency against the 250 ms target;
- the token never appears in a log.

**Open questions.**
1. **Stream transport:**
   - *Recommended:* **Server-Sent Events** over HTTP, with subscriptions changed by HTTP calls. KalTerm3's terminal
     client already reads SSE and has tested reconnect code to reuse in M1.8. The engine side is simpler, and the
     stream only flows one way.
   - *Alternative:* one **WebSocket**, as D3 says. It is two-way, so subscriptions would travel on the socket, but
     it means writing a new client.
   - Choosing SSE amends D3's "one WebSocket" (its HTTP and JSON part stays).
2. **Port:**
   - *Recommended:* a **free port chosen at each start**, written to `api.json` with the token, so it never clashes
     with KalTerm3 (5280) or anything else.
   - *Alternative:* a fixed port, for example 5290.
3. **Ledger records over the API:** *recommended:* yes, paged, token-guarded and loopback only, so the terminal can
   show your ledger (M1.8). *Alternative:* status and counts only until M5.
4. **C# contracts:**
   - *Recommended:* **hand-written C# records, checked by the golden examples**. This needs no new tooling and
     catches every drift.
   - *Alternative:* generate them from the JSON Schema, with a code generator such as NJsonSchema (a new NuGet
     package and build step).

**Owner's answers (2026-10-27):** all four as recommended:
1. Server-Sent Events, with subscriptions over HTTP (D3 amended);
2. a free port at each start, recorded in `api.json`;
3. ledger records over the API, paged;
4. hand-written C# records, checked by golden examples.

#### 1.7 built (2026-10-27)

**Built** (commits `07a0775`, `9772736`, `28e1334`; new packages `starlette` and `uvicorn`, BSD-3-Clause):

| Area | What |
|---|---|
| Contracts | Every payload is a `msgspec` struct in `contracts.py`, exported as JSON Schema (`contracts/ikars-api.schema.json`) and golden examples (`contracts/examples`, 19 files). A test fails on drift; `python -m ikars_engine contracts export` regenerates them. |
| Server | Starlette on uvicorn inside the engine's event loop, on `127.0.0.1` and a free port at each start. uvicorn configures no logging (it fails under `pythonw`). `api.json` holds the port and token: written once the server is serving, the owner's alone (ACL-checked), and removed at stop. |
| Guard | A per-session token compared in constant time; a loopback `Host` only (IPv6 included); any browser `Origin` refused. |
| Snapshots | hello (the handshake: API version, engine version, schema hash), health, markets (search and paging), a market, a book, the portfolio, ledger status and records (paged, newest first), the alert log (by line), recommendations (empty until M1.12). |
| Stream | Server-Sent Events (D3 amended); subscriptions over HTTP; quotes and books sent only when changed, coalesced at 100 ms; the portfolio when it changes; new alerts read through an incremental line index; health every 5 s. At most 8 streams, an exact cap. Streams end at stop. |
| Latency (J) | `api.feed_to_stream_ns` in health, from a frame's arrival to its event leaving. Only updates are timed, and quotes and books keep separate change times. |
| C# | `Ikars.Contracts`: hand-written records with snake_case names; unknown fields, missing required fields and wrong nulls are refused. `Ikars.Contracts.Tests` round-trips every golden example and checks the schema's names. No new NuGet packages. |

**Verified.**
- 1,277 Python tests pass (94% coverage of `api.py`, 93.8% overall), and 24 .NET tests pass (100% of
  `Ikars.Contracts`). ruff, mypy, pip-audit, dotnet format, the data guard and gitleaks are all clean.
- **Live**, against the background engine:

  | Check | Result |
  |---|---|
  | Handshake | API 1.0; a request without the token is refused |
  | Snapshots | 211,362 markets indexed; a live book ready (33 and 58 levels); portfolio; the ledger verified and **exact**; a ledger page |
  | Stream (30 s, 5 watched markets) | 1 opened, 38 quote, 565 book, 1 portfolio and 5 health events; **at most 7.8 events a second per market** |
  | Feed to stream | **p50 ≤ 20 ms, p99 ≤ 200 ms** (target 250 ms; log-bucket bounds) |
  | Exposure | nothing answers on the machine's network address; **the token is in no log** |
  | Stop with a stream open | done in 2.4 s; `api.json` removed |

**Live findings, fixed (`9772736`).**
1. **uvicorn's own logging set-up crashed the background engine.** Under `pythonw` there is no console, so its
   formatter failed at start. `serve`'s start-up logging (M1.6) caught it, and the server now sets
   `log_config=None`. A test runs the server with no stdout or stderr.
2. **The first live p99 was 2,000 ms.** That was a measurement error: the first event after subscribing is
   a market's current state, which is as old as the market is quiet. Now only updates are timed.

**Review (R14).**
- **Per commit (`07a0775`), 3 fixed:**
  1. A bare IPv6 `Host` was refused.
  2. A cut-back alert log froze a stream's alerts.
  3. `api.json` was written before the server was serving.
- **Per commit (`9772736`):** none. A flaky M1.5a test was also fixed: its secret "777" matched a log timestamp
  by chance.
- **Per merge (medium), all 10 fixed in `28e1334`:**

  | # | Finding | Fix |
  |---|---|---|
  | 1 | An open stream kept uvicorn waiting forever at stop | Streams end at stop; a 3 s graceful limit |
  | 2 | Malformed subscription JSON was a bare 500 | A 400 `ApiError` |
  | 3 | A market dropped and added back got no current state, and was timed as an update | Sent afresh, not timed |
  | 4 | Alert line numbers split differently in two readers (U+2028) | Split on `\n` only, everywhere |
  | 5 | A stream that was never iterated stayed registered; there was no cap | Registered when it runs; at most 8 |
  | 6 | A ticker frame that changed no quote moved its time (latency understated) | Only real quote changes move it |
  | 7 | A keep-alive branch that could never run | Removed (health every 5 s) |
  | 8 | The whole alert log was read per page and per stream start | An incremental line index |
  | 9 | The alert log's name was repeated in two places | `alerts.ALERT_LOG` |
  | 10 | `Ledger.page` built its parameters in a roundabout way | Simplified |
- **Per commit (`28e1334`):** 1 fixed: the stream cap was not exact when requests arrived together. An
  `error` stream event was added to the contract.

**Kept, changed and why.**
- *Kept from KalTerm3:* Server-Sent Events for the stream, so its client and reconnect code carry over to M1.8.
- *Changed:* KalTerm3's API was a separate server on a fixed port. IKARS's runs inside the engine, on a free
  port, behind a session token.

**M1.7 done**, subject to the owner's merge.

**M1.7 done:** PR #10 merged as `0ba5d83` (2026-10-27).

### 1.8 Terminal shell (plan APPROVED 2026-10-27, all five answers as recommended; branch `m1/8-terminal`)

**Proposed split** (like M1.4 and M1.5): **M1.8a** the shell, the client, navigation and the main screens;
**M1.8b** hotkeys, layouts, themes, settings with the first-run wizard, and the support bundle. Each is reviewed
and merged on its own.

**M1.8a builds** (new `Ikars.Client` and `Ikars.Terminal` .NET projects, on `Ikars.Contracts`):
1. **The client (`Ikars.Client`):**
   - reads `api.json`, sends the token, and refuses a different API major version (the handshake);
   - typed calls for every snapshot route;
   - the stream from KalTerm3's tested SSE reader and reconnect code (resuming alerts by line), changing
     subscriptions as screens open and close.
2. **Connect or start:** the terminal connects to the running engine. If none is running it starts the engine with
   the command `install-autostart` registered, and shows progress while the engine starts up.
3. **Navigation (R9):**
   - one navigation service over a typed reference (kind plus id);
   - a breadcrumb trail with back and forward;
   - every view model built from a reference, so a link is just a reference.
4. **Screens, per the approved mock-ups:**
   - **Overview:** portfolio and exposure, alerts, and a health strip are live now. Panels whose data comes in
     later slices (top recommendations and journal from M1.12, movers from M1.12, source items from M2, perp
     funding and book signals from M3) are drawn in place with a short "arrives in …" note (open question 2).
   - **Markets:** search and categories (from the engine's index), opening into a market.
   - **Market drill-down:**
     - the title and live quote;
     - the YES price chart for today (from a new engine route, open question 3);
     - the order book (both sides are bids);
     - the linked-items panel. For now it holds same-event markets and your ledger records for the market;
       source items, recommendations and perps join in their slices. Selection stays in sync across panels.
   - **Health:** engine, feed, ledger, backups, GPU and API, with the gap log.
   - **Ledger:** status and paged records (your data; local only).
   - **Alerts:** the alert log by priority.
5. **Rendering:** stream updates are coalesced and repainted at a fixed rate (about 10 Hz), as D3 says, so WPF stays
   light under bursts.
6. **Frame time (J):** measured from WPF's render loop, with p50 and p99 shown in the health view (open
   question 4).
7. **Engine addition:** `GET /v1/markets/{ticker}/candles` (Kalshi's candlesticks through the engine's request
   budget, cached for a minute), with its contract and golden example.

**M1.8b builds:**
- hotkeys (the mock-up's list, and `?` to show them);
- saved layouts across monitors (KalTerm3's window placement);
- light and dark themes (WPF's Windows 11 theme, as KalTerm3);
- versioned terminal settings with a migration, and the **first-run wizard** (G15): it checks the key variables
  (never shows them), the engine, the share, the tax zone and quiet hours;
- the redacted **support bundle** (I): logs, health and versions in one zip, with no keys, tokens, account values
  or paths.

**Kept from KalTerm3, and changed.**
- *Kept:* the MVVM helpers, the SSE reader and reconnect, markets, market detail, order book, chart and layout
  code, the toaster and formatting. Each is read critically and adapted.
- *Removed:* the SQL Server and multi-user parts, trading (recommend-only until M7), and watchlists (to the
  parking lot unless you want them).

**Changes on this machine:** the terminal's settings under `%LOCALAPPDATA%\IKARS\terminal`. Nothing else in M1.8a.
The packaged install comes in M1.9.

**New packages:** `ScottPlot.WPF` (MIT, KalTerm3's chart) for the price chart. Nothing else: KalTerm3's own small
MVVM helpers are reused instead of a toolkit.

**Tests:**
- view models against a fake API: navigation, breadcrumbs, back and forward, subscriptions following the open
  screens, coalescing, the handshake refusal, connect-or-start;
- the client against a local test server: token, SSE parsing, reconnect and resume;
- the candles route in the engine.

**Live checks:**
- the terminal connects to the running engine, and starts one when none is running;
- the Overview shows the real portfolio, alerts and health;
- drill into a live market: its quote, book and chart update;
- the linked-items panel opens and selection stays in sync;
- the ledger view pages your records;
- frame time is measured while 100 markets stream.

**Open questions.**
1. **Split into M1.8a and M1.8b?** *Recommended:* yes, as above.
2. **Overview panels whose data comes later:**
   - *Recommended:* draw them now, in their mock-up places, with a short "arrives in M1.12" (or M2, M3) note, so
     the layout is final and nothing moves later.
   - *Alternative:* hide them until their data exists.
3. **The price chart:** *recommended:* `ScottPlot.WPF` (MIT, KalTerm3's) with the new candles route on the engine.
   *Alternative:* no chart until M1.12.
4. **Frame-time target:** *recommended:* **p99 at most 33 ms** (30 frames a second) while 100 markets stream at
   10 Hz.
5. **Coverage gate for the WPF project:**
   - *Recommended:* the views stay thin (XAML and minimal code-behind) and are left out of the 90% line gate; the
     view models, client and navigation are held to it.
   - *Alternative:* UI automation tests to cover the views too, which are slower and more brittle.

**Owner's answers (2026-10-27):** all five as recommended:
1. split into M1.8a and M1.8b;
2. later panels drawn now, each with an "arrives in" note;
3. ScottPlot with the engine's candles route;
4. p99 frame time at most 33 ms while 100 markets stream;
5. thin views left out of the coverage gate, with view models, client and navigation held to it.

#### 1.8a built (2026-10-27)

**Built** (commits `c485d4a`, `3cbf8fc`, `ee06ba5`, `778cda1`, `a4fa061`, `db1676f`; new package `ScottPlot.WPF`,
MIT):

| Area | What |
|---|---|
| Engine routes | `GET /v1/markets/{ticker}/candles` (Kalshi's candlesticks through the request budget; successes and failures cached for a minute); ledger records by market (`?ticker=`); `GET /v1/gaps` (the gap log for the Health screen); the markets list puts open markets first. Contracts and golden examples updated (24). |
| `Ikars.Client` | The engine connection: `api.json`, the version handshake, a typed API client with its own timeout, an SSE reader, and a stream watcher that subscribes, resumes after the last alert, backs off (a refusal keeps backing off) and drops a stream silent for 20 s. The connector starts the engine through its registered command when none runs. |
| `Ikars.Terminal` | WPF (`ThemeMode=System`). One navigator: drill-down, breadcrumbs, back and forward (R9); a screen left while loading is cancelled and lets go of its subscriptions. An update hub hands the stream's changes to the screens once per 100 ms tick, and sends a subscription change only when the screens' union changed. |
| Screens | **Overview** (cash, open cost, unrealised, positions, exposure, the newest alerts, a health strip, and the six later panels drawn with "arrives in" notes); **Markets** (search, paging, live bid and ask); **Market** (quote, both book sides, the day's price chart, and the linked panel: same-event markets and your ledger records for it); **Ledger** (status, pages newest first); **Alerts** (by priority); **Health** (every engine part, the week's gap log, frame time). |
| Frame time (J) | **Update to screen** (D20 J as amended): for each tick that changed the screen, the time until WPF starts the frame after the one that shows it (an upper bound, high by up to one refresh), and the UI work per update (the hand-over and its layout); the last 600 as p50 and p99, on the status bar and the Health screen. The window listens for frames only while a change waits. |
| Start-up | `--data-folder <path>` points the terminal at another engine's folder (the load test; the portable install later). |

**Verified.**
- 1,279 Python tests pass (93.8% overall) and 69 .NET tests pass: `Ikars.Terminal` 97.7% of lines (views
  excluded, as approved), `Ikars.Client` 93.7%, `Ikars.Contracts` 100%. ruff, mypy, dotnet format, the data guard
  and gitleaks are clean.
- **Live**, against the background engine (driven through UI Automation; counts only):

  | Check | Result |
  |---|---|
  | Start the engine when none runs | the owner-stopped engine was started by the terminal through the registered command; connected in 55 s (start-up includes the market index) |
  | Overview | portfolio, alerts, health strip and the planned panels with live data |
  | Markets | 212,020 markets; search finds a market by ticker |
  | Market drill-down | quote, both book sides (10 levels each), the linked panel with the same-event market; ledger records by market correct once the engine ran the new routes |
  | Ledger paging | 223 records: 223 to 124, 123 to 24, 23 to 1 (Older then disabled), Newest back to the top; chain verified |
  | Health | 87 health rows and the gap log; the API token appears nowhere (only the request budget's `tokens_*` rates) |

**Frame time (J): how the target was settled.** The load for these checks was the engine's real API server over a
made-up context: 100 watched markets whose quotes and books all change every 100 ms.
1. **Over Remote Desktop** the first meter (gaps between rendered frames) read p50 31 ms, p99 63 ms. The remote
   display runs at about 32 Hz with software rendering (render tier 0), so nothing could be judged there.
2. **At the console** (59 Hz, the RTX 5070 Ti, hardware rendering) the owner read p99 34 ms idle and 48 to 49 ms
   under load. Clearing and refilling lists every second was replaced by in-place updates (`144c5c4`); the readings
   did not move.
3. **A trace at the console** showed the terminal's UI work per update at p99 1.3 ms on Markets and 15 ms on a
   market, so the gaps were not IKARS's work. A 1 ms Windows timer resolution changed nothing.
4. **A bare WPF window** (no IKARS code) settled it: p99 46 ms when nothing changes, and p50 and p99 both
   **16.7 ms** (2 gaps over 20 ms in 1,791 frames) when its text changes every frame. **WPF draws only when
   something changed**, so frame gaps measure idling, not slowness. My earlier reading, that WPF's timing on this
   machine was irregular, was wrong. Forcing the high-performance GPU changed nothing (every monitor is on the
   5070 Ti, and WPF already rendered there); the two preference values were removed afterwards.
5. **The owner amended D20 J** (2026-10-27): keep 33 ms, applied to **update to screen**. Measured at the console
   with the new meter (`40e73ae`):

   | Screen | To screen p50 / p99 | UI work p99 |
   |---|---|---|
   | Markets, 100 made-up markets at 10 Hz | 16 / **32 ms** | 2 ms |
   | One market, same load | 16 / **32 ms** | 10 ms |
   | Overview, same load | 15 / **30 ms** | 2 ms |
   | Overview, real engine | 16 / **24 ms** | 2 ms |

   **Within the target** on every screen, with an upper-bound measure.

**Live findings, fixed (`a4fa061`).**
1. **17 of the top 50 markets were closed.** A closed 15-minute market keeps its day's volume and sorted first.
   Open markets now come first (`OPEN_STATUSES`, shared with discovery).
2. **An empty chart drew bare axes.** It now says why it is empty: no trades today, no price history, or Kalshi
   did not answer.
3. **"Your ledger records" showed the same records for every market.** The background engine had started before
   the new routes were committed, so it ignored `?ticker=`. Restarting it fixed this; no code change.
4. **Three "engine restarted" alerts from the evening** were the M1.7 live checks' own stops and the uvicorn
   logging crash fixed in M1.7; nothing new.

**Review.**
- **Per commit (`778cda1`):** 3 fixed:
  1. a screen left while loading kept its subscriptions (the navigator now cancels the load and closes it again);
  2. a failed flush in the timer tick would end the terminal (shown as a problem instead);
  3. a failed subscription change was not retried (it stays pending).
- **Per commit (`a4fa061`):** none.
- **Merge review (medium), 10 findings:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | A Markets page arriving after the screen closed subscribed its 50 markets for the rest of the session | Ignored once the screen is closed; tested |
  | 2 | The Overview re-sent its subscriptions every second with the portfolio | The hub sends only a changed set; tested |
  | 3 | A stream that stays open but silent was never noticed | Dropped after 20 s without an event (health comes every 5 s); tested |
  | 4 | A refused stream (too many) reset the backoff and retried every second | A refusal keeps backing off; tested |
  | 5 | Overlapping timer ticks could send an older subscription set last | One flush at a time |
  | 6 | The frame meter counted a minimise or drag as a slow frame, and sorted the window twice per reading; its render callback ran always | Pauses skipped and one sort per reading (`db1676f`); then superseded by the update-to-screen meter (`40e73ae`), which listens for frames only while a change waits |
  | 7 | A failed candles read was asked of Kalshi again on every open | Failures cached for a minute too; the chart says why it is empty; tested |
  | 8 | A request timeout was taken for an engine restart: reconnect and a repeated Kalshi-backed request | A timeout is not retried (`TimedOut`); tested |
  | 9 | An alert arriving during a screen's snapshot appeared twice; the Alerts lists grew without a bound | Once each, newest first by line, at most 200 per priority; tested |
  | 10 | The ledger's ticker filter has no index | **Parked** (PLAN §9): it needs a ledger migration, and the scan is trivial at today's size |

- **Per commit (`db1676f`):** 1 fixed: disposing the old client on a reconnect would cancel calls still running on
  it. It is no longer disposed; it owns no sockets (one shared handler).
- **Per commit (`144c5c4`):** none.
- **Per commit (`40e73ae`):** 1 fixed: a duplicated doc comment on `TickAsync`.

**Kept, changed and why.**
- *Kept from KalTerm3:* WPF with the Windows 11 theme, ScottPlot for charts, the SSE stream client's shape.
- *Changed:* one navigator owns every screen (KalTerm3 opened windows per view), so drill-down, breadcrumbs and
  back/forward work the same everywhere (R9). Updates are coalesced twice: in the engine (100 ms per market) and in
  the terminal (one hand-over per tick), so a burst never reaches the UI thread as a burst.

**M1.8a built**, subject to the owner's merge.

**M1.8a done:** PR #11 merged as `77849e3` (2026-10-27).

### 1.8w Direction change: a TypeScript web terminal (owner, 2026-10-27)

**Decision.** After M1.8a, the owner compared the WPF terminal with Avalonia, WinUI 3, Blazor Hybrid and a
TypeScript web app, and chose **TypeScript** for flexibility and future expansion and customisation. All the
recommended answers were approved:
1. **Window:** the engine serves the app on its loopback address; it opens as a **Microsoft Edge app window**
   (`msedge --app`) with its own IKARS profile (none of the owner's extensions or cookies). No host program.
2. **Sign-in:** a **one-time code** in the launch link, exchanged by the engine for an `HttpOnly`,
   `SameSite=Strict` cookie for this port only; the session token is never readable by page scripts. The guard
   accepts only the engine's own loopback origin.
3. **Framework:** **React**, with Vite, strict TypeScript, pnpm, Vitest (90%), Playwright, ESLint and `pnpm audit`.
4. **Money:** the web terminal does no money arithmetic. The engine's integers (10^-6 dollars, 10^-2 contracts)
   are exact as JavaScript numbers up to 2^53; a distinct money type and integer-only formatting, with tests,
   keep them that way.

Decisions amended: D1 (the terminal), D3 (static files, sign-in, fetch-based stream reading), D11 (the web gates),
D12 (no .NET runtime shipped); M1.8 replanned as **M1.8a-web** and **M1.8b**. R9 (Overview, drill-down,
cross-reference) and D20 J (update to screen, p99 at most 33 ms) are unchanged.

**What was reusable.**

| Part | Verdict |
|---|---|
| The Python engine | Kept. Two changes come with M1.8a-web: serving the app, and the browser sign-in. |
| `contracts/` (the JSON Schema and golden examples) | Kept: the source of the generated TypeScript types and their golden test. |
| `docs/design/mockups/` (HTML and CSS) | Kept: the starting point of the screens. |
| `Ikars.Terminal`, `Ikars.Client`, `Ikars.Contracts`, `Ikars.Core`, their tests, and the .NET build files | Removed (about 4,100 lines of C# and XAML). History keeps them at the tag `wpf-terminal-final`. |
| CI's `dotnet` job, Dependabot's `nuget` entry, `tools/ci/check-coverage.ps1` | Removed; the web job and `npm` entry come with M1.8a-web. |

**Behaviour that carries over** (settled and tested in M1.8a; re-implemented and re-tested in TypeScript):
1. One navigator: drill-down, breadcrumbs, back and forward; in the browser, real URLs, so every screen can be
   linked to.
2. A screen left while loading cancels its requests and lets go of its subscriptions.
3. Updates handed to the screens about ten times a second; a subscription change sent only when the union changed;
   one flush at a time; a failed change retried.
4. The stream: resume after the last alert; backoff that a refusal does not reset; a stream silent for 20 s is
   dropped; a request timeout is not taken for an engine restart.
5. Lists updated in place; alerts once each, newest first by line, at most 200 per priority.
6. The **update-to-screen meter** (D20 J): from a change to the frame after the one that shows it, with the UI work
   per update; and the console load test (100 made-up markets at 10 Hz through the engine's real API server).
7. Open markets first; an empty chart says why; the book note for unwatched markets.

#### 1.8w prune (2026-10-27)

**Done** on branch `m1/8-prune-wpf`: `2ce5d1e` (the decision record: PLAN D1, D3, D11, D12, M1.8 and §5; this
entry; DEPENDENCIES; README; the mock-ups README; CLAUDE.md), `371d646` (the prune), and the merge-review fixes. The
tag `wpf-terminal-final` (on `d73921b`) keeps the WPF terminal.

**Verified:** 1,279 Python tests pass (93.8%); ruff, mypy, pip-audit, the data guard (168 tracked files) and
gitleaks are clean; CI's jobs are now `python` and `hygiene`. Nothing changed on the machine: the background engine,
watchdog and auto-start are untouched; the local build folders went with the projects.

**Review.**
- **Per commit (`2ce5d1e`):** none (run after the commit; docs only).
- **Per commit (`371d646`):** none.
- **Merge review (medium), 9 findings:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | The `main` ruleset still requires the `dotnet` check, so no PR could merge | **Owner action:** Claude was not permitted to change branch protection. The owner removes `dotnet` from the required checks; M1.8a-web adds `web` |
  | 2 | `contracts.py` said the terminal's golden test exists, but it was deleted and the TypeScript one comes later | Docstring now says only the engine-side test guards the contracts until M1.8a-web |
  | 3 | The .NET ignores were dropped, so building the tag would leave build output that `git add -A` could commit | Restored (`bin/`, `obj/`, `TestResults/`, `.vs/`, `*.user`) |
  | 4 | `dist/` was listed twice in `.gitignore` | Duplicate removed |
  | 5 | D3 still justified SSE by reusing KalTerm3's C# client | Amended: the web terminal keeps that client's rules, not its code |
  | 6 | PLAN's M1.7 row and GPU section still described the C# contract test and WPF rendering as current | Annotated as retired 2026-10-27 |
  | 7 | D11 opened with C# gates as if still enforced | Rewritten: Python gates, the web gates from M1.8a-web, and the retired C# gates listed separately |
  | 8 | The resume point still said the prune was to do and kept stale M1.8a text | Rewritten to the current state |
  | 9 | No terminal gate exists between the prune and M1.8a-web | No change: there is no terminal code in that gap; M1.8a-web adds the `web` job and makes it a required check (recorded in D11) |

### 1.R Base hardening (plan APPROVED 2026-10-27: items 1 to 26; branch `m1/r-hardening`)

Before the web terminal, the owner asked for a review of the whole code base "to have a solid base". `/code-review`
at high effort over all 10,900 source lines gave 31 candidate findings; each was checked against the code (one
disproved by a test: `append_all` does skip a duplicate within one batch). The owner approved fixing items 1 to 26,
each with a test:

- **A. Data loss, a stuck engine, a silent wrong state:** (1) a refused resubscribe keeps a stale sid; (2) a gap on a
  command reply is dropped; (3) one damaged hour file stops every start; (4) shutdown can hang; (5) a failed fold-in
  truncates the hour; (6) a dead pump goes unnoticed; (7) the engine's queue is unbounded; (8) reconciliation
  ignores money held by resting orders; (9) a fill listed late is costed against today's position.
- **B. What the screens show:** (10) a dropped book is never announced; (11) a book with no snapshot never asks for
  one; (12) a gap from before a reconnect is resolved through the new link; (13) unchanged books are re-sent;
  (14) the guard lets WebSocket scopes through.
- **C. Long runs:** (15) the import replays the whole ledger every minute; (16) per-market state and the index are
  never pruned, and every markets request sorts the index; (17) long backfills hold every trade id and restart from
  zero; (18) an unconfirmed archived market is re-read every minute; (19) the plugin gap list grows without bound;
  (20) the governor fails on a file renamed under it.
- **D. Smaller:** (21) a failed start is recorded as a clean shutdown; (22) the restore drill crashes on a damaged
  copy; (23) `ledger annotate` prints tracebacks; (24) the public REST client is never closed; (25) duplicated
  helpers (`series_of`, the nested lookup, the atomic write, backup naming); (26) the ACL check raises off Windows.

Not changed (owner): (27) production as the default environment (D21; a comment added); (28) the data guard's
strict suffix rule; (29) the disproved `append_all` claim.

#### 1.R built (2026-10-27)

**Built** (commits `dd9f8d0`, `4735650`, `edae257`, `595a9b0`, `66fcec0` and the merge-review fixes). No new
packages. One security-relevant change: **`GET /portfolio/orders` joins the account key's allowed reads** (item 8,
approved as "read with GET only"); the key still signs GETs only, and other order paths (`/portfolio/orders/<id>`,
`/historical/orders`, order groups) stay refused, with tests.

| # | Fixed | How |
|---|---|---|
| 1 | A refused resubscribe kept a stale sid | The sid is cleared; the rebalance waits for the retry; a resubscribe of a restored channel sends nothing |
| 2 | A gap on a command reply was dropped | Reported like any other |
| 3 | One damaged hour stopped every start | Any compaction failure is logged and left; an unreadable Parquet hour is set aside as `.damaged` (never deleted); Parquet is fsynced before it replaces anything |
| 4 | Shutdown could hang | The source's output always ends; the engine is waited for 10 s, then cancelled |
| 5 | A failed fold-in truncated the hour | New rows go to a part file (`HH.1.arrow`); readers take every part |
| 6 | A dead pump went unnoticed | The run loop stops the engine on it, so the watchdog restarts it |
| 7 | The engine's input was unbounded | 50,000 items; then the feed's own overflow handling runs |
| 8 | Resting orders were a difference | When the balance falls short, resting buys' cost is read; either reading is exact; both numbers are recorded |
| 9 | A late-listed fill was costed silently | Flagged `out_of_order` with a note for the owner to label |
| 10 | A dropped book was never announced | Sent once as not ready |
| 11 | A book with no snapshot never asked for one | A delta without a book asks (watched markets) and makes no empty book |
| 12 | A gap from a lost link was read through the new one | Gaps from an older link are counted and dropped |
| 13 | Unchanged books were re-sent | The version moves only on a real change |
| 14 | The guard let WebSocket scopes through | Refused (code 1008) |
| 15 | The import replayed the ledger every minute | The importer keeps its book and applies only new records (others' included) |
| 16 | Per-market state and the index grew for ever; every markets request sorted the index | Markets settled more than three days ago leave the index and their state; combo quotes are kept nowhere; markets still unlisted after a cycle are forgotten; the list is sorted once per index change |
| 17 | Long backfills held every trade id and restarted from zero | Each finished slice is remembered; a failure resumes at its slice; one slice's ids at a time; the bookmarks go when the gap closes |
| 18 | An unconfirmed archived market was re-read every minute | Hourly |
| 19 | The plugin gap list grew for ever | The latest 100 and a total |
| 20 | The governor failed on a renamed file | Skipped; a held file is pruned next time |
| 21 | A failed start counted as a clean shutdown | Only a finished start can end cleanly |
| 22 | The restore drill crashed on a damaged copy | A failed drill naming the problem |
| 23 | `ledger annotate` printed tracebacks | A JSON refusal |
| 24 | The public REST client was never closed | Closed at shutdown |
| 25 | Duplicated helpers | One copy each: series and combo rules (`ikars_core.markets`), the nested lookup (`runmode.at`), the atomic write, backup and free names (`files`) |
| 26 | The ACL check raised off Windows | A warning, never a failed start |
| 27 | (no change) Production as the default | A comment says why (D21) |

**Verified.**
- 1,303 Python tests pass (94% coverage; money and fee code 100%); ruff, mypy, pip-audit, the data guard and
  gitleaks are clean.
- **Live**, against the background engine:

  | Check | Result |
  |---|---|
  | Restart on the new code | started, feed connected, no open gaps |
  | Ledger | chain verified, **reconciles exactly**, no import errors |
  | Stop | **3.5 s**; recorded as a clean stop (the next start's gaps say "stopped") |
  | A torn real recording hour (a scratch copy, deleted after) | the start went on; the hour set aside as `07.parquet.damaged`; the open file's rows kept |
  | Resting orders (item 8) | **pending:** needs a resting order of the owner's; IKARS reads it with GET only |

**Live finding, fixed (`66fcec0`).** The minute import re-reads a day of fills; each already-recorded fill older
than a later one was flagged and warned about before being skipped as already recorded (4 warnings). The real
ledger holds no flagged fill; the warning now comes only for a fill actually added, with a test.

**Review.**
- **Per commit:** `dd9f8d0` none; `4735650` none; `edae257` none (a question on `seq` in record bodies checked: it
  is there); `595a9b0` 1 fixed (a comment moved to the wrong constant); `66fcec0` none.
- **Merge review (medium), 10 findings:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | A fold-in stopped between its steps (its Parquet held) was overwritten by the next fold-in of the hour: rows lost | Compaction finishes a pending fold-in first; tested |
  | 2 | The kept book skipped a record another process appended during an import | The importer remembers the records it appended and applies every other; tested |
  | 3 | A failed resting-orders read aborted the whole import | Treated as unknown (not exact); an order without `outcome_side` takes it from `side` and `action`; tested |
  | 4 | A new market's quotes were dropped until the next discovery | Only combos are dropped at once; a market still unlisted after a cycle is forgotten then |
  | 5 | Pruning removed closed markets that may still be held, and they churned back | Only markets settled more than three days ago; `put` never re-adds them; tested |
  | 6 | A resubscribe retry could open a second order-book subscription after a reconnect restored it | A resubscribe of a restored channel sends nothing; tested |
  | 7 | A schema change would have set good hours aside; damaged files escaped the budget | Only an unreadable file is set aside; the governor counts and prunes `.damaged` files; tested |
  | 8 | Backfill bookmarks were never deleted; the live hour was re-read every slice | Deleted when the gap closes; the recent hours are read only when resuming a slice; tested |
  | 9 | `_free_name` repeated the name loop item 25 consolidated | One `files.first_free`, used by both |
  | 10 | The resume point did not mention M1.R | Updated |

**M1.R done:** PR #14 merged as `fca7941` (2026-10-27), CI green on `main`. The resting-order check stays pending.

#### 1.8w-sec Review and security posture for the web terminal (owner, 2026-10-27)

**Why.** In WPF a hostile market title was only text; in a browser, text that runs as script holds the page's API
session (and, from M7, could reach orders), and the engine becomes a web server any open site can try to reach. The
key, GET-only rule and Python checks are unchanged: the engine alone talks to Kalshi and the page never sees the key.

**Decided** (owner: "fold the relevant ones into M1.8a-web and add CodeQL"; PLAN D3, D11, G12, the M1.8 row):
- Browser hardening (D3): a strict CSP and protective headers; a single-use sign-in code that expires in seconds;
  `Origin` and `Sec-Fetch-Site` checks on changing requests; extensions off in the IKARS Edge profile; outside links
  through the engine, `https:` only.
- Checks (D11): ESLint bans on markup injection and `eval`; npm supply-chain rules (Dependabot for npm, install
  scripts allow-listed, a 7-day waiting period, also for CI actions); no money arithmetic in TypeScript; Playwright
  output and `.env*` kept out of the repository.
- Review: two per-commit checklist items (outside text drawn only as text; a new dependency's licence, age and
  install scripts) and the render path as the terminal's hot path; a `/security-review` before the M1.8a-web merge
  and before M7.
- **CodeQL added now** (`.github/workflows/codeql.yml`): `security-extended` queries for Python and the CI workflows
  on every pull request, on `main` and weekly; TypeScript joins with the first terminal code (CodeQL fails a
  language with no source). Pinned to v4.39.0 (12 days old) rather than v4.39.1 (6 days), by the new waiting rule.
  It is not a required check; whether to require it is the owner's ruleset decision.
