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

M0 (D1 to D21) and M1 are done; **M2 (Sources) is in progress**; the scope is frozen (new ideas go to PLAN §9).
Each slice needs its own approved plan before code. Where every slice stands: [PLAN §5](docs/PLAN.md); how each
was built and verified: [BUILD-LOG](docs/BUILD-LOG.md) (the current milestone; M0 and M1 in
[docs/build-log/M1.md](docs/build-log/M1.md)).

### Resume point (keep this current at every step that changes it)

*Last updated 2026-11-02.*

- **The open stack** (each PR's base is the one before; merge in this order, only when the owner says so):
  #35 M2.3 News (`m2/3` → main) → #36 M1.13 Replay → #37 M1.14 Settled markets → #47 M1.R3a → #48 M1.R3b →
  #49 the docs streamlining (`docs/streamline`: PLAN, BUILD-LOG and this file condensed; the log split by
  milestone; owner-approved 2026-11-02). When rebasing onto main, drop `6f38e5f` from `m1/14` and update the
  hashes the docs cite. The owner allows `gh pr merge` by a local permission rule; merge only when asked.
- **Installed:** v0.1.19 (published 2026-11-02 through the four-job release workflow). After the upgrade one
  `ikars open` signs in again (cookies now name their port). **Next:** the M1.R3b live checks on it (BUILD-LOG
  1.R3b, "Live checks").
- **Due:** the M2.3 news gap check (2026-11-02 evening), then the merges above; momentum v1's week ends about
  2026-11-04, then the Strategy review decides v2 (with M1.R3 C1 and C5).
- **Then:** M2.4 (settlement rules and the event calendar), plan first.
- **Still with the owner:** the SignPath application (docs/CODE-SIGNING.md); the two-monitor layout check.
- **Test releases** are published only on the owner's word ("publish 0.1.x"), from a commit whose CI passed.
- **IKARS's own read-only Kalshi key** is in the User environment variables `IKARS_KALSHI_KEY_ID` and
  `IKARS_KALSHI_PRIVATE_KEY_PATH` (separate from KAPS and SPARQ's `KALSHI_*`). Never print or read the id, the path
  or the file; check with `Test-Path` only; load them into a single command's process.
- **Backups** go to the KAPS backup share, only inside its own `ikars\` subtree with its own marker; the real path
  is only in local settings and memory, never in the repository.

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
- **Use sub-agents whenever they help (owner, 2026-11-01; PLAN R16, D11):** parallel read-only research,
  a fresh-eyes reviewer at each merge, worktree spikes, separable background work. Not for a slice's sequential
  build, live workstation checks or step-by-step approved work. Briefs carry the non-negotiables; check results.
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
