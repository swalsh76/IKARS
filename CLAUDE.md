# CLAUDE.md

## Start here

**Read [docs/PLAN.md](docs/PLAN.md) before doing anything.** It is the source of truth for decisions, architecture
and build order, and its §5 tracks progress. Record how each slice was built and verified in
[docs/BUILD-LOG.md](docs/BUILD-LOG.md). Update both when decisions change.

IKARS uses **external news and data sources** to recommend **Kalshi** trades in a real-time Windows 11 GUI, with
P&L net of fees on entry and exit.

- **KAPS** (`%USERPROFILE%\dev\KAPS`, Python) is the infrastructure basis; **KalTerm3** (`%USERPROFILE%\dev\KalTerm3`,
  .NET WPF) was the UI basis and is now a behaviour reference only (the terminal is a TypeScript web app since
  2026-10-27); **SPARQ** (`%USERPROFILE%\dev\SPARQ`, KAPS's successor) may be used freely for code,
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
  - D1: Python engine + terminal (**amended 2026-10-27: a TypeScript web app** — React, Vite, pnpm — served by the
    engine and opened as an Edge app window with a one-time-code cookie sign-in; WPF retired, tag
    `wpf-terminal-final`); navigation **Overview → drill-down → cross-reference** (R9).
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
- **Guard lapsed (owner, 2026-10-27):** SPARQ's S1.1b forward test was dropped unopened and SPARQ's guard lapsed
  with it, so IKARS's matching guard (no edge to settlement on its events before 2026-11-23) lapsed too.
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
  - workstation alerts (amended 2026-10-27: no phone pushes; Windows notifications)
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
  **M1.4a done** (PR #4 merged as `b29a86e`). **M1.4b done** (PR #5 merged as `58abbe5`; BUILD-LOG 1.4b).
  Owner 2026-10-27: GPU detection moved to M1.6; recording volume (about 2.1 GB a day) kept. **Keep
  [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) current** in every slice that adds or drops a dependency (for the
  install instructions). Those docs merged as PR #6 (`a8ff41a`).
  **M1.5 plan APPROVED** (owner 2026-10-27: all 6 as recommended: split a/b, NAS go-ahead for M1.5b, tax zone =
  workstation's (America/Denver), REST import each minute, owner labels unexplained money, FIFO).
  **M1.5a done** (PR #7 merged as `087b819`): reconciles **exactly** with Kalshi.
  **M1.5 done** (PR #8 merged as `5b6f702`): the real ledger lives in `%LOCALAPPDATA%\IKARS\ledger`; share backups
  and the restore drill pass. **M1.6 done** (PR #9 merged as `2efc95f`): the engine runs in the background with the watchdog task and
  auto-start (the owner's sign-out check pending). **M1.7 done** (PR #10 merged as `0ba5d83`): token-guarded loopback API with an SSE stream. **M1.8a (WPF) done** (PR #11 merged as `77849e3`; BUILD-LOG 1.8a); D20 J amended by the owner: **update to screen
  p99 <= 33 ms**. **Direction change (owner, 2026-10-27):** the terminal becomes a **TypeScript web app** (D1, D3,
  D11, D12 amended; BUILD-LOG 1.8w; all recommended answers approved: engine-served Edge app window, one-time code to
  an HttpOnly cookie, React with Vite and pnpm). PR #12 merged (`d73921b`); tag `wpf-terminal-final` pushed. **WPF
  pruned:** PR #13 merged as `1b92d49`. The `main protection` ruleset requires `python`, `hygiene` and
  `CodeQL` (strict; CodeQL added at the owner's request 2026-10-27); `web` joins once that job exists.
  **M1.R Base hardening** (owner 2026-10-27: a whole-code-base review before the web terminal; items 1 to 26
  approved): **done** (PR #14 merged as `fca7941`, CI green on `main`; BUILD-LOG 1.R). Resting orders checked live 2026-10-27 (Kalshi's
  balance does not hold back a resting buy; reconciliation exact). The M1.6 auto-start check passed 2026-10-27.
  **Web review and security posture decided** (owner 2026-10-27; BUILD-LOG 1.8w-sec): browser hardening and npm
  rules fold into M1.8a-web; **CodeQL** added (PR #15 merged as `179dd8c`; green on `main`; required since 2026-10-27).
  **Platform review 1.R2 done** (owner approved placement 2026-10-27): items 1 to 5 go into M1.8a-web (stable
  port and session across restarts, typed health, 2^53 test, one time format, search key), 8 and 9 into M1.12,
  6 and 7 into the new **M1.13** replay slice, 10 parked.
  **M1.8a-web plan APPROVED** (owner 2026-10-27; BUILD-LOG 1.8a-web): four slices, **1a** engine side (serving,
  sign-in with a fixed port and a 30-day sliding DPAPI-signed cookie, `ikars open`, contracts 2.0, made-up-data
  mode), **1b** web foundation (Node 24, `web/`, types, client, hub, money, navigator, CI `web` job, Overview and
  Health), **2a** Markets and Market, **2b** Ledger, Alerts, meter and load test. Answers: fixed port in settings,
  30-day session, `vite build --watch`, validate every reply. **M1.8a-web-1a done** (PR #16 merged as
  `d70cb83`). **M1.8a-web-1b done** (PR #17 merged as `0fe5415`, CI green on `main`;
  BUILD-LOG 1.8a-web-1b; Node 24.22.1 and pnpm 12.14.0 per user; `web/` with Overview and Health). The ruleset now
  requires `python`, `hygiene`, `CodeQL` and **`web`** (owner, 2026-10-27). **M1.8a-web-2a done** (PR #19 merged as `94dbcaa`). **M1.8a-web-2b built** on `m1/8a-web-2b`
  (BUILD-LOG 1.8a-web-2b; Ledger, Alerts, the update-to-screen meter, `pnpm load`; merge review 10 itemized): PR
  **done** (PR #20 merged as `1aeb2bf`; the owner's console run passed: p99 19 ms). **M1.8a-web is complete.**
  **M1.8b plan APPROVED** (owner 2026-10-27, all as recommended; BUILD-LOG 1.8b): **1b-1** mock-ups of
  Settings and the wizard (approved before building), hotkeys, themes, Settings and the wizard; **1b-2** layouts
  (sets of windows) and the support bundle. Mock-ups approved; skins added by the owner (built-in palettes
  only). **M1.8b-1 done** (PR #21 merged as `db0bf9a`; the settings race fix PR #22 as `e6a29f3`).
  **M1.8b-2 done** (PR #23 merged as `47984db`, CI green on `main`; BUILD-LOG 1.8b-2; layouts and the support
  bundle). **M1.8 is complete.** Pending from the owner: the two-monitor layout check.
  **M1.8c New window** (approved addition, owner 2026-10-27; BUILD-LOG 1.8c): **done** (PR #24 merged as `b336930`,
  CI green on `main`; merge review 10 itemized). **M1.9 plan APPROVED** (owner 2026-10-27, all as
  recommended; BUILD-LOG 1.9): **M1.9a** build, install, uninstall (program in `%LOCALAPPDATA%\IKARS-App`, data
  untouched; Windows Sandbox for the clean test, the owner enables it), **M1.9b** CI releases, updates on the
  owner's click, the rollback drill, signing (unsigned now, apply to SignPath). **M1.9a done** (PR #25
  merged as `31a992a`, CI green on `main`; BUILD-LOG 1.9a; CodeQL now scans C# too). **M1.9b done** (PR #26 merged as `d11165d`, CI green on `main`; BUILD-LOG
  1.9b; test pre-releases v0.1.0 to v0.1.6; the rollback drill passed; IKARS runs installed on the workstation).
  **M1.9 is complete.** Start at sign-in points at the installed IKARS (the owner ran `install-autostart`).
  Pending from the owner: the SignPath application (docs/CODE-SIGNING.md). **M1.12 plan APPROVED** (owner 2026-10-27, all as recommended; BUILD-LOG
  1.12): a engine (plugins in the live runner, recording, received time, the journal), b momentum (continuation,
  4c in 5 min on 50 contracts, spread <= 3c, 10c..90c, valid 30 min, scored at 30 and 60 min; 10 contracts;
  registered by hash), c terminal (Overview list, Recommendation drill-down, normal throttled toast). **M1.12a
  done** (PR #27 merged as `099b94b`). **M1.12b done** (PR #28 merged as `3f0f8bd`, CI green on `main`; momentum plugin
  version `0d648804a13a`, pnl.py, merge review 9 fixed; owner: SPARQ guard lapsed; any market with a depth check).
  **Live check passed** on v0.1.7 (2026-10-28; BUILD-LOG 1.12b): about 20 recommendations a minute at first on
  in-play sports, half the 30-minute checks found the market closed. **Owner:** v1 keeps running a week
  unchanged (until about 2026-11-04), then its record decides; a closed market's check is scored at settlement
  (built, `f810687`). The M1.6 auto-start check passed (the installed IKARS started at sign-in after a restart).
  **M1.12c** (owner 2026-10-28: proceed as recommended: a ranked list, toasts for the strongest few) on
  `m1/12c`: **plan APPROVED** (BUILD-LOG 1.12c; all as recommended: top-ranked toasts, 3 an hour; T/I/D
  and C now; the scorecard now; no redraw). **Built** (`cd72d8f` engine, `c1fb678` terminal, `7a4592a` merge
  review 9 fixed, 1 no change); PR #29 open, CI green. **v0.1.8 published** (test pre-release from
  `bac418d`, 2026-10-28); installed; live checks 2026-10-28 19:00 MDT passed: ranked list, drill-down, T/I/D across a restart,
  D off the list, scorecard exact vs the journal, GET only. Notifications: 2 shown in the first hour (cap
  3), none in quiet hours (overnight not yet seen). **M1.12c done** (PR #29 merged at the owner's word).
  **M1.12d approved** (owner 2026-10-28; BUILD-LOG 1.12d): a row opens its recommendation, highlighted row with
  arrow keys, Enter and C/T/I/D on the Overview, a Back button, the last crumb named; branch `m1/12d`. Then M1.11 (M1.13 any time
  before M3). The owner allows `gh pr merge` by a local permission rule; merge only when asked.

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
- **Show the merge review as an itemized list (owner, 2026-10-27).** When reporting a merge review to the owner,
  list every finding, one per line: what was wrong, and what was done (fixed, with the commit; parked; or no change,
  with why). Never only a count.
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

# the web terminal (in web/; Node 24 and pnpm per user, see docs/DEPENDENCIES.md)
pnpm install --frozen-lockfile
pnpm gen            # types and validators from contracts/ (gen:check in CI)
pnpm lint; pnpm typecheck
pnpm test           # Vitest, 90% coverage
pnpm dev            # vite build --watch: the engine serves web/dist (restart it once after the first build)
pnpm build; pnpm e2e   # Playwright in Edge against demo-api (python -m ikars_engine demo-api)
pnpm load          # at the console: 100 markets at 10 Hz in a visible Edge window, update to screen p99 <= 33 ms
pnpm audit

python tools/ci/data_guard.py          # no data, databases or keys tracked
gitleaks git --redact --no-banner .    # secrets in the whole history
```

**Pre-commit hook.** Enable it once per clone with `git config core.hooksPath tools/hooks`. It runs gitleaks on the
staged changes and the data guard. Test-data rules are in [docs/TESTING.md](docs/TESTING.md).
