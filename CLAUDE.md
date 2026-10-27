# CLAUDE.md

## Start here

**Read [docs/PLAN.md](docs/PLAN.md) before doing anything.** It is the source of truth for decisions, architecture
and build order, and its §5 tracks progress. Record how each slice was built and verified in
[docs/BUILD-LOG.md](docs/BUILD-LOG.md). Update both when decisions change.

IKARS uses **external news and data sources** to recommend **Kalshi** trades in a real-time Windows 11 GUI, with
P&L net of fees on entry and exit.

- **KAPS** (`%USERPROFILE%\dev\KAPS`, Python) is the infrastructure basis; **KalTerm3** (`%USERPROFILE%\dev\KalTerm3`,
  .NET WPF) is the UI basis; **SPARQ** (`%USERPROFILE%\dev\SPARQ`, KAPS's successor) may be used freely for code,
  findings and data (R13). Read them; never edit them
  from here. Treat their code as a guide to question and improve, not to copy blindly; record what was kept or
  changed in the BUILD-LOG.

## Status

**M0 is approved (D1 to D21, 2026-10-26); the scope is frozen. M1 is approved and in progress (slice M1.1).** Each
slice still needs its own approved plan before code.

### Resume point (keep this current at every step that changes it)

*Last updated 2026-10-27.*

- Survey of KAPS, KalTerm3 and SPARQ done (BUILD-LOG 0.1).
- **Direction:** beat the humans, not the big bots. Strategies, in order: news/data estimates and momentum, then
  sniping (as fast alerts), then perpetuals (D13).
- **Approved 2026-10-26:**
  - D1: Python engine + .NET WPF terminal; navigation **Overview → drill-down → cross-reference** (R9).
  - D2: single box on the workstation.
  - D3: loopback WebSocket/HTTP with `msgspec`.
  - D5: weather (NWS/NOAA) + general news (RSS/GDELT + LLM).
  - D6: **recommend-only through M6, live trading in M7** behind one executor interface (paper now, real later).
  - D7: P&L with the listed cost-basis factors.
  - D8: keys via environment variables and DPAPI.
  - D9: Claude API **plus a local-model option** on the GPU.
  - D10: **public** `swalsh76/IKARS`, MIT.
  - D11: quality gates, plus a mini review per commit and merge (R14).
  - D12: a **portable zip and a user-space installer**.
  - D13 to D17: strategies, custom order types (watch/paper now, live in M7), GPU, order-book analysis, SPARQ reuse.
- **Guard:** no edge to settlement on SPARQ's forward-test events (closing before 2026-11-23) until SPARQ's S1.1b
  verdict.
- **Also approved:** D4 option C (SQLite state, Parquet raw feeds, DuckDB queries); D12 Velopack.
- **Owner requirement R15 (2026-10-26):** an audit log fit to keep with a Form 1040. Designed as **D18**:
  - an append-only, hash-chained ledger built from Kalshi's own account records, manual trades included,
    reconciled against balance and positions;
  - a per-year package: PDF/A summary, Form 8949 CSV, Form 6781 CSV, the raw JSONL and a SHA-256 manifest;
  - the store in M1.5, the export in M5.
  - backups go to the KAPS backup share (the same one as the KAPS database), only inside its own `ikars\` subtree
    with its own marker; the real path is only in local settings and memory, never in the repository.
- **Gap review approved as D19 (2026-10-26):** G1 to G17 and G19 approved:
  - portfolio and exposure
  - sizing and the risk layer
  - the replay harness
  - pre-registration
  - background run mode
  - phone alerts
  - other-venue prices
  - settlement rules and the calendar
  - the recommendation lifecycle
  - clock and latency
  - the storage governor
  - repository security
  - interest in the ledger
  - the restore drill
  - the first-run wizard
  - hotkeys, layouts and themes
  - compliance review before M7 and per-source terms
  - automatic demotion with a **Strategy review** page

  G18 (fault and soak tests) was dropped; G20 (remote view) and G21 (social sentiment) are parked. Sources,
  signals, custom order types and venues are **plugins** (entry points), so parked items need no restructuring.
- **Second gap review approved as D20 (2026-10-26):**
  - A: point-in-time source times; the replay obeys received time; record-only from M2's first slice
  - B: LLM injection hardening, a response cache, a cost meter
  - C: fill probability and adverse selection
  - D: return per day of capital tied up
  - E: tradability filters
  - F: provenance on every recommendation
  - G: safe upgrades (migrations, backup first, version handshake, rollback)
  - H: alert hygiene
  - I: redacted logs, a support bundle, the workstation-lost runbook
  - J: latency and frame-time targets
- **D21 approved (2026-10-26):** milestone gates with pre-registered stop rules (PLAN §4.2); the end-to-end
  momentum slice M1.12; IKARS's own read-only key and a request budget that protects KAPS's ingest; the test-data
  policy; a monthly cost ceiling. **Scope frozen** at D1 to D21.
- **IKARS's own read-only Kalshi key is set up (2026-10-26):** User environment variables `IKARS_KALSHI_KEY_ID` and
  `IKARS_KALSHI_PRIVATE_KEY_PATH` (separate from KAPS/SPARQ's `KALSHI_*`). Never print or read the id, the path or the
  file; check with `Test-Path` only; load into a single command's process. First use: M1.2's live tests.
- **M1 slice list and gates approved (2026-10-26).** **M1.1 done:** PR #1 merged as `2f19222`, CI green on `main`
  (BUILD-LOG 1.1). **M1.2 done** (PR #3 merged as `3485b16`; BUILD-LOG 1.2). **M1.3 approved for now** (source in `docs/design/mockups/`).
  **M1.4a done** (PR #4 merged as `b29a86e`). **M1.4b built and verified live** on `m1/4b-feeds` (BUILD-LOG 1.4b;
  commits `b2cfe8c`, `71489fc`, `f2d57fb`): PR open, waiting for the owner's merge; open question: where GPU
  detection goes (recommended M1.6 or M2). Next after the merge: M1.5 (ledger) plan. The owner allows
  `gh pr merge` by a local permission rule; merge only when asked.

## How the owner wants the work run

- **Plan before code.** Each milestone starts with a proposal of decisions (D1, D2, ...) and slices; each slice gets
  its own plan (what it builds, what it changes on any machine, new packages with licences, live checks, open
  questions). **Anything beyond a simple edit is presented to the owner for decision and approval first.**
- Announce each new slice and its branch before starting; narrate each step in one plain line.
- **Mini code review on every commit and merge (R14, PLAN D11).** Before each commit, review its diff for:
  - correctness
  - fixed-point money
  - reuse and duplication
  - hot-path efficiency
  - simplification
  - tests
  - secrets

  Before each merge, review the branch diff against `main` plus the touched modules and their callers. Use
  `/code-review` (a low effort level per commit, medium per merge) and a reuse pass. Fix each finding or park it in
  PLAN §9, and list it under **Review** in the slice's BUILD-LOG entry.
- **Scope is frozen at D1 to D21 (owner, 2026-10-26).** New ideas go to PLAN §9 by default and come back only
  through a milestone proposal the owner approves. Name scope creep explicitly when it appears.
- Machines by role (workstation, server host, analytics node); real host names and paths stay out of the repository.

## Non-negotiables (decided)

- Target **Windows 11** (the workstation). Recommendations shown in the GUI; data and GUI update in real time.
- P&L includes fees and cost-basis adjustments on **entry and exit**; money is fixed-point integers (dollars at
  10^-6, counts at 10^-2), never floats.
- Efficiency, reuse and ease of deployment are design criteria in every slice.
- **Recommend-only until M7:** IKARS holds no trading key and sends no orders before the live-trading milestone is
  separately approved (D6).
- **Public repository:** never commit secrets, keys, real host names or paths, Kalshi data extracts or fetched news
  content; commit with the GitHub no-reply address (D10).
- **Terminal navigation:** an Overview main screen, then drill-down and cross-reference views (R9). A new screen is
  drawn as a mock-up and approved before it is built.
- **No real order is sent without the owner's go-ahead.** Never print, log or commit keys, key paths or secrets.
- The owner's name is Steven Walsh.

## Dev commands

`uv` runs as `python -m uv` on this workstation. Python is 3.13, from `.python-version`, managed by `uv`.

```powershell
python -m uv sync
python -m uv run ruff format --check .
python -m uv run ruff check .
python -m uv run mypy packages tools; python -m uv run mypy conftest.py   # two runs: both define a module named conftest
python -m uv run pytest --cov
python -m uv run pip-audit --skip-editable

dotnet restore Ikars.slnx
dotnet format Ikars.slnx --verify-no-changes --no-restore
dotnet build Ikars.slnx -c Release --no-restore
dotnet test --solution Ikars.slnx -c Release --no-build --coverlet --coverlet-output-format cobertura --report-trx --results-directory TestResults
./tools/ci/check-coverage.ps1 -Path TestResults -MinLinePercent 90

python tools/ci/data_guard.py          # no data, databases or keys tracked
gitleaks git --redact --no-banner .    # secrets in the whole history
```

**Pre-commit hook.** Enable it once per clone with `git config core.hooksPath tools/hooks`. It runs gitleaks on the
staged changes and the data guard. Test-data rules are in [docs/TESTING.md](docs/TESTING.md).
