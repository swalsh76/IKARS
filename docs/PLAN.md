# IKARS plan

**Status (2026-10-26): M0 approved (D1 to D21); scope frozen. The final M1 slice list (§4.1) and gates (§4.2) await
the owner.** Nothing below is approved until it is marked so. Progress is tracked in §5 (milestone and slice table); how each slice was built and verified goes in
[BUILD-LOG.md](BUILD-LOG.md).

## 1. Goal

A Windows 11 desktop system that watches **external news and data sources** in real time, relates them to open
**Kalshi** markets, and shows **trade recommendations in a GUI**. Each recommendation carries a potential
profit/loss that includes **fees and other cost-basis adjustments at both entry and exit**. Market data, source data
and recommendations update live.

Requirements from the owner (2026-10-26):

| # | Requirement |
|---|---|
| R1 | Infrastructure based on **KAPS** (`github.com/swalsh76/KAPS`); user interface based on **KalTerm3** (`github.com/swalsh76/KalTerm3`). *Since 2026-10-27 (D1 amended) the terminal is a TypeScript web app: KalTerm3 remains a reference for behaviour (layouts, risk layer, order routes), not for code.* |
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
- Every recommendation is **journalled and scored against outcomes** (M5) before anyone relies on it, and the GUI
  shows expected value **net of fees, spread and funding**, never a gross number.

## 2. Hardware and roles (inherited from KAPS; machine names by role)

| Role | Hardware (summary) | Role in IKARS (D2, approved) |
|---|---|---|
| **Workstation** | Ryzen 7 9800X3D, 64 GB, RTX 5070 Ti 16 GB, Windows 11 | Runs the IKARS **engine** and the **terminal** (GUI). |
| **Server host** | i7-8700, 32 GB, Ubuntu 24.04, PostgreSQL 18 + TimescaleDB | KAPS ingest keeps recording; IKARS may **read** it through a read-only role (optional). |
| **Analytics node** | Mac Studio M4 Max, 36 GB, external 2 TB SSD | Holds the KAPS/SPARQ history (Parquet); source for back-tests (optional, R7). |

## 3. Milestone M0: foundation decisions

Each decision lists the recommendation first. The owner approves, amends or rejects each one.

### D1. Language and stack: Python engine + TypeScript web terminal (APPROVED 2026-10-26; terminal AMENDED 2026-10-27)

**Amendment (owner, 2026-10-27):** the terminal is a **TypeScript web app** (React, Vite, strict TypeScript, pnpm),
served by the engine on its loopback address and opened as a **Microsoft Edge app window** with its own IKARS
profile (`msedge --app`). The owner chose it for flexibility and future expansion and customisation. The .NET WPF
terminal built in M1.8a was retired; it is kept in history at the tag `wpf-terminal-final`, and the behaviour it
settled carries over (BUILD-LOG 1.8w). Alternatives weighed (BUILD-LOG 1.8w): staying with WPF; Avalonia; WinUI 3;
Blazor Hybrid; a .NET or Tauri host around WebView2. The original text follows for the record.


- **Engine (Python 3.13, `uv` workspace):** forks `kaps-core` as `ikars-core` (signing, REST, WebSocket,
  fixed-point money, fees; already portable). Python has by far the best libraries for the new work: feed and
  web ingestion, text processing, LLM SDKs, Polars/DuckDB analytics.
- **Terminal (.NET 10, WPF):** forks KalTerm3's `KalTerm.Terminal` (views, MVVM, charts, toasts, layouts),
  `KalTerm.Client` (reconnecting streams) and `KalTerm.Contracts`. Native Windows 11 look (WPF Fluent theme), low
  overhead, already proven.
- **One source of truth for money:** fees, P&L and recommendations are computed **only in the engine**; the terminal
  displays them. No fee logic is duplicated across languages.
- *Alternatives (not chosen):* (a) **all .NET**, reusing all of KalTerm3; (b) **all Python** with a Qt (PySide6) GUI.
- *Superseded 2026-10-27:* the `.NET 10, WPF` terminal bullet above (see the amendment). The **one source of truth
  for money** stands, and matters more in TypeScript: the web terminal does no money arithmetic; it formats the
  engine's integers with integer operations only.

#### Terminal navigation model (owner, 2026-10-26: R9)

The terminal is organised as **Overview → drill-down → cross-reference**. Proposed shape, to be drawn as mock-ups
and approved in M1 before any screen is built:

- **Overview (main screen):** one glanceable dashboard. It shows the top recommendations ranked by expected value
  net of costs, live movers (momentum), the latest source items with the markets they touch, perp funding, the
  journal's scorecard, and feed/engine health. Every element on it is a link into a drill-down.
- **Drill-down:** going one level deeper at a time, with a breadcrumb trail and back/forward history.
  - Markets path: category → series → event → market → recommendation (the entry/exit P&L scenarios, order book,
    chart and the evidence behind the signal).
  - Sources path: source → item → the markets it is linked to.
  - Strategies path: strategy family → signal → the recommendations it produced → their outcomes.
- **Cross-reference:** any selected item (market, source item, recommendation, perp) opens a linked-items panel.
  - For a market: the source items that moved it, related markets (same event, correlated series), a perp hedge,
    and past recommendations with their outcomes.
  - News items are drawn on the price chart's timeline.
  - Selection stays in sync across open panels.
- **Implementation (for reuse):**
  - Every item has one typed reference (kind plus id).
  - The engine keeps one generic **link graph** and serves it through one cross-reference endpoint, so a new
    source or strategy adds links rather than new screens.
  - The terminal has one navigation service, and every view model resolves itself from a reference.
  - KalTerm3's markets list, market detail, order book, chart and layout code are reused inside this frame.

### D2. Topology: single box on the workstation (APPROVED 2026-10-26)

- Engine and terminal both run on the Windows 11 workstation. The terminal starts and supervises the engine as a
  child process in v1 (easiest to deploy); running the engine as a Windows service is a later option.
- **Change from KAPS's rule** ("the workstation holds no Kalshi key"): IKARS needs a **read-only (data) Kalshi key**
  on the workstation for live market data. **No trading key** until the live-trading milestone (D6, M7).
- The engine stays OS-neutral Python, so it can move to the server host later without a rewrite.

### D3. Engine-to-terminal link: loopback HTTP + Server-Sent Events, JSON (APPROVED 2026-10-26; SSE 2026-10-27)

- Engine serves `127.0.0.1` only: HTTP for snapshots and commands, one **Server-Sent Events** stream for pushed
  **deltas** (markets, source items, recommendations, health), with subscriptions changed over HTTP (owner,
  2026-10-27: amended from one WebSocket, then so KalTerm3's tested SSE client and reconnect code could carry over; since
  the D1 amendment the web terminal reads the stream with `fetch` and keeps that client's rules, not its code). Server: Starlette/uvicorn; serialisation with `msgspec` (fastest
  validated JSON in Python).
- The terminal coalesces updates and repaints at a fixed rate (about 5 to 10 Hz) so the UI stays light under bursts.
- Contracts are defined once in the engine and exported as JSON Schema. *Amended 2026-10-27:* the TypeScript types
  are **generated** from the schema, and a TypeScript test reads every golden example with runtime validation
  (unknown fields refused), so neither side can drift silently.
- **The web terminal (amended 2026-10-27):**
  - The engine also serves the terminal's static files, so the page and the API share one loopback origin (no
    cross-origin rules).
  - **Sign-in:** the launch link carries a **one-time code**; the engine exchanges it for an `HttpOnly`,
    `SameSite=Strict` cookie, so the session token is never readable by page scripts. The guard
    accepts that cookie or the bearer token, and a browser `Origin` only when it is the engine's own loopback
    origin. Loopback only, as before. *Corrected 2026-10-27 (M1.8a-web-1a):* browsers scope cookies by host, not
    port, so another server on 127.0.0.1 visited in the same browser profile would receive the cookie; the
    terminal's own Edge profile, which visits nothing else, is what confines it. The port is fixed in the
    settings (47821) and the cookie is signed with a DPAPI-protected key, so a restart keeps the terminal signed
    in.
  - The stream is read with `fetch` (not `EventSource`), so reconnects and the alert resume work as in M1.8a.
  - **Browser hardening (owner, 2026-10-27).** Text from outside (market titles, and from M2 news and LLM output)
    is drawn in a page holding the API session, so script injection is the main threat:
    - A strict **Content Security Policy** from the engine: scripts only from the engine's own origin (no inline
      script, no `eval`), no frames or plugins, `connect-src 'self'`, `frame-ancestors 'none'`; any style
      exception a library needs (AG Grid's inline styles, if so) is named in the slice's BUILD-LOG. Also
      `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store` on API replies.
      *M1.8a-web-2a:* the one exception is `font-src 'self' data:`, for AG Grid's icon font (a font runs no code);
      scripts and styles stay `'self'` only, by AG Grid's CSS-file theme and Lightweight Charts' logo turned off.
    - The sign-in code is **single-use and expires in seconds**. A request that changes anything must carry the
      engine's own `Origin` and `Sec-Fetch-Site: same-origin`.
    - The IKARS Edge profile runs with **extensions off** (they can read the page).
    - Outside links (news, from M2) open through the engine, `https:` only.
- *Alternative:* gRPC with Protocol Buffers (strong cross-language typing and streaming, but more tooling and it
  discards KalTerm3's stream client; that client is no longer used either way since the D1 amendment).

### D4. Local storage (APPROVED 2026-10-26: option C)

IKARS writes three kinds of data, and they pull in different directions:

1. **High-rate raw feeds:** order-book deltas, ticker frames and trades. This is thousands of rows a second at
   bursts, append-only, and read later for back-tests.
2. **Operational state:** source items, market links, signals, recommendations, watch orders, the outcome journal,
   settings, and later real orders and fills. These are frequent small transactional writes. They must survive a
   crash, and the API reads them while the engine writes.
3. **Analysis:** back-tests and reports over gigabytes (the 77-day history, recorded feeds), mostly columnar scans.

Historical Kalshi data is read from SPARQ/KAPS Parquet copies when present (R7); nothing depends on it.

| Option | Raw feeds | Operational state | Analysis | Other processes reading while the engine runs | Deployment | Verdict |
|---|---|---|---|---|---|---|
| **A. DuckDB only** | Good (appender) | Fair: single-row transactions are much slower than SQLite's | Excellent | **No:** one read-write process locks the file; other processes can open it read-only only when no writer has it | One file, pip package | Simple, but notebooks and tools are blocked while the engine runs, and it is weak at small transactional writes |
| **B. SQLite only (WAL)** | Fair: fine with batching, but the file grows fast and large scans are slow | **Excellent:** durable (`synchronous=FULL`), mature, readers do not block the writer | Poor at GB-scale scans | Yes | Built into Python and .NET; nothing to install | Ideal for state, wrong tool for analysis |
| **C. SQLite (state) + Parquet (raw feeds) + DuckDB as the query engine (recommended)** | **Excellent:** hourly Parquet files (Arrow IPC for the open hour, then compacted to Parquet), the same layout SPARQ uses | **Excellent** (SQLite) | **Excellent:** DuckDB queries Parquet directly and can attach the SQLite file read-only | **Yes:** Parquet and SQLite can both be read while the engine writes | Two embedded libraries, no service | Each tool does what it is best at; the format matches SPARQ/KAPS history, so back-tests use one code path |
| **D. PostgreSQL + TimescaleDB on the workstation** | Excellent | Excellent | Good | Yes | **Poor:** a Windows service, admin install, upgrades and backups. It breaks the portable zip and user-space install (D12). | KAPS's choice for a server, too heavy for a desktop app |
| **E. The KAPS database on the server host** | (KAPS records no book deltas) | Remote writes would need a write role on KAPS's production database | Good | Yes | Depends on the server and the network | Contradicts D2 (single box) and KAPS's maintenance-only rule; read-only use stays optional (D17) |

**Recommendation: C.**
- Live books and the working set stay in memory.
- SQLite runs in WAL mode with full sync for anything the journal or, later, real trading depends on.
- Parquet holds everything high-rate.
- DuckDB is used as a library for queries and back-tests, not as a store.
- *Small risk:* the open hour's buffer (Arrow IPC) can lose its last unflushed batch on a hard crash. That costs
  seconds of raw feed, which Kalshi cannot resend anyway, and the gap is logged as in KAPS.

### D5. First sources and market focus (APPROVED 2026-10-26: B + C)

| Option | Source (cost) | Kalshi markets | Notes |
|---|---|---|---|
| **A** | BLS, BEA, FRED, Fed calendars; nowcasts such as the Cleveland Fed inflation nowcast (free) | Economics (CPI, jobs, rates) | Release moments are a speed race; the edge, if any, is in pre-release estimates. |
| **B** | NWS / NOAA `api.weather.gov` forecasts and station observations (free) | Weather (highs, rain) | Settles on station values; the KAPS review's option A. Structured, hourly, a good first fit. |
| **C** | General news: RSS/Atom feeds and GDELT (free), with an LLM to link items to markets and estimate impact | Politics, mentions, culture, the long tail | The "news" part proper; human-paced markets; LLM cost to budget. |
| **D** | Live scores / odds feeds (mostly paid) | Sports | Fast-moving; professionals dominate. |
| **E** | Exchange spot prices (Coinbase/Kraken WebSocket, free) | Crypto ranges | Fair value from spot and volatility; the KAPS review's option B. |

The recommendation is **B** (structured, measurable) and **C** (the general news pipeline) in v1, built on one
source-adapter interface so that A, D and E are later adapters, not rewrites. **E** moves up if perpetuals (D13 P)
or crypto sniping is chosen early, since spot prices are their fair value. Kalshi's own live feed (ticker, trades,
order book) is always a source: it drives momentum and sniping.

### D6. Recommend-only first, live trading later (APPROVED 2026-10-26; amended the same day)

- **M1 to M6: recommend-only.** IKARS recommends; the owner trades on Kalshi by hand. No trading key, no order
  routes, no order sending.
- Consequence for sniping (S): sniping recommendations are delivered as **fast alerts** (a toast plus an Overview
  highlight, with the price, size available and P&L after costs). The journal records **how long each opportunity
  lasted**, so M5 shows whether there is time to act on them by hand.
- **Eventual goal: live trading in the app (owner, 2026-10-26).** Custom order types (D14) and recommendations
  become real orders in a later milestone, **M7**: one-click confirmed orders first, then armed automatic rules with
  hard caps. M7 gets its own proposal and approval, with:
  - the trading key on the workstation (DPAPI vault)
  - KalTerm3's risk layer and its fixed order routes
  - a kill switch
  - an audit log
  - KAPS's order-write lessons (never retry an unclear write; look the order up by `client_order_id`)
- **Built for it from the start:** every action goes through one **executor interface**. M1 to M6 use a paper
  executor that fills against the live book; M7 adds the real one behind the same interface, so watch orders,
  paper fills and real orders share one code path.
- **No real order is ever sent without the owner's go-ahead.**

### D13. Strategy families (APPROVED 2026-10-26, order as recommended)

All share one pipeline: **live feeds → signals → fair value / edge → recommendation with net P&L → journal**. Each
family is a signal module, not a separate system.

| Family | Edge over humans | Data | Notes |
|---|---|---|---|
| **S. Sniping** | Seconds faster than humans to hit a quote that is stale against a fresh fair value (a related market moved, a data point landed, spot moved). | Kalshi order book (`orderbook_delta`) and ticker; the fair-value source. | Needs the live book (KAPS records no deltas). Delivered as fast alerts (D6). |
| **M. Momentum** | Humans under-react to information and then chase; trend and volume-surge entries with defined exits. | Kalshi ticker and trades; KAPS ingest (since 2026-10-24) and the 77-day history for back-tests. | Fees eat small moves: the P&L engine decides which moves are worth taking. |
| **N. News/data estimates** | Better-informed probability than the crowd on human-paced markets. | Sources in D5. | The original IKARS idea. |
| **P. Perpetuals** | Funding carry (BTC funding positive about 52% of intervals, about 9% a year in the KAPS review), momentum on perps, and hedging binary positions with a perp in the same account. | Kalshi perp endpoints; exchange spot (D5 option E). | New Kalshi product: API, fees, margin and funding rules to be verified first. |

Approved order: **N + M first** (back-testable on existing data), **S** once the live book is in the engine, **P**
after a read-only perp research slice confirms the API and costs.

### D14. Custom order types (R10; APPROVED 2026-10-26)

**Native in Kalshi** (from KalTerm3's trading client and KAPS's plan; to be re-checked against the API reference in
M1): limit orders with good-till-cancelled, immediate-or-cancel or fill-or-kill; post-only; reduce-only (IOC only);
an expiry time (GTC only); self-trade prevention.

**Custom (in-app)**, all built from one **trigger → action** rule engine, so each type is a configuration rather
than separate code:

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

- **M4 to M6:** these run as **watch orders**. When the trigger fires, the engine raises a fast alert with the exact
  action and its P&L after costs. It also **paper-fills** the action through the paper executor against the live
  book, so the journal shows what each custom type would have earned. No order is sent.
- **M7 (owner, 2026-10-26: they are to become live trade functions):** the same rules drive real orders through the
  real executor (D6), each type enabled only once its paper record is reviewed with the owner.
- **KAPS lesson:** custom orders exist only while the engine runs. A stop that is not on Kalshi's book protects
  nothing while the workstation is off, and the terminal must say so on every custom order. For M7 this also means
  the engine should be able to run on its own, without the terminal open (a Windows service or a tray process).

### D15. GPU use on the workstation (R11; APPROVED 2026-10-26)

The RTX 5070 Ti (16 GB, Blackwell) is used where it measurably beats the 9800X3D, and only there. Everything has a
CPU fallback, so the app also runs without CUDA.

| Use | Gain | Library (to confirm in the slice plan) |
|---|---|---|
| **Text embeddings** for linking news to markets, de-duplicating items, and an index over about 150k market titles | Large: re-embedding all market titles takes seconds instead of minutes; live items embed at once | ONNX Runtime (CUDA) for inference, which is smaller to ship than full PyTorch; sentence-transformers models |
| **Model training** for momentum and order-book models (D16), and parameter sweeps | Large for sweeps and sequence models | XGBoost (CUDA) or LightGBM; PyTorch for sequence models |
| **Back-test and Monte-Carlo sweeps** (P&L distributions, rule grids) | Large for wide grids | PyTorch or CuPy tensors |
| A local first-pass news classifier (optional) | Saves LLM calls | Small fine-tuned classifier first. *Note:* a local LLM was trialled for dev tooling in KalTerm3 (2026-10-18) and removed. This would be a product use, raised only because the owner asked about the GPU. |

- **Not on the GPU:** the real-time event loop, order-book upkeep, fees and P&L (exact integers, tiny), and the UI
  (the browser already renders on the GPU; WPF, the terminal until 2026-10-27, rendered through DirectX).
- **Checks first:** PyTorch needs CUDA 12.8 or later for Blackwell (`sm_120`). RAPIDS (cuDF, the Polars GPU
  engine) has no native Windows support, so Polars and DuckDB stay on the CPU.
- **Deployment:** the GPU packages are an **optional extra** (CUDA wheels run to gigabytes), installed only on
  machines with an NVIDIA GPU; the engine picks the device at start-up and reports it in health (detection: M1.6).
  Every runtime, library, driver and service IKARS needs, now and later, is kept in
  [DEPENDENCIES.md](DEPENDENCIES.md) for the install instructions.

### D16. Live order-book analysis (R12; APPROVED 2026-10-26)

The engine keeps live books from Kalshi's `orderbook_delta` channel (a snapshot, then deltas checked by `seq`; a
gap triggers a resync). This needs authentication, which the read-only key provides. Books are kept for a
**watched set**: the most active markets, markets with live signals or recommendations, and markets the owner opens.
The ticker covers everything else. Raw deltas are written to hourly Parquet files for back-tests.

Patterns to measure. Each one is a **hypothesis**, scored in the journal before it may raise a recommendation:

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
- **Limits:** the per-subscription market limit is not documented (error 26), so it is measured live. Deltas for
  every market at once are not the goal.
- SPARQ's planned book recorder (S1.2b) and its refill measure (S1.2a) do the same jobs; D17 says how they are
  reused.

### D17. Using SPARQ's work (R13; APPROVED 2026-10-26)

- **Code base.** Fork `ikars-core` from **SPARQ's `sparq_core`** rather than KAPS's `kaps_core`. It is the same
  KAPS code plus SPARQ's later fixes (for example, a failed ticker resubscribe now ends at the next reconnect), and
  it will gain book models in S1.2b. The KAPS lineage is unchanged.
- **Reused modules (read critically, BUILD-LOG notes what was kept or changed):** `sparq_analytics.live`
  (top-of-book measures, markouts, refills, queue fill rules); `study` (clustered bootstrap, calibration, fee-aware
  edge, rule registration and the forward ledger) for M5's evaluation.
- **Findings used as starting points, not results:**
  - Feed timing: ticker frames about 1 s apart; the feed reaches the server about 0.55 s after Kalshi's timestamp;
    a ticker frame follows 99.95% of trades within 1 s. These set the sniping latency budget.
  - The middle-tier and forward-rule filters (quiet 60 s to 1 h, activity 1k to 100k contracts, price 20 to 80
    cents) are where human-paced trading was found, so they are the first market filter for momentum and sniping.
  - The order-write and kill-switch lessons are kept for when D6 is ever reopened.
- **Data:** the 77-day history and SPARQ's live copies (Parquet on the analytics node) are copied to the workstation
  for back-tests, with no database credential needed. A read-only database role for IKARS on the KAPS database (as
  SPARQ has) is optional and would need the owner to create it.
- **Guard: do not contaminate SPARQ's forward test.** Until SPARQ's S1.1b verdict, IKARS computes no edge to
  settlement on events opened from 2026-10-26 that close before 2026-11-23 (the window closes 2026-11-23; the
  verdict is due from about 2026-11-26). Book measures and short markouts are allowed, as in SPARQ's D11.

### D7. P&L model, net of costs (APPROVED 2026-10-26: the cost-basis factors as listed)

Computed in the engine with fixed-point integers (dollars at 10^-6, counts at 10^-2), reusing `kaps-core`'s fee
arithmetic. For each recommendation:

- **Entry:** price (taker at the best ask, or maker at a limit), entry fee per the series' `fee_type` and
  multiplier (taker 0.07·C·P·(1−P) rounded up; maker share where the series charges one), and spread paid against
  the mid.
- **Exit scenarios:** hold to settlement (payout $1 or $0, no exit fee); sell at a target (exit fee, taker or maker);
  stop/early exit at the current bid (exit fee).
- **Outputs:** cost basis, max gain, max loss, **expected value net of all fees** at the model probability,
  break-even probability, return on capital, and the edge in cents over the market price.
- Other cost-basis adjustments to confirm with the owner: interest on balances/positions if applicable, and
  rounding of the balance to $0.01 for non-direct members (`kaps-core` models this conservatively).
- **Perpetuals** add: perp trading fees, **funding payments** over the expected holding period (8-hourly for crypto,
  daily for index/commodity perps, per the KAPS review; to be verified against Kalshi's API in M1), margin
  requirement and its cost, and a liquidation price shown with the worst case. `kaps-core` has no perp models yet;
  they are new code with contract tests against live responses.
- **Sniping/momentum** add: expected **slippage** (depth at the target price from the live book) and the chance the
  quote is gone before the order lands, so the P&L shown is for the size actually available.

### D8. Keys and secrets (APPROVED 2026-10-26)

- Kalshi read-only key: the owner's existing User environment variables (`KALSHI_KEY_ID`,
  `KALSHI_PRIVATE_KEY_PATH`), as in KAPS. LLM API key (if C is chosen): stored with Windows DPAPI (KalTerm3 has a
  vault for this). Never printed, logged or committed.

### D9. LLM use (APPROVED 2026-10-26, with a local-model option)

- **One LLM provider interface** with two kinds of backend, chosen per task in the settings:
  - the **Claude API**: a fast, cheap model as a first-pass filter, and a stronger model only for candidate matches
    and impact estimates;
  - a **local model on the RTX 5070 Ti** (owner, 2026-10-26). A quantised model of about 7 to 14B parameters fits
    in 16 GB. It would first be used for the high-volume first pass (is this item relevant, and to which
    categories), so the cloud is called only for candidates. The runtime (llama.cpp server, Ollama or ONNX Runtime
    GenAI) is chosen in the slice plan by measured speed and quality against the Claude baseline on a labelled
    sample.
- A daily spend cap on cloud calls is enforced in the engine; every LLM verdict is journalled with its provider, so
  M5 compares local and cloud quality.
- Candidate retrieval before any LLM call (keyword index plus GPU embeddings, D15) keeps calls few.
- The local model is an optional extra in the release (D12), like the GPU packages.

### D10. Repository: public (APPROVED 2026-10-26)

- New git repository **`swalsh76/IKARS`, public, MIT**, like the siblings (owner's reason: unlimited GitHub
  Actions minutes for public repositories, Windows runners included).
- Consequences, the same rules as KAPS and SPARQ:
  - Never commit secrets, keys, `.env` files or personal data.
  - Never commit real host names, addresses or share paths.
  - Never commit **Kalshi data extracts** (`*.parquet`, `*.duckdb`, `*.sqlite`), because the Developer Agreement
    limits storing and sharing API data.
  - Never commit **fetched news content**, because of source licences.
  - Fixtures are small, synthetic or hand-written.
  - Commit with the GitHub no-reply address.
  - The Developer Agreement's §6.4 (prior approval for public statements about Kalshi) stays as KAPS handled it:
    set aside by the owner and not reopened unprompted.
- The repository is created in M1.1, after its slice plan is approved; creating it is the first outward-facing step.

### D11. Quality gates (APPROVED 2026-10-26)

- Python: `ruff`, `mypy --strict`, `pytest` (90% coverage; 95% for money and fees).
- **Web terminal (amended 2026-10-27):** TypeScript `strict`, ESLint, Vitest with **90% coverage** enforced by its
  thresholds, Playwright for whole screens, `pnpm audit` and a frozen lockfile in CI, as a required `web` check.
  They start with M1.8a-web, the first terminal code since the prune. *Added by the owner 2026-10-27:*
  - ESLint **bans** `dangerouslySetInnerHTML`, `innerHTML`/`outerHTML`/`insertAdjacentHTML`, `eval` and
    `new Function`.
  - **npm supply chain:** Dependabot for npm; pnpm's install-script block with a named allow-list
    (`onlyBuiltDependencies`); a **7-day waiting period** before a new release is used (`minimumReleaseAge`, and the
    same rule for CI actions); few runtime libraries, each with its licence in DEPENDENCIES.md.
  - **Money in TypeScript:** JavaScript numbers are floats, so the terminal does **no** money arithmetic; it formats
    integers through a distinct money type, with a test.
  - Playwright traces, screenshots and reports, and `.env*` files, are ignored and refused by the data guard (they
    can hold live data).
- **CodeQL** (owner, 2026-10-27): static security analysis (`security-extended`) on every pull request, on `main`
  and weekly, for Python and the CI workflows; TypeScript joins with M1.8a-web's first code.
- *Retired 2026-10-27 with the C# code:* C# warnings as errors, xUnit v3 and its coverage gate (CI's `dotnet` job). CI on GitHub Actions **windows-latest** (the target platform). Live Kalshi tests GET-only, as
  in KAPS.
- **Rolling mini code reviews (R14, owner instruction 2026-10-26, APPROVED):** besides tests and CI, every commit
  and every merge gets a small, focused review, so large periodic whole-codebase reviews are rarely needed.
  - **Per commit:** review the commit's diff before committing, checking:
    - correctness and edge cases
    - money stays in fixed-point integers
    - reuse: no duplicate of existing code, and shared code goes in the shared module
    - efficiency on the hot path (in the terminal, the render path: re-renders and allocations per update)
    - simplification
    - tests cover the change
    - no secrets, keys, real host names or paths
    - *(terminal, 2026-10-27)* outside text is drawn only as text, never as markup or a link it chose
    - *(2026-10-27)* a new dependency: licence, release age, install scripts
  - **Per merge:** review the whole branch diff against `main`, plus the modules it touches and their callers,
    checking:
    - consistency across the slice
    - contracts between the engine and the terminal stay in step
    - drift from the PLAN decisions
    - docs and guides are current
  - **How:** the `/code-review` skill on the diff (a low effort level per commit, medium per merge) plus a reuse
    and simplification pass.
  - **Security reviews (owner, 2026-10-27):** a `/security-review` before merging M1.8a-web (the sign-in and the
    file serving), and again before M7.
  - **Findings:** each finding is fixed in the same branch or parked in §9 with its reason. The BUILD-LOG slice
    entry gets a **Review** heading listing the findings and what was done about each.
  - A full-codebase review is still held at each milestone's close-out, and should then be short.

### D12. Deployment: portable zip and user-space installer (APPROVED 2026-10-26: Velopack)

The owner wants two downloads, as many GitHub projects offer:
- a **portable** zip that runs from any folder;
- an **installer** (exe/msi) that installs to the user's profile with no admin rights.

Both are built by CI from a tag and published to GitHub Releases.

**What goes into either package:**
- **Engine:** a standalone CPython (python-build-standalone, the build `uv` itself uses) with the locked packages
  pre-installed. Nothing is frozen (unlike PyInstaller), so there are no import quirks and no antivirus false
  alarms from frozen executables.
- **Terminal:** `dotnet publish` self-contained for `win-x64`. *Amended 2026-10-27:* the web terminal's built static
  files, served by the engine. No .NET runtime is shipped; Edge (part of Windows 11) shows the app window.
- **Optional extras:** the GPU packages and the local model (D9, D15) are downloaded on demand from the terminal's
  settings, never bundled, so the base download stays small.
- Settings, data and logs go under `%LOCALAPPDATA%\IKARS`, never in the program folder. The portable build instead
  keeps them beside itself when a `portable.marker` file is present.

**Installer tooling (proposed):**

| Tool | Output | Per-user, no admin | Updates | Licence | Notes |
|---|---|---|---|---|---|
| **Velopack (recommended)** | `Setup.exe` **and** a portable zip from one command | Yes (`%LOCALAPPDATA%`) | **Built-in auto-update and delta updates from GitHub Releases** | MIT | Successor to Squirrel; packages any folder, so the Python engine rides along. Whether its MSI output fits is checked in the slice. |
| WiX Toolset | MSI | Yes (per-user MSI) | None built in | MS-RL | The standard if a true MSI is needed (for example for managed deployment); more authoring work. |
| Inno Setup | exe | Yes (`PrivilegesRequired=lowest`) | None built in | Free (Inno licence) | Simple and proven; the zip is built separately. |
| MSIX | msix | Yes | Through App Installer | — | Needs a trusted signing certificate; its sandbox complicates a child Python process. |

- **Recommendation: Velopack** for `Setup.exe`, the portable zip and auto-update. WiX is added only if an MSI is
  required.
- **Code signing:** unsigned downloads trigger SmartScreen warnings. The options are Azure Trusted Signing (paid,
  monthly) or SignPath's free plan for open-source projects. This is decided in the packaging slice.
- **The packaging spike comes early (M1)**, so the hardest deployment risk is retired before features pile up.

### D18. Audit and tax ledger (R15; APPROVED 2026-10-26)

**Purpose.** One permanent, tamper-evident record of everything that moved money in the Kalshi account, and of
every action IKARS took. From it, IKARS produces a **per-tax-year package** that can be filed with a 1040's
records.

*Not tax advice:* how Kalshi event contracts and perpetuals are taxed is a question for the owner's tax adviser.
Possible treatments include capital gains on Form 8949 / Schedule D, Section 1256 contracts on Form 6781, or other
income. The ledger keeps every field each of those treatments needs, so the choice can be made, or changed, later
without re-collecting data.

**Source of truth: Kalshi's own account records, not IKARS's guesses.**
- The engine imports the account's **fills, settlements, fees, deposits and withdrawals, rebates or incentive
  payments, and perp funding** with the read-only key. Portfolio reads are GETs; that the read-only key may read
  them is checked live in the slice.
- This covers the owner's **manual trades too**, so the ledger is useful from day one, long before M7.
- Each record is enriched with IKARS's own context where it exists: the recommendation, signal or custom order that
  led to it.
- **Reconciliation:** each import checks the ledger against Kalshi's balance and position endpoints. Any difference
  is shown in the terminal and in the year's report, never silently fixed.
- Paper fills (D6) are kept in a **separate, clearly marked stream** and never enter the tax package.

**What each record holds:**
- **Identity and position:**
  - sequence number
  - kind (fill, settlement, fee, funding, transfer, adjustment, app action)
  - time in UTC, and in the owner's tax time zone (a setting)
  - Kalshi ids (fill or trade, order, `client_order_id`, settlement)
  - market, event and series tickers, and the market title as shown at the time
  - side and action
  - contracts (counts at 10^-2)
- **Money:** price, fee and amount in **fixed-point dollars at 10^-6**, plus the balance after the record where
  Kalshi provides it.
- **Provenance:** Kalshi's raw JSON for the record, stored verbatim, and the IKARS context ids.
- **Chain:** `prev_hash` and `hash` (SHA-256 over the canonical record), forming a hash chain.
- **App actions** (settings and risk-limit changes, kill switch, key loaded, M7 orders sent) go into the same chain,
  so the trading history and the decisions behind it are one record.

**Tamper evidence and durability:**
- The ledger is **append-only**: corrections are new adjustment records that point to the one they correct, never
  edits.
- The hash chain is checked on every start and by a `verify` command.
- Each day's last hash is written to a **daily checkpoint file**.
- Storage is SQLite (D4) in WAL mode with `synchronous=FULL`, in a separate file from the operational state.
- **Backups go to the same NAS share as the KAPS database backups** (the share SPARQ's data comes from; owner,
  2026-10-26), in IKARS's **own subfolder tree**. The real share path lives only in the local settings, never in the
  repository; docs use `\\<nas>\<share>` and tests use `\\127.0.0.1\ikars-no-such-share`.

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

  - **Safety:**
    - Every write goes to a temporary name, is hash-checked after the copy, and is then renamed.
    - The code resolves every path under `<share>\ikars\` and refuses anything outside it, so nothing of KAPS's
      can be touched.
    - A failed or skipped backup is shown in the terminal's health view.
  - **Retention on the share:**
    - Ledger: daily copies for 90 days, monthly copies and checkpoints forever.
    - Tax packages: forever.
    - State: daily copies for 30 days.
    - The local ledger itself is never pruned.
  - **Not copied by default:** the raw-feed Parquet (large); it can be added later as `ikars\feeds\` if wanted.
  - **Access:** the workstation reaches the share with the owner's own NAS credential (Windows' saved network
    credential), checked in M1.5. Engine and terminal run as the owner, so nothing new is stored.
- The ledger is **never pruned**. IRS guidance is to keep records for at least 3 years, and longer in some cases,
  so IKARS keeps everything and leaves deletion to the owner.

**Tax-year package** (`ikars ledger export --year 2026`, also a button in the terminal):

| File | Contents |
|---|---|
| `IKARS-2026-summary.pdf` (**PDF/A**, the archival PDF standard) | Account, period, totals by kind, realised gain or loss short and long term, fees, funding, deposits and withdrawals, the reconciliation result, the lot-matching method, the ledger's chain head hash. Printable and fileable with the return's records. |
| `IKARS-2026-form8949.csv` | One row per closed lot, in the columns of Form 8949: (a) description, (b) date acquired, (c) date sold or settled, (d) proceeds, (e) cost basis, (f) code, (g) adjustment, (h) gain or loss. Short- and long-term are separate. **Fees are in the basis on entry and reduce proceeds on exit**, the same cost-basis rules as D7. |
| `IKARS-2026-6781.csv` | The same closed positions summarised for Section 1256 treatment (year-end mark-to-market at the settlement or last price), in case the adviser chooses it. |
| `IKARS-2026-ledger.jsonl` | Every ledger record of the year, raw Kalshi JSON included. |
| `IKARS-2026-manifest.txt` | SHA-256 of every file in the package and the chain's start and end hashes, so anyone can later prove nothing was changed. |

- **Lot matching:** FIFO by default, with specific identification selectable before any year is exported. The
  method is printed in the package and cannot change silently for a year already exported.
- **Settlement:** a contract that settles counts as disposed at its payout ($1 or $0) on the settlement date.
- **Year boundaries:** use the owner's tax time zone.
- **Cross-check:** the package is compared with the **Kalshi 1099 or annual statement** when it arrives. A
  `--compare` option reads Kalshi's CSV statement, if Kalshi provides one, and lists any differences.

**Where it is built:** the ledger store, hash chain, `verify` and account import are slice **M1.5** (below), so the
record starts as early as possible. The tax-year package is part of **M5** (needed first for the 2026 tax year,
early 2027). M7 adds the real orders sent by IKARS to the same chain.

### D19. Gap review additions (APPROVED 2026-10-26)

The owner asked "what am I missing"; the answers were approved the same day. Each item lands in the milestone
shown.

| # | Addition | Where |
|---|---|---|
| G1 | **Portfolio and exposure:** open positions from the ledger import, marked to market; unrealised P&L after exit costs; exposure by event and category; balance. On the Overview. Recommendations take existing positions and correlations into account. | M1.5 (model), M1.8 (view), M4 (in recommendations) |
| G2 | **Sizing and risk limits while recommend-only:** suggested size (fractional Kelly, capped by depth at the price); limits per market, event, category and day; a daily loss stop that mutes recommendations. **One risk layer**, reused by M7. | M4 |
| G3 | **Replay harness at the core:** the engine's input is an interface with a live source and a replay source (recorded Parquet, deterministic clock), so back-tests, bug reproduction and tuning run the same code as live. *Partly done (2026-10-27 review):* M1.4 built
the interface and the generic replay; real Kalshi recordings replay from M1.13. | M1.4, M1.13 |
| G4 | **Pre-registration (SPARQ practice):** a rule's settings are locked by hash before it is scored on held-out or forward data; a registry records every rule, its hash and its scoring windows. | M3 |
| G5 | **Uptime on a desktop:** the engine runs as a background process that does not need the terminal; it auto-starts at login, keeps the machine awake while running, logs gaps, and a scheduled watchdog restarts it. (Pulled forward from M7.) | M1.6 |
| G6 | **Workstation alerts** (owner, 2026-10-27: no phone pushes; amended from "phone alerts" with ntfy): Windows notifications on the workstation for engine down, feed stale and backup failed (M1.6), then top recommendations (M4), and in the terminal from M1.8. | M1.6, M4 |
| G7 | **Other venues as fair value:** Polymarket, sportsbook odds (an odds API) and other prediction markets as source plugins; cross-venue mispricing alerts. | M3 |
| G8 | **Settlement rules and event calendar:** parse each market's rules and resolution source for linking; a calendar of releases, games and close times; pre-event alerts; no recommendation that is about to be settled by fine print. | M2 |
| G9 | **Recommendation lifecycle and owner feedback:** expiry, invalidation, de-duplication; taken, ignored or dismissed; the ledger links the owner's fills back to the recommendation. | M4 |
| G10 | **Clock and latency:** server-time skew (KAPS's clock module) and feed-to-alert latency in the health view. | M1.4 |
| G11 | **Storage budget on the workstation:** a governor with a budget, compaction and a health warning. | M1.4 |
| G12 | **Public-repository security:** secret scanning in CI (gitleaks); hash-pinned dependencies with Dependabot; signed release builds; the loopback API token; read-only key rotation; *(2026-10-27)* CodeQL and the web terminal's browser hardening (D3, D11). | M1.1, M1.8, M1.9 |
| G13 | **Interest and other income in the ledger** as their own record kinds (likely 1099-INT), if Kalshi pays them. | M1.5 (amends D18) |
| G14 | **Ledger restore drill:** `ledger restore-drill` restores from the share into a scratch file and checks the hash chain. | M1.5 |
| G15 | **Settings and a first-run wizard:** key environment variables (checked, never shown), the share path, LLM choice, GPU extras, quiet hours, tax time zone. | M1.8, M1.9 |
| G16 | **Speed in the UI:** hotkeys (alert → market in one key), multi-monitor layouts (KalTerm3's), dark and light themes. | M1.3 (mock-ups), M1.8 |
| G17 | **Compliance review:** before M7, review Kalshi's rulebook on manipulation (iceberg, peg, fast cancel/replace), its rule against trading on material non-public information (sources must be public), and the Developer Agreement's §6.4 with a public repository that names Kalshi. Also check **each news source's terms of use** when its adapter is planned. | M2 (per source), M7 (first slice) |
| G18 | Fault and soak testing: **dropped** (owner: proved in SPARQ). Ordinary reconnect and error tests stay in each slice. | — |
| G19 | **Automatic strategy demotion with its own review page:** a strategy, pattern, custom type or LLM provider whose journal turns negative after costs stops producing recommendations. A separate **Strategy review** page in the terminal lists each one's record, demotions and reasons, with promote and demote by the owner. | M5 |
| G20 | Remote (laptop/phone) read-only view: **parked**. | §9 |
| G21 | Social sentiment (X, Reddit): **parked**, and the design must let it be added as a plugin without restructuring (below). | §9 |

#### Plugin architecture (for G7, G21 and every later source or strategy)

- **Engine:** sources, signal modules, custom order types and venues each implement a small typed interface and
  register through Python **entry points** (`importlib.metadata`).
  - A new plugin is a package (in the workspace, or installed beside the engine) and needs no change to the engine.
  - Each declares its settings schema, the secrets it needs, its rate budget and the kinds of item it emits.
  - The engine gives each plugin its own task with bounded queues, timeouts and a crash boundary, so one failing
    plugin cannot stall the others.
- **Link graph:** what a plugin emits is stored as typed items with links (D1's cross-reference model). It then
  appears in the Overview feeds, drill-down and cross-reference panels **without new screens**.
- **Terminal:** generic views render any item kind from its declared fields. A plugin may optionally ship a custom
  panel later; the generic view comes first.
- The first plugins are the D5 sources (M2); M1.4 builds the registry and the interfaces.

### D20. Second gap review (APPROVED 2026-10-26)

| # | Addition | Where |
|---|---|---|
| A | **Point-in-time correctness.** Every source item stores the time IKARS **received** it as well as the publisher's time. The replay source (G3) releases each item only at its received time, so back-tests never see the future. Source plugins start in **record-only mode** from M2's first slice, so the evaluation clock starts as early as possible. GDELT's archive is used for historical news back-tests, flagged as reconstructed (no received time). | M1.4 (fields, replay rule), M2 |
| B | **LLM safety and reproducibility.** Source text is untrusted input:<br>- strictly structured outputs, validated<br>- no tools available to the model<br>- source text kept apart from instructions<br>- an LLM verdict is never the sole basis for a recommendation<br><br>Responses are cached by input hash, with the model and prompt version, so replays are exact and not paid for twice. A cost meter sits beside the spending cap. | M2 |
| C | **Fill probability and adverse selection** for maker entries: the chance a limit fills and the typical move after it fills (SPARQ's markouts); the paper executor models queue position and latency, and is checked against the owner's real fills when they exist. | M3 (model), M4 (in P&L) |
| D | **Capital tied up and time to settlement:** recommendations are ranked by **return per day of capital tied up**; time to settlement is shown; interest earned or forgone is included where Kalshi pays it. Amends D7. | M4 |
| E | **Tradability filters:** Kalshi's per-market position limits and the account's limits (`/account/limits`), market status, the exchange schedule and maintenance windows, and markets the account cannot trade. | M1.4 (exchange status), M4 |
| F | **Provenance on every recommendation:** code version, settings hash, model and prompt version, and the input item ids. Any recommendation can be replayed exactly and explained, and M5 compares versions fairly. | M4, M5 |
| G | **Safe upgrades:**<br>- versioned schema migrations for every SQLite file, with an automatic backup before each migration (the ledger's to the share as well)<br>- versioned settings<br>- a version handshake between the terminal and the engine<br>- an update rollback path<br><br>**An update must never break the tax ledger.** | M1.5, M1.7, M1.9 |
| H | **Alert hygiene:** priority levels; de-duplication and throttling; quiet hours; a digest for low-priority items. Only the top tier interrupts. | M1.6 (framework), M4 (recommendations) |
| I | **Diagnostics and rebuild:**<br>- structured logs with rotation and **redaction** (keys, key paths and account values never reach a log)<br>- a one-click redacted support bundle<br>- a tested **"workstation lost" runbook**: reinstall, restore the ledger and settings from the share, verify | M1.4, M1.8, M1.11 |
| J | **Performance targets, measured continuously and shown in health:** feed-to-alert latency for the sniping path (target under 250 ms, confirmed in M1.4's plan) and the terminal's frame time under bursts. *Amended 2026-10-27 (owner, M1.8a):* the terminal's target is **update to screen**: from the terminal applying a change to the change being on the display, **p99 at most 33 ms** while 100 markets stream at 10 Hz, with the UI work per update shown alongside. Gaps between WPF's frames are not a measure, since WPF draws only when something changed. | M1.4, M1.8, M4 |

### D21. Third gap review and scope freeze (APPROVED 2026-10-26)

| # | Addition | Where |
|---|---|---|
| 1 | **Success criteria and go/no-go gates:** every milestone has a stated "done means" (§4.2). Evaluation uses **pre-registered stop rules**, and there is a project-level rule for when nothing passes. | §4.2, every milestone |
| 2 | **Value early, through a thin end-to-end slice:** momentum on Kalshi's own feed, from signal to a recommendation on the Overview with P&L after fees, journalled with provenance. It needs no news sources and no LLM. It proves the whole pipeline, starts the evaluation clock for the first strategy, and later milestones widen this path rather than building layers in isolation. | M1.12 |
| 3 | **One Kalshi account, three projects:**<br>- a **separate read-only key for IKARS**, so it can be revoked and audited on its own<br>- account-level rate and WebSocket subscription limits measured in M1.2, without the rate-limit ramp, which stays banned<br>- a request and subscription budget, so IKARS backs off before it can starve KAPS's production ingest<br>- sharing SPARQ's order-book recorder considered when both need the same books | M1.2, M1.4b |
| 4 | **Test data policy for a public repository:**<br>- synthetic fixture generators: books, trades, news items, and account histories for the ledger<br>- tests that need real recordings run locally only and skip in CI<br>- only tiny hand-written samples of live data are committed<br>- never committed: real feeds, fetched articles, account data | M1.1 (policy), every slice |
| 5 | **Running-cost ceiling:** one monthly budget across paid services (LLM, odds API, code signing, any paid source). Each plugin's spend is shown in the health view, and plugins degrade (local model, slower polling) instead of overrunning. | M1.4a (framework), M2 onward |

**Scope freeze (owner, 2026-10-26).** The scope is frozen as D1 to D21. New ideas go to §9 by default and come back
only through a milestone proposal approved by the owner.

## 4. Milestone outline (detail is proposed per milestone when it starts)

| Milestone | Builds | Depends on |
|---|---|---|
| **M0** | These decisions; tracking docs. | done |
| **M1 Skeleton** | Repository, CI, security and the test-data policy; `ikars-core`; UI mock-ups; engine with plugin registry, point-in-time replay, clock, latency, storage governor and redacted logs; audit and tax ledger with portfolio and safe migrations; background run mode with prioritised workstation alerts; versioned loopback API; terminal shell; packaging with rollback; perp research; rebuild runbook; **the end-to-end momentum slice** (D21). Slices in §4.1; gate in §4.2. | M0 |
| **M2 Sources** | Weather and news source plugins (D5), **recording from the first slice** (D20 A), each with a terms-of-use check (G17); settlement-rules parsing and the event calendar (G8); GPU embeddings; the LLM provider interface with injection hardening, a response cache and a cost meter (D9, D20 B); GDELT history for back-tests; live source panel and drill-down. | M1 |
| **M3 Signals** | Signal modules per D13 (news/data estimates, momentum first); linking items to markets; order-book pattern measures (D16); other-venue fair values (G7); the fill-probability and adverse-selection model (D20 C); the pre-registration registry (G4); back-tests through the point-in-time replay on KAPS/SPARQ data. | M2 |
| **M4 Recommendations** | P&L engine (D7) with fill probability (C) and return per day of capital (D); sizing and the risk layer (G2); tradability filters (E); provenance (F); the recommendation lifecycle and feedback (G9); the trigger→action rule engine with custom types as watch orders and the paper executor (D6, D14); Overview, drill-down and cross-reference views for recommendations (R9); prioritised workstation alerts for recommendations (G6, H); the latency target on the sniping path (J). | M3 |
| **M5 Evaluation** | Outcome journal reports by version (F); calibration and P&L after costs per strategy, pattern, custom type and LLM provider; automatic demotion and the Strategy review page (G19); the tax-year package (D18). | M4 |
| **M6 Release** | Installer and portable release with auto-update (D12); deploy guide; close-out of the recommend-only product. | M5 |
| **M7 Live trading** | Compliance review first (G17); trading key, the risk layer (G2) enforcing on real orders, kill switch, audit in the ledger, real executor; one-click orders, then custom types and armed rules (D6, D14). Own proposal and approval. | M5 evidence, M6 |

### 4.1 M1 slices (APPROVED 2026-10-26; each gets its own slice plan before any code)

| Slice | Builds | Outward-facing or machine changes |
|---|---|---|
| **M1.1 Repository, CI and security** | `git init`; public `swalsh76/IKARS` (MIT); the `uv` workspace (`packages/ikars-core`, `packages/ikars-engine`) and the .NET solution (`src/`, `tests/`, central package management); `.editorconfig`, `.gitignore` (data, keys, news content, ledger files, logs); GitHub Actions on `windows-latest`: ruff, mypy, pytest, dotnet build and test, coverage gates; gitleaks, hash-pinned dependencies, Dependabot (G12); the **test-data policy** with the first synthetic fixture generators (D21-4); the mini-review routine (R14). | **Creates the public GitHub repository.** |
| **M1.2 `ikars-core`** | Seeded from `sparq_core` (signing, REST, WebSocket, fixed-point, fees, clock), renamed, green on Windows. Adds `orderbook_delta`, portfolio (fills, settlements, balance, positions, interest if present), account limits and exchange schedule models. Re-checks Kalshi's native order options (D14). **Measures account-level rate and subscription limits** without the ramp (D21-3). Live tests on Windows. | Live GET requests to Kalshi with **IKARS's own read-only key** (the owner creates it). |
| **M1.3 UI mock-ups** | Overview (with portfolio and exposure, G1), drill-down (markets path), cross-reference panel; hotkeys, layouts, dark and light (G16); the health view (latency, feeds, backups, storage); Windows 11 Fluent look. **Owner approval before M1.8.** | None. |
| **M1.4a Engine core** | asyncio service: configuration and paths; **structured, rotated, redacted logs** (I); the **plugin registry and interfaces** (D19); the **input interface with live and replay sources**, the replay releasing every item at its **received time** (G3, A); the item and link model with publisher and received times; clock skew (G10); the latency and frame-time metrics framework (J); the **cost-tracking and budget framework** (D21-5). | None. |
| **M1.4b Kalshi feeds and recording** | Kalshi WebSocket (ticker, trades, lifecycle for all markets; order books for a watched set, `seq`-gap resync); exchange status and schedule (E); in-memory state; feed-to-alert latency measured live against the target (J); raw feeds to hourly Parquet with a gap log; the storage governor (G11); the **Kalshi request and subscription budget** that protects KAPS's ingest (D21-3). *(GPU detection moved to M1.6 by the owner, 2026-10-27.)* | Uses IKARS's read-only key. |
| **M1.5 Audit and tax ledger, portfolio** | D18's store, hash chain, checkpoints, `verify`. Account import (fills, settlements, fees, transfers, funding, interest) with reconciliation. Positions and exposure marked to market (G1). The paper stream kept apart. **Versioned schema migrations with a backup before each one** (G). Nightly backup to the share's `ikars\` subtree; `ledger restore-drill` (G14). | Reads the owner's account (GET only). **Creates `ikars\` and its marker on the NAS share** (owner's go-ahead at the slice plan). |
| **M1.6 Run mode and alerts** | The engine as a background process independent of the terminal; auto-start at login; keep-awake while running; a scheduled watchdog that restarts it (G5). The **alert framework**: priority levels, de-duplication, throttling, quiet hours, digest (H). **Windows notifications on the workstation** for engine down, feed stale and backup failed (G6; no phone pushes, owner 2026-10-27). **GPU detection** (D15): the device, driver and CUDA version in health, CPU paths when there is none (moved from M1.4b, owner 2026-10-27). | Adds a per-user logon task and a scheduled watchdog task (no admin); shows test notifications on the workstation. |
| **M1.7 Loopback API and contracts** | Starlette/uvicorn on `127.0.0.1` with a per-session token and a **version handshake** (G). Snapshots over HTTP, deltas over one WebSocket (markets, books, portfolio, ledger, alerts, health). `msgspec` contracts exported as JSON Schema; the C# contract test (retired 2026-10-27 with the D1 amendment; the TypeScript golden test replaces it in M1.8a-web). | None. |
| **M1.8 Terminal shell** | *Replanned 2026-10-27 (D1 amended):* **M1.8a-web** (plan approved 2026-10-27 as four slices, 1a engine side, 1b web foundation with Overview and
Health, 2a Markets and Market, 2b Ledger, Alerts and the meter; BUILD-LOG 1.8a-web) (the engine serves the app with the one-time-code sign-in and an `open` command; generated types and golden tests; the stream reader, update hub and URL navigator; the screens from the mock-ups with Lightweight Charts and AG Grid; the update-to-screen meter and its load test; the browser hardening of D3 and the web checks of D11, CodeQL for
TypeScript, and a `/security-review` before merge; *from the 2026-10-27 platform review (BUILD-LOG 1.R2), items
1 to 5:* a **stable port and a session that survive an engine restart** (the port saved in settings, falling back
only when taken, with `ikars open` relaunching; the session secret kept DPAPI-protected, D8), a **typed health
contract**, a contract test that wire integers fit 2^53 (`_ns` fields documented as approximate in the browser),
**one time format** on the wire, and a precomputed market search key with a debounced search) and **M1.8b** (hotkeys, layouts, themes, settings and wizard, support bundle). The original text: from KalTerm3, stripped of the SQL Server and multi-user parts. The navigation service, the Overview per the approved mock-ups (portfolio included), the linked-items panel, the health view, hotkeys, layouts, themes. Live markets and a ledger view. Versioned settings and the first-run wizard (G15, G). A redacted **support bundle** (I). Frame time measured (J). Connects to the running engine or starts it. | None. |
| **M1.9 Packaging** | Velopack `Setup.exe` and the portable zip with the standalone Python engine and the terminal. Per-user install on a clean Windows 11 account. The first-run wizard on a fresh install. An auto-update from one release to the next **with migrations**, and a tested **rollback** (G). Signing decided (G12). | A test pre-release on GitHub. |
| **M1.10 Perp research** | Read-only: perp endpoints, fees, funding schedule, margin and liquidation rules; `docs/research/perps.md`. | Live GET requests. |
| **M1.12 End-to-end momentum** | *From the 2026-10-27 platform review (BUILD-LOG 1.R2), items 8 and 9:* the live
runner **loads the plugins** (`discover`), **records what they emit** (the generic recording schema, D20 A) and
hands recommendations to the API through a listener; the host, not the plugin, **stamps `received_ns`** on emit.
The thin vertical slice (D21-2): a minimal momentum signal on Kalshi's feed, **registered by hash** (G4); a minimal P&L after fees (D7's entry and exit costs, `ikars-core` fees); the recommendation on the Overview with a drill-down and provenance (F); a priority-tiered alert (H); journalled and scored forward from day one. No sizing beyond a fixed small size, and no news or LLM. | None (read-only). |
| **M1.13 Replay of real recordings** | *From the 2026-10-27 platform review (BUILD-LOG 1.R2), items 6 and 7; owner
approved its own slice, before M3's back-tests.* A reader that turns the recorder's Kalshi channel files (trade,
ticker, lifecycle, book snapshot and delta) into the same items the live source emits, so a back-test runs the live
code (completes G3); and a **streaming** merge in received-time order, file by file, instead of loading every
recording into memory (about 2.1 GB a day). | None (local files). |
| **M1.11 Close-out** | Gate evidence; the **"workstation lost" runbook** tested end to end (I): reinstall, restore the ledger and settings from the share, verify. Docs current; milestone review (short, thanks to R14); leftovers parked. | Restores from the share into a scratch location (read only on the share). |

The order is M1.1 → M1.2 → M1.4a → M1.4b and M1.5 (with M1.3 alongside) → M1.6 → M1.7 → M1.8 → M1.9 → M1.12 →
M1.11. M1.10 can run any time after M1.2; M1.13 any time after M1.12 and before M3's back-tests. (M1.12 is listed before the close-out because the close-out checks it.)

### 4.2 Milestone gates (D21; each is confirmed or tightened in the milestone's own proposal)

| Milestone | Done means | If it fails |
|---|---|---|
| **M1** | Installed with Velopack on a clean account; live markets in the terminal through the engine; the ledger reconciles exactly with Kalshi's balance and positions; the restore drill and the workstation-lost runbook pass; the latency and frame-time targets are measured; M1.12's momentum recommendations appear end to end and are journalled. | Fix within M1; nothing in M2 starts. |
| **M2** | The weather and news plugins have recorded for **at least 7 days** with received times and no unlogged gaps; the LLM injection tests pass; spend is within the budget. | Fix the plugin, or drop that source with the owner. |
| **M3** | Every signal rule is registered by hash before scoring; replays are reproducible byte for byte; at least one signal is positive after costs on the validation part, which makes it a forward-test candidate. | No candidate: the owner decides whether to narrow the scope or rethink. M4 does not start for a signal without a candidate result. |
| **M4** | Every recommendation carries a size, fill probability, return per day, tradability check and provenance; alerts meet the latency target. | Fix within M4. |
| **M5** | **Pre-registered stop rule, per strategy:** at least N scored recommendations (N fixed in M3 when the rule is registered; 300 by default, as in KAPS and SPARQ), a mean P&L after costs above zero, and a 95% lower bound above zero (bootstrap clustered by event). Passing strategies are kept; the rest are demoted (G19). | **Project rule:** if no strategy passes, IKARS does not go to M7. The owner chooses: stop, rethink, or a new pre-registered test. |
| **M6** | Install, update with migrations, rollback and the runbook pass on a clean account; docs current. | Fix within M6. |
| **M7** | Compliance review cleared; only M5-passing strategies are eligible; staged from paper to tiny live limits with the owner's go-ahead on each step. | Stays at the last passing stage. |

## 5. Progress

| Item | State |
|---|---|
| M0: D1 to D18 | **approved 2026-10-26** (D4 option C; D6 recommend-only through M6, live trading in M7; D9 with a local-model option; D10 public; D12 Velopack; D18 backups to the KAPS share, own `ikars\` subtree) |
| D19 gap review (G1 to G21) | **approved 2026-10-26** (G18 dropped; G20, G21 parked) |
| D20 second gap review (A to J) | **approved 2026-10-26** |
| D21 third gap review (gates, end-to-end slice, shared account, test data, cost ceiling) and **scope freeze** | **approved 2026-10-26** |
| M1 slices (§4.1) and gates (§4.2) | **approved 2026-10-26** |
| M1.1 Repository, CI and security | **done** (BUILD-LOG 1.1; PR #1 merged 2026-10-26 as `2f19222`) |
| M1.2 `ikars-core` | **done** (BUILD-LOG 1.2; PR #3 merged as `3485b16`) |
| M1.3 UI mock-ups | **approved for now** 2026-10-27 (BUILD-LOG 1.3; source in `docs/design/mockups/`; revisable before M1.8) |
| M1.4a engine core | **done** (BUILD-LOG 1.4a; PR #4 merged as `b29a86e`) |
| M1.4b Kalshi feeds and recording | **done** (BUILD-LOG 1.4b; PR #5 merged as `58abbe5`); recording volume of about 2.1 GB a day accepted as is |
| M1.5a Audit ledger | **done** (BUILD-LOG 1.5a; PR #7 merged as `087b819`): reconciles exactly with Kalshi |
| M1.5b Share backups, restore drill, portfolio | **done** (BUILD-LOG 1.5b; PR #8 merged as `5b6f702`) |
| M1.6 Run mode and alerts | **done** (BUILD-LOG 1.6; PR #9 merged as `2efc95f`); the owner's sign-out check of auto-start pending |
| M1.7 Loopback API and contracts | **done** (BUILD-LOG 1.7; PR #10 merged as `0ba5d83`) |
| M1.R Base hardening (owner, 2026-10-27) | **built** on `m1/r-hardening` (BUILD-LOG 1.R): a whole-code-base review's items 1 to 26 fixed, plus 10 merge-review findings; **done** (PR #14, `fca7941`) |
| M1.8a-web-1a Engine side of the web terminal | **done** (PR #16, `d70cb83`; BUILD-LOG 1.8a-web-1a): file serving with the browser hardening, cookie sign-in that survives restarts, `ikars open`, contracts 2.0, `demo-api`; security review (1 finding, fixed) and merge review (8, fixed) |
| M1.8a-web-2b Ledger, Alerts and the meter | **built** on `m1/8a-web-2b` (BUILD-LOG 1.8a-web-2b): Ledger and Alerts screens, the update-to-screen meter on Health, `pnpm load` (100 markets at 10 Hz); merge review (10, fixed); the owner's console check **passed** (p99 19 ms, after the Markets grid was made faster); PR #20 pending |
| M1.8a-web-2a Markets and Market | **done** (PR #19, `94dbcaa`), built on `m1/8a-web-2a` (BUILD-LOG 1.8a-web-2a): the Markets grid and the Market screen (quote, chart, book, linked items); the CSP's font exception; merge review (10, fixed) |
| M1.8a-web-1b Web foundation, Overview and Health | **done**, built on `m1/8a-web-1b` (BUILD-LOG 1.8a-web-1b): `web/` (types and validators from the contracts, client, stream, hub, money, navigator, Overview, Health), the `web` CI job, CodeQL for TypeScript, Dependabot npm; security review clean, merge review (10: 9 fixed, 1 no change); **done** (PR #17, `0fe5415`) |
| M1.8 Terminal shell | M1.8a (WPF) done and merged as `77849e3`, then **retired by the D1 amendment** (2026-10-27; BUILD-LOG 1.8w; tag `wpf-terminal-final`). **WPF pruned** (PR #13 merged as `1b92d49`). Next: the **M1.8a-web** plan, for approval; then M1.8b |

## 9. Parking lot

- Remote read-only view from the laptop or phone (G20).
- Social sentiment sources such as X and Reddit (G21), to be added as a source plugin.
- Engine on the server host.
- Further source plugins (D5 options A, D, E; paid news APIs).
- An MSI build (WiX) if a true MSI is ever needed (D12).
- An index on the ledger's `(stream, ticker, seq)` for the Market screen's "your ledger records" (M1.8a merge
  review). It needs a ledger migration (backup first); the scan is trivial at today's few hundred records. Revisit
  when the ledger reaches tens of thousands.
- One `times` module for the duplicated time helpers (`api.utc_text` and `ledger.iso_utc`; `alerts._ns` and
  `ledger_import.ns_of`): with the next slice that touches those files (2026-10-27 platform review, item 10).
- Readable screen-reader names for list items: the terminal's lists expose each record's raw text to UI Automation
  (M1.8a live check). Natural with M1.8b's hotkeys and themes, or later.
