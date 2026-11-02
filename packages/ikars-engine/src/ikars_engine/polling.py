"""Polling sources (M2.1; PLAN §4.3): the base for source plugins that fetch over HTTP (weather,
news) and the engine-side poller they share.

A ``PollingSource`` names its feeds and how to read one reply; the base does the rest, the same for
every feed:

- **Schedule.** Each feed polls on its own interval with a little jitter, never sooner than the
  server says its data can change (``Cache-Control: max-age``). After a start the first poll comes
  when the next one is due, not all at once.
- **Politeness.** HTTPS only; a request budget per host; ``Retry-After`` honoured on 429 and 503;
  errors back off with jitter; a reply larger than the limit is refused; every call has a timeout.
  The User-Agent names IKARS and a contact (NWS requires one; owner, 2026-10-28: the repository's
  URL, and an email only from the local settings).
- **Conditional GET.** The server's ``ETag`` and ``Last-Modified`` are kept in the state store, so a
  restart asks "changed since?". Both are sent: NWS answers 304 to ``If-Modified-Since`` but its
  weak ``ETag`` names the server that answered, so it never matches (M2.1 live probe).
- **Once only.** Each feed remembers the ids it emitted (in the state store, for ``SEEN_DAYS``), so
  a restart or an overlapping page never records an item twice. Feeds that share a ``seen`` scope
  (M2.3: every news feed, by cleaned link) share those ids, so an item two of them list goes out
  once, from whichever delivered it first.
- **Gaps.** A feed unreachable for more than ``GAP_AFTER`` times its interval (owner, 2026-10-28),
  or across an engine stop that long, gets an entry in the engine's gap log for the window it
  missed, once it answers again. Shorter blips are retried quietly and counted in health.

Items go out through ``ctx.emit``, so the host stamps their received time and the recorder keeps
them (D20 A); in M2 nothing consumes them yet (record-only).
"""

import asyncio
import copy
import logging
import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import msgspec
from ikars_core.ratelimit import TokenBucket

from ikars_engine.clocks import EventClock, exact_ns
from ikars_engine.model import Item, Ref
from ikars_engine.plugins import PluginBase, PluginContext, PluginError
from ikars_engine.store import StateStore

log = logging.getLogger("ikars_engine.polling")

REPOSITORY = "https://github.com/swalsh76/IKARS"
NS = 1_000_000_000
MAX_BYTES = 5_000_000  # a feed reply larger than this is refused
GAP_AFTER = 2  # times a feed's interval (owner, 2026-10-28)
SEEN_DAYS = 14  # feeds list only recent items
JITTER = 0.1  # of the interval, either way
BACKOFF_MAX_S = 900.0
RETRY_AFTER_MAX_S = 3_600.0
HOST_RATE = 1.0  # requests a second to one host, across every feed on it
TIMEOUT_S = 30.0


def user_agent(version: str, contact: str = "") -> str:
    """``IKARS/<version> (+<repository>[; <contact>])``; the contact only from local settings."""
    tail = f"; {contact}" if contact.strip() else ""
    return f"IKARS/{version} (+{REPOSITORY}{tail})"


class Feed(msgspec.Struct, frozen=True, kw_only=True):
    name: str  # unique across all sources: "<plugin>.<feed>"
    url: str
    every_s: float
    accept: str = "*/*"
    kind: str | None = None  # the item kind for this feed; None: the source's item_kind
    seen: str | None = None  # the scope of the ids remembered; None: the feed's own (M2.3)
    conditional: bool = True  # False: always the whole reply (a small index read every time)
    # the source fetches what a gap missed and logs its own gaps, what it recovered and what it
    # could not, so the poller logs none for it (M2.3: GDELT's files; per-commit review)
    backfills: bool = False
    # how far back one reply reaches (a search over the last hour); a gap longer than this lost
    # what fell before it, so it is logged as unrecoverable (M1.R3 C7); None: the whole feed
    reach_s: float | None = None


class StopFeed(Protocol):
    """Stops a feed by name; ``closed``: on purpose, as nothing it serves trades now, so the time
    it is off is no gap (M1.R3 C7)."""

    def __call__(self, name: str, *, closed: bool = False) -> None: ...


class Found(msgspec.Struct, frozen=True, kw_only=True):
    """One item a reply holds, as its source reads it."""

    id: str  # stable for the same item in every reply that lists it
    published_ns: int | None = None
    payload: dict[str, Any] = {}


class PollError(Exception):
    """A poll that failed; ``retry_after_s`` when the server said when to come back."""

    def __init__(
        self,
        reason: str,
        retry_after_s: float | None = None,
        *,
        throttled: bool = False,
        status: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.retry_after_s = retry_after_s
        self.throttled = throttled  # 429 or 503: the server asked IKARS to slow down
        self.status = status  # the HTTP status, when the server answered


@dataclass(slots=True)
class FeedHealth:
    every_s: float
    polls: int = 0
    not_modified: int = 0
    errors: int = 0
    emitted: int = 0
    gaps: int = 0
    last_ok_ns: int | None = None
    wait_s: float | None = None  # the healthy wait: the interval, or the server's max-age
    last_error: str | None = None
    failing_since_ns: int | None = None
    next_ns: int | None = None


class Fetched(msgspec.Struct, frozen=True):
    body: bytes
    max_age_s: float | None
    etag: str | None = None  # kept only once the reply's items have gone out (per-commit review)
    last_modified: str | None = None


class Poller:
    """The engine's one HTTP client for polling sources: the budget per host, the validators, the
    ids already emitted, the gap log and each feed's health. Shared by every polling plugin."""

    def __init__(
        self,
        store: StateStore,
        clock: EventClock,
        agent: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_bytes: int = MAX_BYTES,
        host_rate: float = HOST_RATE,
    ) -> None:
        self._store = store
        self._clock = clock
        self._max_bytes = max_bytes
        self._host_rate = host_rate
        self._buckets: dict[str, TokenBucket] = {}
        self._client = httpx.AsyncClient(
            headers={"User-Agent": agent},
            timeout=TIMEOUT_S,
            follow_redirects=False,  # a redirect is a reply to read, never a hop to another host
            transport=transport,
        )
        self.feeds: dict[str, FeedHealth] = {}
        self.notes: dict[str, dict[str, Any]] = {}  # a source's own state (M2.2: its stations)
        self._pruned_ns: int | None = None
        # ids handed out by fresh() and not yet emitted, per scope: two feeds of one scope polled
        # at once never both send an item while the first is still going out
        self._claimed: dict[str, set[str]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    def health(self) -> dict[str, object]:
        """Each feed's counts and times (no URLs: a feed's address can carry a query), and each
        source's own notes."""
        return {
            "feeds": {name: _health_view(h) for name, h in sorted(self.feeds.items())},
            # a copy: the snapshot never changes when a source edits its notes (merge review 8)
            "notes": copy.deepcopy(dict(sorted(self.notes.items()))),
        }

    async def turn(self, url: str) -> None:
        """Wait for ``url``'s host to allow one more request. A poll waits here before its timed
        call, so a queue of feeds on a slow host never runs out its timeout waiting (merge review
        5); the request that follows uses this turn."""
        await self._host(url).acquire(1.0)

    async def fetch(self, feed: Feed, *, turned: bool = False) -> Fetched | None:
        """The feed's reply, or None when it has not changed since the last good poll.
        ``turned``: the host's turn was already taken (``turn``)."""
        headers = {"Accept": feed.accept}
        if feed.conditional:
            etag, modified, _, _ = self._store.poll_feed(feed.name)
            if etag:
                headers["If-None-Match"] = etag
            if modified:
                headers["If-Modified-Since"] = modified
        return await self._get(feed.url, headers, turned=turned)

    async def once(self, url: str, accept: str = "*/*", max_bytes: int | None = None) -> bytes:
        """One lookup (a station, a forecast grid, a file an index names), with the same rules as
        a poll but nothing kept: HTTPS, the host's budget, the size limit."""
        fetched = await self._get(url, {"Accept": accept}, max_bytes)
        if fetched is None:  # never asked "changed since?", so never 304
            raise PollError("HTTP 304", status=304)
        return fetched.body

    def limit_host(self, host: str, per_second: float) -> None:
        """A host that asks for fewer requests than the default (M2.3: GDELT's API, one every 5
        seconds), across every feed and lookup on it."""
        self._buckets[host] = self._bucket(per_second)

    def _bucket(self, per_second: float) -> TokenBucket:
        return TokenBucket(
            per_second,
            1.0,
            clock=lambda: self._clock.monotonic_ns() / NS,  # the engine's clock, as everywhere
            sleep=self._clock.sleep,
        )

    def _host(self, url: str) -> TokenBucket:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise PollError("only https feeds are polled")
        bucket = self._buckets.get(parts.hostname)
        if bucket is None:
            bucket = self._buckets[parts.hostname] = self._bucket(self._host_rate)
        return bucket

    async def _get(
        self,
        url: str,
        headers: dict[str, str],
        max_bytes: int | None = None,
        *,
        turned: bool = False,
    ) -> Fetched | None:
        limit = self._max_bytes if max_bytes is None else max_bytes  # 0 is a limit too
        bucket = self._host(url)
        if not turned:
            await bucket.acquire(1.0)
        try:
            async with self._client.stream("GET", url, headers=headers) as reply:
                if reply.status_code == 304:
                    return None
                code = reply.status_code
                if code in (429, 503):
                    retry = _retry_after(reply.headers, self._clock.now_ns())
                    raise PollError(f"HTTP {code}", retry, throttled=True, status=code)
                if code != 200:
                    raise PollError(f"HTTP {code}", status=code)
                body = bytearray()
                async for chunk in reply.aiter_bytes():
                    body += chunk
                    if len(body) > limit:
                        raise PollError(f"the reply is larger than {limit} bytes", status=code)
                etag, modified = reply.headers.get("etag"), reply.headers.get("last-modified")
        except httpx.HTTPError as exc:
            raise PollError(type(exc).__name__) from exc
        return Fetched(bytes(body), _max_age(reply.headers), etag, modified)

    def started(self, feed: Feed) -> FeedHealth:
        """The feed's health, with its last good poll and healthy wait as kept across restarts."""
        health = self.feeds.setdefault(feed.name, FeedHealth(feed.every_s))
        _, _, health.last_ok_ns, health.wait_s = self._store.poll_feed(feed.name)
        return health

    def succeeded(
        self, feed: Feed, health: FeedHealth, fetched: Fetched | None, wait_s: float
    ) -> None:
        """A good poll, its items emitted: the reply's validators are kept now (so a reply whose
        items failed to go out is asked for again), and a gap is logged for the window since the
        last good poll when it was longer than ``GAP_AFTER`` healthy waits (the interval, or the
        server's max-age when longer: merge review 3)."""
        now = self._clock.now_ns()
        last_ok, healthy = health.last_ok_ns, max(feed.every_s, health.wait_s or 0.0)
        if not feed.backfills and last_ok is not None and now - last_ok > GAP_AFTER * healthy * NS:
            cause = health.last_error or "the engine was not running"
            if feed.reach_s is not None and now - last_ok > feed.reach_s * NS:
                detail = f"no good poll ({cause}); a reply reaches back only {feed.reach_s:.0f} s"
                self.log_gap(feed.name, last_ok, now, "unrecoverable", detail)
            else:
                detail = f"no good poll ({cause}); the next one returned the feed's current items"
                self.log_gap(feed.name, last_ok, now, "refetched", detail)
            health.gaps += 1
        if fetched is None:  # 304: the validators stay
            self._store.set_poll_ok(feed.name, now, wait_s)
        else:
            self._store.set_poll_feed(feed.name, fetched.etag, fetched.last_modified, now, wait_s)
        health.last_ok_ns, health.wait_s = now, wait_s
        health.failing_since_ns = None
        health.last_error = None

    def bookmark(self, name: str) -> int | None:
        """Where a source got to (M2.3: GDELT's last file read), kept across restarts."""
        return self._store.bookmark(name)

    def set_bookmark(self, name: str, value: int) -> None:
        self._store.set_bookmark(name, value)

    def clear_bookmark(self, name: str) -> None:
        self._store.delete_bookmarks(name)

    def log_gap(self, channel: str, start_ns: int, end_ns: int, recovery: str, detail: str) -> None:
        """A window a source missed, in the engine's gap log: recovered (``refetched``,
        ``backfill``) or not (``unrecoverable``)."""
        now = self._clock.now_ns()
        gap = self._store.add_gap(channel, "unreachable", start_ns, end_ns, recovery, now, detail)
        if recovery != "unrecoverable":
            self._store.finish_gap(gap, "done", detail)

    def log_backfill(self, channel: str, start_ns: int, end_ns: int, detail: str) -> None:
        """A window a source fetched afterwards, logged as ``backfill`` apart from the parts it
        already logged as lost, so no window is ever logged both ways (merge review)."""
        lost = sorted(
            (g.start_ns, g.end_ns)
            for g in self._store.gaps("unrecoverable")
            if g.channel == channel and g.end_ns > start_ns and g.start_ns < end_ns
        )
        at = start_ns
        for a, b in lost:
            if a > at:
                self.log_gap(channel, at, a, "backfill", detail)
            at = max(at, b)
        if at < end_ns:
            self.log_gap(channel, at, end_ns, "backfill", detail)

    def fresh(self, feed: Feed, found: list[Found]) -> list[Found]:
        """The items ``feed``'s scope has not emitted before and no other feed is sending now,
        each once, in their order. They stay claimed until ``emitted``."""
        scope = feed.seen or feed.name
        claimed = self._claimed.setdefault(scope, set())
        first: dict[str, Found] = {}
        for f in found:
            if f.id not in claimed:
                first.setdefault(f.id, f)
        fresh = [first[i] for i in self._store.unseen(scope, list(first))]
        claimed.update(f.id for f in fresh)
        return fresh

    def emitted(self, feed: Feed, ids: list[str], offered: Sequence[str] = ()) -> None:
        """``ids`` emitted, or still listed, by ``feed`` now; ``offered`` (what ``fresh`` handed
        out) is no longer claimed. An id another feed has claimed is not remembered here: that
        feed may yet fail to send it (merge review 1)."""
        now = self._clock.now_ns()
        scope = feed.seen or feed.name
        claimed = self._claimed.get(scope, set())
        mine = set(offered)
        try:
            self._store.remember(scope, [i for i in ids if i in mine or i not in claimed], now)
        finally:
            claimed.difference_update(mine)  # released even when the store fails (merge review 8)
        if self._pruned_ns is None or now - self._pruned_ns > 86_400 * NS:  # once a day
            self._pruned_ns = now
            self.forget_old()

    def closed(self, name: str) -> None:
        """``name`` stopped on purpose: its last good poll is forgotten, so starting it again later
        logs no gap for the time it was off (M1.R3 C7)."""
        self._store.clear_poll_ok(name)

    def unchanged(self, feed: Feed) -> None:
        """``feed`` answered "not modified": what it listed is still listed, so never forgotten,
        however long it stays unchanged (an off-season feed: M1.R3 C3)."""
        self._store.touch_seen(feed.seen or feed.name, self._clock.now_ns())

    def forget_old(self) -> int:
        return self._store.forget_seen(self._clock.now_ns() - SEEN_DAYS * 86_400 * NS)


class PollingSource(PluginBase):
    """A source plugin that polls feeds. Subclasses set ``spec`` (a source that produces
    ``item_kind``) and ``item_kind``, and implement ``feeds`` and ``parse``. The engine offers the
    shared ``Poller`` as the service ``"poller"``."""

    item_kind: str

    def feeds(self, ctx: PluginContext) -> list[Feed]:
        raise NotImplementedError

    def parse(self, feed: Feed, body: bytes, ctx: PluginContext) -> list[Found]:
        raise NotImplementedError

    async def read(
        self, feed: Feed, body: bytes, poller: Poller, ctx: PluginContext
    ) -> list[Found]:
        """The items a reply holds. The default parses it; a source whose reply names more to
        fetch overrides this (M2.3: GDELT's index names its newest files)."""
        return self.parse(feed, body, ctx)

    def delivered(self, feed: Feed) -> None:
        """Every item of ``feed``'s last reply went out (M2.3: GDELT moves its bookmark on)."""

    async def manage(
        self,
        ctx: PluginContext,
        poller: Poller,
        start: Callable[[Feed], None],
        stop: StopFeed,
    ) -> None:
        """Start the feeds. The default starts ``feeds()`` once; a source whose feeds come and go
        (M2.2: a city named by markets that open and close) overrides it, and calls ``start`` and
        ``stop`` (by feed name) as they do."""
        for feed in self.feeds(ctx):
            start(feed)

    async def run(self, ctx: PluginContext) -> None:
        get = ctx.services.get("poller")
        if get is None:
            ctx.log.warning("no poller in this engine: the source stays idle")
            return
        poller: Poller = get()
        running: dict[str, asyncio.Task[None]] = {}
        # a TaskGroup: one feed's loop that fails cancels the others, so a restart never leaves
        # two loops polling one feed (merge review 2); a feed stopped on purpose just ends
        async with asyncio.TaskGroup() as group:

            def start(feed: Feed) -> None:
                if feed.name not in running:
                    task = group.create_task(self._loop(feed, poller, ctx), name=feed.name)
                    running[feed.name] = task

            def stop(name: str, *, closed: bool = False) -> None:
                task = running.pop(name, None)
                if task is not None:
                    task.cancel()
                poller.feeds.pop(name, None)  # a stopped feed leaves health (merge review 3)
                if closed:
                    poller.closed(name)

            await self.manage(ctx, poller, start, stop)

    async def _loop(self, feed: Feed, poller: Poller, ctx: PluginContext) -> None:
        health = poller.started(feed)
        rng = random.Random(feed.name)  # noqa: S311 (jitter, not security; the same per feed)
        last_ok = health.last_ok_ns
        now = ctx.clock.now_ns()
        due = now if last_ok is None else max(now, last_ok + int(feed.every_s * NS))
        failures = 0
        while True:
            health.next_ns = due
            await _sleep_until(ctx.clock, due)
            health.polls += 1
            wait_s = feed.every_s
            try:
                await poller.turn(feed.url)  # outside the timeout (merge review 5)
                fetched = await ctx.call(poller.fetch(feed, turned=True), TIMEOUT_S * 2)
                if fetched is None:
                    health.not_modified += 1
                    poller.unchanged(feed)
                else:
                    await self._emit_new(feed, fetched.body, poller, ctx, health)
                    if fetched.max_age_s is not None:
                        wait_s = max(wait_s, fetched.max_age_s)
                poller.succeeded(feed, health, fetched, wait_s)
                failures = 0
            except PluginError:
                raise  # a fault in the plugin, not the feed: the host counts it (merge review 7)
            except Exception as exc:  # one feed's trouble never stops the others
                failures += 1
                health.errors += 1
                health.last_error = str(exc) if isinstance(exc, PollError) else type(exc).__name__
                if health.failing_since_ns is None:
                    health.failing_since_ns = ctx.clock.now_ns()
                error = exc if isinstance(exc, PollError) else None
                wait_s = backoff_s(feed.every_s, failures, error)
                ctx.log.warning(
                    "poll failed", extra={"feed": feed.name, "error": health.last_error}
                )
            wait_s *= 1 + rng.uniform(-JITTER, JITTER)
            due = ctx.clock.now_ns() + int(wait_s * NS)

    async def _emit_new(
        self, feed: Feed, body: bytes, poller: Poller, ctx: PluginContext, health: FeedHealth
    ) -> None:
        listed = await self.read(feed, body, poller, ctx)
        sent: list[str] = []
        done = False
        fresh = poller.fresh(feed, listed)
        try:
            for one in fresh:
                await ctx.emit(
                    Item(
                        ref=Ref(feed.kind or self.item_kind, one.id),
                        source=self.spec.name,
                        received_ns=0,  # the host stamps it
                        published_ns=one.published_ns,
                        payload={**one.payload, "feed": feed.name},
                    )
                )
                sent.append(one.id)
            done = True
        finally:
            # all went out: every listed id is refreshed (still listed: never forgotten); part
            # went out: those are remembered, so a retry never sends them twice (merge review 6)
            offered = [f.id for f in fresh]
            poller.emitted(feed, [f.id for f in listed] if done else sent, offered)
            health.emitted += len(sent)
        self.delivered(feed)


def iso_ns(text: str | None) -> int | None:
    """An ISO 8601 time in exact nanoseconds; None when it has no offset (never guessed as local
    time: M2.2 merge review 9) or does not parse."""
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return exact_ns(when)


async def _sleep_until(clock: EventClock, due_ns: int) -> None:
    left = (due_ns - clock.now_ns()) / NS
    if left > 0:
        await clock.sleep(left)


_MAX_AGE = re.compile(r"(?:^|,)\s*max-age\s*=\s*(\d+)", re.IGNORECASE)


def _max_age(headers: httpx.Headers) -> float | None:
    found = _MAX_AGE.search(headers.get("cache-control", ""))
    return float(found.group(1)) if found else None


def backoff_s(every_s: float, failures: int, error: PollError | None) -> float:
    """The wait after the ``failures``-th failure in a row. A first ordinary error is retried at
    half the interval, so one blip stays under the gap rule; then from the interval, doubling up to
    ``BACKOFF_MAX_S`` (or the interval, when longer). A server that said slow down is never asked
    sooner than the interval or its Retry-After (merge review 1 and 4)."""
    if failures == 1 and not (error is not None and error.throttled):
        return every_s / 2
    wait = min(every_s * 2.0 ** min(failures - 1, 10), max(BACKOFF_MAX_S, every_s))
    if error is not None and error.retry_after_s is not None:
        wait = max(wait, min(error.retry_after_s, RETRY_AFTER_MAX_S))
    return wait


def _retry_after(headers: httpx.Headers, now_ns: int) -> float | None:
    value = headers.get("retry-after", "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    now = datetime.fromtimestamp(now_ns / NS, UTC)  # the engine's clock (merge review 8)
    return max(0.0, (when - now).total_seconds())


def _health_view(h: FeedHealth) -> dict[str, Any]:
    return {
        "every_s": h.every_s,
        "polls": h.polls,
        "not_modified": h.not_modified,
        "errors": h.errors,
        "emitted": h.emitted,
        "gaps": h.gaps,
        "last_ok_ns": h.last_ok_ns,
        "last_error": h.last_error,
        "failing_since_ns": h.failing_since_ns,
        "next_ns": h.next_ns,
    }


def poller_service(poller: Poller) -> Callable[[], Poller]:
    """For ``HostOptions.services``: the plugins ask for the shared poller by name."""
    return lambda: poller
