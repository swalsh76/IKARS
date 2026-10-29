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
  | Resting orders (item 8) | **Checked (2026-10-27).** Both resting orders are read correctly: a **sell** closing part of a held YES position (Kalshi labels it `side` yes, `action` sell, `outcome_side` no, as the model maps it) and a **buy** of YES. **Kalshi's `balance` does not hold back a resting buy's cost:** with the buy open, the balance equals the ledger's cash exactly, so every reconciliation stayed exact with a zero difference, and the resting-cost allowance was not needed. It is kept (it is read only when the balance falls short) |

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
  It is not a required check; whether to require it is the owner's ruleset decision. *Later the same day:*
  merged as PR #15 (`179dd8c`); at the owner's request the aggregate `CodeQL` check (GitHub's code-scanning app)
  is now **required** on `main` beside `python` and `hygiene`, so a new language needs no ruleset change.

#### 1.R2 Platform review for the new direction (owner, 2026-10-27)

**Asked:** "one more full milestone review, keeping in mind the new direction, with an eye towards reusability and
overall platform stability". M1.R had just reviewed every module for correctness, so this pass read the API,
contracts, engine core, plugins, sources, runner, recorder and services for three questions: is the engine ready for
the web terminal, can M1.12 and M2 plug in, and does it stay up for weeks. **Placement approved by the owner.**

| # | Finding | Placed |
|---|---|---|
| 1 | High: every engine start picks a new port and token; a browser page cannot rediscover them, so each watchdog restart, update or reboot leaves a dead terminal window | M1.8a-web: a stable port in settings (fallback when taken, `ikars open` relaunches) and a session secret that survives restarts (DPAPI, D8) |
| 2 | `HealthView.parts` is `dict[str, Any]`: the generated TypeScript type would be `any`, unchecked at runtime | M1.8a-web: a typed health contract |
| 3 | Nanosecond integers (`received_ns`, health times) exceed 2^53 and are rounded by JavaScript (by about 0.25 µs); no test guards the rule | M1.8a-web: a contract test; `_ns` documented as approximate in the browser |
| 4 | Mixed time formats on the wire (seconds and nanoseconds; `AlertView.at` is local time) | M1.8a-web: one format, settled before a client exists |
| 5 | Market search filters and lowercases the whole index per request on the event loop; type-ahead could stall the feed | M1.8a-web: a precomputed search key, a debounced search, measured at the real index size |
| 6 | High for back-tests: the recorder writes Kalshi channel schemas, but `ReplaySource` reads only the generic item schema (only the synthetic demo uses it), so G3 does not hold for real data | New slice **M1.13**, before M3's back-tests; G3 marked partly done |
| 7 | `ReplaySource` loads every recording into memory (about 2.1 GB a day) | M1.13: a streaming merge |
| 8 | The live runner never loads plugins (`discover`) or registers listeners: a signal or source plugin has no path in, its items are not recorded (D20 A) and never reach the API | M1.12 |
| 9 | A plugin's emitted item keeps the `received_ns` the plugin chose; a buggy source could let a replay see the future | M1.12: the host stamps it |
| 10 | Duplicated time helpers (`api.utc_text`/`ledger.iso_utc`, `alerts._ns`/`ledger_import.ns_of`) | PLAN §9: with the next slice touching them |

**Checked, no change needed.** Off the event loop: hourly compaction, the storage governor, share backups, ledger
checks, GPU detection. Bounded: metric names (per channel and plugin, never per market), the plugin gap list. Slow
growth only: the alert log and the gap and exchange-status tables (a few rows a day). Shutdown, restart, damaged
files and the feed were covered by M1.R.

### 1.8a-web The TypeScript terminal, first part (plan APPROVED 2026-10-27; four slices)

**Builds.**
- **Engine (Python):**
  1. Serves the built terminal at `/` (one loopback origin) with a strict Content Security Policy (scripts only from
     the engine, no inline script or `eval`, no frames; AG Grid's inline styles, if needed, by a per-page nonce),
     `nosniff`, no referrer and `no-store` on API replies; a plain page when the terminal is not built.
  2. Sign-in that survives restarts (review 1.R2 item 1): a **fixed port** in settings (owner-changeable; when taken,
     a random port and a Windows alert); an `HttpOnly`, `SameSite=Strict` cookie for **30 days, sliding**, signed with
     a DPAPI-protected key in the data folder (D8); a **one-time code** (single use, 30 s) in the launch link's `#`
     fragment, exchanged by the page and removed from the address bar; changing requests need the engine's own
     `Origin` and `Sec-Fetch-Site: same-origin`. The per-start bearer token in `api.json` stays for the CLI.
  3. `ikars open`: a code from the running engine, then Edge as an app window with its own profile under
     `%LOCALAPPDATA%\IKARS\browser`, extensions off.
  4. Contracts (review items 2 to 5; API 2.0): typed health fields (plus a free-form detail part); one time format
     (`*_utc`, `*_local`; `AlertView.at` becomes `at_local`); a test that wire money, counts and ids fit 2^53 (`_ns`
     documented as approximate in the browser); a lowercase search key per market; a made-up-data API mode for
     Playwright and the load test.
- **Web (`web/`, React, strict TypeScript):**
  5. Types generated from `contracts/` (CI checks the committed copy); every reply and stream event validated with
     unknown fields refused, by validators compiled at build time (no `eval`, so the CSP holds).
  6. The client: request timeouts, the `fetch` stream reader, the four stream rules (behaviour 4), and the update
     hub (10 Hz, subscription changes only on a change of the union; behaviour 3).
  7. Money: a distinct type for money and counts, integer-only formatting, no arithmetic; 100% tested.
  8. A small own URL navigator (`/overview`, `/markets?q=`, `/market/<ticker>`, `/health`, `/ledger`, `/alerts`),
     breadcrumbs, the browser's back and forward; a screen left cancels its requests and subscriptions (behaviours
     1 and 2).
  9. Screens from the approved mock-ups (their CSS reused): Overview (later panels with "arrives in" notes),
     Markets (AG Grid, debounced search, open first), Market (live quote, Lightweight Charts from the candles route,
     the book, linked items), Health with the gap log, Ledger (paged), Alerts by priority.
  10. The update-to-screen meter (D20 J; behaviour 6) in Health, and the load test (100 made-up markets at 10 Hz).
- **CI and repository:**
  11. A `web` job on Windows: frozen install, ESLint (with the injection bans), type checks, the generated-types
      check, Vitest at 90% (thin screen components left to Playwright, as decided for WPF), build, Playwright on the
      installed Edge, `pnpm audit`.
  12. pnpm's `minimumReleaseAge` (7 days) and install-script allow-list; Dependabot's `npm` entry with a 7-day
      cooldown; CodeQL adds `javascript-typescript`.
  13. Playwright output and `.env*` ignored and refused by the data guard.
  14. Docs: DEPENDENCIES, README, PLAN, this log, CLAUDE.md's dev commands.
  15. A `/security-review` before the merges that carry the sign-in and the page.

**Changes on this machine:** Node.js 24 LTS (`winget install OpenJS.NodeJS.LTS`, per user) and pnpm through
Corepack (development only); no Playwright browser download (the installed Edge); the IKARS Edge profile folder; the
DPAPI-protected session key; a settings migration adding the port (backup first); the engine restarted on new code.

**New packages** (each at least 7 days old): react and react-dom 19.3.1, ag-grid-community and ag-grid-react 36.2.0,
lightweight-charts 5.2.1 (TradingView's attribution logo kept) (MIT, MIT, Apache-2.0; these ship); vite 8.3.4,
@vitejs/plugin-react 6.1.2, typescript **6.0.3** (typescript-eslint does not support 7 yet), vitest and
@vitest/coverage-v8 5.0.5, @playwright/test 1.64.1, eslint 10.13.0, typescript-eslint 8.72.0,
eslint-plugin-react-hooks 7.1.1, json-schema-to-typescript 16.0.0, ajv 8.20.0, pnpm 12.14.0 (build and test only).

**Live checks:** `ikars open` shows the real Overview; a live market's quote, book and chart update; after a watchdog
restart the window reconnects with no new sign-in; the ledger pages; a request from another origin is refused and an
ordinary browser tab cannot read the API; no CSP violations; update to screen p99 <= 33 ms with 100 markets (the
owner, at the console).

**Owner's answers (2026-10-27):**
1. **Split: four slices**, each merged on its own (the owner split each proposed half again):
   | Slice | Builds | Gate |
   |---|---|---|
   | **M1.8a-web-1a** engine side | items 1 to 4, the made-up-data mode, a placeholder page | Python tests; `/security-review` |
   | **M1.8a-web-1b** web foundation | items 5 to 8 and 11 to 13; Overview and Health | the `web` job (the owner adds it to the ruleset); `/security-review` |
   | **M1.8a-web-2a** markets | Markets and Market | merge review |
   | **M1.8a-web-2b** the rest | Ledger, Alerts, the meter and the load test | merge review; the console check |
2. Port: a fixed default in settings, the owner can change it; a random fallback with an alert.
3. Session: 30 days, sliding.
4. Development: `vite build --watch`, served by the engine (one origin, production's CSP).
5. Every reply and stream event validated in production.

#### 1.8a-web-1a built (2026-10-27)

**Built** on `m1/8a-web-1a`: `5ce96fb` (DPAPI helper, sessions, settings v4), `c08f9af` (two pre-existing fixes
from the per-commit review), `8ce2538` (serving, hardening, sign-in, fixed port), `6f9b82f` (contracts 2.0),
`22547ce` (`ikars open`, `demo-api`, the watchdog's port rule), `ab83c07` (the security-review fix), `efc8824` (the
merge-review fixes). **No new packages.**

- **`ikars_core.dpapi`**: DPAPI for the current user through `ctypes` (D8).
- **`session`**: one-time codes (single use, 30 s, at most 16 waiting) and HMAC-SHA256-signed cookies, 30 days and
  renewed once a day old; the signing key in `data\session.key`, DPAPI-protected, so a restart keeps the terminal
  signed in. Without DPAPI the key lives in memory; an undecryptable key is set aside under a free name and
  replaced; a key that cannot be read right now fails the start (the watchdog retries) rather than signing every
  terminal out.
- **`webserve`** and the guard: the terminal's files (the placeholder page until `web/dist` exists), a fixed
  content-type table, screen addresses answered by `index.html`; on every response a strict CSP, `nosniff`, no
  referrer, `DENY` framing, same-origin opener and resource policies, `no-store` on the API and `no-cache` on
  files. The API takes the bearer token or the cookie; any `Origin` but the engine's own is refused; a change on a
  cookie needs the engine's own page (`Origin` and `Sec-Fetch-Site: same-origin`). `POST /v1/session/code` (bearer
  only) and `POST /v1/session`.
- **The port**: `[terminal] port = 47821` (settings version 4, migrated with a backup), bound exclusively, retried
  for 5 s, then a free one reported as `port_fallback` (the watchdog alerts).
- **Contracts 2.0** (review 1.R2 items 2 to 5): typed health (`FeedHealth`, `LedgerHealth`, `BackupHealth`, the
  clock, GPU, API and plugins; the whole report in `detail`); `AlertView.at_local`; the time rule written down; a
  test holding every wire integer to `_ns`, `_e6`, `_e2` or `SMALL_INTEGERS`; `SessionCode`, `SessionCodeIssued`;
  a lowercase search key made once per market.
- **`ikars open`** (`python -m ikars_engine open`): starts the engine if needed, gets a code with the bearer token,
  opens Edge as an app window with `<data root>\browser` as its profile and extensions off.
- **`demo-api`**: the real API server on made-up markets, books, ledger and alerts in a temporary folder.

**Verified.** 1,377 Python tests pass (94%); ruff, mypy, the data guard and gitleaks are clean. **Live**, on the
background engine:

| Check | Result |
|---|---|
| Restart on the new code | settings migrated to version 4 (one backup); port 47821, no fallback; session key kept |
| Sign in as the page does, restart the engine (new token), use the same cookie | **200 on the same port** (review 1.R2 item 1) |
| The same with a stream open during the restart | same port, no fallback, the cookie still works |
| A spent code; another origin; a change without the own page | 401; 403; 403 |
| A UNC path and a drive path at the file route (after the fix) | 404, nothing outside the folder touched |
| `ikars open` (the owner, at the console) | the IKARS Edge window opened and showed "Connected to the engine", API 2.0 |
| After the restarts | feed connected, the ledger verified and reconciled exactly, restart gaps backfilled |

**Review.**
- **Per commit:** `5ce96fb` 2 pre-existing findings, fixed in `c08f9af` (a removed long-settled market did not move
  the index version; a file gone mid-prune was counted as pruned); `8ce2538` none; `6f9b82f` 1 fixed (a non-ASCII
  cookie raised in `compare_digest`, a 500, now refused); `22547ce` none; `ab83c07` none; `efc8824` none.
- **Security review** (`/security-review`), 1 finding:

  | # | Finding | Done |
  |---|---|---|
  | 1 | Medium: the public file route resolved the request path before checking it, so a UNC path (`\\host\share`) from any web page (an `<img>` at `127.0.0.1:47821`) made Windows contact that host and send the owner's NTLM credentials | Fixed in `ab83c07`: only plain names pass (no backslash, colon, empty part, `.` or `..`), checked on the text before any filesystem call; tested, and probed live |

- **Merge review (medium), 8 findings:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | Overlapping gaps could store a trade twice: an earlier backfill's rows carry their fetch time, which M1.R's per-slice change stopped reading | Fixed in `efc8824`: the recent two hours read once per backfill; tested |
  | 2 | The exclusive port bind might fail after a restart while old connections linger, so the terminal would lose its port | No change to the bind: tested on this Windows (rebinding works with TIME_WAIT, exclusive or not) and live with a stream open (same port). The fallback socket is now exclusive too |
  | 3 | A second damaged copy (`.damaged.1`) escaped the storage budget; the recorder's docstring said damaged files are never deleted | Fixed in `efc8824`: the governor counts and prunes any `.damaged` name; docstring corrected; tested |
  | 4 | An unreadable session key overwrote an earlier set-aside copy, and a passing read failure signed every terminal out | Fixed in `efc8824`: free names; only an undecryptable key is replaced, a read failure fails the start; tested |
  | 5 | A wrong method on an API route was a plain-text 405, not the contract's JSON | Fixed in `efc8824`: a JSON error; tested |
  | 6 | Each stream rebuilt the whole health report every 5 s | Fixed in `efc8824`: one view per half interval for all streams; tested |
  | 7 | `ikars open` crashed on an `api.json` with a null port | Fixed in `efc8824`: waited past, then a plain refusal; tested |
  | 8 | `demo-api --hz 0` crashed after starting | Fixed in `efc8824`: refused by the command line; tested |

**M1.8a-web-1a built**, subject to the owner's merge.

#### 1.8a-web-1b built (2026-10-27)

**Built** on `m1/8a-web-1b`: `cea9ffd` (the web terminal), `7869da7` (CI and repository settings), `d26af73` (the
merge-review fixes). After 1a merged as PR #16 (`d70cb83`).

- **On this machine:** Node.js 24.22.1 per user, from the official zip checked against `SHASUMS256.txt`, in
  `%LOCALAPPDATA%\Programs\nodejs` on the user `PATH` (winget's package is a machine-wide MSI needing admin; the
  plan said per user); pnpm 12.14.0 through Corepack. No Playwright browser download: the installed Edge.
- **`web/`** (React 19, strict TypeScript 6.0, Vite 8, pnpm 12): `scripts/gen-contracts.mjs` makes `src/api/contracts.ts`
  and ajv's standalone validators from `contracts/` (unknown fields refused; no code compiled in the page, so the CSP
  holds; `gen:check` in CI). The client (the cookie; timeouts; signed-out, refused, timeout, unreachable and contract
  failures kept apart). The stream reader over `fetch` (resume after the last alert; the backoff grows and only an
  opened stream resets it; 20 s of silence drops it; signed-out apart). The update hub (10 Hz flushes; subscriptions
  by the union of every screen's markets, one change in flight, retried, re-sent to a new stream). Money: a distinct
  type, integer-only formatting with BigInt, no arithmetic (100% tested). The navigator over real addresses with
  breadcrumbs. The Overview (portfolio and exposure, alerts; later panels drawn with "arrives in" notes) and Health
  (the cards, the gap log, the engine's whole report) from the mock-ups' tokens, dark or light with Windows.
- **Checks:** ESLint with the injection bans; Vitest 131 tests (98% lines, 91% functions; thin screens left to
  Playwright as decided); Playwright 6 screen tests in Edge against `demo-api`, watching for CSP refusals; `pnpm
  audit` clean. CI's `web` job runs all of it on Windows; CodeQL adds `javascript-typescript`; Dependabot watches
  `web/` with a 7-day cooldown (actions too); the data guard and `.gitignore` refuse Playwright output, coverage,
  builds, `node_modules`, `.har` and `.env` files.
- **Found on the way:** `demo-api`'s made-up books went below 1 cent near the price limits and its snapshot failed
  (found by the screen tests; fixed, tested); the Playwright setup left the demo server running (uv's child survives
  `kill()` on Windows; the tree is stopped now).

**Verified.** All of the above green locally and in CI on PR #17 (`python`, `web`, `hygiene`, CodeQL for Python,
actions and TypeScript). **Live:** the engine restarted on the same port with the old cookie still valid, and serves
the built terminal; **the owner opened it with `ikars open` and saw the real Overview** (portfolio, live feed, the
latest alerts); the status strip was cut off at the bottom of a short window, so header and strip now stay in view
(a screen test checks it).

**Review.**
- **Per commit:** `cea9ffd` 1 fixed before committing (a stream that opened while a subscription change was in
  flight got none; the hub now catches up after every change); `7869da7` none; `d26af73` 2 fixed before committing
  (a screen's error boundary kept failing on the next screen: keyed by the address; two first loads could run at
  once: one at a time).
- **Security review** (`/security-review`): **no findings** (outside text drawn only as text and banned otherwise;
  links built from fixed addresses or encoded; the code never visible to scripts; every reply validated; no inline
  script in the build; the supply-chain settings in force).
- **Merge review (medium), 10 findings:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | An unreadable reply (not JSON, or a timeout part way) threw a raw error and the start-up left a blank window | Fixed in `d26af73`: always a failure kind; `main.tsx` shows a start-up error as text; tested |
  | 2 | A malformed %-escape in the address threw and blanked the page | Fixed: it is "Not found"; tested |
  | 3 | One value that cannot be drawn (an integer past 2^53) took down the whole terminal | Fixed: each Overview panel and each screen is guarded and says so in its place; tested |
  | 4 | When the first load failed (engine starting, signed out), the alert history was never loaded | Fixed: the first values load once a stream opens; tested |
  | 5 | A renumbered alert log (cut back) would mix old and new lines | No change: the engine only appends to the alert log; one cut back by hand needs a reload |
  | 6 | The strip and pill worked out their own levels and disagreed with the Health cards | Fixed: one place decides a level; tested |
  | 7 | Quotes and books stayed for the session after no screen wanted them, and came back stale | Fixed: released markets' values are dropped; tested |
  | 8 | Every render re-subscribed each hub listener | Fixed: memoised per key |
  | 9 | A failed refresh blanked the gap table; failed subscription changes stacked retry timers that stop never cleared | Fixed: the last value stays, marked stale; one retry timer, cancelled at stop; tested |
  | 10 | DEPENDENCIES.md still listed the web packages as to be chosen | Fixed: in use, with versions and licences |

**M1.8a-web-1b built**, subject to the owner's merge (and then `web` added to the required checks).

#### 1.8a-web-2a built (2026-10-27)

**Built** on `m1/8a-web-2a`: `ec841bb` (the screens), `3d8d432` (the merge-review fixes). After 1b merged as PR #17
(`0fe5415`); the owner then made `web` a required check.

- **Markets** (`/markets?q=&category=`): AG Grid with the markets the engine finds (open first, up to 200, the
  count shown), a search that waits for a pause in typing and keeps the address current, a category filter kept in
  the address, live bid, ask and last prices from the stream (rows reused per market and quote, so only changed rows
  redraw), and a row that opens the market.
- **Market** (`/market/<ticker>`): the live quote, the YES price for the last 24 hours (Lightweight Charts from the
  candles route; an empty chart says why), the order book (both sides' bids) or the note that the engine does not
  watch the market, and the linked items: same-event markets and your ledger records (source items,
  recommendations and perps arrive in M1.12, M2 and M3).
- Both screens load their libraries only when first opened (the start bundle stays about 365 kB; AG Grid about
  700 kB and the chart about 170 kB come on demand).
- **Engine:** `demo-api` serves made-up candles in Kalshi's own shape (through the real `Candlestick` model) and a
  favicon; the screen tests now fail on **any** console error, not only on CSP refusals.

**The CSP and the libraries** (checked in Edge before building on them):
- AG Grid's default theme injects about a dozen `<style>` blocks, all refused by `style-src 'self'`. Its **CSS-file
  theme** (`theme: "legacy"` with `ag-grid.css` and `ag-theme-quartz.css`, coloured from the terminal's tokens) needs
  none. Its icon font is embedded in that CSS as a `data:` URI, so the CSP's **one exception** is `font-src 'self'
  data:` (a font runs no code; scripts and styles stay `'self'`; a test pins the whole policy).
- Lightweight Charts' attribution logo injects an inline style, so it is **off**; the credit and link to TradingView
  that its licence (Apache-2.0) asks for are drawn under the chart instead, opening outside the terminal.
- AG Grid's modular build needs each feature's module registered: a missing one (row and cell classes) only logs to
  the console and stopped the grid updating; caught by the screen tests, fixed, and why they now fail on any console
  error.
- The grid needs a definite height (`min-height` alone collapses it to 2 px and the rows hang outside it); found by
  the screen tests.

**Verified.** Vitest 144 tests (98% lines, 93% functions); Playwright 8 screen tests in Edge with no console error
or CSP refusal; lint, types and audit clean; Python 1,383 tests.

**Review.**
- **Per commit:** `ec841bb` none; `3d8d432` none.
- **Merge review (medium), 10 findings, all fixed in `3d8d432`:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | A streamed quote with an emptied side (null) fell back to the snapshot's older price | A received quote wins for every field; tested |
  | 2 | When a screen's market list changed, a market in both lists was released first and lost its live quote | The new list is wanted before the old is let go; tested |
  | 3 | A new search that failed kept the last search's results under it | A new address starts from "loading"; tested |
  | 4 | Every quote update rebuilt all 200 grid rows | Rows reused per market and quote (a weak cache), so only changed rows redraw; tested |
  | 5 | The chart re-fitted every minute, undoing the owner's zoom | It redraws only on new candles and fits only once |
  | 6 | The category list showed only the current page's categories, and the category was not in the address | Every category seen is kept; the category is in the address; tested |
  | 7 | Same-event markets were cut at 50 without saying so | Up to 200, and a note when there are more |
  | 8 | The status badge was always green | Green only while trading; tested |
  | 9 | Ledger times used a second format beside the closing times | The one time format; tested |
  | 10 | Duplicated CSS; the chart kept its colours after a theme change | Removed; the chart follows the theme |

**M1.8a-web-2a built**, subject to the owner's merge.

#### 1.8a-web-2b built (2026-10-27)

**Built** on `m1/8a-web-2b`: `5c16da6` (the screens, the meter, the load test), `6667308` (the merge-review fixes).
After 2a merged as PR #19 (`94dbcaa`).

- **Ledger** (`/ledger`): its state (chain, reconciliation, records by kind, tax zone) and your records, 100 a page
  with Older and Newer, a ticker filter, each market linked; times in the ledger's tax zone; refreshed each minute.
- **Alerts** (`/alerts`): the alert log by priority (urgent, then high, then the rest), newest first, live from
  the stream; a heading says when a priority shows only its latest 200.
- **The update-to-screen meter** (D20 J, behaviour 6), on Health: **from the hub applying a change to the frame after
  the one that shows it** (two animation frames), with the page's own work per update, over the last 1,000 screen
  updates; one measurement at a time, only for quotes a screen shows, and none while the window is hidden or across
  a hide. For information, the time from the engine receiving the change (its `received_ns`, the same machine's
  clock) is shown too: it adds the engine's and the hub's 10 Hz batching (up to 100 ms each, by design, D3), so it
  is not held to the target.
- **`pnpm load`** (`playwright.load.config.ts`): `demo-api` with 100 made-up markets moving at 10 Hz, the Markets
  screen in a **visible** Edge window for 40 s, then the meter's level read from Health; it passes only when the
  card says OK (exact p99 <= 33 ms). Kept out of CI and the normal screen tests: it is a console check.
- Dependabot skips major `@types/node` updates (they follow the Node we run); its PR #18 (to 26) is closed.

**A correction on the way.** The meter first measured from the engine receiving each change, and read p50 132 ms
and p99 188 ms headless with the page's own work at 1.5 ms: the time was the two 10 Hz batching steps, not drawing.
D20 J as the owner amended it measures **from the terminal applying a change**; the meter was corrected to that and
the engine-to-screen time kept beside it for information.

**Verified.** Vitest 154 tests (98% lines, 90% functions); Playwright 10 screen tests in Edge, no console error or
CSP refusal; lint, types, audit clean. **Headless sanity run** of `pnpm load` (not the measurement; no real
drawing): update to screen p50 9 ms, p99 19 ms; the page's own work p99 1.4 ms; engine to screen p50 69 ms, p99
126 ms. **Pending: the owner's run at the console** (`pnpm load`, a visible window; not over Remote Desktop).

**Review.**
- **Per commit:** `5c16da6` none; `6667308` 1 fixed before committing (a window hidden and shown again between a
  measurement's start and its first frame still recorded the hidden time: any visibility change now drops it).
- **Merge review (medium), 10 findings, all fixed in `6667308`:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | With the window minimised, held frames piled up and then recorded huge times at once | One measurement at a time, none while hidden or across a hide; tested |
  | 2 | Quotes no screen showed were measured (cheap samples pulling p99 down) | Only quotes a screen listens to; tested |
  | 3 | The load test checked the rounded p99 text, so 33.4 ms passed | It checks the card's level, which uses the exact p99 |
  | 4 | The ledger's ticker filter was case-sensitive | Upper-cased (Kalshi's tickers are upper case) |
  | 5 | Record times were in the browser's zone beside the tax zone shown | In the ledger's tax zone |
  | 6 | The records did not refresh while the status beside them did | Refreshed each minute too |
  | 7 | Each reading sorted every window twice | One sort per window per reading; tested |
  | 8 | One window size for two very different sample rates | 1,000 screen updates (about 100 s) and 10,000 quotes (about 15 s); tested |
  | 9 | The load test repeated the sign-in helper and did not check the answer | One shared helper (`e2e/demo.ts`) that checks it |
  | 10 | An alert heading showed the kept 200 as if it were the total | It says "the latest 200" |

**The owner's console check** (`pnpm load`, a visible window, 59 Hz 4K displays).
- **First run: failed narrowly.** p50 11 ms, p99 33 ms (just over: the card read WATCH), the page's own work p99
  17.5 ms, and only 101 screen updates (the window was likely covered part of the time; the meter skips frames Edge
  does not draw). At 59 Hz two frames are about 34 ms, so any update whose work spilled past one frame landed near
  the target. The owner chose to make the Markets screen faster before moving on.
- **The fix (`ca59b71`):** each quote update re-rendered the screen through React and AG Grid compared every row.
  Now `useQuoteChanges` hands each flush's changed tickers to the grid as one transaction (only those rows' cells
  redraw), and React renders the grid only when the list of markets changes. Per-commit review: 2 fixed before
  committing (a quote before the grid was ready was dropped until that market's next change: the grid takes every
  current value when ready; a batch still queued when the list changed or the screen closed was delivered: dropped).
  A screen test checks rows change live in the grid.
- **Second run: passed.** Update to screen **p50 10 ms, p99 19 ms** (target <= 33 ms), the page's own work p99
  **2.8 ms** (from 17.5), over 372 screen updates; engine to screen p50 159 ms, p99 216 ms (information: the 10 Hz
  batching on both sides).

**M1.8a-web-2b built and its console check passed**, subject to the owner's merge.

### 1.8b Hotkeys, layouts, themes, settings and wizard, support bundle (plan APPROVED 2026-10-27; two slices)

The M1.8 plan's second part (G15, G16, D20 G, I), redrafted for the web terminal (WPF themes and KalTerm3's window
placement no longer apply). **Owner's answers (2026-10-27): all as recommended.**

| Slice | Builds |
|---|---|
| **M1.8b-1** | mock-ups of the Settings screen and the first-run wizard (approved before they are built); the hotkeys; the themes; the Settings screen and the first-run wizard, writing the engine's settings |
| **M1.8b-2** | layouts (several IKARS windows, saved and restored); the redacted support bundle |

- **Hotkeys** (the mock-up's list): `Ctrl+K` search; `Ctrl+1`..`5` the screens; `Alt+Left/Right` back and forward
  (Edge's own); `Enter` the newest urgent alert; `B` the order book; `X` the Linked panel; `Ctrl+Shift+L` the theme;
  `?` the list. `T`/`I`/`D` and `C` are listed as arriving with the recommendations (M1.12).
- **Themes:** dark, light or Windows', remembered. **Skins (owner, 2026-10-27, after the plan):** built-in palettes
  only, chosen from Settings: Dark, Light, High contrast, Midnight and Paper, or "Follow Windows" (Dark or Light);
  `Ctrl+Shift+L` switches between Dark and Light; saved with the engine's settings. Colours only, so the CSP is
  unchanged. Own skins (editing colours) were offered and not chosen.
- **Mock-ups approved as shown** (owner, 2026-10-27): `Settings.html` and `Wizard.html`; the Appearance section's
  theme switch becomes the skin picker.
- **Layouts:** a layout is a set of IKARS windows (each a screen, a position and a size, on any monitor); "Save
  layout as..."; `ikars open --layout NAME`; the last one restored at start. Panes docked within a window are parked
  (PLAN section 9).
- **Settings and the wizard:** terminal preferences and the engine settings the owner would change (share, tax zone,
  quiet hours, port, session length), each written through the engine (versioned, migrated, backed up first), with a
  note when a restart is needed; the wizard opens when there is no settings file and checks the key variables (present
  or not, never shown), the engine and feed, the share, the tax zone and quiet hours.
- **Support bundle:** saved to `%LOCALAPPDATA%\IKARS\support\` (logs, health, versions, settings without the share
  path, the gap log; never keys, tokens, key paths or account amounts, checked before it is kept); never sent.
- **Machine:** a settings migration (backup first) and the `support\` folder. **Packages:** none.
- **Live checks:** each hotkey; the theme across a restart; two windows on two monitors saved and restored; the
  wizard on a scratch data folder; a bundle searched for the key id, token, share path and amounts.

#### 1.8b-1 built (2026-10-27)

**Built** on `m1/8b`: `fd8e4b8` (the mock-ups), `8e97baa` (hotkeys and themes), `74fe784` (the engine's settings
for the terminal), `8581d27` (Settings, the wizard and skins), `95b9412` (the merge-review fixes). After M1.8a-web
completed (PR #20, `1aeb2bf`). **No new packages.**

- **Hotkeys** (`src/app/keys.ts`, `screens/Keys.tsx`): `Ctrl+K`, `Ctrl+1`..`5`, `Enter` (the Alerts screen), `B`,
  `X`, `Ctrl+Shift+L`, `?` (the list; `T`/`I`/`D` and `C` shown as arriving in M1.12); single keys are left to a text
  box, `Enter` to a focused control, `Esc` to the box unless the list is open.
- **Skins** (owner: built-in palettes only): Dark, Light, High contrast, Midnight, Paper, or follow Windows; one
  `Skin` type in `settings.py`, used by the settings, the contracts and the generated terminal type; `data-theme`
  on the page; the chart repaints; loaded at start; `Ctrl+Shift+L` and the picker save it at once. CSP unchanged.
- **Engine** (`settings_api.py`; routes `GET`/`PUT /v1/settings`, `POST /v1/settings/share-check` and
  `share-init`, `POST /v1/engine/autostart` and `restart`): the view (the key as yes or no only, also from Windows'
  current user variables with a restart note; which saved settings wait for a restart; start at sign-in), a change
  checked and saved with a backup first (a skin makes none; the latest 20 kept), the share tested and set up in a
  thread with a time limit and written only inside its `ikars\` folder, start at sign-in, and a restart (a marker
  and a stop; `serve` starts the fresh engine before releasing its lock; a start spends a stale marker; a foreground
  engine refuses). Off the event loop: the view, a change and start at sign-in. `demo-api` gets a config with an
  in-memory registry and `--first-run`.
- **Settings and the wizard** (the approved mock-ups): Settings with the skin picker, the key, alerts, ledger and
  backups (a share test), feed and storage, terminal, and Save / Discard / Restart the engine; the wizard at
  `/welcome` when there are no settings (key, engine and feed, backups: test, set up or skip, tax zone and quiet
  hours, done with start at sign-in).

**Verified.** Python 1,407 tests; Vitest 184 (98% lines, 92% functions); Playwright 14 screen tests in Edge (a skin
kept across a reload; a change and its restart note; the whole wizard on a first-run demo server; hotkeys); lint,
types, audit, data guard clean. **Live:** the engine restarted on the new code (the same port, the old cookie valid);
its settings view: not a first run, skin system, nothing waiting for a restart, the key set (file found, readable by
the owner only), the share set, start at sign-in installed, the tax zone America/Denver.

**Review.**
- **Per commit:** `8e97baa` 2 fixed before committing (Enter on a focused link or button, and Esc in a box, were
  taken); `74fe784` 1 fixed (an unreadable settings file gave a 500: refused in words, never overwritten); `8581d27`
  none; `95b9412` 1 fixed (an expandable registry value was not expanded).
- **Merge review (medium), 10 findings, all fixed in `95b9412`:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | The settings view ran the task scheduler and file reads on the event loop (so did a change and start at sign-in) | In a thread |
  | 2 | A restart request left behind (by a foreground engine) turned a later stop into a restart | A start spends it; a foreground engine refuses a restart; tested |
  | 3 | The wizard saved a share before setting it up; a relative path was accepted | Set up first, saved only with its marker; a full path is required; tested |
  | 4 | Every skin toggle made a new settings backup, without limit | A skin makes none; the latest 20 kept; tested |
  | 5 | After a port change the restart said the window reconnects by itself | It says to reopen with ikars open on the new port |
  | 6 | The key's status read the engine's own environment, fixed at its start | Also Windows' current user variables, with a restart note; tested |
  | 7 | A bad tax zone in the file gave a 500; a blank zone was refused | Refused in words; blank means the workstation's; tested |
  | 8 | The watchdog could start a second engine in the restart gap | The fresh engine starts before the old releases its lock |
  | 9 | Three copies of the in-memory registry fake | One, `autostart.MemoryRegistry` |
  | 10 | The skin's values were listed by hand in Python and TypeScript | One `Skin` Literal; the TypeScript type is generated from it |

**M1.8b-1 built**, subject to the owner's merge.

#### 1.8b-2 built (2026-10-27)

**Built** on `m1/8b-2`: `a7a6f58` (the engine: layouts and the bundle), `8109c7e` (the terminal: Settings' layouts
and Health's bundle), `24a19cd` (the merge-review fixes), `9ba176a` (the bundle closes its database connections).
After M1.8b-1 (PR #21, `db0bf9a`) and the settings race fix (PR #22, `e6a29f3`). **No new packages.**

- **Layouts** (G16): a layout is a named set of IKARS windows, each a screen (an address in the terminal) and a place
  and size in Windows' screen coordinates, so any monitor. They live in the engine's settings (**version 5**; the
  version 4 file is kept as a backup on first load), at most 20 of 8 windows each. `GET`/`PUT /v1/layouts`,
  `PUT /v1/layouts/start`, `DELETE /v1/layouts/{name}`, each checked (a screen is a path of the terminal's own
  characters: no host, no `#`, nothing that could reach Edge's command line) and backed up first. In the terminal,
  every window answers "where are you?" on a `BroadcastChannel` (the engine's own pages only); Settings' Terminal
  section saves the open windows (the saving window counted once), chooses the one `ikars open` restores (or one
  window) and deletes one. `ikars open --layout NAME` opens each window with a sign-in code of its own; since Edge
  applies its window switches to its first process only, each window also carries its place (`#...&at=x,y,w,h`) and
  moves itself there when it loads; saving asks Edge once for its window-management permission so a window may sit
  on another monitor. Panes docked within a window stay parked (PLAN section 9).
- **Support bundle** (D20 I): `ikars support` (through the running engine, else made by the command) and **Make a
  support bundle** on Health. One zip in `<data root>\support\` (the latest 10 kept): the newest logs (up to 50 MB),
  the health report, the versions, the settings with the share's path left out, the gap log (the newest 1,000), and
  a README. Redacted as it is written: the key's id and path (either slash, the file's name alone, and as JSON spells
  them), the API token (even when the engine is stopped), the share's path, PEM blocks and bearer tokens, and the
  account's balance and position amounts (from the engine's portfolio, else the cash the ledger file explains: as
  dollars, millionths, and cents from $1,000); the user's folder and the computer's name become placeholders. Then
  the zip is read back and searched for the same: anything found and it is deleted, the answer naming the file, never
  the value. One bundle at a time; settings read, never rewritten; never sent anywhere.

**Verified.** Python 1,441 tests (94% coverage); Vitest 200 (98% lines, 91% functions); Playwright 16 screen tests in
Edge (two windows saved as a layout, the start choice kept across a reload, deleted; a bundle made from Health); lint,
types, audit, data guard and gitleaks clean. **Live:** the engine restarted on the new code and answers; the settings
are version 5 with the version 4 file kept; the layouts route answers (none saved, one window at start); a real bundle
was kept (7 files, 2 logs, with health, versions, settings and gaps; no part file left) and searched: **no file** held
the key id, the key path, the API token, the share's path, the profile folder, the computer's name, an account amount
or a private key block. **Pending (the owner):** two windows on two monitors saved and reopened with `ikars open`.

**Review.**
- **Per commit:** `a7a6f58` none from the review; my own pass compiled the placeholders into one pattern; a test
  found that JSON-escaped paths (doubled backslashes) escaped both fences, fixed before committing; `8109c7e` none
  from the review; my own pass refused names starting with a dot (`..` in an address is a folder, so it could not be
  deleted); the screen tests found the saving window counted twice (fixed before committing); `9ba176a` found by a
  ResourceWarning.
- **Merge review (medium), 10 findings:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | A later layout window that could not get a sign-in code crashed `ikars open` with a traceback | Said in words (OpenError); tested; `24a19cd` |
  | 2 | Edge reads `--window-position`/`--window-size` from its first process only: windows joining a running Edge would not land in place | Each window also carries its place and moves itself there; saving asks for Edge's window-management permission; `24a19cd`; the owner's live check confirms |
  | 3 | `ikars support` with the engine stopped did not redact the API token | The token read from `api.json` whenever it is there; tested; `24a19cd` |
  | 4 | A half-written `api.json` crashed `ikars support` instead of falling back | Read inside the fallback; tested; `24a19cd` |
  | 5 | A screen with characters outside the engine's set (a search for `*`) blocked saving any layout | %-encoded in the terminal (the same address); tested; `24a19cd` |
  | 6 | The bundle loaded the settings with `load`, which migrates and rewrites outside the settings lock | `settings.read` (migrated in memory, never written); tested; `24a19cd` |
  | 7 | Two bundles at once (a double click) shared one part file | One at a time, a part file of its own, the button waits; tested; `24a19cd` |
  | 8 | Cents of a small balance (like 10000) redacted common numbers; the README claimed every amount | Cents searched for from $1,000 only; the README names the balance and position amounts; tested; `24a19cd` |
  | 9 | The final check held whole members in memory and searched once per secret | Line by line with one pattern; `24a19cd` |
  | 10 | No BUILD-LOG entry or resume point for this slice yet | This entry, PLAN section 5 and the resume point |

**M1.8b-2 built**, subject to the owner's merge and two-monitor check.

### 1.8c New window (approved 2026-10-27; its own slice after PR #23)

The owner asked for a "New window" menu item (opening a second window by command line was awkward:
PowerShell drops an empty `--layout ""`). A scope addition, approved: **a browser popup** (the page opens it with
`window.open`; same Edge profile, so already signed in; terminal only, no engine change), on the same screen as the
window it came from, slightly offset; at the foot of the side navigation. Edge shows a thin read-only address strip on
a popup, unlike `ikars open`'s app windows (accepted). Offered and not chosen: the engine starting a bare app window
on request from the page. Built after PR #23 merges, as its own slice.

#### 1.8c built (2026-10-27)

**Built** on `m1/8c`: `aaa768c` (New window), then the merge-review fixes. After M1.8b-2 (PR #23, `47984db`).
**No new packages; terminal only.**

- **New window** at the foot of the side navigation (hidden on the first-run wizard): a browser popup on the same
  screen as the window it came from, 30 px right and down, the same size (smaller than a window that fills the
  screen, so it never hides it whole). Edge's shared profile signs it in; the link back to its opener is cut once it
  is open; it answers layout asks like any other window, so it can be saved in a layout.

**Verified.** Vitest 202 (98% lines, 91% functions); Playwright 17 screen tests in Edge (New window opens the Ledger
in a popup, signed in, with no toolbar and no opener); lint, types, audit, data guard and gitleaks clean.

**Review.**
- **Per commit:** `aaa768c` none.
- **Merge review (medium), 10 findings:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | `noopener` with the popup's size and place may make Edge open a tab instead | Dropped; the opener is cut after opening; the screen test checks it is a popup with no opener |
  | 2 | The new window copied a maximised window's whole size, so it hid it | At most the screen less twice the offset; tested |
  | 3 | New window on the first-run wizard opened a second wizard saving alongside the first | Hidden on the wizard |
  | 4 | The screen test checked only the address, never that it is a popup | It checks no toolbar and no opener (Edge's headless size is the test's own, so not the size) |
  | 5 | The resume point named M1.8c as both current and next | Rewritten |
  | 6 | No BUILD-LOG entry or PLAN section 5 row | This entry and the row |
  | 7 | The M1.8b-2 row said both built and done | Rewritten as done |
  | 8 | A layout saved from a popup keeps its outer size, address strip included, so an app window reopens that much taller | Parked (PLAN section 9): small |
  | 9 | The unit test's stub was cast to `Window`, hiding a missing field | Cast to the opener's own type (`Opener`), with the screen's size |
  | 10 | `Math.round` on values the browser gives as whole numbers | Removed |

**M1.8c built**, subject to the owner's merge.

### 1.9 Packaging (plan APPROVED 2026-10-27; two slices)

PLAN M1.9, D12, G, G12. **Owner's answers (2026-10-27): all as recommended.**

| Slice | Builds |
|---|---|
| **M1.9a** | the `ikars` command (and window-less `ikarsw`); a launcher that answers Velopack's install and uninstall steps first (a Start-menu entry "IKARS"; on uninstall, start at sign-in, the watchdog task and the notification name removed, **never data**), else opens the terminal; a build script: a standalone relocatable Python with the locked packages (no development tools) and the built web terminal, packed by Velopack into `Setup.exe` and a portable zip (with `portable.marker`); install, first-run wizard, portable run and uninstall checked in **Windows Sandbox**; download sizes measured |
| **M1.9b** | a CI release workflow (a `vX.Y.Z` tag builds both and publishes a GitHub pre-release; pinned, release write permission only); update checks at start and daily, shown on Health and Settings, **installed only when the owner clicks Restart to update** (the engine stops cleanly, Velopack swaps the program, migrations back up first); the rollback drill (N, then N+1 with a real migration, then N again with the pre-migration copies restored; the ledger verifies and reconciles exactly); the signing decision recorded |

- **Folders:** the program installs to `%LOCALAPPDATA%\IKARS-App` (Velopack deletes its own folder on uninstall);
  the data stays in `%LOCALAPPDATA%\IKARS`, which install, update and uninstall never touch (tested).
- **Signing:** unsigned pre-releases for now (the owner is the only user); an application to SignPath Foundation's
  free open-source signing in parallel (the publisher shown would be SignPath Foundation; a published signing policy
  is required); decided for good before the M6 release. Azure Artifact Signing (paid, the owner's name) was offered.
- **Packages:** `velopack` (PyPI, the launcher) and `vpk` (Velopack's .NET packing tool, builds only); both MIT, to
  be confirmed in the slice.
- **Machine:** Windows Sandbox enabled once (the owner; admin, one restart); test installs go into the Sandbox only.

#### 1.9a built (2026-10-27)

**Built** on `m1/9a`: `401dad6` (the `ikars` command, `IKARS.exe`, the build), `7e98d2a` (setup mode), `6e17772`
and `d4f3aba` (the Sandbox checks), `e4ec0cc` (the merge-review fixes). After M1.8c (PR #24, `b336930`).

**Owner's answers during the slice (2026-10-27):** the launcher is a tiny C# program (not a PyInstaller freeze, which
D12 rules out); setup mode for a machine without a usable key.

- **`ikars`**: the engine's command line as a console script (`.venv\Scripts\ikars` in development).
- **`IKARS.exe`** (`tools/launcher`): about 40 lines of C# for **.NET Framework 4.8**, part of every Windows 11, so
  7 KB, no runtime shipped and no C++ tools to build it (NativeAOT, first planned, needs Visual C++'s linker, which
  this workstation lacks). It starts the bundled `pythonw.exe -I -m ikars_engine.launcher` with its arguments.
- **`ikars_engine.launcher`**: answers Velopack's steps first (updates wait for the owner's click; before an update,
  the engine stops; before uninstalling, the engine stops and start at sign-in, the watchdog's task and the
  notification name are removed, never the data), else opens the terminal; a failure shows a message box.
- **The build** (`tools/release/build.py`): uv's standalone CPython 3.13.16 (never the project's `.venv`, which uv
  otherwise prefers; its own `site-packages` never copied), the locked packages with hashes and IKARS's two wheels,
  compiled; `web/dist`; `IKARS.exe`; `vpk pack` (program id `IKARS-App`, the Start menu only,
  `--skipVeloAppCheck` since Python answers the steps). **`Setup.exe` 69 MB, portable zip 65 MB** (installed:
  about 190 MB, most of it pyarrow). It also writes `build/IKARS-test.wsb` (this machine's folders: never committed).
- **Folders:** installed, the program is `%LOCALAPPDATA%\IKARS-App` and the data `%LOCALAPPDATA%\IKARS`; the portable
  zip keeps its data in `<its folder>\IKARS-data`, beside Velopack's `current\` (which an update replaces).
- **Setup mode** (owner): found by the first Sandbox run, where a machine without a key never reached the wizard
  (the engine raised at start). With no usable key on a fresh install (no ledger records), the engine serves the API
  and the terminal only (no feed, no ledger import, no call to Kalshi); `HealthView.setup_reason` and the Health
  feed card and strip say why and what to do; the watchdog tells the owner and does not restart it. A fresh engine
  gets the key's variables as Windows has them now, so Restart the engine after setting the key starts it in full.
  With ledger records, a failing key stops the engine as before (the watchdog restarts it, urgently).

**Verified.** Python 1,456 tests; Vitest 203; Playwright 17 in Edge; lint, types, audit, data guard and gitleaks
clean; CI builds the launcher. **Bundle:** its Python uses only its own packages and the bundled terminal, and starts
the bundled engine isolated; the bundled engine without a key starts in setup mode (a scratch folder). **Clean machine
(Windows Sandbox, the owner, three runs):** no Python there; installed to `%LOCALAPPDATA%\IKARS-App`; the Start-menu
entry, no desktop shortcut; started from the Start menu in setup mode with the wizard in Edge; uninstalled: the
program and the Start-menu entry gone, **the data and the settings kept**, start at sign-in and the watchdog task
removed; the portable zip ran from its own folder with its data beside it, nothing written inside `current\`.
Found on the way: a silent install starts nothing (the check now starts IKARS from the Start menu); `Expand-Archive`
on the portable zip crashed the Sandbox (`tar` now, with 4 GB).

**Review.**
- **Per commit:** `401dad6`, `7e98d2a` none.
- **Merge review (medium), 10 findings:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | `ikars run --minutes N` without a key never ended (minutes not passed to setup mode) | Passed on; tested |
  | 2 | On an install with history, any key error left the engine in setup mode for good, with backups and ledger checks quiet | Setup mode only without ledger records; otherwise the engine stops and the watchdog restarts it urgently; tested |
  | 3 | The engine, the watchdog's start and start at sign-in ran Python without `-I` (PYTHON* variables, the working folder) | `-I` everywhere; tested |
  | 4 | The engine's key check read the standard names while the wizard read the settings' names | The signers read the settings' names; tested. Refreshing custom names on a restart parked (PLAN section 9) |
  | 5 | A key variable cleared in Windows kept its old value in a restarted engine | No change: a key set only for a session must survive a restart; clearing one takes a sign-out |
  | 6 | A failed build step showed only an exit code | Its output is printed |
  | 7 | The managed Python's own `site-packages` (anything installed by hand) would ship | Never copied: locked packages only |
  | 8 | Nothing stopped the engine before Velopack replaces the program on an update | A before-update step stops it; tested |
  | 9 | Setup mode was visible only in Health's untyped details | `HealthView.setup_reason`, shown on the feed card and the strip; tested |
  | 10 | The Sandbox config did not escape its folder paths | Escaped as XML |

**M1.9a built**, subject to the owner's merge.

#### 1.9b built (2026-10-27)

**Built** on `m1/9b`: `a41b0f8` (updates and the release workflow), `b72701d` (the pre-upgrade snapshot and
`ikars rollback`), `1431125` (the state migration v0.1.1 carried), `a6e53fc`, `800c80d` and `7c14960` (the update's
progress, from the owner's three rounds of notes), `6098a74` (what a rollback leaves aside), `e5b9acc` (the code
signing policy), `e42b07c` (the merge-review fixes). After M1.9a (PR #25, `31a992a`). **No new packages.**

**Owner's answers during the slice (2026-10-27):** a pre-upgrade snapshot for the rollback (not forward-fix only);
the drill on the workstation, installed (not the Sandbox); the **IKARS Update window** (approved from its mock-up,
not Velopack's own window); publishing each test pre-release (v0.1.4, v0.1.5, v0.1.6 at the owner's word).

- **Releases** (`.github/workflows/release.yml`): a `vX.Y.Z` tag builds `Setup.exe` and the portable zip (the
  previous release fetched first, so an update is a small delta) and publishes a GitHub **pre-release**;
  `contents: write` only there; the tag read from the environment, never pasted into a script. Seven releases,
  v0.1.0 to v0.1.6, each built on its first run.
- **Updates** (`updates.py`; `GET /v1/update`, `POST /v1/update/check` and `/apply`): the installed program checks a
  minute after the start and then daily, downloads in the background (Velopack's progress, a delta's own size,
  a short step log), and installs only on **Restart to update**: Velopack waits for the engine to stop, swaps the
  program folder (never the data), and restarts `IKARS.exe --after-update`, which starts the engine without a
  second window. Velopack's manager allows one thread at a time: every use holds the updater's lock and the
  version is read from its manifest. Health (Program) and Settings (Version) show the version, Check now with a
  download bar, and Restart to update.
- **The IKARS Update window** (`/update`, the owner's design): opened by Restart to update, centred over the
  terminal; the download with its bar, then each step ticked as it is seen (the engine stops, Velopack installs,
  the engine starts, the terminal reloads) with the elapsed time, and Details; once the new version answers every
  IKARS window reloads (a message on the windows' channel, which the Update window itself ignores) and it says
  **Update complete** and closes after a 5-second countdown.
- **Safe upgrades** (`upgrades.py`): every start of the installed program (`serve`, `run`, `IKARS.exe`), before the
  settings are first read, compares its version with `data\program.version`; on a change it copies the settings,
  the state, the ledger and its checkpoints to `backups\pre-upgrade\<old version>\` with a manifest (the ledger to
  the share too; a development engine's data as `unversioned`; the latest 5 kept). A failed snapshot is logged and
  never stops the start.
- **`ikars rollback VERSION`**: checks the snapshot and downloads that release first (Velopack, allowed to go back),
  refuses if the engine does not stop, sets the current data aside in `backups\rolled-back\` (never deleted; a
  failed swap undoes exactly what it did), restores the snapshot, and has Velopack install the release; it reports
  the owner's annotations and IKARS's own records left in the copy set aside.
- **Code signing** ([docs/CODE-SIGNING.md](CODE-SIGNING.md), linked from the README): the policy SignPath asks for
  (the release workflow the only build; the owner approves; what IKARS connects to). **Decision (owner): unsigned
  pre-releases now**; the SignPath Foundation application is the owner's to file; decided for good before M6.

**Verified.** Python 1,483 tests; Vitest 212; Playwright 18 in Edge; lint, types, audit, data guard, gitleaks and
CodeQL (C# included) clean. **Live, on the workstation:**
- **Installed:** v0.1.0 from its GitHub release, silently, started from the Start menu; it took the `unversioned`
  snapshot of the development engine's data and ran on the owner's key: the feed live, the ledger **verified and
  reconciled exactly**.
- **Update with a migration:** 0.1.0 to 0.1.1 on the owner's click: the 0.1.0 snapshot first, then the state
  migration (schema 2, the new index); the ledger verified and reconciled exactly.
- **Rollback drill:** `ikars rollback 0.1.0`: 0.1.0 downloaded, the engine stopped, the data set aside, the
  snapshot restored (schema 1), Velopack installed 0.1.0; the engine ran on the owner's key, the ledger **verified
  and reconciled exactly**. Records after the snapshot were only IKARS's own `app` events (now reported, kept
  aside; their place in M5's tax package parked).
- **The update experience**, three rounds of the owner's notes: 0.1.2 (little feedback, a second window, the
  layout), 0.1.4 (the window stayed open, a cut-off button, a 500 from Velopack's one-thread rule), and **0.1.5 to
  0.1.6 approved** ("looks good").

**Pending (the owner):** start at sign-in and the watchdog task still point at the development Python
(`install-autostart` from the installed program, which the classifier left to the owner); the SignPath application.

**Review.**
- **Per commit:** `b72701d` 3 found and fixed before committing (snapshot lookup by version, the settings file's
  name, the undo of a failed swap); the others none.
- **Merge review (medium), 10 findings, fixed in `e42b07c` unless noted:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | A failed restore's undo deleted every live place, including data it never moved | It undoes exactly what it did; tested |
  | 2 | A rollback went on when the engine had not stopped in time | Refused, nothing changed; tested |
  | 3 | `IKARS.exe` read (and migrated) the settings before the snapshot | One start hook, `upgrades.on_start`, before any read |
  | 4 | A failed daily recheck turned a downloaded update into a failure | The update stays ready; no recheck while one waits; tested |
  | 5 | The Update window failed when a check was already running | It waits for that check |
  | 6 | A failed snapshot stopped every start; two starts at once collided | Logged, never stops the start, the version recorded; each start its own folder; tested |
  | 7 | A second Restart to update handed the update to Velopack twice | Installing after the first; tested |
  | 8 | The snapshot lookup and its message were in two places | `upgrades.require_snapshot` |
  | 9 | A whole update manager was built only to read the version | Read from Velopack's manifest; tested |
  | 10 | No BUILD-LOG entry for the slice | This entry, PLAN section 5 and the resume point |

**M1.9b built**, subject to the owner's merge. **M1.9 is then complete.**

### 1.12 End-to-end momentum (plan APPROVED 2026-10-27; three slices)

PLAN M1.12 (D21-2; platform review 1.R2 items 8 and 9). **Owner's answers (2026-10-27): all as recommended.**

| Slice | Builds |
|---|---|
| **M1.12a** | the live runner loads the plugins (`discover`), records what they emit (the generic recording schema, D20 A) and hands recommendations to the API and the stream through a listener; the host, not the plugin, stamps `received_ns`; the **journal** (`journal.sqlite` in the data folder, apart from the tax ledger): each recommendation with its provenance (F) and lifecycle (G9: active, expired, superseded), scored after a fixed time |
| **M1.12b** | the momentum plugin, its rule and parameters **registered by hash** (G4); P&L after fees on entry and exit (D7, `ikars-core` fees); tradability filters (D20 E) |
| **M1.12c** | the Overview's recommendation list, the Recommendation drill-down (the approved M1.3 mock-up, to the parts that exist now), the alert tier (H) |

- **The rule (pre-registered):** continuation on watched markets: the YES mid moves 4c or more within 5 minutes on at
  least 50 contracts traded, the spread 3c or less, the price from 10c to 90c: recommend the same direction, valid 30
  minutes; scored at 30 and 60 minutes after fees. A change makes a new version with its own record.
- **Size:** a fixed 10 contracts (paper only; no orders; recommend-only until M7, D6).
- **Alerts:** a normal-priority Windows notification, throttled, quiet hours and the digest applying; the Overview and
  Alerts screens too.
- **Guard:** no recommendations on SPARQ's forward-test events closing before 2026-11-23 (their list read from SPARQ,
  read only) until SPARQ's S1.1b verdict.
- **Machine:** `journal.sqlite` in the data folder, backed up with the state. **Packages:** none expected.
- **Live checks:** the engine runs the plugin on the owner's key; recommendations reach the Overview; the journal
  scores them; no order is ever sent.

#### 1.12a built (2026-10-27)

**Built** on `m1/12a`: `c4d26ab` (plugins, recording, the journal), `e6977ab` (the merge-review fixes). After M1.9b
(PR #26, `d11165d`). **No new packages.**

- **Plugins in the live engine** (review 1.R2 item 8): the runner loads every registered plugin (`discover`) with the
  settings' host options; one that cannot load, or that the host refuses (two of one name), is listed in health and
  never stops the engine. Every item a plugin emits is recorded in the recorder's generic **`item`** channel (D20 A:
  times, kind, id, source, hops, the payload as JSON); what plugins emit while the engine drains is recorded too.
- **Received time** (review 1.R2 item 9): the host stamps `received_ns` on every emit (live the wall clock, replayed
  the replay's), never the plugin's word.
- **The journal** (`journal.py`, `journal.sqlite` in the data folder, apart from the tax ledger): a `recommendation`
  item's payload is a `Recommendation` (unknown fields refused; price, count, P&L, horizons and expiry bounded). Each
  is kept with its provenance and registered version; a newer one of the same rule on the same market supersedes it;
  it expires at its time; at each horizon the market's YES bid and ask are marked (waiting up to ten minutes for a
  quote; a mark the engine was down for is still made, its time showing how late). Fed by an engine listener that
  only queues; written once a second on the event loop; what is queued at shutdown is written. Health: recorded,
  rejected, dropped, failed passes. In the pre-upgrade snapshot and the share's daily state folder.
- **`GET /v1/recommendations`**: the journal's newest 200, with their marks (`RecommendationView`, `MarkView`).
- A flaky privacy test fixed: a log record's creation time could hold an amount's digits by chance.

**Verified.** Python 1,502 tests; Vitest 214; lint, types, audit and the data guard clean. **Live:** folded into
M1.12b's live check (no plugin exists before it, so the journal stays empty).

**Review.**
- **Per commit:** `c4d26ab` 1 found and fixed before committing (the journal's counts and a lost batch).
- **Merge review (medium), 10 findings, all fixed in `e6977ab`:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | The same recommendation id again superseded the newer one and was counted | It changes nothing; tested |
  | 2 | Two plugins of one name stopped the engine from starting | The second is left out and said in health |
  | 3 | A horizon that fell due with no quote yet was marked empty for ever | It waits up to ten minutes for one; tested |
  | 4 | What plugins emitted while the engine drained reached a closed recorder | The recorder closes once more, last |
  | 5 | What was queued at shutdown never reached the journal | Written before it closes; tested |
  | 6 | `ikars ledger backup` replaced the share's state folder without the journal | It sends the journal too |
  | 7 | One out-of-range value from a plugin could break the route for every reader | Bounded and refused; tested |
  | 8 | The mark check scanned every mark and decoded a day of payloads each second | A table of pending marks, indexed by when they fall due |
  | 9 | A horizon over a day was never marked; a long outage lost marks silently | No window: still due; tested |
  | 10 | Recommendations made in the same instant listed in a changing order | A stable tie-break |

**M1.12a built**, subject to the owner's merge.

#### 1.12b built (2026-10-28)

**Built** on `m1/12b`: `4a0e180` (the signal, P&L after fees, tradability), `e463699` (the merge-review fixes),
`f810687` (settlement scoring, after the live check). After M1.12a (PR #27, `099b94b`). **No new packages.**

**Owner's answers during the slice:** SPARQ's guard lapsed with S1.1b (2026-10-27; CLAUDE.md, PLAN); momentum may use
any market the ticker feed covers, with a depth check; after the live hour (2026-10-28): **v1 keeps running a week
unchanged**, then its record decides (pre-registration: no tuning after one evening), and a check on a closed market
is **scored at its settlement**.

- **The signal** (`signals/momentum.py`, registered under `ikars.plugins`): continuation, as registered (4c in 5
  minutes on 50 contracts traded; buy the side it moved toward; valid 30 minutes; marked at 30 and 60; 10
  contracts). Its parameters hashed into its version (G4): **v1 `0d648804a13a`**. The window keeps the price at its
  start (Kalshi sends a ticker only on a change). One recommendation per market at a time, across restarts (the
  journal says what is still active).
- **Tradability (D20 E):** trading ("active" or "open") and closing after the validity; spread 3c or less; mid 10c
  to 90c; the side's best ask holds the 10 contracts; the series' fee modelled.
- **P&L after fees (D7, `pnl.py`):** the one place for the fee rule (both legs' taker fees, a cent per order; the
  portfolio uses it too) and a side's buy and sell prices (no bid is no price, never a price of nothing). A
  recommendation carries its P&L at the rule's target; each journal mark is scored after fees, or **at the
  market's settlement** ($1 or $0, the entry fee only) when it had stopped trading then and has since settled
  (journal migration 2: settlements, learned once a minute from the market index, and whether each mark's market
  was closed).
- **Plugins get read-only services** from the host (a mapping proxy): a ticker's details, a series' fees, and
  whether a strategy's recommendation is still active.

**Verified.** Python 1,525 tests (the P&L checked by hand: $0.04 on 10 contracts bought at 46c, sold at 50c; $5.22
held to a winning settlement); Vitest 214; lint, types, audit and the data guard clean. **Live, on the owner's
workstation (v0.1.7, 2026-10-28 05:31 to 06:32 UTC; counts and states only):**
- the engine ran the plugin on the owner's key; nothing failed to load, nothing rejected or dropped; one version
  (v1); one recommendation per market;
- **374 recommendations in the first hour** (about 20 a minute at first, mostly in-play NBA spreads and points and
  tennis); 228 expired on time; every mark within 1 second of its due time;
- **30-minute marks: 237; 131 (55%) found no bid** (the games had ended and the markets closed): the settlement
  scoring above follows from it; of 106 scored, 47 above zero, median -$0.28 and mean -$0.42 per 10 contracts
  after fees (the standard fee; one evening, far too few to judge the rule). **60-minute marks:** 32, 6 scored;
- 9 recommendations shown superseded by one made the second they expired: fixed (only a still-valid one is
  superseded);
- no order was ever sent (IKARS has no order code; its key reads only).
- **The M1.6 auto-start check passed:** after the owner's restart the installed IKARS started at sign-in.

**Review.**
- **Per commit:** `4a0e180` none; `f810687` 1 found and fixed before committing (an empty book while the market
  still traded would have been scored at the later settlement: each mark now records whether it was closed).
- **Merge review (medium), 9 findings, all fixed in `e463699`:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | A fractional contract count could make the recommendations route fail (scoring raised) | Exact arithmetic in `pnl`; the journal takes whole contracts; tested |
  | 2 | A side with no bid was scored as a sale at nothing (a full loss) | No bid is no price: unscored (and later, at settlement); tested |
  | 3 | Markets in the "open" status were never recommended | Both trading statuses count |
  | 4 | After a quiet spell a real move was measured from the newest price | The window keeps the price at its start; tested both ways |
  | 5 | After a restart a second recommendation could come while the first was valid | The journal's "still active", as a host service; tested |
  | 6 | A new market record on every feed item; markets never forgotten | Made only when new; idle for an hour, dropped |
  | 7 | The fee rule in two places | `pnl.taker_fee_e6`, used by the portfolio |
  | 8 | `pnl` depended on the ledger importer and the portfolio | It depends on neither |
  | 9 | The services could be changed by a plugin | A read-only mapping; tested |

**M1.12b done:** PR #28 merged as `3f0f8bd` (2026-10-28), CI and CodeQL green on `main`. Momentum v1 keeps
running on the installed IKARS.

### 1.12c The terminal side of momentum (plan APPROVED 2026-10-28)

The third slice of M1.12 (approved in 1.12): the Overview's recommendation list, the Recommendation drill-down
and the alert tier (D20 H). **Owner (2026-10-28): proceed as recommended**: momentum v1 makes about 7 to 20
recommendations a minute, so a notification for each is out; a ranked list instead, and notifications for only
the strongest few. Branch `m1/12c`, after M1.12b (PR #28, `3f0f8bd`).

**Engine**
- **Views:** `/v1/recommendations` takes `state=active` and returns the active ones ranked;
  `/v1/recommendations/{id}` returns one of any age (the list stops at the newest 200). Each view gains the market's
  title, the return on capital at the rule's target and per day, the worst case (the side ends at $0: the cost and
  the entry fee) and the owner's response.
- **Ranking:** by return per day of capital tied up, at the rule's target and after fees (D20 D; the mock-up's
  order). Momentum's validity is fixed at 30 minutes, so this orders as return on capital does. Its per-day
  figures look large (a 4c move on 46c over half an hour).
- **Live list:** a `recommendations` stream event carries the active list (ranked, up to 50) when it changes.
  The engine checks once a second, so the Overview needs no polling.
- **The owner's response (G9):** taken, ignored or dismissed, written to the journal (migration 3: the latest
  answer per recommendation) through `PUT /v1/recommendations/{id}/response`. "Taken" is only the owner's word:
  IKARS places nothing (D6).
- **Scorecard:** for each strategy and version over the last 30 days: how many were scored, the share above
  zero, and the mean and median after costs at each check. The journal service works it out once a minute and
  the API serves that copy. At about 10,000 recommendations a day it is never computed per request.
- **Alerts (H):** for the first time, the engine raises alerts of its own.
  - `normal` alerts now show outside quiet hours, throttled, and wait for the digest in quiet hours. Nothing
    raised `normal` before: the watchdog uses high and urgent.
  - A recommendation alerts only if it ranks first among the active ones when it is made, and the hourly budget
    (3) is not spent. The rest appear only on the Overview, never in the alert log.
  - The text is the market, the side, the price and the move. As before, no amounts.
  - The engine's alert desk keeps its own state file, because each watchdog pass rewrites the watchdog's. It
    appends to the same alert log under a lock, and shows notifications on a worker thread, never on the event
    loop.

**Terminal**
- **Overview, "Top recommendations"** (replacing the placeholder):
  - columns: market (its title and the why), action, size, P&L at the target after costs, return per day,
    strategy and version, and expiry (counting down);
  - active ones only, ranked, up to 50 (scrolling), with the active count;
  - a row opens the drill-down.
- **Overview, "Journal scorecard (30 days)"** (replacing the placeholder): one line per strategy version.
- **Recommendation drill-down** (`/recommendations/:id`), from the approved M1.3 mock-up, limited to what exists:
  - the headline (Buy YES 10 @ $0.46), with the strategy, version, state and expiry;
  - P&L at the target, return per day, and the worst case;
  - **Mark taken / Ignore / Dismiss** (`T`/`I`/`D`), and **Copy order** (`C`: ticker, side, size and price to
    the clipboard); both hotkeys were promised for M1.12 in 1.8b-1;
  - **Why:** the summary and the inputs, with received times;
  - the **checks** at 30 and 60 minutes: bid and ask, and the score after fees or at settlement;
  - **provenance:** the engine version, the rule and its registered version, the number of inputs, and "LLM not
    used".
- **Left out, because nothing yet feeds them:**
  - on the drill-down: fill chance (D20 C), size and limits (the risk layer), the bracket (watch orders), the
    settings hash, and Replay (M1.13);
  - on the Overview: the Fill column. Its "EV after costs" column becomes "P&L at target" (momentum has no
    probability estimate);
  - "Live movers" stays a placeholder: it needs a scan of every market, not the journal. It is relabelled for M2.
- **demo-api:** made-up recommendations and a scorecard, for the screen tests and `pnpm load`.

**Machine:** journal migration 3 (backed up first, as before), and the engine's alert state file
(`alert-desk-engine.json` in the data folder). **Packages:** none.

**Tests:** ranking, worst case and return per day by hand; the response round trip and the migration; the
scorecard against a hand-built journal; the hourly budget, quiet hours and the throttle; the lock on the shared
log; the screens in Vitest and Playwright (the list live, the drill-down, `T`/`I`/`D`/`C`).

**Live checks** (on a test pre-release, published on the owner's word):
- the Overview's list updates live and ranked, and a row opens the drill-down;
- `T`/`I`/`D` are kept across a restart, and `C` copies the order;
- at most 3 recommendation notifications an hour, and none in quiet hours;
- the scorecard matches the journal read directly;
- no order is ever sent.

**Owner's answers (2026-10-28): all as recommended.** Notifications: top-ranked only, at most 3 an hour; the
response and Copy order come now; the scorecard comes now; no mock-up redraw (the relabel and omissions as
listed).

#### 1.12c built (2026-10-28)

**Built** on `m1/12c`: `cd72d8f` (the engine side), `c1fb678` (the terminal side), `7a4592a` (the merge-review
fixes). After M1.12b (PR #28, `3f0f8bd`). **No new packages.** API 2.1 (contracts and the web types regenerated).

- **Figures and ranking** (`recommendations.py`): the outlay (the contracts and the entry fee; also the worst case),
  the return at the rule's target after fees, and that per day of validity (D20 D), all integers (millionths). The
  **board** keeps the active ones ranked as they come and go; the API serves the top 50
  (`/v1/recommendations?state=active`) and the stream sends them as a `recommendations` event when they change.
  `/v1/recommendations/{id}` returns one of any age.
- **The owner's response (G9):** `PUT /v1/recommendations/{id}/response` (taken, ignored, dismissed; the same
  again takes it back); a dismissed one leaves the list. Journal migration 3.
- **Kept scores:** each check's score is kept with it once known (at once, at the market's settlement, or, when the
  fees stay unknown for an hour, kept unscored), so an old check keeps its score after its market leaves the index,
  and the **scorecard** (`/v1/scorecard`: each strategy version over 30 days: checks, scored, above zero, mean,
  median) is a query on a worker thread once a minute.
- **Notifications (H):** only a recommendation that ranks first when it is made, at most 3 an hour, none in quiet
  hours; the engine's own alert desk state (`alert-desk-engine.json`); the alert log written under a lock shared
  with the watchdog; `normal` alerts now show by day (nothing raised them before).
- **Terminal:** the Overview's "Top recommendations" (live, ranked, countdowns) and "Journal scorecard (30 days)";
  "Live movers" relabelled for M2. The **Recommendation screen** (`/recommendation/<id>`), from the approved
  mock-up to what exists: the headline, P&L at the target, return per day, the outlay and worst case; Mark taken,
  Ignore, Dismiss (`T`/`I`/`D`) and Copy order (`C`); why, the checks made and to come, the provenance. Left out
  as planned: fill chance, size and limits, the bracket, the settings hash, Replay; the Overview's Fill column.
- **demo-api:** six made-up active recommendations and one with its check, for the screen tests and `pnpm load`.

**Verified.** Python 1,537 tests (the figures by hand: 10 at 46c lay out $4.78; +$0.04 is 0.8368%, 40.17% a day
over half an hour; held to a win, +$5.22; the scorecard's mean and median by hand); Vitest 228 (functions 91.6%);
Playwright 19 in Edge against demo-api (the list ranked, the drill-down, `T` and back, `C`); lint, types, audit,
the generated-types check and the data guard clean.

**Live checks on v0.1.8** (test pre-release from `bac418d`, published on the owner's word; the owner's
workstation, 2026-10-28 19:00 to 20:05 MDT; counts and states only):
- the list: about 1,000 active, the top 50 served, ranked by return per day, every row titled; one by id returns
  the same one with its 30- and 60-minute checks;
- `I` (by the API) kept across a clean stop and start of the installed engine; `D` takes one off the list and
  undoing it puts it back; both answers cleared afterwards;
- the scorecard, read just after its minute's refresh, equal to the journal read directly (checks, scored, above
  zero, mean, median) at both horizons;
- notifications: 2 shown in the first hour (cap 3), none in quiet hours (overnight not yet seen); no errors in the
  engine log;
- no order: the signer and the REST transport refuse any method but GET.
- **The owner found the list hard to use:** the market name opened the Market screen and only the action text the
  Recommendation screen, where Copy order and `T`/`I`/`D`/`C` live, so `C` "did not work". Taken up as **M1.12d**
  (below); merged as is at the owner's word.

**Review.**
- **Per commit:** `cd72d8f` 1 found and fixed before committing (a check whose fees were not known yet was kept
  unscored for good: it now waits, up to an hour); `c1fb678` 2 found and fixed before committing (the rule label
  and the headline duplicated their formatting: one helper each).
- **Merge review (medium), 10 findings: 9 fixed in `7a4592a`, 1 no change:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | At stop the notices closed before the journal's last flush; an offer then raised and lost the batch | Closed after the flush; an offer after closing is refused; tested |
  | 2 | A page built before the board loaded stayed empty until the next change | The load counts as a change; tested |
  | 3 | Checks waiting for fees could fill every scoring batch and hold back the rest | Paged in order of made time; tested |
  | 4 | The scorecard query was awaited inside the journal loop | Its own task beside the loop |
  | 5 | The desk's per-market throttle could spend the hourly budget on nothing | Keyed by the recommendation; tested |
  | 6 | The ranked page's cache missed marks, scores and index changes | They count as changes; the index version is in the key; tested |
  | 7 | "Ranks first" compared with ones expired but not yet pruned | Only those still valid when it was made; tested |
  | 8 | Dismissals of replaced or expired recommendations stayed in memory for good | Only one on the board stays hidden; tested |
  | 9 | Normal alerts now pop up for every caller, not only recommendations | No change: approved in this slice's plan (normal shows by day, nothing else raises it) |
  | 10 | demo-api called the journal service's private `_write` | It uses `listen` and `flush` |

**Added at the owner's request (2026-10-28, after a network outage):** a watchdog "recovered" notification
repeated the problem ("recovered: the engine is not running"), which read as if it still were. Each problem now has
its own all-clear text ("the engine is running again", "Kalshi's feed is live again", ...), and a test holds every
problem the watchdog can raise to one. Per-commit review: none.

**M1.12c done**: merged at the owner's word (2026-10-28).

### 1.12d Navigation: rows, Back and named crumbs (approved 2026-10-28)

**Owner, 2026-10-28** ("let's rethink that part of the GUI"; then "okay"): approved as recommended:
- **Overview list:** a click anywhere on a row opens the Recommendation screen (one target; the market link stays
  on that screen); a highlighted row, `Up`/`Down` to move it, `Enter` to open; `C`/`T`/`I`/`D` on the highlighted
  row on the Overview too, with a short "Copied" line; a hint line names the keys. Mock-up: the canvas's Main
  board (revised, versions 9 and 10).
- **Back (R9):** a Back button left of the breadcrumbs, the same as `Alt+Left`, greyed when there is nowhere to
  go back to (the Edge app window hides the browser's own).
- **Named last crumb:** e.g. `Overview › Recommendation: <market>`, not just "Recommendation".
- Not now: a trail of the exact path taken (Back covers it).

No engine change, no new packages.

#### 1.12d built (2026-10-28)

**Built** on `m1/12d`: `c6a424f` (the slice), `3b11a27` (the merge-review fixes). After M1.12c (PR #29, `d39f623`).

- **The Overview's list:** a click anywhere on a row opens its Recommendation screen (the market name is the row's
  one link, for the keyboard; a modified click or a text selection is left alone). `Up`/`Down` highlight a row and
  focus its link; `Enter` opens the highlighted row even when the focus has moved; `T`/`I`/`D`/`C` act on it, with
  a note under the list ("<market>: Marked taken.", "Copied: ..."). The highlight follows the recommendation's
  id, never its place, so a re-ranked list cannot move it onto another. The arrows are the list's from inside it,
  the top bar or nowhere; from another Overview card and on every other screen they stay the browser's.
- **Back:** a button left of the breadcrumbs (`Alt+Left`), greyed on the screen the window opened on: the navigator
  counts its moves in the history entry's state.
- **Named crumb:** the Recommendation screen names its market for the last crumb and the window title
  ("Recommendation: <market>").
- **Reuse:** the answer and Copy order are one helper (`app/answers.ts`), `T`/`I`/`D`/`C` one hook
  (`useAnswerKeys`), a plain click one test (`plainClick`). The approved mock-up is in `docs/design/mockups/`.

**Verified.** Vitest 232 (the navigator's Back and depth, the screen's name, the arrows, the answer and Copy order
helpers); Playwright 19 in Edge against demo-api (a click on an empty cell opens the row, the named crumb, Back
and its greyed state, `Down`, `C` with the clipboard read back, `T` and back, `Enter` after the focus moved); lint,
types and the data guard clean.

**Live check passed** on v0.1.9 (test pre-release from `66d8699`, published on the owner's word, 2026-10-28): the
owner used the list, the keys, Back and the named crumb on the workstation: "everything looks good".

**Review.**
- **Per commit:** `c6a424f` 1 found and fixed before committing (a move clears the screen's name and it was only
  given again when the name changed: now after every move).
- **Merge review (medium), 8 findings: 7 fixed in `3b11a27`, 1 no change:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | An answer on the Overview dropped the engine's copy, so the same key again saved it again | The copy is kept until the next list; tested end to end |
  | 2 | `Enter` went to the alerts when a row was highlighted but the focus had moved | `Enter` opens the highlighted row first; tested end to end |
  | 3 | The arrows were taken over the whole Overview | Only from the list, the top bar or nowhere |
  | 4 | The row's link to the Market screen is gone | No change: the approved design (one target per row; the market is linked on the Recommendation screen) |
  | 5 | A key's note was not shown with an empty list | The note is always there |
  | 6 | The `T`/`I`/`D`/`C` event wiring was copied from the Recommendation screen | One hook, `useAnswerKeys` |
  | 7 | The row repeated the link's modified-click test | One helper, `plainClick` |
  | 8 | A row's element id held the raw recommendation id | Encoded |

**M1.12d built and checked live**, subject to the owner's merge.

## 1.11 Close-out (plan APPROVED 2026-10-28)

**Owner, 2026-10-28:** the plan as proposed, with these answers:
- **M1.10 perp research moves to M2** (not done in M1; not in M1's gate).
- **M1 closes without M1.13**; the replay of real recordings comes before M3's back-tests, as planned.
- **Build `ikars restore`** rather than manual copy steps.
- **Fix the network wait at start** in this slice (after the reboot outage the engine stopped on its first account
  request and the watchdog restarted it within 5 minutes).

**Builds:**
1. **Gate evidence** (§4.2), re-checked fresh where stale: v0.1.9 in Windows Sandbox on a clean account; live
   markets; `ledger verify` and an exact reconcile; `ledger restore-drill`; the latency and frame-time measurements;
   M1.12 end to end.
2. **`ikars restore`**: the newest share backup of the ledger, the journal, the settings and the engine state into
   an **empty** data root; checksums and the hash chain checked; a root with data refused. **`docs/RUNBOOK.md`**
   (workstation lost: install, key variables, restore, verify, start; no real paths). Tested end to end on the
   workstation into a scratch root (the share read only, Kalshi GET only, the live data untouched): the ledger
   verifies and reconciles exactly, the journal's counts match, the settings load.
3. **The network wait at start:** the engine retries its first Kalshi requests with backoff instead of stopping.
4. **Docs current;** **the milestone review** (`/code-review medium` over M1's code, itemized); leftovers parked.

**Machine:** a scratch data root on the workstation (removed afterwards); a Windows Sandbox run. **No new packages.**

**Added during the slice (owner, 2026-10-28):** **Back up now**. The owner asked for a way to start a backup
by hand. The Health screen's Backups card gets a button, from the mock-up the owner approved (the canvas's
Health board, version 11). It is built in this slice, as recommended.

#### 1.11 built (2026-10-28)

**Built** on `m1/11`:

| Commit | What |
|---|---|
| `5032883` | docs |
| `85f2ec7` | restore, runbook, network wait |
| `311b91f` | milestone-review fixes |
| `c5a60db` | the test Sandbox |
| `05b0842` | Back up now |
| `6811c47` | merge-review fixes |

After M1.12d (PR #30, `40283b4`). **No new packages.** Contracts 2.2.

- **`ikars backup restore --share <share>`** puts the newest share backup into an **empty** data root:
  - **the ledger:** SHA256SUMS, hash chain, and a head equal to the day's checkpoint, using one proof shared with
    the drill (`proved_newest`);
  - **its checkpoints, the state, the journal and the settings**, all of the ledger copy's day or before;
  - **staging:** everything is checked in a staging folder, then moved in all or nothing.

  It refuses a root that holds data, a running engine, and a damaged backup; a refusal says why, never a
  traceback. The share is only read.

  **`docs/RUNBOOK.md`**, for a lost workstation: install, stop the setup-mode engine and set its folder aside,
  a new read-only key, restore, verify and reconcile, start.
- **The network wait at start:**
  - **What waits:** the first Kalshi request retries with backoff (5 s up to a minute each time) instead of
    stopping, through a dropped network, a 5xx outage, a lingering 429 or a captive portal. A key or API refusal
    still stops the engine.
  - **Stopping:** a stop, `run --minutes`, or the end of a try in flight ends the wait cleanly.
  - **What you see:** the starting snapshot says how long it has waited, and after 5 minutes the watchdog
    reports "Kalshi unreachable" (not urgent), with its own all-clear text.
- **Back up now:**
  - **On the Health screen:** the button runs the engine's own backup at once (`POST /v1/backup`), one at a
    time, and the 24-hour clock restarts from it. The card says BACKING UP while one runs, and when the next
    automatic copy is due (`BackupHealth.running`, `next_in_h`).
  - **From the command line:** `ikars backup run` goes through the running engine. When the engine cannot take
    it (setup mode, or waiting for the network) the command makes the backup itself.
  - **Shared helper:** the command line's calls to the engine are one helper (`api.engine_post`).
- **The test Sandbox:** 8 GB, and its virtual GPU off (below).
- **`open_store`** closes its connection when a file is not a database (it stayed held open on Windows).

**M1 gate evidence (PLAN §4.2), 2026-10-28:**

| Gate | Evidence |
|---|---|
| Installed with Velopack on a clean account | Windows Sandbox, two runs (below). **First run:** M1.9a's local build `0.1.0-dev.3`, upgraded in place by v0.1.9 (the owner). **Second run:** v0.1.9 alone, 7 of 7 checks yes (no Python on the machine, per-user install, the Start-menu entry, no desktop shortcut, the engine in setup mode, Edge open on the wizard, data apart from the program), stable for 7½ min. |
| Live markets in the terminal through the engine | Live on v0.1.7 to v0.1.9; the feed live in health |
| The ledger reconciles exactly with Kalshi | Live: chain verified, reconciled exactly. **Restored:** see the runbook row. |
| The restore drill and the workstation-lost runbook pass | **Drill:** passes on the live ledger, 299 records. **Runbook, end to end on the workstation** (a scratch root; share read only; GET only; the live data untouched): **ledger:** 315 records, 3 checkpoints, verify ok, **an exact reconcile with Kalshi** (cash matches, no position differences); **journal:** 16,692 recommendations, all in the live journal (7 newer there); **settings:** equal to the live ones; **staging:** nothing left. The scratch roots were deleted. |
| Latency and frame-time targets measured | **Feed to alert:** measured live in M1.4b. **Update to screen:** p99 19 ms against the 33 ms target (the owner's console run, M1.8a-web-2b). |
| M1.12's recommendations end to end, journalled | Live on v0.1.7, v0.1.8 and v0.1.9; the scorecard equals the journal read directly (BUILD-LOG 1.12c) |

**The Sandbox crash, found and solved:**
- **What happened:** with v0.1.9, the Sandbox died about 3 minutes after IKARS opened ("the virtual machine
  exited unexpectedly", `0x80370106`).
- **What the evidence showed:** a recorder inside the Sandbox, copying logs and memory figures to the host every
  10 s, showed about 5.8 GB free and no IKARS error. The host's Windows Error Reporting held a
  `ContainerCrashDump`: the Sandbox's own kernel bugchecked, `0x3B` with an access violation. That points to the
  Sandbox's virtual GPU path (Edge draws through it to the host's NVIDIA driver).
- **The fix:** with `<vGPU>Disable</vGPU>`, the next run was clean.

**Data restored by the restore test** stayed on the workstation in a scratch folder, now deleted. Nothing was
written to the share.

**Verified.**
- **Python:** 1,554 tests, coverage 93.3%.
- **Vitest:** 232.
- **Playwright:** 19 in Edge against demo-api (Back up now clicked, the note, the card's new time).
- **Checks:** lint, types, the generated contracts and the data guard are clean.

**Live check of Back up now: pending** (a test pre-release on the owner's word).

**Review.**
- **Per commit:**
  - `85f2ec7`: 2 found and fixed before committing (an empty checkpoints folder made the move fail on Windows; a
    part-way move left files behind).
  - `05b0842`: none.
- **Milestone review (medium, over M1's code), 8 findings, all fixed in `311b91f`**, plus one found on the way:

  | # | Finding | Done |
  |---|---|---|
  | 1 | `restore` let a damaged copy or a dropped share escape as a traceback | One shared proof with the drill; every failure is a refusal; tested |
  | 2 | A try against a network that drops packets outlasted `ikars stop` | A stop cancels the try in flight; tested |
  | 3 | A 5xx outage at start still stopped the engine | Waited out; a 4xx still stops; tested |
  | 4 | The "waiting for the network" mark stayed after Kalshi answered | Cleared once it answers; tested |
  | 5 | `restore` could run under a live engine | Refused while the engine runs; tested |
  | 6 | `run --minutes` waited for ever without a network | The limit ends the wait |
  | 7 | A failed move did not put back the empty checkpoints folder | Put back; any failure rolls back; tested |
  | 8 | The proof steps were copied between the drill and the restore | `proved_newest`, shared |
  | (found) | `open_store` held a file that was not a database open | Closed on failure; tested |

- **Merge review (medium, `main...m1/11`), 10 findings, all fixed in `6811c47`:**

  | # | Finding | Done |
  |---|---|---|
  | 1 | `engine_backup` missed a half-written `api.json` and a bad reply | One helper, `api.engine_post`, handles both; tested |
  | 2 | `backup run` could not back up while the engine was in setup mode or waiting | It falls back to making the backup itself (the route answers 503 without a service); tested |
  | 3 | A lingering 429 or a captive portal stopped the engine at start | Waited out; tested |
  | 4 | A failed move during a restore escaped as a raw error | A refusal; tested |
  | 5 | The daily check could back up again straight after the owner's | Asked again inside the lock; tested |
  | 6 | A restore could mix pieces from different backup runs | Checkpoints and state of the ledger copy's day or before; tested |
  | 7 | The restore's settings flag looked at the share again | Taken from what was moved |
  | 8 | Three copies of the "call the running engine" code | `api.engine_post`, used by the backup and the support bundle |
  | 9 | "Backed up at" used the browser's time format | The terminal's one format, `local()` |
  | 10 | The web copied the engine's 24 hours | The engine reports `next_in_h`; tested |

**Leftovers:**
- **Moved:** M1.10 perp research moves to M2; M1.13 comes before M3.
- **With the owner:** the two-monitor layout check; the SignPath application.
- **Parked:** nothing new.

**M1.11 built.** It waits for the live check of Back up now and the owner's merge. **The merge closes M1.**
