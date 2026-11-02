"""Replaying real recordings (M1.13; PLAN D19 G3): the engine and its plugins on what was recorded,
in received-time order, on a virtual clock, so a back-test runs the live code.

**What a replay reads.** Every channel the recorder writes, hour file by hour file, merged in
received-time order. Ties go by channel (market details, then feed events, lifecycle, book
snapshots, book deltas, ticker, trades, plugin items) and then by file and row order, so a replay
is deterministic (SPARQ's lesson: an unstable sort once made two runs differ). A compacted hour is
streamed a batch at a time; an hour still open, or in parts, is read whole and sorted first. So a
replay holds about one batch per channel, never the whole recording (review 1.R2 item 7).

**What it rebuilds.**

- Kalshi's items, from their rows, with the live source's own functions (``kalshi.rows``: one row,
  one item), and the kept state (quotes, books) through ``KalshiState``, as live. A book checkpoint
  (hourly) sets a book without being an item: the live engine never saw it as one.
- Market details and fee terms as they were, from the ``market`` channel. A replay reads that
  channel from ``MARKET_LOOKBACK_NS`` before it starts (a full dump comes at least daily).
- Plugin items (weather, news) as recorded, except those of plugins the replay runs again: those
  are made afresh.

**What it never touches.** The recordings and the live state are only read; the replay's journal is
a scratch file. Only signal plugins run: a source plugin would fetch from the network.

**Warm-up.** Plugins need history (momentum: 5 minutes; one recommendation per market for 30). A
replay starts ``warmup`` before the window it reports on, from the start of that hour (where the
hour's book checkpoint is); recommendations made in the warm-up are not reported.
"""

import hashlib
import heapq
import json
import sqlite3
from collections import Counter
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ikars_core.fees import FeeSchedule, fee_schedule
from ikars_core.fixedpoint import E6
from ikars_core.markets import is_combo

from ikars_engine.clocks import VirtualClock, ns_time
from ikars_engine.kalshi.discovery import MarketInfo
from ikars_engine.kalshi.rows import (
    KalshiState,
    delta_message,
    item_of,
    snapshot_item,
    snapshot_message,
)
from ikars_engine.model import Item
from ikars_engine.recorder import HOUR_NS, hour_of, item_from_row, iter_hour

NS = 1_000_000_000
TICK_NS = NS  # the journal writes, expires and marks once a (virtual) second, as live
MARKET_LOOKBACK_NS = 25 * HOUR_NS
ORDER = ("market", "feed", "lifecycle", "book_snapshot", "book_delta", "ticker", "trade", "item")
RANK = {channel: n for n, channel in enumerate(ORDER)}


def merged_rows(
    root: Path, start_ns: int, end_ns: int, channels: tuple[str, ...] = ORDER
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every row of ``channels`` received in [``start_ns``, ``end_ns``), in received-time order,
    ties by channel then file and row order. Streams hour by hour."""

    def one(channel: str) -> Iterator[tuple[int, int, int, str, dict[str, Any]]]:
        rank, n = RANK[channel], 0
        hour = start_ns - start_ns % HOUR_NS
        while hour < end_ns:
            for batch in iter_hour(root, channel, hour_of(hour)):
                for row in batch.to_pylist():
                    at = row["received_ns"]
                    if start_ns <= at < end_ns:
                        n += 1
                        yield at, rank, n, channel, row
            hour += HOUR_NS

    for _at, _rank, _n, channel, row in heapq.merge(*(one(c) for c in channels)):
        yield channel, row


@dataclass
class ReplayMarkets:
    """Market details and fee terms as the ``market`` channel had them (the replay's index)."""

    markets: dict[str, MarketInfo] = field(default_factory=dict)
    fees: dict[str, FeeSchedule | None] = field(default_factory=dict)
    rows: int = 0

    def apply(self, row: dict[str, Any]) -> None:
        self.rows += 1
        series = row["series_ticker"] or ""
        close = row["close_ns"]
        self.markets[row["ticker"]] = MarketInfo(
            ticker=row["ticker"], event_ticker=row["event_ticker"] or "", series_ticker=series,
            category=row["category"] or "", title=row["title"] or "", status=row["status"] or "",
            close_time=None if close is None else ns_time(close), result=row["result"],
            volume_24h_e2=0, climate_code=row["climate_code"],
        )  # fmt: skip
        if row["fee_type"] is not None and row["fee_multiplier_e6"] is not None:
            self.fees[series] = fee_schedule(row["fee_type"], row["fee_multiplier_e6"])

    def fees_of(self, ticker: str) -> FeeSchedule | None:
        info = self.markets.get(ticker)
        return None if info is None else self.fees.get(info.series_ticker)


@dataclass
class ReplayStats:
    items: Counter[str] = field(default_factory=Counter)
    rows: Counter[str] = field(default_factory=Counter)
    late_rows: int = 0  # rows recorded after a later one (a pump behind): released at once
    digest: Any = field(default_factory=lambda: hashlib.sha256())


class RecordingReplay:
    """The engine's input from recordings: an ``Item`` stream on a virtual clock, like the live
    source's, and the kept state (``state``, ``markets``) the services read."""

    def __init__(
        self,
        root: Path,
        start_ns: int,
        end_ns: int,
        clock: VirtualClock,
        *,
        rerun: frozenset[str] = frozenset(),
        tick: Callable[[], None] | None = None,
    ) -> None:
        self.root = root
        self.start_ns = start_ns
        self.end_ns = end_ns
        self.clock = clock
        self.rerun = rerun  # plugins run again: their recorded items are left out
        self.tick = tick  # the journal's pass, once a virtual second
        # a combo's quote is kept only while it is watched, as live: here, while it has a book
        # (the live source only gets books for the markets it watches)
        self.state = KalshiState(keep=lambda t: not is_combo(t) or t in self.state.books)
        self.markets = ReplayMarkets()
        self.stats = ReplayStats()
        self._snapshot: list[dict[str, Any]] = []

    def _before(self) -> None:
        """Market details from before the start: applied, never released as items."""
        for _, row in merged_rows(
            self.root, self.start_ns - MARKET_LOOKBACK_NS, self.start_ns, ("market",)
        ):
            self.markets.apply(row)

    async def items(self) -> AsyncIterator[Item]:
        self._before()
        next_tick = self.start_ns
        for channel, row in self._grouped(merged_rows(self.root, self.start_ns, self.end_ns)):
            at = row["received_ns"]
            if at < self.clock.now_ns():
                self.stats.late_rows += 1
                at = self.clock.now_ns()
            while self.tick is not None and next_tick <= at:
                self.clock.advance_to(max(next_tick, self.clock.now_ns()))
                self.tick()
                next_tick += TICK_NS
            self.clock.advance_to(at)  # time reaches the row before anything sees it
            self.stats.rows[channel] += 1
            item = self._apply(channel, row)
            if item is None:
                continue
            self.stats.items[item.ref.kind] += 1
            self.stats.digest.update(f"{item.received_ns}|{item.ref.kind}|{item.ref.id}\n".encode())
            yield item
        if self.tick is not None:  # the last pass: what is due at the end is marked
            self.clock.advance_to(max(self.end_ns, self.clock.now_ns()))
            self.tick()

    def _grouped(
        self, rows: Iterator[tuple[str, dict[str, Any]]]
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Snapshot rows gathered into one row per snapshot (``{"rows": [...]}``)."""
        group: list[dict[str, Any]] = []
        for channel, row in rows:
            if group and (
                channel != "book_snapshot"
                or (row["ticker"], row["snapshot"], row["received_ns"])
                != (group[0]["ticker"], group[0]["snapshot"], group[0]["received_ns"])
            ):
                yield "book_snapshot", {"received_ns": group[0]["received_ns"], "rows": group}
                group = []
            if channel == "book_snapshot":
                group.append(row)
            else:
                yield channel, row
        if group:
            yield "book_snapshot", {"received_ns": group[0]["received_ns"], "rows": group}

    def _apply(self, channel: str, row: dict[str, Any]) -> Item | None:
        at = row["received_ns"]
        if channel == "market":
            self.markets.apply(row)
            return None
        if channel == "feed":
            self.state.feed_event(row)
            return None
        if channel == "item":
            return None if row["source"] in self.rerun else item_from_row(row)
        if channel == "book_snapshot":
            rows = row["rows"]
            item = snapshot_item(rows)
            if item.payload["checkpoint"]:
                # never an item live, and never a change: it only sets a book this replay lacks (at
                # its start), so each book's change time stays as live (per-commit review)
                kept = self.state.books.get(item.ref.id)
                if kept is None or not kept.ready:
                    self.state.snapshot(snapshot_message(rows), at)
                return None
            self.state.snapshot(snapshot_message(rows), at)
            return item
        if channel == "book_delta":
            self.state.delta(delta_message(row), at)
        elif channel == "ticker":
            self.state.ticker(row)
        return item_of(channel, row)


# --- checks ------------------------------------------------------------------------------------


SETTLED_NS = 500_000_000  # a book unchanged this long: the ticker has caught up with it


def _empty_side(price: int | None, empty: int) -> int | None:
    """Kalshi's ticker marks an empty side with a 0-cent bid or a $1 ask (SPARQ's lesson)."""
    return None if price == empty else price


def book_agreement(root: Path, start_ns: int, end_ns: int) -> dict[str, int]:
    """How often a rebuilt book's best YES bid and ask equal the recorded ticker's, for markets
    with a trusted book (SPARQ's planned 99% check, built here). The ticker and the book are
    separate messages, so while a change is in flight they briefly differ: ``settled`` counts only
    books unchanged for ``SETTLED_NS``, which is what the 99% is held to (2026-11-01: every
    mismatch fell within 500 ms of a book change). Only the feed, books and ticker are read; no
    engine runs."""
    clock = VirtualClock(start_ns)
    replay = RecordingReplay(root, start_ns, end_ns, clock)
    state = replay.state
    seen = agree = settled = settled_agree = 0
    channels = ("feed", "book_snapshot", "book_delta", "ticker")
    for channel, row in replay._grouped(merged_rows(root, start_ns, end_ns, channels)):
        if channel == "ticker":
            ticker = row["ticker"]
            book = state.books.get(ticker)
            if book is not None and book.ready and row["yes_bid_e6"] is not None:
                bid, ask = book.best_bid("yes"), book.best_ask("yes")
                same = (bid[0] if bid else None, ask[0] if ask else None) == (
                    _empty_side(row["yes_bid_e6"], 0), _empty_side(row["yes_ask_e6"], E6)
                )  # fmt: skip
                seen += 1
                agree += same
                if row["received_ns"] - state.book_changed_ns.get(ticker, 0) >= SETTLED_NS:
                    settled += 1
                    settled_agree += same
        replay._apply(channel, row)
    return {"compared": seen, "agreed": agree, "settled": settled, "settled_agreed": settled_agree}


def recommendations(path: Path, start_ns: int, end_ns: int) -> list[tuple[str, str, str, int, int]]:
    """A journal's recommendations made in the window (read only): strategy, version, ticker and
    side, made at, entry price. None at all when there is no journal yet."""
    if not path.is_file():
        return []
    uri = f"{path.as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        rows = db.execute(
            "SELECT strategy, version, ticker, side, received_ns, payload FROM recommendation"
            " WHERE received_ns >= ? AND received_ns < ? ORDER BY received_ns, id",
            (start_ns, end_ns),
        ).fetchall()
    return [
        (f"{s}@{v}", t, side, at, int(json.loads(p).get("entry_price_e6") or 0))
        for s, v, t, side, at, p in rows
    ]


def compare(
    live: list[tuple[str, str, str, int, int]], replayed: list[tuple[str, str, str, int, int]]
) -> dict[str, int]:
    """Live against replayed: the same rule, market, side and entry price, made within a second
    (the live journal writes once a second)."""
    left = list(replayed)
    matched = 0
    for rule, ticker, side, at, entry in live:
        for n, (r2, t2, s2, at2, e2) in enumerate(left):
            if (r2, t2, s2, e2) == (rule, ticker, side, entry) and abs(at2 - at) <= NS:
                matched += 1
                del left[n]
                break
    return {"live": len(live), "replayed": len(replayed), "matched": matched,
            "live_only": len(live) - matched, "replay_only": len(left)}  # fmt: skip


# --- a replay run ------------------------------------------------------------------------------

WARMUP_NS = 40 * 60 * NS  # momentum's 5-minute window and its 30-minute validity, with room


async def run_replay(
    recordings: Path,
    out: Path,
    window: tuple[int, int],
    *,
    plugins: list[str] | None = None,
    plugin_settings: dict[str, dict[str, Any]] | None = None,
    warmup_ns: int = WARMUP_NS,
) -> dict[str, Any]:
    """Replay [``window``) of ``recordings`` through the signal plugins (all, or those named),
    with a scratch journal in ``out`` (an existing folder); returns the summary. Nothing
    outside ``out`` is written."""
    from ikars_engine.engine import Engine
    from ikars_engine.journal import Journal, JournalService
    from ikars_engine.kalshi.discovery import OPEN_STATUSES
    from ikars_engine.plugins import HostOptions, PluginKind, discover

    start_ns, end_ns = window
    begin = start_ns - warmup_ns
    begin -= begin % HOUR_NS  # from the hour's start, where its book checkpoint is
    found, _ = discover()
    chosen = [
        p for p in found
        if p.spec.kind is PluginKind.SIGNAL and (plugins is None or p.spec.name in plugins)
    ]  # fmt: skip
    unknown = sorted(set(plugins or ()) - {p.spec.name for p in chosen})
    if unknown:
        raise ValueError(f"not a signal plugin here: {', '.join(unknown)}")
    clock = VirtualClock(begin)
    journal = Journal(out / "journal.sqlite")
    service: JournalService | None = None

    def tick() -> None:
        if service is not None:
            service.step()

    replay = RecordingReplay(recordings, begin, end_ns, clock,
                             rerun=frozenset(p.spec.name for p in chosen), tick=tick)  # fmt: skip
    markets = replay.markets

    def result(ticker: str) -> str | None:
        info = markets.markets.get(ticker)
        return None if info is None else info.result

    def trading(ticker: str) -> bool | None:
        info = markets.markets.get(ticker)
        return None if info is None else info.status in OPEN_STATUSES

    service = JournalService(journal, replay.state.quotes, clock.now_ns, result=result,
                             trading=trading, fees=markets.fees_of)  # fmt: skip
    options = HostOptions(
        settings=plugin_settings or {},
        services={
            "market": markets.markets.get,
            "fees": markets.fees.get,
            "active": journal.active,
        },  # fmt: skip
    )
    engine = Engine(clock, replay, deterministic=True, host_options=options)
    for plugin in chosen:
        engine.host.add(plugin)
    engine.listeners.append(service.listen)
    try:
        await engine.run()
    finally:
        journal.close()
    made = recommendations(out / "journal.sqlite", start_ns, end_ns)
    stats = replay.stats
    return {
        "window": [ns_time(start_ns).isoformat(), ns_time(end_ns).isoformat()],
        "from": ns_time(begin).isoformat(),
        "plugins": sorted(p.spec.name for p in chosen),
        "rows": dict(sorted(stats.rows.items())),
        "items": dict(sorted(stats.items.items())),
        "late_rows": stats.late_rows,
        "market_rows": markets.rows,
        "partial": []
        if markets.rows
        else [
            "no market details recorded for this window (recorded before M1.13): signals that"
            " need them make nothing"
        ],
        "recommendations": len(made),
        "digest": stats.digest.hexdigest()[:16],
        "journal": str(out / "journal.sqlite"),
    }


def gaps_in(state: Path, start_ns: int, end_ns: int) -> list[dict[str, Any]]:
    """The engine's logged gaps overlapping the window (read only)."""
    if not state.is_file():
        return []
    with closing(sqlite3.connect(f"{state.as_uri()}?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT channel, reason, start_ns, end_ns, recovery, status FROM gap"
            " WHERE end_ns > ? AND start_ns < ? ORDER BY start_ns",
            (start_ns, end_ns),
        ).fetchall()
    return [
        {"channel": c, "reason": r, "from": ns_time(a).isoformat(), "to": ns_time(b).isoformat(),
         "recovery": rec, "status": s}
        for c, r, a, b, rec, s in rows
    ]  # fmt: skip
