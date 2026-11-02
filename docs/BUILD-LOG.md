# IKARS build log

How each slice was planned, built and verified: the owner's decisions that shaped it, what was found, what exists,
the tests and live checks with their numbers, and every review finding with what was done about it (R14). The
decisions themselves, the build order and the progress table are in [PLAN.md](PLAN.md); a slice there points here
as "BUILD-LOG <id>". Old wording is in the git history (condensed 2026-11-02, nothing decided or measured dropped).

Each slice uses these blocks, leaving out any that would be empty:

```
### <id> <title>
**Status.** Plan approved <date> (<answers>). Built on `<branch>`: `<sha>` (what), ... Done: PR #N merged as
`<sha>` | Open: PR #N. Releases. Packages. Machine changes.
**Owner's decisions (<date>).** Choices that shaped the code; deviations from the plan.
**Found.** Facts learned while planning, building or checking live.
**Built.** What exists, by area. Never the plan restated.
**Verified.** Tests and gates; live checks as check | result (the final outcome, with date and version).
**Review.** Per commit; the merge review as an itemized table (finding, what was done).
**Kept, changed and why.** Reference code (KAPS, SPARQ, KalTerm3) taken, changed or left.
**Open.** Owner actions still pending; items parked in PLAN §9.
```

A passed check replaces its pending line, and the merge goes into Status.

**Where each slice is.** A citation "BUILD-LOG <id>" names the file by the id's first part:

| Ids | File |
|---|---|
| 0.1 and 1.x (M0 and M1, closed; M1.R3's live checks still being added) | [build-log/M1.md](build-log/M1.md) |
| 2.x, the M2 proposal, Candidate family R (M2, current) | below |

## M2 Sources

### M2 proposal

**Status.** Approved 2026-10-28: PLAN §4.3, decisions D22 to D30, slices M2.1 to M2.10. **Done:** PR #32 merged as
`dfc3f4b`. All within the frozen scope (D5, D9, D15, D20 A/B, G8, G17); perp research moves in from M1.10. Each
slice still gets its own plan before code.

**Owner's decisions (2026-10-28).**
- **The local LLM:** the Strata server on the home network, beside Claude (then amended, below).
- **The monthly ceiling:** **$20**.
- **News:** GDELT plus curated RSS.
- **The plan:** as proposed, with M1.13 after M2.3.
- **Amended** ("slower response rate is acceptable"; "approved as amended"): M2 uses **the local LLM only**.
  - **Claude** is deferred to an M3 decision, made on M2's labelled-sample measure.
  - **Embeddings (now M2.5)** come before the LLM layer (now M2.6), so the pre-filter bounds the volume.
  - **Thinking** is turned off for classification if measurement supports it.
  - **The gate** adds the quality measure.
  - **Setup:** no Claude account and no `anthropic` package.

**Found.** The comparison came from a probe of the Strata model, `qwen3.8-flash-next`, quantised to IQ3_S, on six
made-up items, one an injection attempt: valid JSON 6 of 6; relevance 6 of 6; category 4 of 6 (both misses
defensible); the injection resisted; 2.5 to 5 s per item at 36 to 54 tokens a second, most of it the model
"thinking".

**D9 as approved on 2026-10-26, before this amendment:** the Claude API (a fast, cheap model as a first-pass filter,
a stronger one for candidate matches and impact estimates) plus a local model on the RTX 5070 Ti (a quantised model
of about 7 to 14B parameters fits in 16 GB), first for the high-volume first pass, so the cloud is called only for
candidates; the runtime (llama.cpp server, Ollama or ONNX Runtime GenAI) to be chosen by measured speed and quality
against the Claude baseline on a labelled sample; a daily spend cap on cloud calls; the local model an optional
extra in the release. D24 makes the Strata server the local backend and defers Claude to M3; the daily cap on
cloud calls is not built (there are none before M3); the $20 monthly ceiling (D21-5) bounds paid spend.

### 2.1 Polling-source framework

**Status.** Plan approved 2026-10-28 (as proposed). Built on `m2/1`: `2ed9abf` (the framework), `6815d92` (merge-review
fixes), `976f707` (a test closing its store). **Done:** PR #33 merged as `86726d7`. No new packages.

**Owner's decisions (2026-10-28).** The User-Agent contact is the repository's URL (an email optional, in the local
settings only). A feed unreachable for more than **twice its healthy wait** gets a gap entry for the missed window;
shorter blips are retried quietly and counted in health.

**Built.**
- `polling.py`: `PollingSource` (a source plugin names its feeds and reads one reply) and `Poller`, the engine's one
  HTTP client for polling sources (the host service `"poller"`; health under `sources`, with no URLs; closed after
  the plugins stop).
- Per feed: its own interval with jitter, never sooner than the server's `max-age`; HTTPS only, no redirects, a 5 MB
  reply limit, about one request a second per host on the engine's clock; waits of half the interval after a first
  blip, at least the interval or `Retry-After` after a slow-down, otherwise doubling to 15 minutes; conditional GET
  with both validators, kept only after the reply's items have gone out; each id emitted once, refreshed by every
  sighting and dropped after 14 days unseen. A source's feeds run in a TaskGroup; a plugin fault (an item kind it
  may not emit) crashes the plugin instead of being retried as a feed problem.
- State store migration 3 (`poll_feed`, `poll_seen`). Settings version 6: `[sources] contact_email`; an unchanged $40
  ceiling becomes the owner's **$20** (now the default; a ceiling the owner set stays).
- `docs/research/sources.md`: the NWS and GDELT terms checks (G17) and how every polling source behaves.

**Verified.** 1,574 Python tests (20 for polling, against a fake HTTP server and clock); `polling.py` 96%; lint,
types and the data guard clean. **Live probe of `api.weather.gov`** (2026-10-28; public, our User-Agent accepted):
a weak `ETag`, `Last-Modified` and `max-age=280`; `If-Modified-Since` gives a 304 but `If-None-Match` alone does not
(the weak `ETag` names the server that answered), **so IKARS sends both**; `/points/{lat},{lon}` gives a location's
forecast URLs (used by M2.2). The migrations first ran live with M2.2's v0.1.11.

**Review.** Per commit: `2ed9abf` 1 fixed before committing (the validators were saved before the reply's items
went out, so a failed parse or emit lost them behind a 304; now kept in `succeeded()`). Merge review (medium), 10
findings, all fixed in `6815d92`:

| # | Finding | Done |
|---|---|---|
| 1 | `2.0**failures` overflowed after about 17 hours of failures and crashed the source | The exponent is capped; tested at 5,000 |
| 2 | `gather` left the other feeds' loops running when one failed, so a restart doubled them | A TaskGroup cancels them |
| 3 | A server `max-age` over twice the interval gave a false gap on every poll | The gap rule uses the healthy wait, kept with the feed; tested |
| 4 | The "backoff" retried sooner than the healthy interval | After the first blip, never sooner than the interval; tested |
| 5 | An item still listed after 14 days was forgotten and recorded again | Each sighting refreshes it; tested |
| 6 | A partial emit failure re-sent the items already out | What went out is remembered; tested |
| 7 | Plugin faults were retried for ever as feed problems | They crash the plugin; tested |
| 8 | `Retry-After` dates were read against the real clock | The engine's clock |
| 9 | Remembering ids was one fsync per row on the event loop | One transaction |
| 10 | The dedup ran twice, and the store was read twice per poll | One pass, one read |

### 2.2 Weather

**Status.** Plan approved 2026-10-29 (as proposed). Built on `m2/2`: `d347239` (the slice), `eca9ef5` (merge-review
fixes). **Done:** PR #34 merged as `436bef2`. Releases: v0.1.11 (from `f0e5b10`, installed 2026-10-29 00:32 MDT;
M2's 7-day weather clock starts with it). No new packages.

**Owner's decisions.**
- 2026-10-29: NWS is the proxy for the settlement source, measured later (M2.4); stations: every climate-report code
  named by an open market.
- 2026-11-01 (CI before the merge): `pnpm audit` began failing on GHSA-g3hv-75wg-m249 (moderate: denial of service in
  minimatch below 11.0.1), reached only through the lint tools, with no parent accepting minimatch 11 yet. Owner:
  ignore it, dated, in `web/pnpm-workspace.yaml`; its removal is parked in PLAN §9.

**Found.** Kalshi's weather markets settle on **The Weather Company's** value, from the government station their
rules name ("recorded at New York City (CLINYC) … according to The Weather Company"), not on the NWS climate report.
The Weather Company has no free feed and no terms for automated access (G17). So the NWS report for the same station
is recorded as the proxy. D22 in PLAN §4.3 and `docs/research/sources.md` note it, and M2.4 measures the proxy
against Kalshi's settlements.

**Built.**
- **Stations from the rules:** the market index keeps each market's climate-report code (`\(CLI[A-Z]{3}\)` in its
  rules); the engine offers the codes open markets name (`climate_codes`); a code maps to its station (`K` plus its
  location, confirmed at `/stations`) and its forecast grid (`/points`, four decimal places). On 2026-10-28, open
  markets named **24 codes**, and all 24 mapped.
- **The `nws` source plugin** (entry point `nws`), through the shared poller:
  - `weather_obs`: the latest observation, every 5 min;
  - `weather_forecast`: the hourly and daily forecasts, hourly, the first 48 hours kept;
  - `weather_climate`: the latest climate report, every 15 min, parsed: the day and whether it is "today"
    (preliminary) or "yesterday" (final); the high and low, with NWS's record flag; precipitation and snow as text
    ("T" a trace; "MM" stored as None); the report's text, kept.
- **The plugin over time:** codes are checked every 10 minutes (new ones start their feeds; those whose markets have
  all closed stop theirs); a code that does not map is shown in health and tried again; a city is looked up again
  weekly, or after 6 hours of failing feeds; `plugin_settings.nws.enabled = false` turns it off.
- **The polling base:** feeds that start and stop while it runs, a per-feed item kind, one-off lookups, and
  per-source notes in health (`sources: {feeds, notes}`).

**Verified.** Python: 1,585 tests, 11 for NWS, with hand-written made-up replies (rules and codes, parsing, mapping,
an unmapped code, a retry, closing, a timeout and a record value); `nws.py` 93%; lint, types and the data guard
clean. Live (counts and states only):

| Check | Result |
|---|---|
| Dry run against the real NWS (2026-10-28 23:00 MDT, a scratch store, three minutes) | 24 of 24 codes mapped; 96 feeds, no errors; first round 24 observations, 48 forecasts and 24 climate reports, every report parsed (e.g. NYC's 28 October preliminary: high 61, low 45, no precipitation; Boston's final: high 52, low 38) |
| Install of v0.1.11 (2026-10-29 00:32 MDT) | settings version 6 with the **$20** ceiling and `[sources]`, after a backup; the state store on schema 3, after a backup; `momentum` and `nws` running; 24 of 24 codes mapped; first round 96 feeds polled, 96 items emitted, 0 errors, 0 gaps; recorded in `recordings\item`: 24 observations, 48 forecasts and 23 climate reports (the 24th still in the hour's buffer) |
| Gap check (2026-11-01 19:18 MST, 91.8 hours after the install, read only) | **Passed.** No restart; 96 feeds made 39,670 polls against about 39,660 expected, 21,628 "not modified"; no feed failing, none last good more than two intervals ago; 5 polls failed, the next poll of each succeeded; the gap log's one weather entry: `nws.CLIMDW.cli` unreachable 31 minutes (2026-11-01 07:06 MST), then refetched; the rest Kalshi's own reconnects (the install at 00:32, a drop at 00:58 of about 30 minutes recovered by discovery and backfill, 10 seconds on 2026-11-01 at 01:00), all logged; recorded 6,381 observations, 1,202 forecasts and 254 climate reports; every hour has weather items and every recording file reads |
| Quiet hours (M1.12c), nights of 2026-10-28 to 2026-10-31 | **Passed.** Momentum made 2,457 to 4,166 recommendations each night from 22:00 to 07:00 and no notification was shown then (a recommendation is not offered in quiet hours, so the alert log has no record of them); since 2026-10-28, 107 recommendation notifications shown, at most 3 in any hour (the cap) |

**Found by the gap check: holes are NWS's, not losses.** An item is recorded when NWS publishes something new, so the
time between items follows NWS's own schedule: 1.8 hours median for observations, about 12 hours between forecast
issues. The longest were KNYC's observation (3.6 hours, while the station's latest stayed the same) and Phoenix's and
San Antonio's climate reports (24 hours, one issue a day). The polls through those holes all succeeded. **No unlogged
gaps.**

**Review.** Per commit: `d347239`, 3 found before committing, 2 fixed, 1 no change: a lookup that failed once kept a
city unmapped until a restart (now tried again); a city whose markets closed was polled for ever (its feeds now stop);
the health shape changed (no change: only the engine reads it). Merge review (medium), 10 findings, all fixed in
`eca9ef5`:

| # | Finding | Done |
|---|---|---|
| 1 | A station lookup timing out (30 s) crashed the whole source, which could end up paused | Caught; the city is unmapped and tried again; lookups get 2 minutes; tested |
| 2 | A record value ("92R") was dropped | Read, with a `high_record`/`low_record` flag; tested |
| 3 | Stopped feeds stayed in health for ever | Removed on stop; tested |
| 4 | A blank value borrowed the next line's, or the next section's | Read on its own line within its own section; tested |
| 5 | A city's forecast URLs were never looked up again | Weekly, or after 6 hours of failing feeds |
| 6 | Rules that no longer name a station kept the old code | Only an update without rules keeps it; tested |
| 7 | The codes service built and sorted ticker lists to throw away | `open_climate_codes`, codes only; tested |
| 8 | Health read `sources.sources`, and the notes were shared, not copied | `{feeds, notes}`, the notes deep-copied |
| 9 | NWS times went through floats, and a time without an offset was read as local | Exact integer nanoseconds; none without an offset; tested |
| 10 | Codes were checked only hourly, so feeds started up to an hour late | Every 10 minutes |

**Open.** Parked in PLAN §9: removing the minimatch `pnpm audit` exception.

### 2.3 News

**Status.** Plan approved 2026-11-01 (GDELT both; government RSS only; `defusedxml` and our own parser; the rest as
shown). Built on `m2/3`: `7c0d40d` (the slice), `a19a332` (merge-review fixes). **Merged** as `1786381` (PR #35, 2026-11-02).
Releases: v0.1.12 (from `b2548b5`, 2026-11-01; M2's 7-day news clock starts with it); also in v0.1.13 (1.13).
Packages: `defusedxml` 0.7.1 (PSF-2.0, no dependencies); type stubs not added (one targeted `type: ignore`).

**Owner's decisions (2026-11-01).**
- **GDELT:** both. The GKG files are the main stream; DOC API topic queries are added when it answers, with
  duplicates removed between them (D23 amended).
- **RSS:** government feeds only to start. Publisher feeds come only by the owner's decision, feed by feed.
- **Parser:** `defusedxml` and a small parser of our own.
- **The rest as shown:** a feed without a recorded terms note is not polled; duplicates are removed by cleaned link
  across all news feeds; settings version 7 holds the starting list; no terminal change until M2.7; a dry run, then
  a test release on the owner's word.

**Found (probes while planning, 2026-11-01, public GETs).**
- **GDELT's DOC 2.0 API** answered every query with 429 ("one request every 5 seconds"), even 90 seconds apart, and
  a 15-minute span was refused as too short. A query returns at most 250 articles.
- **GDELT's raw files over HTTPS work.** `lastupdate.txt` names the newest 15-minute files. The GKG file was 2.5 MB
  zipped (7.5 MB unzipped) with `max-age=3600` and an ETag. It held 551 articles from 150 sources, every one with
  its title. That is about 240 MB a day to download.
- **Government feeds** answer, most with ETag or Last-Modified: the Fed, BEA, Census, EIA, the National Hurricane
  Center, the White House, SEC, FDA and USGS. BLS refuses the User-Agent (403), and Treasury's feed address needs
  finding.

**Built.**
- **The `gdelt` plugin (entry point `gdelt`):**
  - **GKG files.** The index (`lastupdate.txt`) is read every 2 minutes, and each new file is fetched once.
    - Files missed are fetched by name, up to a day back, four per read, with their true (late) received time.
      Older ones are logged as unrecoverable.
    - A file is unpacked and read in a worker thread: one member only, with caps on the unzipped size and the
      ratio. The newest file is checked against the index's MD5.
    - The bookmark moves on, and gaps are logged, only once a read's items have gone out.
    - A backlog's start is kept across restarts. Once caught up, it is logged once as `backfill`, leaving out any
      window already logged as lost.
    - A damaged or missing file at least two slots older than the newest is logged and passed. The network, a
      busy server and the newest file are retried.
  - **DOC 2.0 searches** from the settings, every 15 minutes. GDELT's API gets one request every 10 seconds at
    most, and a poll waits for its turn before its timed call.
  - **Kept:** title, link, source, GDELT's time, the publisher's time (only when GDELT has it), themes, places,
    people, organisations and tone (as text). Never quotations or the article's text.
- **The `rss` plugin (entry point `rss`):**
  - RSS 2.0, RSS 1.0 and Atom, read with `defusedxml`.
  - Kept: the headline, link, publisher time and summary, as plain text cut to 1,000 characters. Times are exact,
    and a time with no zone is never guessed.
  - Ids are by cleaned link, or by guid for a feed that reuses links (Census). A feed on link ids whose reply
    repeats a link is flagged in health.
  - Feeds without a terms note, with a bad or duplicate name, or not on HTTPS are refused, and health says why.
- **Duplicates:** every news item's id is its cleaned link (tracking parameters, `www.` and the fragment dropped), in
  one seen scope shared by all news feeds; the first source to deliver a link wins. Claims keep two feeds from both
  sending one item, and an id is remembered only by the feed that sent it.
- **Settings version 7:** `[news]` with the GDELT switch, 12 US government feeds (Fed ×2, BEA, Census, EIA ×2,
  NHC ×2, White House ×2, FDA, USGS; each feed's interval in the settings, 5 minutes by default) and five GDELT
  searches. BLS and SEC wait for a contact email (`docs/research/sources.md`, which has a terms row per feed).
- **The polling base:** index feeds read whole every time; a per-host rate; the HTTP status on errors; `read` and
  `delivered` hooks for sources; a source that backfills logs its own gaps; `iso_ns` and `exact_ns` shared with NWS.

**Verified.** Python: 1,612 tests (27 for news), with made-up feeds, GKG rows and zips; `gdelt.py` 92%, `rss.py`
97%, `polling.py` 95%; lint, types and the data guard clean. Live (counts and states only):

| Check | Result |
|---|---|
| Dry run against the real sources (2026-11-01 about 20:00 MST, a scratch store) | GKG: one file gave 1,101 to 1,178 articles from 280 to 293 sources, all but a handful with a title, about half with the publisher's time; RSS: the 12 feeds gave 233 items with no errors, 232 with a time, all with a summary; ids all unique, no gaps; payloads average 1.2 KB (about 120 MB a day before compression); DOC searches answered 429 again, with one exception that returned nothing new |
| Install of v0.1.12 (2026-11-01 about 20:39 MST, read only) | settings version 7, the version 6 file kept beside it first, plus the installer's pre-upgrade copy; `gdelt`, `momentum`, `nws` and `rss` running; RSS: all 12 feeds polled, no errors, none refused, 235 items, none flagged for repeated links; GKG: two index reads, one file recorded (521 articles), the bookmark on the newest file and nothing behind; DOC searches: all five answered 429, as expected (GDELT still refusing them) |

The terminal's Health screen shows only each plugin's state; the per-feed cards are M2.7's (D29, mock-up first).

**Review.** Per commit: `7c0d40d`, 1 found before committing, fixed (after a day-long outage, the poller logged the
whole window as backfilled while GDELT also logged its oldest part as unrecoverable; now a backfilling source logs
its own gaps). Merge review: `/code-review medium`, 10 findings, plus a fresh-eyes reviewer sub-agent (PLAN R16), 9
findings; five found by both. All fixed in `a19a332` unless marked:

| # | Finding | Done |
|---|---|---|
| 1 | A feed remembered every id it listed, including one another feed had claimed but then failed to send: that item was lost | Only ids it sent or no one holds are remembered; tested (both reviews) |
| 2 | GKG unzip and parse ran on the event loop, about 50 ms a file and 4 files a read during a catch-up, against the 33 ms screen target | A worker thread (both) |
| 3 | Any non-404 failure on one file (a damaged zip, too large) stalled the GKG stream for up to a day | A damaged or missing old file is logged and passed; tested |
| 4 | Gaps were logged before delivery: a failed read logged its window again each retry, and the backfill entry was "done" before any file came | Logged at delivery, once; tested (both) |
| 5 | Waiting for GDELT's API turn counted against the 60 s call timeout: the fifth search waited 40 s, a seventh always timed out | The turn is taken before the timed call (both) |
| 6 | A 404 on a file just behind the newest was written off, though GDELT may still be publishing it | Only from two slots back; tested |
| 7 | Titles were decoded twice, so `&lt;script&gt;` became markup and was dropped | Decoded once |
| 8 | A store error in `emitted` left ids claimed until a restart | Released in `finally` (both) |
| 9 | `Feed.max_bytes` was never used | Removed |
| 10 | A redundant condition in the gap rule | One condition |
| 11 | A backfill window that held a lost file was logged as both recovered and lost; a restart mid-catch-up logged an overlapping second backfill | The backlog's start is kept in the state store; the backfill leaves out lost windows; tested (sub-agent) |
| 12 | GDELT's crawl time was recorded as the publisher's time | The publisher's time only; GDELT's stays in `gdelt_time` (sub-agent) |
| 13 | The missed-file count divided as a float | Integer division (sub-agent) |
| 14 | Only the newest file has an MD5 in the index | No change: documented; a backfilled file is checked by unzipping (sub-agent) |
| 15 | `max_bytes or default` read 0 as the default | `is None` (sub-agent) |

**Open.** The gap check: a day of recording with no unlogged gaps (due 2026-11-02 evening). The stack merged first,
on the owner's word (2026-11-02).

### Candidate family R. Reversion / fade (owner, 2026-11-01; not approved)

**Status.** Recorded in PLAN D13 (summary, not approved) and §9 (comes back through the M3 Signals proposal). Docs
only: PR #38 merged as `935829d`. The owner's trade's figures stay out of the repository.

**The idea** (from the owner's manual in-play trade, 2026-11-01).

| Family | Edge over humans | Needs | Main risk |
|---|---|---|---|
| **R. Reversion / fade** | Humans over-react to an early setback, such as a heavy favourite's price dropping sharply in the first quarter, and the price comes back. Buy the drop and sell on the rebound. | A fair value to tell an over-reaction from a real repricing: other-venue odds (G7) or a live score feed (D5 option D, parked); before either exists, the pre-game price and the time left as a rough anchor. D14's scale-in / ladder and take-profit (or a bracket with a stop), the sizing cap (G2), and D7's fees on every fill. | Averaging down into a real repricing (an injury, say) compounds the loss, and a binary contract can go to 0. |

**Variants**, by how the position is built and closed:
- **Averaging down / buying the dip.** Add to a position after the price falls, which lowers the average cost. The
  exit target can be the first entry price, or only above the blended cost: the 2026-11-01 trade exited above its
  blended cost but below its first entry, so the second lot rescued a first lot that was losing.
- **Scale-in ladder.** The adds are planned at set price levels (D14). If each add is bigger than the last, it is a
  martingale, which the sizing cap (G2) must forbid.
- **Trading out.** Exit on the rebound instead of holding to settlement.
- **Repeated round trips (range trading; scalping when the moves are small).** Re-enter after an exit and repeat the
  cycle. Fees on each fill (D7) decide whether the small moves are worth taking.

For the ledger and tax records, a laddered position closes lot by lot under FIFO (D18), so one exit can close a
first lot at a loss and a later lot at a gain. Whether wash-sale rules reach event contracts is a question for M5's
tax export. R is the opposite of momentum: on the same drop, the momentum rule (M) would point the other way. If both
are approved, the Strategy review (G19) compares their records on the same markets.
