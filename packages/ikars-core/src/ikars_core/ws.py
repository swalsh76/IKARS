"""The WebSocket feed for Kalshi's public data channels.

``KalshiFeed`` connects with a signed handshake, subscribes, and yields **events** in order:

* ``Data``: one parsed frame, with the local times it arrived and the connection it came on;
* ``Reconnected``: the link was lost and a new one is open. It is queued *before* anything is read
  from the new connection, so every ``Data`` after it is from the new link and the consumer knows
  what may have been missed (Kalshi replays nothing). ``reason`` says why: the link dropped, the
  consumer fell behind (``overflow``) or nothing arrived for too long (``stalled``);
* ``SequenceGap``: a subscription's ``seq`` jumped, so messages were lost on a link that stayed up;
* ``ResubscribeFailed``: Kalshi refused to restore one subscription after a reconnect.

Every wait and every queue is bounded. A feed whose consumer is slow does not grow without limit:
past ``queue_limit`` the link is dropped, the queued events are still delivered in order, and a
``Reconnected(overflow)`` follows. A link that stays up but sends nothing is dropped by the
watchdog. After a short-lived connection the reconnect delay keeps growing instead of starting
over, so a flapping link cannot hammer the server. ``sid`` and ``seq`` restart with each link, so
sequence tracking restarts with it.
"""

import asyncio
import contextlib
import json
import logging
import random
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlparse

import websockets.asyncio.client
from websockets.exceptions import ConnectionClosed, WebSocketException

from ikars_core.auth import DataSigner
from ikars_core.config import WS_URLS, Environment
from ikars_core.ratelimit import backoff
from ikars_core.ws_models import MARKET_DATA_CHANNELS, Frame, parse_frame

log = logging.getLogger("ikars_core")

STABLE_AFTER = 30.0  # a link that lasted this long counts as healthy: the next delay starts over


class FeedError(Exception):
    """A command was refused or failed, or the feed was lost for good."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message if code is None else f"[{code}] {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class FeedConfig:
    url: str = WS_URLS[Environment.DEMO]  # demo unless production is asked for (CLAUDE.md)
    queue_limit: int = 200_000
    command_timeout: float = 10.0
    idle_timeout: float | None = 60.0  # no frame at all for this long: the link is stalled
    ping_interval: float | None = 20.0
    ping_timeout: float | None = 20.0
    max_frame_bytes: int = 4 * 1024 * 1024
    close_timeout: float = 2.0  # how long a polite close may take before the link is cut
    reconnect_base: float = 0.5
    reconnect_max: float = 30.0
    max_reconnect_attempts: int | None = None  # None: keep trying

    def __post_init__(self) -> None:
        if self.queue_limit < 2:
            raise ValueError("queue_limit must be at least 2")
        for name in (
            "command_timeout",
            "reconnect_base",
            "reconnect_max",
            "max_frame_bytes",
            "close_timeout",
        ):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be positive")
        if self.reconnect_max < self.reconnect_base:
            raise ValueError("reconnect_max must be at least reconnect_base")
        if self.idle_timeout is not None and not self.idle_timeout > 0:
            raise ValueError("idle_timeout must be positive or None")


@dataclass(frozen=True, slots=True)
class Data:
    frame: Frame
    received_ns: int  # wall clock, nanoseconds since the epoch, as the frame was read
    mono_ns: int  # monotonic clock at the same moment, for ordering and delays
    generation: int  # which connection it came on (1 is the first)


@dataclass(frozen=True, slots=True)
class Reconnected:
    generation: int  # the new connection
    reason: str  # connection_lost, overflow or stalled
    downtime: float  # seconds from losing the old link to having the new one open


@dataclass(frozen=True, slots=True)
class SequenceGap:
    sid: int
    expected: int
    got: int
    generation: int


@dataclass(frozen=True, slots=True)
class ResubscribeFailed:
    channel: str
    error: str
    generation: int


FeedEvent = Data | Reconnected | SequenceGap | ResubscribeFailed
_END = object()


class _EventStream:
    def __init__(self, feed: "KalshiFeed") -> None:
        self._feed = feed

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> FeedEvent:
        return await self._feed._next_event()  # noqa: SLF001 (the stream is part of the feed)


class _Overflow(Exception):
    """Internal: the consumer fell too far behind; the link is dropped."""


@dataclass
class _Subscription:
    channel: str
    params: dict[str, Any]
    sid: int | None = None


class KalshiFeed:
    def __init__(
        self,
        signer: DataSigner,
        config: FeedConfig | None = None,
        *,
        connect: Callable[..., Awaitable[Any]] = websockets.asyncio.client.connect,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        time_ns: Callable[[], int] = time.time_ns,
        rng: random.Random | None = None,
    ) -> None:
        self._signer = signer
        self._config = config or FeedConfig()
        self._path = urlparse(self._config.url).path
        self._connect = connect
        self._sleep = sleep
        self._monotonic = monotonic
        self._time_ns = time_ns
        self._rng = rng or random.Random()  # noqa: S311 (reconnect jitter, not a secret)
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._subscriptions: list[_Subscription] = []
        self._pending: dict[int, asyncio.Future[Frame]] = {}
        self._next_id = 1
        self._conn: Any = None
        self._supervisor: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._closing = False
        self._lost: str | None = None
        self._generation = 0
        self._last_seq: dict[int, int] = {}
        self._last_frame_at = 0.0
        self._stats: Counter[str] = Counter()
        self._by_type: Counter[str] = Counter()
        self._unknown: Counter[str] = Counter()
        self._errors: Counter[str] = Counter()
        self._reasons: Counter[str] = Counter()
        self._high_water = 0
        if self._config.url == WS_URLS[Environment.PRODUCTION]:
            log.warning("Kalshi WebSocket feed using the PRODUCTION environment (read-only)")

    # --- lifecycle -------------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        """Open the first connection. Raises ``FeedError`` if the attempts run out first."""
        self._supervisor = asyncio.create_task(self._supervise())
        waiter = asyncio.create_task(self._ready.wait())
        done, _ = await asyncio.wait(
            {waiter, self._supervisor}, return_when=asyncio.FIRST_COMPLETED
        )
        if waiter not in done:
            waiter.cancel()
            raise FeedError(self._lost or "could not connect")

    async def close(self) -> None:
        self._closing = True
        conn = self._conn
        if conn is not None:
            with contextlib.suppress(Exception):
                await conn.close()
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
        self._fail_pending("the feed was closed")
        self._queue.put_nowait(_END)

    # --- commands --------------------------------------------------------------------------

    async def subscribe(self, channel: str, market_tickers: list[str] | None = None) -> int:
        """Subscribe to a channel (all markets if no tickers); returns the subscription id.

        The subscription is remembered and restored after every reconnect. Only the market-data
        channels are allowed: the connection is signed with the data key, which never reaches
        account data, so a private channel is refused here before anything is sent.
        """
        if channel not in MARKET_DATA_CHANNELS:
            raise FeedError(
                f"the data key subscribes to market-data channels only, not {channel!r}"
            )
        params: dict[str, Any] = {"channels": [channel]}
        if market_tickers:
            params["market_tickers"] = list(market_tickers)
        reply = await self._command("subscribe", params)
        sid = self._sid_of(reply)
        self._subscriptions.append(_Subscription(channel, params, sid))
        return sid

    def sid_of(self, channel: str) -> int | None:
        """The current subscription id of ``channel`` (it changes with every link), or None."""
        for sub in self._subscriptions:
            if sub.channel == channel:
                return sub.sid
        return None

    async def resubscribe(self, channel: str) -> int:
        """Send a remembered subscription again (after a failed restore); returns its new sid."""
        for sub in self._subscriptions:
            if sub.channel == channel:
                reply = await self._command("subscribe", sub.params)
                sub.sid = self._sid_of(reply)
                return sub.sid
        raise FeedError(f"no subscription to {channel!r} to send again")

    def channel_of(self, sid: int) -> str | None:
        """The channel a subscription id belongs to on the current link, or None if unknown.

        ``sid`` changes with every link, so a sid taken from an event is only meaningful against
        the feed's present subscriptions (a gap on a lost link is named by its channel at once).
        """
        for sub in self._subscriptions:
            if sub.sid == sid:
                return sub.channel
        return None

    async def update_subscription(
        self, sid: int, action: str, market_tickers: list[str]
    ) -> list[str]:
        """Add (``add_markets``) or remove (``delete_markets``) markets on a live subscription.

        Returns the full market set after the change, which also replaces the remembered set.
        """
        reply = await self._command(
            "update_subscription",
            {"sid": sid, "market_tickers": market_tickers, "action": action},
        )
        message = reply.raw.get("msg")
        current = message.get("market_tickers") if isinstance(message, dict) else None
        if current is None:
            raise FeedError(f"{action}: the reply carried no market set")
        for sub in self._subscriptions:
            if sub.sid == sid:
                sub.params = {**sub.params, "market_tickers": list(current)}
        return [str(ticker) for ticker in current]

    async def get_snapshot(self, sid: int, market_tickers: list[str]) -> None:
        """Ask for a fresh ``orderbook_snapshot`` of each market on an order-book subscription,
        without changing the subscription (``update_subscription`` with ``get_snapshot``). The
        snapshots arrive on the feed like any frame."""
        await self._command(
            "update_subscription",
            {"sid": sid, "market_tickers": market_tickers, "action": "get_snapshot"},
        )

    async def unsubscribe(self, sid: int) -> None:
        await self._command("unsubscribe", {"sids": [sid]})
        self._subscriptions = [s for s in self._subscriptions if s.sid != sid]

    async def _command(self, cmd: str, params: dict[str, Any]) -> Frame:
        conn = self._conn
        if conn is None or not self._ready.is_set():
            raise FeedError("not connected")
        command_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[Frame] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        try:
            await conn.send(json.dumps({"id": command_id, "cmd": cmd, "params": params}))
            return await asyncio.wait_for(future, self._config.command_timeout)
        except TimeoutError:
            raise FeedError(f"{cmd} timed out") from None
        except ConnectionClosed:
            raise FeedError("connection closed") from None
        finally:
            self._pending.pop(command_id, None)

    @staticmethod
    def _sid_of(reply: Frame) -> int:
        message = reply.raw.get("msg")
        sid = message.get("sid") if isinstance(message, dict) else None
        if not isinstance(sid, int) or isinstance(sid, bool):
            raise FeedError("the subscribe reply carried no subscription id")
        return sid

    def _fail_pending(self, why: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(FeedError(why))
        self._pending.clear()

    # --- the event stream ------------------------------------------------------------------

    def events(self) -> AsyncIterator[FeedEvent]:
        """The events in order; ends on a deliberate close, raises if the feed is lost.

        This is an iterator object, not a generator, on purpose: cancelling a wait for the next
        event (a timeout around ``anext``) must lose nothing and must not end the stream.
        """
        return _EventStream(self)

    async def _next_event(self) -> FeedEvent:
        item = await self._queue.get()
        if item is _END:
            self._queue.put_nowait(_END)  # later reads end too
            if self._closing:
                raise StopAsyncIteration
            raise FeedError(self._lost or "the feed ended")
        return item  # type: ignore[no-any-return]

    def _emit(self, event: FeedEvent) -> None:
        depth = self._queue.qsize()
        if depth >= self._config.queue_limit and isinstance(event, Data):
            self._stats["overflows"] += 1
            raise _Overflow
        self._queue.put_nowait(event)
        self._high_water = max(self._high_water, depth + 1)

    def stats(self) -> dict[str, Any]:
        """Everything ``status`` needs: queue health, reconnects, gaps, schema drift."""
        idle = self._monotonic() - self._last_frame_at if self._last_frame_at else None
        return {
            "connected": self._ready.is_set(),
            "generation": self._generation,
            "queue_depth": self._queue.qsize(),
            "queue_limit": self._config.queue_limit,
            "high_water": self._high_water,
            "reconnects": dict(self._reasons),
            "overflows": self._stats["overflows"],
            "stalls": self._stats["stalls"],
            "sequence_gaps": self._stats["gaps"],
            "bad_frames": self._stats["bad_frames"],
            "frames_by_type": dict(self._by_type),
            "parse_errors_by_type": dict(self._errors),
            "unknown_fields_by_type": dict(self._unknown),
            "seconds_since_last_frame": idle,
        }

    # --- the connection --------------------------------------------------------------------

    async def _open(self) -> Any:
        headers = self._signer.headers("GET", self._path)  # fresh: the timestamp must be current
        return await self._connect(
            self._config.url,
            additional_headers=headers,
            max_size=self._config.max_frame_bytes,
            close_timeout=self._config.close_timeout,
            ping_interval=self._config.ping_interval,
            ping_timeout=self._config.ping_timeout,
        )

    def _backoff(self, attempt: int) -> float:
        cfg = self._config
        return backoff(attempt, cfg.reconnect_base, cfg.reconnect_max) * self._rng.uniform(0.5, 1.0)

    async def _supervise(self) -> None:
        attempt = 0
        reason = "connection_lost"
        lost_at: float | None = None
        try:
            while not self._closing:
                conn = None
                while conn is None and not self._closing:
                    limit = self._config.max_reconnect_attempts
                    if limit is not None and attempt >= limit:
                        self._lost = "reconnect attempts exhausted"
                        return
                    if attempt > 0 or lost_at is not None:
                        await self._sleep(self._backoff(max(attempt - 1, 0)))
                    attempt += 1
                    try:
                        conn = await self._open()
                    except (OSError, WebSocketException, TimeoutError) as exc:
                        log.warning("Kalshi WebSocket connect attempt %d failed: %s", attempt, exc)
                if conn is None:
                    return
                self._generation += 1
                self._last_seq.clear()
                self._last_frame_at = self._monotonic()
                if self._generation > 1:
                    downtime = self._monotonic() - (lost_at or self._monotonic())
                    self._queue.put_nowait(Reconnected(self._generation, reason, downtime))
                    self._reasons[reason] += 1
                opened = self._monotonic()
                self._conn = conn
                reason = await self._session(conn)
                self._ready.clear()
                self._conn = None
                self._fail_pending("connection closed")
                lost_at = self._monotonic()
                if lost_at - opened >= STABLE_AFTER:
                    attempt = 0
                if reason == "overflow":
                    # reconnecting into a still-full queue would only overflow again
                    while self._queue.qsize() > self._config.queue_limit // 2 and not self._closing:
                        await self._sleep(0.05)
        finally:
            self._ready.clear()
            self._queue.put_nowait(_END)

    async def _session(self, conn: Any) -> str:
        """Read one connection until it ends; returns why."""
        self._ready.set()
        generation = self._generation
        reader = asyncio.create_task(self._read(conn))
        watchdog = asyncio.create_task(self._watch())
        restorer = asyncio.create_task(self._restore(generation)) if generation > 1 else None
        tasks = {reader, watchdog} | ({restorer} if restorer else set())
        reason = "connection_lost"
        try:
            while True:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if watchdog in done:
                    self._stats["stalls"] += 1
                    reason = "stalled"
                    break
                if reader in done:
                    exc = reader.exception()
                    reason = "overflow" if isinstance(exc, _Overflow) else "connection_lost"
                    break
                if restorer is not None and restorer in done:
                    if restorer.exception() is not None:
                        break  # the link failed while restoring: treat as lost
                    tasks.discard(restorer)
        finally:
            for task in tasks | {reader, watchdog}:
                task.cancel()
            await asyncio.gather(*tasks, reader, watchdog, return_exceptions=True)
            with contextlib.suppress(Exception):
                if reason in ("overflow", "stalled"):
                    # Nothing is reading this link any more, so a polite close could not finish:
                    # cut it now instead of waiting out the close timeout.
                    conn.transport.abort()
                else:
                    await conn.close()
        return reason

    async def _read(self, conn: Any) -> None:
        try:
            async for raw in conn:
                self._handle(raw)
        except ConnectionClosed:
            return
        except WebSocketException as exc:
            log.warning("Kalshi WebSocket read failed: %s", exc)

    async def _watch(self) -> None:
        idle = self._config.idle_timeout
        if idle is None:
            await asyncio.Event().wait()  # never fires
            return
        while True:
            await self._sleep(idle / 4)
            if self._subscriptions and self._monotonic() - self._last_frame_at > idle:
                return

    async def _restore(self, generation: int) -> None:
        """Put the subscriptions back on a new link, in the order they were made."""
        for sub in list(self._subscriptions):
            try:
                reply = await self._command("subscribe", sub.params)
                sub.sid = self._sid_of(reply)
            except FeedError as exc:
                if exc.code is None:
                    raise  # transport trouble: the session ends and tries again
                sub.sid = None  # the old link's id means nothing (or another channel) on this one
                log.warning("resubscribe to %s failed: %s", sub.channel, exc)
                self._queue.put_nowait(ResubscribeFailed(sub.channel, str(exc), generation))

    def _handle(self, raw: str | bytes) -> None:
        received_ns, mono_ns = self._time_ns(), int(self._monotonic() * 1e9)
        self._last_frame_at = self._monotonic()
        try:
            frame = parse_frame(raw)
        except ValueError:
            self._stats["bad_frames"] += 1
            log.warning("dropping an unparseable WebSocket frame: %.200r", raw)
            return
        self._by_type[frame.type] += 1
        if frame.is_response:
            gap = self._count_sequence(frame)  # a reply uses up a number, so it can show a gap
            if gap is not None:
                self._emit(gap)
            self._resolve(frame)
            return
        if frame.error:
            self._errors[frame.type] += 1
        for name in frame.unknown:
            self._unknown[f"{frame.type}.{name}"] += 1
        gap = self._count_sequence(frame)
        if gap is not None:
            self._emit(gap)
        self._emit(Data(frame, received_ns, mono_ns, self._generation))

    def _count_sequence(self, frame: Frame) -> SequenceGap | None:
        """Track ``seq`` per subscription. A reply to a command uses up a number too."""
        if frame.sid is None or frame.seq is None:
            return None
        last = self._last_seq.get(frame.sid)
        self._last_seq[frame.sid] = frame.seq
        if last is None or frame.seq == last + 1:
            return None
        self._stats["gaps"] += 1
        return SequenceGap(frame.sid, last + 1, frame.seq, self._generation)

    def _resolve(self, frame: Frame) -> None:
        future = self._pending.pop(frame.id, None) if frame.id is not None else None
        if future is None or future.done():
            log.debug("unmatched reply: %s", frame.type)
            return
        if frame.type == "error":
            message = frame.raw.get("msg")
            detail = message if isinstance(message, dict) else {}
            code = detail.get("code")
            future.set_exception(
                FeedError(str(detail.get("msg", "error")), code if isinstance(code, int) else None)
            )
        else:
            future.set_result(frame)
