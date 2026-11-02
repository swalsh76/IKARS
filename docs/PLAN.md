# IKARS plan

This file is the source of truth for IKARS's **decisions** (in their current form), the **build order** (milestones,
slices and gates), **progress** (§5) and the **parking lot** (§9). How each slice was built and verified, with its
numbers, findings and reviews, is in [BUILD-LOG.md](BUILD-LOG.md); "BUILD-LOG <id>" names a heading there. The
current state and the next step are in `CLAUDE.md`.

**State:** M0 approved (D1 to D21, 2026-10-26), scope frozen; M1 complete; M2 in progress (§5).

## 1. Goal

A Windows 11 desktop system that watches **external news and data sources** in real time, relates them to open
**Kalshi** markets, and shows **trade recommendations in a GUI**. Each recommendation carries a potential
profit/loss that includes **fees and other cost-basis adjustments at both entry and exit**. Market data, source data
and recommendations update live.

Requirements from the owner (2026-10-26; R16 2026-11-01):

| # | Requirement |
|---|---|
| R1 | Infrastructure based on **KAPS** (`github.com/swalsh76/KAPS`). The terminal is a TypeScript web app (D1); **KalTerm3** (`github.com/swalsh76/KalTerm3`) is a reference for behaviour (layouts, risk layer, order routes), not for code. |
| R2 | Every step is planned ahead; anything beyond a simple edit is presented to the owner for decision and approval. |
| R3 | Progress tracked in `CLAUDE.md` (session resume) and in this plan plus `BUILD-LOG.md`. |
| R4 | Target platform **Windows 11**; hardware as in KAPS (workstation, server host, analytics node). |
| R5 | Recommendations are made **in the GUI**; data and GUI update in **real time**. |
| R6 | P&L includes fees and similar cost-basis adjustments on **entry and exit**. |
| R7 | Historical data as gathered for **SPARQ** may be used, but the system does not depend on it. |
| R8 | Code efficiency, reuse and ease of deployment; use the most capable and efficient libraries for each job. |
| R9 | The interface has an **Overview** main screen, then follows a **drill-down** and **cross-reference** view model (§3 D1, "Terminal navigation model"). |
| R10 | Trade/order types that Kalshi does not offer natively are considered for **in-app ("custom") equivalents** (D14). |
| R11 | Use the workstation's **GPU** where it actually helps (D15). |
| R12 | **Live order-book analysis** to discern implied patterns (D16). |
| R13 | Anything SPARQ has produced (code, findings, data) may be used to augment IKARS (D17). |
| R14 | Besides unit tests and CI, a **mini code review on every commit and merge**, so fewer large periodic reviews are needed (D11). |
| R15 | An **audit log** kept so that, if needed, it can be stored alongside a **Form 1040** tax return (D18). |
| R16 | **Sub-agents are used whenever they help the plan's development**: parallel research, fresh-eyes reviews and isolated spikes (D11). |

### 1.1 Direction: beat the humans, not the bots (owner, 2026-10-26)

- **The target counterparty is the human trader, not the professional bot.** IKARS does not need to win millisecond
  races in headline markets; it needs to be faster, better-informed or more disciplined than people trading at a
  human pace (seconds to hours).
- Evidence that supports this (exploratory, from KAPS/SPARQ, **leads not results**): takers trading 1 s to 1 h after a
  market's previous trade lost 4 to 7 cents a contract; the **first trade into a quiet market** (picking off a stale
  quote) made **+4.66 cents** as taker (95% +0.03 to +14.37), while the stale maker lost 11.48. KAPS K0.5 found no
  edge in price alone, so the edge has to come from **information or speed relative to humans**.
- Strategy families in scope (D13): **news/data-informed estimates**, **sniping** stale or mispriced human quotes,
  **momentum** (humans under-react, then chase), and **perpetuals** (Kalshi perps: funding carry, momentum, and
  hedging binary positions).
- Every recommendation is **journalled and scored against outcomes** before anyone relies on it, and the GUI shows
  expected value **net of fees, spread and funding**, never a gross number.

## 2. Hardware and roles (inherited from KAPS; machine names by role)

| Role | Hardware (summary) | Role in IKARS (D2) |
|---|---|---|
| **Workstation** | Ryzen 7 9800X3D, 64 GB, RTX 5070 Ti 16 GB, Windows 11 | Runs the IKARS **engine** (in the background) and the **terminal** (an Edge app window). |
| **Server host** | i7-8700, 32 GB, Ubuntu 24.04, PostgreSQL 18 + TimescaleDB | KAPS ingest keeps recording; IKARS may **read** it through a read-only role (optional, not created). |
| **Analytics node** | Mac Studio M4 Max, 36 GB, external 2 TB SSD | Holds the KAPS/SPARQ history (Parquet); source for back-tests (optional, R7). |
| **Home network** | the NAS share; the Strata LLM server | Ledger and state backups (D18); the local LLM backend for M2 (D24). |

## 3. Decisions (M0: D1 to D21)

The owner approved D1 to D21 on 2026-10-26 (BUILD-LOG 0.1) and froze the scope there (D21). Each decision is in its
current form; a trailer names any later amendment and where it is recorded. M2's D22 to D30 are in §4.3.

### D1. Language and stack: Python engine + TypeScript web terminal

- **Engine (Python 3.13, `uv` workspace):** `ikars-core` (signing, REST, WebSocket, fixed-point money, fees, clock),
  seeded from SPARQ's `sparq_core` (D17), and `ikars-engine`. Python has the best libraries for the work: feed and
  web ingestion, text processing, LLM clients, Polars/DuckDB analytics.
- **Terminal: a TypeScript web app** (React, Vite, strict TypeScript, pnpm), served by the engine on its loopback
  address (D3) and opened as a **Microsoft Edge app window** (`msedge --app`) with its own IKARS profile.
- **One source of truth for money:** fees, P&L and recommendations are computed **only in the engine**. The web
  terminal does no money arithmetic; it formats the engine's integers with integer operations only (D11).

*Amended 2026-10-27 (terminal: .NET WPF → TypeScript web app; WPF kept at the tag `wpf-terminal-final`): BUILD-LOG
1.8w.*

#### Terminal navigation model (owner, 2026-10-26: R9)

The terminal is organised as **Overview → drill-down → cross-reference**. Every new screen is drawn as a mock-up and
approved before it is built (the first set: BUILD-LOG 1.3, `docs/design/mockups/`).

- **Overview (main screen):** one glanceable dashboard: the top recommendations ranked by expected value net of
  costs, live movers (momentum), the latest source items with the markets they touch, perp funding, the journal's
  scorecard, and feed/engine health. Every element on it links into a drill-down.
- **Drill-down:** one level deeper at a time, with a breadcrumb trail and back/forward history.
  - Markets: category → series → event → market → recommendation (entry/exit P&L scenarios, order book, chart, the
    evidence behind the signal).
  - Sources: source → item → the markets it is linked to.
  - Strategies: family → signal → the recommendations it produced → their outcomes.
- **Cross-reference:** any selected item (market, source item, recommendation, perp) opens a linked-items panel. For
  a market: the source items that moved it, related markets (same event, correlated series), a perp hedge, and past
  recommendations with their outcomes. News items are drawn on the price chart's timeline; selection stays in sync
  across open panels.
- **Implementation (for reuse):** every item has one typed reference (kind plus id). The engine keeps one generic
  **link graph** served through one cross-reference endpoint, so a new source or strategy adds links rather than
  screens. The terminal has one navigator with real URLs, and every screen resolves itself from a reference
  (BUILD-LOG 1.8w, behaviour 1).

### D2. Topology: single box on the workstation

- Engine and terminal both run on the Windows 11 workstation.
- **The engine runs in the background**, independent of the terminal (G5): started at sign-in by a per-user `HKCU`
  Run entry, restarted by a per-user scheduled **watchdog** task (every 5 minutes), one instance only, keeping the
  machine awake while it runs. `ikars open` (or IKARS from the Start menu) starts it if needed and opens the
  terminal. No admin rights, no Windows service.
- Unlike KAPS's workstation, it holds a **read-only Kalshi key**, IKARS's own (D8, D21-3), for market data and the
  account import. **No trading key** until M7 (D6).
- The engine stays OS-neutral Python, so it can move to the server host later without a rewrite (§9).

*Amended 2026-10-27 (G5 pulled forward from M7 replaces the terminal-supervised child process): BUILD-LOG 1.6.*

### D3. Engine-to-terminal link: loopback HTTP + Server-Sent Events, JSON

- The engine serves `127.0.0.1` only (Starlette on uvicorn, in the engine's event loop): **HTTP** for snapshots and
  commands, one **Server-Sent Events** stream for pushed **deltas** (markets, books, portfolio, recommendations,
  alerts, health), subscriptions changed over HTTP; serialisation with `msgspec`. Updates are coalesced per topic in
  the engine (about 10 Hz) and handed to the screens about ten times a second, so the UI stays light under bursts.
- **Version handshake** (`GET /v1/hello`): a different major API version is refused with the reason (D20 G).
- **Contracts** are defined once in the engine and exported as JSON Schema; the TypeScript types and validators are
  **generated** from it, and a TypeScript test reads every golden example with runtime validation (unknown fields
  refused), so neither side can drift silently.
- **The web terminal:**
  - The engine serves the terminal's static files, so page and API share one loopback origin (no cross-origin
    rules). The stream is read with `fetch` (not `EventSource`), keeping KalTerm3's client rules, not its code.
  - **Sign-in:** the launch link carries a **one-time code** (single-use, expiring in seconds), exchanged for an
    `HttpOnly`, `SameSite=Strict` cookie that page scripts cannot read, signed with a DPAPI-protected key, sliding
    over 30 days and naming its port. The guard accepts that cookie or the bearer token (`api.json`, owner-only, for
    the command line), and a browser `Origin` only when it is the engine's own.
  - The **port is fixed in the settings** (47821), so a restart keeps the terminal signed in. If it is taken, the
    engine falls back to another port, rotates the session key (every window signs in again) and raises an alert.
  - Browsers scope cookies by host, not port: what confines the cookie is the terminal's own Edge profile, which
    visits nothing else.
  - **Browser hardening (owner, 2026-10-27).** Outside text (market titles, news, LLM output) is drawn in a page
    holding the API session, so script injection is the main threat:
    - a strict **Content Security Policy**: scripts and styles only from the engine's origin (no inline script, no
      `eval`), no frames or plugins, `connect-src 'self'`, `frame-ancestors 'none'`; the one exception is
      `font-src 'self' data:` for AG Grid's icon font. Also `nosniff`, `Referrer-Policy: no-referrer` and
      `Cache-Control: no-store` on API replies;
    - a changing request must carry the engine's own `Origin` and `Sec-Fetch-Site: same-origin`; bodies need a
      `Content-Length` and are capped;
    - the IKARS Edge profile runs with **extensions off**; outside links open through the engine, `https:` only.

*Amended 2026-10-27 (one WebSocket → SSE; the web terminal's serving, sign-in and hardening): BUILD-LOG 1.7, 1.8w,
1.8w-sec, 1.8a-web-1a. 2026-11-02 (port-bound cookie, fallback rotation, body caps): BUILD-LOG 1.R3 (D1, D4).*

### D4. Local storage: option C (SQLite state, Parquet raw feeds, DuckDB queries)

IKARS writes high-rate raw feeds (book deltas, ticker frames, trades: thousands of rows a second at bursts,
append-only), operational state (items, links, signals, recommendations, the journal, settings, later orders and
fills: small writes that must survive a crash) and analysis over gigabytes (columnar scans). Each goes to the tool
best at it:

- Live books and the working set stay in memory.
- **SQLite** in WAL mode, with full sync for anything the journal or, later, real trading depends on; the tax ledger
  is a separate file (D18).
- **Parquet** for everything high-rate: hourly files (Arrow IPC for the open hour, then compacted), SPARQ's layout,
  so recordings and SPARQ/KAPS history share one back-test code path. History is used when present (R7).
- **DuckDB** as a query library, not a store: it reads Parquet and attaches SQLite read-only while the engine writes.
- *Small risk:* the open hour's Arrow buffer can lose its last unflushed batch on a hard crash: seconds of raw feed,
  which Kalshi cannot resend anyway; the gap is logged as in KAPS.

*The options weighed: BUILD-LOG 0.1.*

### D5. First sources and market focus: weather (B) and general news (C)

- **B. Weather:** NWS / NOAA `api.weather.gov` forecasts and station observations (free); weather markets settle on
  station values (D22).
- **C. General news:** RSS/Atom feeds and GDELT (free), with an LLM to link items to markets and estimate impact;
  politics, mentions, culture, the long tail (D23 to D25).
- Both use one source-plugin interface, so the other options are later adapters, not rewrites: **A** economic
  releases and nowcasts (BLS, BEA, FRED, Fed calendars), **D** live scores and odds (mostly paid), **E** exchange
  spot prices (Coinbase/Kraken WebSocket). **E** moves up if perpetuals (D13 P) or crypto sniping is chosen early,
  since spot is their fair value.
- Kalshi's own live feed (ticker, trades, order book) is always a source: it drives momentum and sniping.

*The options weighed: BUILD-LOG 0.1.*

### D6. Recommend-only first, live trading later

- **M1 to M6: recommend-only.** IKARS recommends; the owner trades on Kalshi by hand. No trading key, no order
  routes, no order sending.
- Sniping (S) is delivered as **fast alerts** (a toast plus an Overview highlight, with the price, size available
  and P&L after costs). The journal records **how long each opportunity lasted**, so M5 shows whether there is time
  to act on them by hand.
- **Eventual goal: live trading in the app (owner, 2026-10-26).** Custom order types (D14) and recommendations
  become real orders in **M7**: one-click confirmed orders first, then armed automatic rules with hard caps. M7 has
  its own proposal and approval, with the trading key on the workstation (DPAPI vault), KalTerm3's risk layer and
  fixed order routes, a kill switch, the audit ledger (D18), and KAPS's order-write lessons (never retry an unclear
  write; look the order up by `client_order_id`).
- **Built for it from the start:** every action goes through one **executor interface**. M1 to M6 use a paper
  executor that fills against the live book; M7 adds the real one behind it, so watch orders, paper fills and real
  orders share one code path.
- **No real order is ever sent without the owner's go-ahead.**

*Amended 2026-10-26 (recommend-only through M6, live trading in a new M7): BUILD-LOG 0.1.*

### D7. P&L model, net of costs

Computed in the engine with fixed-point integers (dollars at 10^-6, counts at 10^-2) and `ikars-core`'s fee
arithmetic. For each recommendation:

- **Entry:** price (taker at the best ask, or maker at a limit), entry fee per the series' `fee_type` and
  multiplier (taker 0.07·C·P·(1−P) rounded up; maker share where the series charges one), spread paid against the
  mid.
- **Exit scenarios:** hold to settlement (the payout: $1 or $0, or a scalar market's; no exit fee); sell at a target
  (exit fee, taker or maker); stop/early exit at the current bid (exit fee).
- **Outputs:** cost basis, max gain, max loss, **expected value net of all fees** at the model probability,
  break-even probability, return on capital, the edge in cents over the market price; from M4, return per day of
  capital tied up (D20 D).
- Still to confirm with the owner: interest on balances or positions (Kalshi has no interest endpoint; G13), and
  rounding of the balance to $0.01 for non-direct members (`ikars-core` models it conservatively). The fee unit
  (Kalshi charges at $0.0001, IKARS rounds each leg up to a cent) is parked (§9).
- **Perpetuals** add perp trading fees, **funding payments** over the expected holding period (8-hourly for crypto,
  daily for index/commodity perps, per the KAPS review; verified in M2.9), margin and its cost, and a liquidation
  price with the worst case: new code with contract tests against live responses.
- **Sniping/momentum** add expected **slippage** (depth at the target price from the live book) and the chance the
  quote is gone before the order lands, so the P&L shown is for the size actually available.

### D8. Keys and secrets

- **Kalshi:** IKARS's **own read-only key** (D21-3), separate from KAPS's and SPARQ's, in the User environment
  variables `IKARS_KALSHI_KEY_ID` and `IKARS_KALSHI_PRIVATE_KEY_PATH`, loaded into the engine's own process. Settings
  hold only the variables' **names**; the terminal shows the key's status as yes or no only.
- **Secrets at rest** (the session-signing key; later any API key IKARS stores) use Windows **DPAPI**.
- **LLM:** the Strata server's key is in a User environment variable named in the owner's local notes, never in the
  repository (D24); a Claude key, if Claude is added, goes in an environment variable or DPAPI.
- **No trading key** before M7 (D6). Keys, key ids and key paths are never printed, logged or committed.

### D9. LLM use

- **One LLM provider interface**, the backend chosen per task in the settings, so a backend is added without rework.
- **M2 uses a local model only:** the owner's **Strata** server on the home network (OpenAI-compatible; D24). A
  quantised model on the workstation's GPU (D15) remains an option behind the same interface.
- **Whether to add the Claude API is decided at the start of M3**, from M2's labelled-sample measure (D24). If added,
  the approved design holds: a fast, cheap model as a first-pass filter, a stronger one only for candidate matches
  and impact estimates.
- Paid spend is bounded by the **$20 monthly cost ceiling** (D21-5). D9 as approved also had a daily spend cap
  on cloud calls, enforced in the engine; it is not built yet (no cloud calls exist before M3's Claude
  decision).
- Every verdict is journalled with its provider and model, so M5 compares providers; responses are cached by input
  hash (D20 B). Candidate retrieval first (keyword index plus embeddings, D15, D27) keeps calls few.

*Amended 2026-10-28 (M2 local only through Strata; Claude an M3 decision; the $20 ceiling): BUILD-LOG M2 proposal.*

### D10. Repository: public

- **`swalsh76/IKARS`, public, MIT**, like the siblings (owner's reason: unlimited GitHub Actions minutes for public
  repositories, Windows runners included). Created in M1.1.
- The same rules as KAPS and SPARQ: never commit secrets, keys, `.env` files or personal data; real host names,
  addresses or share paths; **Kalshi data extracts** (`*.parquet`, `*.duckdb`, `*.sqlite`: the Developer Agreement
  limits storing and sharing API data); or **fetched news content** (source licences). Fixtures are small,
  synthetic or hand-written (D21-4, `docs/TESTING.md`). Commit with the GitHub no-reply address.
- The Developer Agreement's §6.4 (prior approval for public statements about Kalshi) stays as KAPS handled it: set
  aside by the owner and not reopened unprompted (G17 reviews it before M7).

### D11. Quality gates

- **Python:** `ruff` (format, lint), `mypy --strict`, `pytest` with **90% coverage** and **95% for every module that
  computes money** (fixed point, fees, P&L, ledger, ledger import, portfolio, costs), `pip-audit`.
- **Web terminal:** TypeScript `strict`, ESLint, Vitest with **90% coverage** thresholds, Playwright screens in Edge,
  generated contracts checked current, `pnpm audit`, a frozen lockfile. *Added by the owner 2026-10-27:*
  - ESLint **bans** `dangerouslySetInnerHTML`, `innerHTML`/`outerHTML`/`insertAdjacentHTML`, `eval`, `new Function`.
  - **Supply chain:** Dependabot for npm; pnpm's install scripts blocked except a named allow-list
    (`onlyBuiltDependencies`); a **7-day waiting period** before a new release is used (`minimumReleaseAge`; the
    same rule for CI actions and pinned build tools); few runtime libraries, each licence in DEPENDENCIES.md.
  - **Money in TypeScript:** numbers are floats, so the terminal does **no** money arithmetic; it formats integers
    through a distinct money type, with a test.
  - Playwright traces, screenshots and reports, and `.env*` files, are ignored and refused by the data guard.
- **Hygiene:** the data guard (no data, databases, keys, settings, logs or real paths tracked) and gitleaks over the
  whole history, in CI and the pre-commit hook; the C# launcher builds.
- **CodeQL** (owner, 2026-10-27): `security-extended` for Python, TypeScript, C# (the launcher) and the CI
  workflows, on every pull request, on `main` and weekly.
- **Required on `main`:** `python`, `hygiene`, `web`, `CodeQL` (a strict ruleset). CI runs on **windows-latest**,
  the target platform. Live Kalshi tests are GET-only, opt-in and never in CI.
- **Rolling mini code reviews (R14, owner 2026-10-26),** so large whole-codebase reviews are rarely needed:
  - **Per commit**, the diff before committing: correctness and edge cases; money in fixed-point integers; reuse (no
    duplicate code; shared code in the shared module); hot-path efficiency (in the terminal, the render path:
    re-renders and allocations per update); simplification; tests cover the change; no secrets, keys, real host
    names or paths; *(2026-10-27)* outside text drawn only as text, never as markup or a link it chose; a new
    dependency's licence, release age and install scripts.
  - **Per merge**, the branch diff against `main` plus the touched modules and their callers: consistency across the
    slice; engine and terminal contracts in step; drift from the PLAN decisions; docs and guides current.
  - **How:** `/code-review` on the diff (low effort per commit, medium per merge), a reuse and simplification pass,
    and a fresh-eyes sub-agent at each merge (R16).
  - **Security reviews (owner, 2026-10-27):** a `/security-review` before merging the web terminal's sign-in and file
    serving (done in M1.8a-web), and again before M7.
  - **Findings** are fixed in the branch or parked in §9 with the reason, and listed under **Review** in the slice's
    BUILD-LOG entry; a merge review is reported to the owner itemized, one line per finding, never only a count
    (owner, 2026-10-27).
  - A full-codebase review is still held at each milestone's close-out, and should then be short.
- **Sub-agents (R16, owner 2026-11-01):** used wherever they are an advantage: read-only research that splits into
  independent parts (how KAPS and SPARQ handled something; terms checks for many sources); a fresh-eyes reviewer at
  each merge that never saw the slice's reasoning; spikes in their own worktree; long, separable background work.
  **Not** for a slice's sequential build, live checks on the workstation (keys, the installed engine), or steps the
  owner approves one at a time. Every brief carries the non-negotiables (no secrets, key paths, real host names or
  paths; Kalshi GET only; nothing fetched committed); results are checked by the main session before use, and a
  review agent's findings go under **Review** like any other.

*Amended 2026-10-27 (web gates replace the retired C# gates; CodeQL added and required): BUILD-LOG 1.8w, 1.8w-sec.
2026-11-01: R16. 2026-11-02 (the money gate covers every money module): BUILD-LOG 1.R3 (E15).*

### D12. Deployment: portable zip and user-space installer (Velopack)

Two downloads, as many GitHub projects offer: a **portable** zip that runs from any folder and an **installer**
(`Setup.exe`) into the user's profile with no admin rights, both built by **Velopack**, which also does updates.

- **Contents:** a standalone CPython (python-build-standalone, as `uv` uses) with the locked packages installed from
  hashes and run isolated (`-I`); nothing frozen (no PyInstaller), so no import quirks or antivirus false alarms.
  `IKARS.exe`, a tiny C# launcher for .NET Framework 4.8 (part of Windows 11), answers Velopack's install, update and
  uninstall steps and otherwise opens the terminal. The web terminal's built files are served by the engine; Edge
  shows the window; no .NET runtime is shipped. **Optional extras** (GPU packages, any local model on the
  workstation; D9, D15) are downloaded on demand from Settings, never bundled.
- **Folders:** the program in `%LOCALAPPDATA%\IKARS-App`; settings, data and logs in `%LOCALAPPDATA%\IKARS`, which
  install, update and uninstall never touch (uninstall removes start at sign-in, the watchdog task and the
  notification name). The portable build keeps its data in `IKARS-data` beside the program, marked by Velopack's
  `.portable` file, never inside `current` (which an update replaces; an older `portable.marker` there is ignored).
- **Releases:** a `vX.Y.Z` tag runs the release workflow, the only build ever released: **gate** (the tagged
  commit's `python`, `hygiene`, `web` and `CodeQL` checks passed), **build** (read-only token, no stored credential;
  `SHA256SUMS` of the release's own files), **attest** (a build-provenance attestation), **publish** (checks the
  sums; a draft until `SHA256SUMS` is beside the downloads). Test releases are GitHub pre-releases; updates are
  deltas.
- **Updates:** checked a minute after the start and then daily, downloaded in the background, installed only when
  the owner clicks **Restart to update** and then **Install** in the IKARS Update window: the engine stops, Velopack
  swaps the program folder, migrations back up first (D20 G). Each version change takes a pre-upgrade snapshot;
  `ikars rollback VERSION` restores it and installs that release, setting the newer data aside, never deleting it.
  An engine still down 15 minutes after an update is restarted by the watchdog, with an alert.
- **Code signing:** unsigned pre-releases for now (the owner is the only user); SignPath Foundation's free
  open-source signing applied for ([CODE-SIGNING.md](CODE-SIGNING.md)); decided for good before the M6 release. An
  MSI (WiX) only if one is ever required (§9).

*As built: BUILD-LOG 1.9a, 1.9b. Amended 2026-10-27 (web terminal files, no .NET runtime): BUILD-LOG 1.8w.
2026-11-02 (the four-job release workflow; `.portable`): BUILD-LOG 1.R3 (E1 to E3, E8).*

*The options weighed: BUILD-LOG 0.1.*

### D13. Strategy families (order as recommended)

All share one pipeline: **live feeds → signals → fair value / edge → recommendation with net P&L → journal**. Each
family is a signal module (a plugin), not a separate system.

| Family | Edge over humans | Data | Notes |
|---|---|---|---|
| **S. Sniping** | Seconds faster than humans to hit a quote that is stale against a fresh fair value (a related market moved, a data point landed, spot moved). | Kalshi order book (`orderbook_delta`) and ticker; the fair-value source. | Needs the live book (KAPS records no deltas). Delivered as fast alerts (D6). |
| **M. Momentum** | Humans under-react to information and then chase; trend and volume-surge entries with defined exits. | Kalshi ticker and trades; KAPS ingest (since 2026-10-24) and the 77-day history for back-tests. | Fees eat small moves: the P&L engine decides which are worth taking. First rule: M1.12's, registered by hash. |
| **N. News/data estimates** | Better-informed probability than the crowd on human-paced markets. | Sources in D5. | The original IKARS idea. |
| **P. Perpetuals** | Funding carry (BTC funding positive about 52% of intervals, about 9% a year in the KAPS review), momentum on perps, and hedging binary positions with a perp in the same account. | Kalshi perp endpoints; exchange spot (D5 option E). | New Kalshi product: API, fees, margin and funding rules verified first (M2.9). |

Approved order: **N + M first** (back-testable on existing data), **S** once the live book is in the engine, **P**
after a read-only perp research slice confirms the API and costs.

**Candidate family, not approved** (scope frozen; it can return only through a milestone proposal, §9): **R.
Reversion / fade** (owner's manual in-play trade, 2026-11-01). Humans over-react to an early setback, such as a heavy
favourite's price dropping sharply early in a game, and the price comes back: buy the drop, sell the rebound. It
needs a fair value to tell an over-reaction from a real repricing (other-venue odds, G7, or a live score feed); its
main risk is averaging down into a real repricing. On the same drop it points the opposite way to momentum. The
variants and notes: BUILD-LOG Candidate family R.

### D14. Custom order types (R10)

**Native in Kalshi** (checked in M1.2: `docs/research/kalshi-order-options.md`): every order is a priced limit on the
YES book, with time-in-force good-till-cancelled, immediate-or-cancel or fill-or-kill; post-only; reduce-only (IOC
only); an expiry time (GTC only); self-trade prevention; cancel on an exchange pause; and order groups (all cancelled
when a contracts cap is hit within a rolling 15 s). There is no market, stop, take-profit, trailing, OCO or bracket.

**Custom (in-app)**, all built from one **trigger → action** rule engine, so each type is a configuration:

| Custom type | Trigger | Action |
|---|---|---|
| Stop / stop-limit | Bid or last trade crosses a level | Exit at market-equivalent (IOC at a bound) or at a limit |
| Take-profit | Bid reaches the target | Exit at limit |
| Bracket / OCO | Either of two triggers | Fire one, cancel the other |
| Trailing stop | Price falls a set distance from its high since entry | Exit |
| If-touched / conditional | Another market, a source item, a spot or perp price, a book pattern (D16) | Entry or exit |
| Time-based | A clock time, or N minutes before close | Entry or exit |
| Scale-in / ladder | Price levels | Several limits at once |
| Iceberg | The shown size fills | Replenish a small visible size |
| Peg | Best bid or mid moves | Re-price a resting limit |
| Pair / hedge | One leg fills | Place the other leg (a related market or a perp hedge) |

- **M4 to M6: watch orders.** When the trigger fires, the engine raises a fast alert with the exact action and its
  P&L after costs, and **paper-fills** it through the paper executor against the live book, so the journal shows
  what each type would have earned. No order is sent.
- **M7 (owner, 2026-10-26: they are to become live trade functions):** the same rules drive real orders through the
  real executor (D6), each type enabled only once its paper record is reviewed with the owner; iceberg and peg need
  M7's compliance review first (G17).
- **KAPS lesson:** custom orders exist only while the engine runs. A stop that is not on Kalshi's book protects
  nothing while the workstation is off, and the terminal must say so on every custom order. The engine already runs
  without the terminal open (G5).

### D15. GPU use on the workstation (R11)

The RTX 5070 Ti (16 GB, Blackwell) is used where it measurably beats the 9800X3D, and only there; everything has a
CPU fallback, so IKARS also runs without CUDA.

| Use | Gain | Library |
|---|---|---|
| **Text embeddings** for linking news to markets, de-duplicating items, and an index over about 150k market titles | Re-embedding all titles takes seconds instead of minutes; live items embed at once | ONNX Runtime (CUDA) with a small open model (D27) |
| **Model training** for momentum and order-book models (D16), and parameter sweeps | Large for sweeps and sequence models | XGBoost (CUDA) or LightGBM; PyTorch for sequence models |
| **Back-test and Monte-Carlo sweeps** (P&L distributions, rule grids) | Large for wide grids | PyTorch or CuPy tensors |
| A local first-pass news classifier (optional) | Saves LLM calls | A small fine-tuned classifier |

- **Not on the GPU:** the real-time event loop, order-book upkeep, fees and P&L (exact integers, tiny), and the UI
  (the browser already renders on the GPU).
- **Checks first:** PyTorch needs CUDA 12.8 or later for Blackwell (`sm_120`). RAPIDS (cuDF, the Polars GPU engine)
  has no native Windows support, so Polars and DuckDB stay on the CPU.
- **Deployment:** GPU packages are an **optional extra** (CUDA wheels run to gigabytes), only on machines with an
  NVIDIA GPU. The engine reports the device, driver and CUDA version in health ("CPU only" without one; M1.6).
  Everything IKARS needs on a machine is listed in [DEPENDENCIES.md](DEPENDENCIES.md).

### D16. Live order-book analysis (R12)

The engine keeps live books from Kalshi's `orderbook_delta` channel (a snapshot, then deltas checked by `seq`; a gap
triggers a resync), authenticated with the read-only key, for a **watched set** of up to 100 markets within a
deltas-a-second budget: the most active markets, markets with live signals or recommendations, and markets the
owner opens. The ticker covers everything else. Raw deltas go to hourly Parquet for back-tests.

*As built (M1.4b): the owner's pinned markets (`[feed] watch`) first, then the most active; watching by
signal, recommendation or an opened Market screen is not built yet. BUILD-LOG 1.4b.*

Each pattern is a **hypothesis**, scored in the journal before it may raise a recommendation:

| Pattern | What it may imply | Feeds |
|---|---|---|
| Depth imbalance; microprice against mid | Short-horizon direction | Momentum, sniping |
| **Quote refresh speed** (refill within 1 s of being hit, regular sizes, price-stepping) | Whether the counterparty is a **bot or a human**, which tells where the "beat the humans" thesis can apply | Every strategy's market choice |
| Stale quotes (unchanged while fair value, a related market or spot moved) | A sniping chance | Sniping |
| Walls that appear and are pulled as price nears | Spoofing or layering; walls not to be trusted | Risk flags on recommendations |
| A level refilled again and again at the same price | Hidden (iceberg) size | Slippage estimate (D7) |
| Aggressor bursts; trade-size clusters (round lots) | Retail chasing; momentum start | Momentum |
| Cross-market inconsistency (bracket sums, related markets, lead-lag) | Mispricing between markets | Sniping, cross-reference view |

- **Terminal:** a book-signals tile on the Overview; in drill-down, a per-market depth heatmap over time and the
  pattern events; in cross-reference, the markets that share a pattern.
- **Limits:** the per-subscription market limit is not documented (error 26), so it is learned passively, never
  probed: a "limit reached" error is logged and shrinks the watched set (BUILD-LOG 1.4b). Deltas for every market
  at once are not the goal.
- SPARQ's refill measure (S1.2a) and planned book recorder (S1.2b) do the same jobs; D17 says how they are reused.

### D17. Using SPARQ's work (R13)

- **Code base:** `ikars-core` is seeded from **SPARQ's `sparq_core`** rather than KAPS's `kaps_core`: the same KAPS
  code plus SPARQ's later fixes (for example, a failed ticker resubscribe now ends at the next reconnect). SPARQ had
  no book code, so IKARS's order book is its own (M1.2).
- **Reused modules (read critically; BUILD-LOG notes what was kept or changed):** `sparq_analytics.live` (top-of-book
  measures, markouts, refills, queue fill rules); `study` (clustered bootstrap, calibration, fee-aware edge, rule
  registration and the forward ledger) for M5's evaluation.
- **Findings used as starting points, not results:**
  - Feed timing: ticker frames about 1 s apart; the feed reaches the server about 0.55 s after Kalshi's timestamp;
    a ticker frame follows 99.95% of trades within 1 s. These set the sniping latency budget.
  - The middle-tier and forward-rule filters (quiet 60 s to 1 h, activity 1k to 100k contracts, price 20 to 80
    cents) are where human-paced trading was found: the first market filter for momentum and sniping.
  - The order-write and kill-switch lessons are kept for M7.
- **Data:** the 77-day history and SPARQ's live copies (Parquet on the analytics node) are copied to the workstation
  for back-tests, with no database credential. A read-only role for IKARS on the KAPS database is optional and would
  need the owner to create it.

*Guard lapsed 2026-10-27 (owner): SPARQ's S1.1b forward test was dropped unopened, so the guard against
contaminating it (no edge to settlement on its events before 2026-11-23) no longer applies: BUILD-LOG 1.12b.*

### D18. Audit and tax ledger (R15)

**Purpose.** One permanent, tamper-evident record of everything that moved money in the Kalshi account, and of every
action IKARS took, from which IKARS produces a **per-tax-year package** to keep with a 1040's records.

*Not tax advice:* how Kalshi event contracts and perpetuals are taxed is for the owner's tax adviser (capital gains on
Form 8949 / Schedule D, Section 1256 contracts on Form 6781, or other income). The ledger keeps every field each
treatment needs, so the choice can be made, or changed, later without re-collecting data.

**Source of truth: Kalshi's own account records, not IKARS's guesses.**
- The engine imports **fills, settlements, fees, deposits, withdrawals, transfers, rebates or incentives, and perp
  funding** with the read-only key (portfolio reads are GETs it may make; confirmed in M1.2), over REST every minute
  while it runs. This covers the owner's **manual trades**, so the ledger is useful long before M7. Records carry
  IKARS's context where it exists (the recommendation, signal or custom order behind them).
- **Reconciliation:** each import checks the ledger against Kalshi's balance and positions. **Exact means a zero
  difference** (Kalshi's balance does not hold back a resting buy; its cost is shown as information). A difference is
  shown in the terminal and the year's report, never silently fixed. Kalshi has no interest endpoint, so interest or
  any unexplained balance change stays unexplained until the owner labels it (`ledger annotate`, an appended
  adjustment of the chosen kind).
- The ledger is bound to its Kalshi environment (production); an import from another is refused.
- Paper fills (D6) are a **separate, clearly marked stream** and never enter the tax package.

**Each record:** sequence; kind (fill, settlement, fee, funding, deposit, withdrawal, transfer, interest,
adjustment, app action); time in UTC and in the owner's tax time zone (a setting, the workstation's zone by
default); Kalshi ids (fill or trade, order, `client_order_id`, settlement); market, event and series tickers and the
market title at the time; side, action, contracts (10^-2); price, fee, amount and, where Kalshi gives it, the balance
after, in **fixed-point dollars at 10^-6**; Kalshi's raw JSON verbatim and IKARS's context ids; `prev_hash` and
`hash` (SHA-256 over the canonical record), forming a hash chain. **App actions** (engine start and stop, settings
and risk-limit changes, kill switch, key loaded by its fingerprint only, M7 orders sent) go into the same chain.

**Tamper evidence and durability:**
- **Append-only:** corrections are new adjustment records pointing to the one they correct, never edits.
- The chain is checked on every start and by `verify`; each day's last hash goes to a **daily checkpoint file**.
- SQLite (D4) in WAL mode with `synchronous=FULL`, a file apart from the operational state, with versioned
  migrations and a backup before each (D20 G).
- **Backups go to the NAS share that holds the KAPS database backups** (the share SPARQ's data comes from;
  owner, 2026-10-26), in IKARS's **own
  subfolder tree**. The real share path is only in the local settings, never in the repository; docs use
  `\\<nas>\<share>` and tests `\\127.0.0.1\ikars-no-such-share`.

  ```text
  <share>\
    .kaps-backup-marker, pgbackrest\ ...     KAPS's; IKARS never reads, writes or lists them
    ikars\
      .ikars-backup-marker                  IKARS writes only if this marker exists (an unmounted or wrong share is refused)
      ledger\
        daily\<yyyy>\<yyyy-mm-dd>\         ikars-ledger.sqlite (SQLite online backup), checkpoint.json, SHA256SUMS
        monthly\<yyyy>\<yyyy-mm>\          the last daily copy of each month
        checkpoints\<yyyy>\<yyyy-mm-dd>.json   the chain head hash of each day
      tax\<yyyy>\                          each tax-year package (D18), written once, never overwritten
      state\daily\<yyyy>\<yyyy-mm-dd>\     the operational SQLite file (D4), for a quick rebuild
      config\                              a settings export, with no secrets
  ```

  - **Safety:** every write goes to a temporary name, is hash-checked after the copy, then renamed; every path is
    resolved under `<share>\ikars\` and anything outside is refused, so nothing of KAPS's can be touched; a failed or
    skipped backup shows in Health and is alerted by the watchdog.
  - **Retention on the share:** ledger daily copies 90 days, monthly copies and checkpoints forever; tax packages
    forever; state daily copies 30 days. The local ledger is never pruned.
  - **Not copied by default:** the raw-feed Parquet (large); it can be added later as `ikars\feeds\`.
  - **Access:** the owner's own saved Windows network credential; engine and terminal run as the owner, so nothing
    new is stored.
  - **Restore:** `ledger restore-drill` restores the newest copy into a scratch file and checks the chain (G14);
    `ikars backup restore` rebuilds a workstation from the share (D20 I, `docs/RUNBOOK.md`); **Back up now** on
    Health.
- The ledger is **never pruned**: IRS guidance is to keep records at least 3 years, longer in some cases, so IKARS
  keeps everything and leaves deletion to the owner.

**Tax-year package** (`ikars ledger export --year 2026`, also a button in the terminal):

| File | Contents |
|---|---|
| `IKARS-2026-summary.pdf` (**PDF/A**, the archival PDF standard) | Account, period, totals by kind, realised gain or loss short and long term, fees, funding, deposits and withdrawals, the reconciliation result, the lot-matching method, the ledger's chain head hash. Printable and fileable with the return's records. |
| `IKARS-2026-form8949.csv` | One row per closed lot, in the columns of Form 8949: (a) description, (b) date acquired, (c) date sold or settled, (d) proceeds, (e) cost basis, (f) code, (g) adjustment, (h) gain or loss. Short- and long-term are separate. **Fees are in the basis on entry and reduce proceeds on exit**, the same cost-basis rules as D7. |
| `IKARS-2026-6781.csv` | The same closed positions summarised for Section 1256 treatment (year-end mark-to-market at the settlement or last price), in case the adviser chooses it. |
| `IKARS-2026-ledger.jsonl` | Every ledger record of the year, raw Kalshi JSON included. |
| `IKARS-2026-manifest.txt` | SHA-256 of every file in the package and the chain's start and end hashes, so anyone can later prove nothing was changed. |

- **Lot matching:** FIFO (also the portfolio's cost basis), with specific identification selectable before any year
  is exported; the method is printed in the package and cannot change silently for a year already exported.
- **Settlement:** a settled contract is disposed at its payout on the settlement date (the market's settlement time,
  else its close time). **Year boundaries** use the owner's tax time zone.
- **Cross-check:** the package is compared with the **Kalshi 1099 or annual statement**; `--compare` reads Kalshi's
  CSV statement, if Kalshi provides one, and lists any differences.

**Where it is built:** store, chain, `verify`, import and portfolio in **M1.5** (a, b); restore and the runbook in
M1.11; the tax-year package in **M5** (first needed for the 2026 tax year, early 2027); M7 adds IKARS's real orders
to the same chain.

*Owner's answers 2026-10-27 (tax zone, REST each minute, unexplained money labelled by the owner, FIFO): BUILD-LOG
1.5. Exact means zero; the environment binding: BUILD-LOG 1.R3 (A4, A10).*

### D19. Gap review additions

The owner asked "what am I missing"; the answers were approved the same day. Each item lands where shown.

| # | Addition | Where |
|---|---|---|
| G1 | **Portfolio and exposure:** open positions from the ledger import, marked to market; unrealised P&L after exit costs; exposure by event and category; balance. On the Overview. Recommendations take existing positions and correlations into account. | M1.5 (model), M1.8 (view), M4 (in recommendations) |
| G2 | **Sizing and risk limits while recommend-only:** suggested size (fractional Kelly, capped by depth at the price); limits per market, event, category and day; a daily loss stop that mutes recommendations. **One risk layer**, reused by M7. | M4 |
| G3 | **Replay harness at the core:** the engine's input is an interface with a live source and a replay source (recorded Parquet, deterministic clock), so back-tests, bug reproduction and tuning run the same code as live. M1.4 built the interface and generic replay; M1.13 replays real Kalshi recordings. | M1.4, M1.13 |
| G4 | **Pre-registration (SPARQ practice):** a rule's settings are locked by hash before it is scored on held-out or forward data; a registry records every rule, its hash and its scoring windows. | M1.12 (first rule), M3 |
| G5 | **Uptime on a desktop:** the engine runs in the background without the terminal; it auto-starts at sign-in, keeps the machine awake while running, logs gaps, and a scheduled watchdog restarts it. (Pulled forward from M7.) | M1.6 |
| G6 | **Workstation alerts:** Windows notifications for engine down, feed stale and backup failed (M1.6), then top recommendations (M1.12, M4), and in the terminal from M1.8. No phone pushes. | M1.6, M4 |
| G7 | **Other venues as fair value:** Polymarket, sportsbook odds (an odds API) and other prediction markets as source plugins; cross-venue mispricing alerts. | M3 |
| G8 | **Settlement rules and event calendar:** parse each market's rules and resolution source for linking; a calendar of releases, games and close times; pre-event alerts; no recommendation that is about to be settled by fine print. | M2 (D26) |
| G9 | **Recommendation lifecycle and owner feedback:** expiry, invalidation, de-duplication; taken, ignored or dismissed; the ledger links the owner's fills back to the recommendation. | M1.12 (first), M4 |
| G10 | **Clock and latency:** server-time skew (KAPS's clock module) and feed-to-alert latency in the health view. | M1.4 |
| G11 | **Storage budget on the workstation:** a governor with a budget, compaction and a health warning. | M1.4 |
| G12 | **Public-repository security:** gitleaks; hash-pinned dependencies with Dependabot; the loopback API token; read-only key rotation; CodeQL and the browser hardening (D3, D11); releases gated on green CI, with checksums and a provenance attestation, signed once SignPath approves (D12). *Amended 2026-11-02 (signing pending; gate, checksums, provenance): BUILD-LOG 1.9b, 1.R3b.* | M1.1, M1.8, M1.9, M1.R3 |
| G13 | **Interest and other income in the ledger** as their own record kinds (likely 1099-INT), if Kalshi pays them. | M1.5 (amends D18) |
| G14 | **Ledger restore drill:** `ledger restore-drill` restores from the share into a scratch file and checks the hash chain. | M1.5 |
| G15 | **Settings and a first-run wizard:** key environment variables (checked, never shown), the share path, LLM choice, GPU extras, quiet hours, tax time zone. | M1.8, M1.9 |
| G16 | **Speed in the UI:** hotkeys (alert → market in one key), multi-monitor layouts, and skins: built-in palettes (Dark, Light, High contrast, Midnight, Paper) chosen in Settings; own skins not chosen. | M1.3 (mock-ups), M1.8 |
| G17 | **Compliance review:** before M7, review Kalshi's rulebook on manipulation (iceberg, peg, fast cancel/replace), its rule against trading on material non-public information (sources must be public), and the Developer Agreement's §6.4 with a public repository that names Kalshi. Also check **each news source's terms of use** when its adapter is planned. | M2 (per source), M7 (first slice) |
| G18 | Fault and soak testing: **dropped** (owner: proved in SPARQ). Ordinary reconnect and error tests stay in each slice. | — |
| G19 | **Automatic strategy demotion with its own review page:** a strategy, pattern, custom type or LLM provider whose journal turns negative after costs stops producing recommendations. A **Strategy review** page lists each one's record, demotions and reasons, with promote and demote by the owner. | M5 |
| G20 | Remote (laptop/phone) read-only view: **parked**. | §9 |
| G21 | Social sentiment (X, Reddit): **parked**; it must be addable as a plugin without restructuring (below). | §9 |

*Amended 2026-10-27 (owner): G6 no phone pushes (was "phone alerts" with ntfy); G16 skins.*

#### Plugin architecture (for G7, G21 and every later source or strategy)

- **Engine:** sources, signal modules, custom order types and venues each implement a small typed interface and
  register through Python **entry points** (`importlib.metadata`). A new plugin is a package (in the workspace, or
  installed beside the engine) and needs no engine change. Each declares its settings schema, the secrets it needs,
  its rate budget and the kinds of item it emits. Each runs in its own task with bounded queues, timeouts and a crash
  boundary, so one failing plugin cannot stall the others. The live runner loads the plugins and records what they
  emit; the host, not the plugin, stamps the received time (D20 A).
- **Link graph:** what a plugin emits is stored as typed items with links (D1's cross-reference model), so it appears
  in the Overview feeds, drill-down and cross-reference panels **without new screens**.
- **Terminal:** generic views render any item kind from its declared fields; a custom panel may come later.
- Built in M1.4a (registry, interfaces) and M1.12a (live loading); the first plugins are momentum (M1.12) and the D5
  sources (M2).

### D20. Second gap review

| # | Addition | Where |
|---|---|---|
| A | **Point-in-time correctness.** Every source item stores the time IKARS **received** it as well as the publisher's time. The replay source (G3) releases each item only at its received time, so back-tests never see the future. Source plugins start in **record-only mode** from M2's first slice, so the evaluation clock starts as early as possible. GDELT's archive is used for historical news back-tests, flagged as reconstructed (no received time). | M1.4 (fields, replay rule), M2 |
| B | **LLM safety and reproducibility.** Source text is untrusted input:<br>- strictly structured outputs, validated<br>- no tools available to the model<br>- source text kept apart from instructions<br>- an LLM verdict is never the sole basis for a recommendation<br><br>Responses are cached by input hash, with the model and prompt version, so replays are exact and not paid for twice. A cost meter sits beside the spending limits (D9, D21-5). | M2 |
| C | **Fill probability and adverse selection** for maker entries: the chance a limit fills and the typical move after it fills (SPARQ's markouts); the paper executor models queue position and latency, and is checked against the owner's real fills when they exist. | M3 (model), M4 (in P&L) |
| D | **Capital tied up and time to settlement:** recommendations are ranked by **return per day of capital tied up**; time to settlement is shown; interest earned or forgone is included where Kalshi pays it. Amends D7. | M4 |
| E | **Tradability filters:** Kalshi's per-market position limits and the account's limits (`/account/limits`), market status, the exchange schedule and maintenance windows, and markets the account cannot trade. | M1.4 (exchange status), M1.12, M4 |
| F | **Provenance on every recommendation:** code version, settings hash, model and prompt version, and the input item ids. Any recommendation can be replayed exactly and explained, and M5 compares versions fairly. | M1.12 (first), M4, M5 |
| G | **Safe upgrades:**<br>- versioned schema migrations for every SQLite file, with an automatic backup before each migration (the ledger's to the share as well)<br>- versioned settings<br>- a version handshake between the terminal and the engine<br>- an update rollback path<br><br>**An update must never break the tax ledger.** | M1.5, M1.7, M1.9 |
| H | **Alert hygiene:** priority levels; de-duplication and throttling; quiet hours; a digest for low-priority items. Only the top tier interrupts. | M1.6 (framework), M1.12, M4 (recommendations) |
| I | **Diagnostics and rebuild:**<br>- structured logs with rotation and **redaction** (keys, key paths and account values never reach a log)<br>- a one-click redacted support bundle<br>- a tested **"workstation lost" runbook**: reinstall, restore the ledger and settings from the share, verify | M1.4, M1.8, M1.11 |
| J | **Performance targets, measured continuously and shown in health:** feed-to-alert latency for the sniping path (target under 250 ms, confirmed in M1.4's plan) and, for the terminal, **update to screen**: from the terminal applying a change to the change being on the display, **p99 at most 33 ms** while 100 markets stream at 10 Hz, with the UI work per update shown alongside. | M1.4, M1.8, M4 |

*Amended 2026-10-27 (owner): J's terminal target, update to screen (was frame time under bursts): BUILD-LOG 1.8a.*

### D21. Third gap review and scope freeze

| # | Addition | Where |
|---|---|---|
| 1 | **Success criteria and go/no-go gates:** every milestone has a stated "done means" (§4.2). Evaluation uses **pre-registered stop rules**, and there is a project-level rule for when nothing passes. | §4.2, every milestone |
| 2 | **Value early, through a thin end-to-end slice:** momentum on Kalshi's own feed, from signal to a recommendation on the Overview with P&L after fees, journalled with provenance. No news sources and no LLM. It proves the whole pipeline, starts the evaluation clock for the first strategy, and later milestones widen this path rather than building layers in isolation. | M1.12 |
| 3 | **One Kalshi account, three projects:**<br>- a **separate read-only key for IKARS**, revocable and auditable on its own (D8)<br>- account-level limits read in M1.2 (the WebSocket subscription limit learned passively); the rate-limit ramp stays banned<br>- a request and subscription budget, so IKARS backs off before it can starve KAPS's production ingest<br>- sharing SPARQ's order-book recorder considered when both need the same books | M1.2, M1.4b |
| 4 | **Test data policy for a public repository** (`docs/TESTING.md`):<br>- synthetic fixture generators: books, trades, news items, and account histories for the ledger<br>- tests that need real recordings run locally only and skip in CI<br>- only tiny hand-written samples of live data are committed<br>- never committed: real feeds, fetched articles, account data | M1.1 (policy), every slice |
| 5 | **Running-cost ceiling:** one monthly budget across paid services (LLM, odds API, code signing, any paid source): **$20 a month** (owner, 2026-10-28). Each plugin's spend is shown in health; plugins degrade (local model, slower polling) at 80% and cloud calls pause at 100%, instead of overrunning. | M1.4a (framework), M2 onward |

**Scope freeze (owner, 2026-10-26).** The scope is frozen as D1 to D21. New ideas go to §9 by default and come back
only through a milestone proposal approved by the owner.

## 4. Milestone outline

Detail is proposed per milestone when it starts; each slice gets its own approved plan before code.

| Milestone | Builds | Depends on |
|---|---|---|
| **M0** | The decisions D1 to D21; tracking docs. | — |
| **M1 Skeleton** | The foundation: repository and CI, `ikars-core`, engine, ledger, run mode, loopback API, the web terminal, packaging, the rebuild runbook and **the end-to-end momentum slice** (D21-2). Slices in §4.1. | M0 |
| **M2 Sources** | Weather and news plugins recording from the first slice (D20 A); rules and calendar (G8); embeddings; the LLM layer (D9, D20 B); GDELT history; the source screens; perp research. §4.3. | M1 |
| **M3 Signals** | Signal modules per D13 (news/data estimates, momentum first); linking items to markets; whether to add Claude (D24); order-book pattern measures (D16); other-venue fair values (G7); the fill-probability and adverse-selection model (D20 C); the pre-registration registry (G4); back-tests through the point-in-time replay on recorded and KAPS/SPARQ data. | M2 |
| **M4 Recommendations** | P&L engine (D7) with fill probability (C) and return per day of capital (D); sizing and the risk layer (G2); tradability filters (E); provenance (F); the recommendation lifecycle and feedback (G9); the trigger→action rule engine with custom types as watch orders and the paper executor (D6, D14); Overview, drill-down and cross-reference views for recommendations (R9); prioritised alerts for recommendations (G6, H); the latency target on the sniping path (J). | M3 |
| **M5 Evaluation** | Outcome journal reports by version (F); calibration and P&L after costs per strategy, pattern, custom type and LLM provider; automatic demotion and the Strategy review page (G19); the tax-year package (D18). | M4 |
| **M6 Release** | The release of the recommend-only product: installer and portable zip with updates (built in M1.9), code signing decided for good (D12), a deploy guide; close-out. | M5 |
| **M7 Live trading** | Compliance review (G17) and security review (D11, §9) first; trading key, the risk layer (G2) enforcing on real orders, kill switch, audit in the ledger, real executor; one-click orders, then custom types and armed rules (D6, D14). Own proposal and approval. | M5 evidence, M6 |

### 4.1 M1 slices (complete)

Approved 2026-10-26, with the owner's later additions. Each slice's build is in its BUILD-LOG entry, its state in §5.

| Slice | Builds |
|---|---|
| **M1.1 Repository, CI and security** | Public `swalsh76/IKARS`; the `uv` workspace; CI on `windows-latest` with coverage gates; gitleaks, hash-pinned dependencies, Dependabot (G12); the test-data policy and generators (D21-4); the mini-review routine (R14). |
| **M1.2 `ikars-core`** | Seeded from `sparq_core` (D17); the order book; portfolio and account-limit models; native order options re-checked (D14); live GET tests with IKARS's own key (D21-3). |
| **M1.3 UI mock-ups** | Overview with portfolio (G1), drill-down, cross-reference, hotkeys, layouts, themes (G16), health. |
| **M1.4a Engine core** | Settings; redacted logs (D20 I); the plugin registry (D19); live and replay input by received time (G3, D20 A); items and links; clock skew (G10); metrics (D20 J); cost tracking (D21-5). |
| **M1.4b Kalshi feeds and recording** | The Kalshi WebSocket with books for the watched set; exchange status (D20 E); feed-to-alert latency; hourly Parquet with a gap log; the storage governor (G11); the request budget (D21-3). |
| **M1.5 Audit and tax ledger, portfolio** | **a:** D18's store, chain, checkpoints, `verify`, import with exact reconciliation, migrations with a backup (D20 G). **b:** share backups, `ledger restore-drill` (G14), positions and exposure (G1). |
| **M1.6 Run mode and alerts** | The background engine, start at sign-in, the watchdog (G5); the alert framework (D20 H); Windows notifications (G6); GPU detection (D15). |
| **M1.7 Loopback API and contracts** | Starlette/uvicorn on `127.0.0.1`, token and version handshake (D20 G); snapshots over HTTP, deltas over one **Server-Sent Events** stream (D3); `msgspec` contracts as JSON Schema with golden examples. |
| **M1.8 Terminal shell** | First in WPF (M1.8a), **retired** by the D1 amendment (M1.8w; the prune; tag `wpf-terminal-final`), then rebuilt as the web terminal below; M1.8w-sec set its security posture (D3, D11, CodeQL). |
| **M1.8a-web** | **1a** engine side (serving, hardening, cookie sign-in on a fixed port, `ikars open`, `demo-api`); **1b** `web/` foundation, Overview, Health, the `web` job; **2a** Markets and Market; **2b** Ledger, Alerts, the update-to-screen meter and load test (D20 J). |
| **M1.8b** | **1** hotkeys, skins, Settings, the first-run wizard (G15, G16); **2** layouts as sets of windows, the support bundle (D20 I). |
| **M1.8c New window** | A popup on the same screen, signed in (addition, 2026-10-27). |
| **M1.9 Packaging** | **a** `ikars`, `IKARS.exe`, `Setup.exe` and portable zip, setup mode, Sandbox checks; **b** the release workflow, updates on the owner's click, snapshot and `ikars rollback`, the signing policy (D12). |
| M1.10 Perp research | **Moved to M2** as M2.9 (owner, 2026-10-28). |
| **M1.11 Close-out** | M1 gate evidence; the "workstation lost" runbook end to end (`ikars backup restore`, `docs/RUNBOOK.md`); the network wait at start; **Back up now** (owner, 2026-10-28). |
| **M1.12 End-to-end momentum** | **a** plugins in the live runner, recording, host-stamped received time, the journal (lifecycle, provenance); **b** the momentum rule registered by hash (G4), P&L after fees (D7), tradability, scoring; **c** the Overview's ranked list and scorecard, the Recommendation screen, notifications for the top-ranked; **d** row navigation, Back, named crumbs (addition). |
| **M1.13 Replay of real recordings** | The recorder's Kalshi channels replayed as live items in received-time order, streaming; `ikars replay`; completes G3. After M2.3, before M3's back-tests. |
| **M1.14 Settled markets after a restart** | Closed markets at start, recent settlements in the background, lookups on request; the journal settles what the index lacks, scalar payouts included (brought forward from §9, owner, 2026-11-01). |
| **M1.R Base hardening** | A whole-code-base review's items 1 to 26 (owner, 2026-10-27). |
| **M1.R2 Platform review** | Items 1 to 5 placed in M1.8a-web, 6 and 7 in M1.13, 8 and 9 in M1.12, 10 in §9. |
| **M1.R3 Fresh-eyes review** | Five sub-agent reviewers (owner, 2026-11-02): **a** correctness and data (A, B, C), **b** security, release and docs (D, E, F1); both before M2.4. C1, C5 to momentum v2; C9 in M2.6; the rest parked (§9). |

Built in the order M1.1 → M1.2 → M1.4a → M1.4b and M1.5 (M1.3 alongside) → M1.6 → M1.7 → M1.8 (WPF) → M1.R →
M1.8a-web → M1.8b → M1.8c → M1.9 → M1.12 → M1.11, which closed M1. M1.13, M1.14 and M1.R3 followed during M2.

### 4.2 Milestone gates (D21-1)

The only place the gates are stated; each is confirmed or tightened in the milestone's own proposal.

| Milestone | Done means | If it fails |
|---|---|---|
| **M1** | Installed with Velopack on a clean account; live markets in the terminal through the engine; the ledger reconciles exactly with Kalshi's balance and positions; the restore drill and the workstation-lost runbook pass; the feed-to-alert latency and update-to-screen targets (D20 J) are measured; M1.12's momentum recommendations appear end to end and are journalled. | Fix within M1; nothing in M2 starts. |
| **M2** | As tightened by D30: the weather and news plugins have recorded for **at least 7 days** with received times and no unlogged gaps; the LLM injection test suite passes and the labelled-sample quality is measured; spend stays within the **$20** monthly ceiling (no paid LLM in M2); each source's terms check is recorded; `docs/research/perps.md` exists. | Fix the plugin, or drop that source with the owner. |
| **M3** | Every signal rule is registered by hash before scoring; replays are reproducible byte for byte; at least one signal is positive after costs on the validation part, which makes it a forward-test candidate. | No candidate: the owner decides whether to narrow the scope or rethink. M4 does not start for a signal without a candidate result. |
| **M4** | Every recommendation carries a size, fill probability, return per day, tradability check and provenance; alerts meet the latency target. | Fix within M4. |
| **M5** | **Pre-registered stop rule, per strategy:** at least N scored recommendations (N fixed in M3 when the rule is registered; 300 by default, as in KAPS and SPARQ), a mean P&L after costs above zero, and a 95% lower bound above zero (bootstrap clustered by event). Passing strategies are kept; the rest are demoted (G19). | **Project rule:** if no strategy passes, IKARS does not go to M7. The owner chooses: stop, rethink, or a new pre-registered test. |
| **M6** | Install, update with migrations, rollback and the runbook pass on a clean account; docs current. | Fix within M6. |
| **M7** | Compliance review cleared; only M5-passing strategies are eligible; staged from paper to tiny live limits with the owner's go-ahead on each step. | Stays at the last passing stage. |

### 4.3 M2 Sources: decisions and slices

**Goal:** weather and news enter IKARS from the first slice, recorded with the time IKARS received them (D20 A).
Behind them come GPU embeddings (D15), the LLM layer with its safeguards (D9, D20 B), and the settlement rules and
calendar (G8). **M2 makes no new recommendations:** linking items to markets and turning them into signals is M3.
All of it sits under D5, D9, D15, D20 A/B, G8 and G17. The Strata server (D24) is the one new resource, a
home-network service like the NAS.

**Owner's answers (2026-10-28):** Strata is the local LLM backend; the monthly ceiling is **$20** (D21-5); news
from GDELT plus curated RSS; the plan as proposed, with M1.13 after M2.3. **Amended the same day** ("slower response
rate is acceptable"): **the local LLM only**; Claude deferred to an M3 decision on M2's labelled-sample results;
embeddings before the LLM layer, so the pre-filter bounds the volume; the model's thinking off for classification if
measurement supports it (the probe: BUILD-LOG M2 proposal).

| # | Decision |
|---|---|
| **D22 Weather** | NWS `api.weather.gov`, free and public domain (a contact User-Agent is required). The stations Kalshi's weather markets settle on, found from each market's rules, not a hand-made list. It records **hourly forecasts**, **observations every 5 min** and the **daily climate report (CLI)**. Kalshi settles on The Weather Company's value from the same government station; that company has no free feed or automated-access terms, so the NWS climate report is recorded as the proxy, and M2.4 measures how close it is (owner, 2026-10-28). |
| **D23 News** | **GDELT:** the **GKG 15-minute files** (every article GDELT sees, with its title) are the main stream, plus **DOC 2.0 API topic queries** when it answers (it caps a query at 250 articles). **RSS/Atom:** a curated list in Settings, starting with US government feeds (public domain); publisher feeds only by the owner's decision per feed; a feed without a recorded terms note is not polled (G17). Duplicates removed by cleaned link across all news feeds. Kept: headline, link, source, publisher time, received time, the feed's own summary (GDELT: themes, places, people, organisations, tone). Full articles and quotations are never fetched or kept in M2; nothing fetched is ever committed. |
| **D24 LLM backends (local only)** | One provider interface (D9), so a backend can be added without rework. **M2 uses only the owner's Strata server** on the home network: OpenAI-compatible, reached through IKARS's existing HTTP client, its key in the user variable named in the owner's local notes, never in the repository. Its quality is measured on a labelled sample in M2. **Whether to add Claude** (for linking and probability estimates) **is decided at the start of M3**, from those results. When Strata is away, items are still recorded and classified when it returns. |
| **D25 First LLM use** | First-pass classification of news items: relevance, categories and urgency. Every verdict is journalled with its provider and model, and cached by input hash (D20 B). Only items the embedding pre-filter places near a live market are classified. No recommendations are made from it. |
| **D26 Settlement rules and calendar (G8)** | Each market's rules text is stored and its resolution source parsed (for weather, the station). A calendar of close times and scheduled reports. Signals get a "settles soon, by fine print" guard. Shown on the Market screen and a Calendar panel. |
| **D27 Embeddings (D15)** | ONNX Runtime (CUDA, with a CPU fallback) and a small open embedding model. It indexes market titles and source items, removes duplicate items, and gives candidate markets for an item, which M3's linking uses. An optional GPU extra. |
| **D28 GDELT history** | A bounded pull late in M2: 90 days, English and US, flagged as reconstructed (no received time), for M3's back-tests. |
| **D29 Terminal** | The Overview's **Latest source items** goes live. A **Source item** screen (mock-up first). Per-source cards and the **cost meter** on Health. |
| **D30 Gate** | Tightens the M2 gate (§4.2): 7 days of recording, the LLM checks and spend, the terms checks, the perp research doc. |

*Amended 2026-10-28: D24 local only (BUILD-LOG M2 proposal). 2026-11-01 (owner): D23, GKG files as the main stream and
government feeds to start (BUILD-LOG 2.3); D22's proxy note (BUILD-LOG 2.2).*

| Slice | Builds |
|---|---|
| **M2.1 Polling-source framework** | Request budget, backoff, conditional GET, received time, a gap log and health per source, recording through the generic recorder; `docs/research/sources.md` with the terms checks. |
| **M2.2 Weather** | D22, record-only. The 7-day clock for weather starts here. |
| **M2.3 News** | D23, record-only, duplicates removed. The 7-day clock for news starts here. |
| **M1.13 Replay of real recordings** | §4.1; after M2.3, so the replay covers news and weather recordings too. |
| **M2.4 Rules and calendar** | D26. |
| **M2.5 Embeddings** | D27, before the LLM, so its pre-filter bounds the volume. |
| **M2.6 LLM layer** | D24, D25: the interface and the Strata backend, the hardening, the cache, the cost meter (idle with no paid backend; M1.R3 C9), the injection test suite, the labelled-sample measure, the news first pass. |
| **M2.7 Terminal** | D29, mock-ups first. |
| **M2.8 GDELT history** | D28. |
| **M2.9 Perp research** | From M1.10. Read-only: perp endpoints, fees, funding schedule, margin and liquidation rules; `docs/research/perps.md`. Any time. |
| **M2.10 Close-out** | Gate evidence (§4.2). |

**Machine and outward changes:** outbound reads to NWS, GDELT, the RSS hosts and Strata on the home network; the
owner's setup is only Strata's key (already set). Packages, licences confirmed per slice: `defusedxml` (PSF-2.0)
with IKARS's own small feed parser (M2.3); `onnxruntime-gpu` (MIT) and `tokenizers` (Apache 2.0) planned for M2.5.
An embedding model of about 100 MB is downloaded. News and weather recordings are small next to Kalshi's.

## 5. Progress

States: **done** (merged to `main`), **open** (built and checked, PR not merged), **built** (checks pending), **in
progress**, **not started**, **moved**, **retired**.

| Item | State | BUILD-LOG | PR |
|---|---|---|---|
| M0 decisions D1 to D21; scope frozen | done 2026-10-26 | 0.1 | — |
| M1 slices (§4.1) and gates (§4.2) | approved 2026-10-26 | 0.1 | — |
| **M1 Skeleton** | **done** 2026-10-28 (gate passed) | 1.11 | #31 |
| M1.1 Repository, CI and security | done | 1.1 | #1 |
| M1.2 `ikars-core` | done | 1.2 | #3 |
| M1.3 UI mock-ups | done (approved for now; revisable) | 1.3 | — |
| M1.4a Engine core | done | 1.4a | #4 |
| M1.4b Kalshi feeds and recording | done | 1.4b | #5, #6 |
| M1.5a Audit ledger | done | 1.5a | #7 |
| M1.5b Share backups, restore drill, portfolio | done | 1.5b | #8 |
| M1.6 Run mode and alerts | done | 1.6 | #9 |
| M1.7 Loopback API and contracts | done | 1.7 | #10 |
| M1.8a Terminal shell (WPF) | retired (D1 amended) | 1.8a | #11 |
| M1.8w Direction change: TypeScript web terminal; WPF pruned | done | 1.8w | #12, #13 |
| M1.8w-sec Security posture for the web terminal; CodeQL | done | 1.8w-sec | #15 |
| M1.8a-web-1a Engine side of the web terminal | done | 1.8a-web-1a | #16 |
| M1.8a-web-1b Web foundation, Overview and Health | done | 1.8a-web-1b | #17 |
| M1.8a-web-2a Markets and Market | done | 1.8a-web-2a | #19 |
| M1.8a-web-2b Ledger, Alerts and the meter | done | 1.8a-web-2b | #20 |
| M1.8b-1 Hotkeys, skins, Settings and the wizard | done | 1.8b-1 | #21, #22 |
| M1.8b-2 Layouts and the support bundle | done (two-monitor check open) | 1.8b-2 | #23 |
| M1.8c New window | done | 1.8c | #24 |
| M1.9a Build, install, uninstall | done | 1.9a | #25 |
| M1.9b Releases, updates, rollback, signing policy | done | 1.9b | #26 |
| M1.10 Perp research | moved to M2.9 | — | — |
| M1.11 Close-out | done | 1.11 | #31 |
| M1.12a Plugins in the engine, the journal | done | 1.12a | #27 |
| M1.12b Momentum, P&L after fees | done | 1.12b | #28 |
| M1.12c The terminal side of momentum | done | 1.12c | #29 |
| M1.12d Navigation: rows, Back, named crumbs | done | 1.12d | #30 |
| M1.13 Replay of real recordings | open (stacked on M2.3) | 1.13 | #36 |
| M1.14 Settled markets after a restart | open (stacked on M1.13) | 1.14 | #37 |
| M1.R Base hardening | done | 1.R | #14 |
| M1.R2 Platform review | done (items placed) | 1.R2 | — |
| M1.R3a Correctness and data | open (stacked on M1.14) | 1.R3 | #47 |
| M1.R3b Security, release and docs | built (live checks pending; stacked on M1.R3a) | 1.R3 | #48 |
| Candidate family R recorded (not approved) | done | Candidate family R | #38 |
| **M2 Sources** | **in progress** | M2 proposal | #32 |
| M2.1 Polling-source framework | done | 2.1 | #33 |
| M2.2 Weather | done (7-day gap check passed) | 2.2 | #34 |
| M2.3 News | built (recording; the 7-day gap check pending) | 2.3 | #35 |
| M2.4 Rules and calendar | not started (after M1.R3) | — | — |
| M2.5 to M2.10 | not started | — | — |
| M3 to M7 | not started | — | — |

**Open with the owner:**
- The two-monitor layout check (M1.8b-2).
- The SignPath application ([CODE-SIGNING.md](CODE-SIGNING.md)).
- M2.3's news gap check (due 2026-11-02 evening), then the stack's merges in order: #35 → #36 → #37 → #47 → #48.
- Momentum v1's week ends about 2026-11-04, then the Strategy review: whether to register a v2 (M1.R3 C1 and C5; the
  fee unit, §9).
- M1.R3b's live checks on its test release (published 2026-11-02).

## 9. Parking lot

Ideas and findings set aside, grouped by where they come back. Scope is frozen (D21): an item returns only through a
milestone proposal or a slice plan the owner approves.

**During M2: momentum v1's review (about 2026-11-04)**
- Momentum v1's record after a week decides whether a v2 is registered (owner, 2026-10-28), read through the
  scorecard and later the Strategy review (G19). A v2 takes M1.R3 C1 (an empty side's false mid) and C5 (a window
  across an outage); first, a read-only replay of v1's week counts the recommendations that rest on a false mid.
- The fee unit in scores and unrealised P&L (M1.R3 A6): Kalshi charges the owner's account at $0.0001, IKARS rounds
  each leg up to a cent (conservative). Kept in cents through v1's review (owner, 2026-11-02); decided with v2.

**M3 Signals proposal**
- Candidate family **R. Reversion / fade** (D13; owner, 2026-11-01), with its variants: averaging down, the scale-in
  ladder, trading out, and repeated round trips. Ideally with other-venue odds (G7); first run as watch orders and
  paper fills (D14). A ladder whose adds grow is a martingale, which the sizing cap (G2) must forbid; if R and
  momentum are both approved, the Strategy review compares their records on the same markets.

**M5 Evaluation**
- Restore from older copies (M1.R3 A9): the drill and `restore` use only the newest daily copy; fall back to older
  dailies, then monthlies, and restore only checkpoints at or below the restored head.
- A rollback whose install fails **after** IKARS has exited (Velopack's `Update.exe`, files held): the newer program
  starts on the older data (M1.R3b merge review, sub-agent 2). A "rolling back" marker read at start, which puts the
  set-aside data back and alerts, would cover it; with M5's restore work.
- A rollback keeps the newer version's own `app` records (IKARS's events, not Kalshi's) only in the ledger copy set
  aside in `backups\rolled-back\`: M5's per-year tax package should include such copies (M1.9b drill).
- Whether wash-sale rules reach event contracts (a laddered position closes lot by lot under FIFO, so one exit can
  close a first lot at a loss and a later one at a gain): a question for M5's tax export.

**M7: the security review before live trading**
- (M1.R3b merge review, sub-agent 1) A program that takes the terminal's port while the engine is down and lets it
  go before the engine starts again (an update, the watchdog's gap, a stop with windows open) keeps any cookie the
  windows sent it, as the engine then gets its own port and rotates nothing; a window that loads a page from it (a
  reload, a layout window) could also register a service worker in the IKARS Edge profile that outlives a new key.
  Narrowing: `ikars open` clearing the profile's service workers while Edge is closed, the page unregistering any at
  load, and an engine proof (a nonce the engine signs) before any request that carries the cookie. Needs a hostile
  process on the workstation in that window; recommend-only.

**Unscheduled (each with its trigger)**
- Remote read-only view from the laptop or phone (G20).
- Social sentiment sources such as X and Reddit (G21), as a source plugin.
- Engine on the server host (D2).
- Further source plugins (D5 options A, D, E; paid news APIs).
- An MSI build (WiX), if a true MSI is ever needed (D12).
- An index on the ledger's `(stream, ticker, seq)` for the Market screen's "your ledger records" (M1.8a merge
  review): it needs a ledger migration (backup first), and the scan is trivial at a few hundred records. When the
  ledger reaches tens of thousands.
- One `times` module for the duplicated time helpers (`api.utc_text` and `ledger.iso_utc`; `alerts._ns` and
  `ledger_import.ns_of`; 1.R2 item 10). Still duplicated (checked 2026-11-02); with the next slice
  that changes time handling.
- A fresh engine refreshes only the standard key variable names (`IKARS_KALSHI_*`) from Windows: with custom names in
  the settings, a key set since the old engine started needs a sign-out (M1.9a merge review 4).
- A layout saved from a New window popup keeps the popup's outer size, including Edge's address strip, so reopened
  as an app window its content is that strip taller (M1.8c merge review 8). Small; if it shows.
- Panes docked, floated and moved within an IKARS window (the Layout mock-up's caption): M1.8b's layouts are sets of
  windows instead (owner, 2026-10-27); a docking library would come back through a proposal.
- Readable screen-reader names for list items: the lists expose each record's raw text to UI Automation (M1.8a live
  check). With the next slice that reworks the terminal's lists.
- Rare feed and replay timings (M1.R3 B8, B9, B10): a rebalance during a refused resubscribe can open a second book
  subscription; a lagging pump can stamp an hour's checkpoint after the hour; a checkpoint between deltas of one
  clock makes a replay skip the second. Each needs a rare timing or an unconfirmed Kalshi behaviour; if one is seen.
- The Markets list's sort (M1.R3 B5): when volumes move, the next Markets request re-sorts the whole index on the
  loop (about 280 ms at 560k markets, at most once a minute). A sort off the loop, or by open markets only, when the
  Markets screen is next worked on.
- Profile the engine's loop with an open terminal (M1.R3 F2): about 20 to 35 ms of loop time each second follows an
  open window, though each part of its pass measures well under a millisecond; a sampling profiler (`py-spy`, a new
  development tool) would say where. Reply p99 stays near 30 ms, inside D20 J's 33 ms.
- Remove the `pnpm audit` exception for GHSA-g3hv-75wg-m249 (minimatch < 11.0.1, lint tools only; owner,
  2026-11-01) in `web/pnpm-workspace.yaml` once ESLint and typescript-eslint accept minimatch 11 and that release
  passes the 7-day rule; Dependabot shows it.
