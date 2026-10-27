"""The WebSocket feed against a real local server that drops, stalls, floods and skips."""

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fakewsserver import (
    FakeKalshi,
    error_reply,
    read_command,
    subscribe_then,
    subscribed,
    trade,
)
from ikars_core.auth import HEADER_KEY, HEADER_SIGNATURE, HEADER_TIMESTAMP, DataSigner
from ikars_core.config import WS_URLS, Environment
from ikars_core.ws import (
    Data,
    FeedConfig,
    FeedError,
    KalshiFeed,
    Reconnected,
    ResubscribeFailed,
    SequenceGap,
)
from ikars_core.ws_models import TradeMsg
from websockets.asyncio.server import ServerConnection

FAST = {"reconnect_base": 0.01, "reconnect_max": 0.05, "command_timeout": 2.0}


class Top(random.Random):
    """A jitter source that always returns the longest delay, so delays are predictable."""

    def uniform(self, a: float, b: float) -> float:
        return b


def signer() -> DataSigner:
    ticks = iter(range(1_000, 10**9, 7))
    return DataSigner("test-key", Ed25519PrivateKey.generate(), now_ms=lambda: next(ticks))


def feed_config(server: FakeKalshi, **config: Any) -> FeedConfig:
    return FeedConfig(url=server.url, **{**FAST, **config})


def feed_for(server: FakeKalshi, **config: Any) -> KalshiFeed:
    return KalshiFeed(signer(), feed_config(server, **config), rng=Top())


async def take(events: AsyncIterator[Any], count: int, limit: float = 5.0) -> list[Any]:
    got: list[Any] = []

    async def pull() -> None:
        while len(got) < count:
            got.append(await anext(events))

    await asyncio.wait_for(pull(), limit)
    return got


def numbers(items: list[Any]) -> list[str]:
    return [
        i.frame.payload.trade_id
        for i in items
        if isinstance(i, Data) and isinstance(i.frame.payload, TradeMsg)
    ]


# --- the happy path ----------------------------------------------------------------------------


async def test_a_signed_feed_subscribes_and_yields_data_in_order() -> None:
    frames = subscribe_then(trade(1, seq=1), trade(2, seq=2), trade(3, seq=3))
    async with FakeKalshi(frames) as srv, feed_for(srv) as feed:
        sid = await feed.subscribe("trade", ["M"])
        events = await take(feed.events(), 3)
    assert sid == 1 and numbers(events) == ["t-1", "t-2", "t-3"]
    assert all(e.generation == 1 and e.received_ns > 0 and e.mono_ns > 0 for e in events)
    assert srv.commands[0][0]["cmd"] == "subscribe"
    assert srv.commands[0][0]["params"] == {"channels": ["trade"], "market_tickers": ["M"]}
    headers = srv.handshakes[0]
    assert headers[HEADER_KEY.lower()] == "test-key" and headers[HEADER_SIGNATURE.lower()]
    assert headers[HEADER_TIMESTAMP.lower()].isdigit()


async def test_every_connection_is_signed_afresh() -> None:
    async def drop(ws: ServerConnection) -> None:
        await ws.close()

    async with FakeKalshi(drop, subscribe_then()) as srv, feed_for(srv) as feed:
        await asyncio.wait_for(_until_connections(srv, 2), 5)
        assert feed.stats()["generation"] >= 2
    stamps = [h[HEADER_TIMESTAMP.lower()] for h in srv.handshakes]
    assert len(set(stamps)) == len(stamps)


async def _until_subscribed(srv: FakeKalshi, connection: int) -> None:
    """Wait until the given connection has been sent a subscribe command."""
    while not any(c["cmd"] == "subscribe" for c in srv.commands[connection]):  # noqa: ASYNC110
        await asyncio.sleep(0.01)  # polling a list on a test fake


async def _until_connections(srv: FakeKalshi, count: int) -> None:
    while srv.connections < count:  # noqa: ASYNC110 (polling a counter on a test fake)
        await asyncio.sleep(0.01)


async def test_subscribing_with_no_tickers_asks_for_every_market() -> None:
    async with FakeKalshi(subscribe_then()) as srv, feed_for(srv) as feed:
        await feed.subscribe("ticker")
    assert srv.commands[0][0]["params"] == {"channels": ["ticker"]}


async def test_a_command_before_the_feed_is_started_is_refused() -> None:
    async with FakeKalshi(subscribe_then()) as srv:
        with pytest.raises(FeedError, match="not connected"):
            await feed_for(srv).subscribe("trade")


# --- losing the link ---------------------------------------------------------------------------


async def test_after_a_drop_the_feed_reconnects_resubscribes_and_says_so_first() -> None:
    first = subscribe_then(trade(1, seq=1), then=lambda ws: ws.close())
    second = subscribe_then(trade(2, seq=1), sid=5)
    async with FakeKalshi(first, second) as srv, feed_for(srv) as feed:
        await feed.subscribe("trade", ["M"])
        events = await take(feed.events(), 3)
    assert isinstance(events[0], Data) and events[0].generation == 1
    assert isinstance(events[1], Reconnected)
    assert (events[1].generation, events[1].reason) == (2, "connection_lost")
    assert isinstance(events[2], Data) and events[2].generation == 2
    # the new link got the same subscription, and sid and seq starting over was not a gap
    assert srv.commands[1][0]["params"] == srv.commands[0][0]["params"]
    assert (
        feed.stats()["reconnects"] == {"connection_lost": 1} and feed.stats()["sequence_gaps"] == 0
    )


async def test_data_that_arrives_before_the_resubscribe_reply_still_follows_the_event() -> None:
    async def second(ws: ServerConnection) -> None:
        command = await read_command(ws)
        await ws.send(trade(9, seq=None, sid=None))  # before the reply
        await ws.send(subscribed(command, 7))
        await ws.send(trade(10, sid=7, seq=1))
        await ws.wait_closed()

    first = subscribe_then(then=lambda ws: ws.close())
    async with FakeKalshi(first, second) as srv, feed_for(srv) as feed:
        await feed.subscribe("trade")
        events = await take(feed.events(), 3)
    assert isinstance(events[0], Reconnected)
    assert numbers(events) == ["t-9", "t-10"]  # in order, none held back, none lost


async def test_a_link_that_fails_while_restoring_is_tried_again() -> None:
    async def breaks(ws: ServerConnection) -> None:
        await read_command(ws)
        await ws.close()  # the resubscribe is cut off

    first = subscribe_then(then=lambda ws: ws.close())
    third = subscribe_then(trade(3, seq=1))
    async with FakeKalshi(first, breaks, third) as srv, feed_for(srv) as feed:
        await feed.subscribe("trade")
        events = await take(feed.events(), 3)
    assert [type(e).__name__ for e in events] == ["Reconnected", "Reconnected", "Data"]
    assert [e.generation for e in events if isinstance(e, Reconnected)] == [2, 3]
    assert numbers(events) == ["t-3"]


async def test_a_subscription_kalshi_refuses_to_restore_is_reported_not_fatal() -> None:
    async def second(ws: ServerConnection) -> None:
        command = await read_command(ws)
        await ws.send(error_reply(command, 7, "no such channel"))
        await ws.send(trade(4, sid=None))
        await ws.wait_closed()

    first = subscribe_then(then=lambda ws: ws.close())
    async with FakeKalshi(first, second) as srv, feed_for(srv) as feed:
        await feed.subscribe("trade")
        events = await take(feed.events(), 3)
    failure = next(e for e in events if isinstance(e, ResubscribeFailed))
    assert failure.channel == "trade" and "[7] no such channel" in failure.error
    assert numbers(events) == ["t-4"]


async def test_a_refused_subscription_can_be_sent_again_and_its_sid_looked_up() -> None:
    async def second(ws: ServerConnection) -> None:
        command = await read_command(ws)
        await ws.send(error_reply(command, 7, "busy"))
        await ws.send(subscribed(await read_command(ws), 9))  # the retry holds
        await ws.wait_closed()

    first = subscribe_then(then=lambda ws: ws.close())
    async with FakeKalshi(first, second) as srv, feed_for(srv) as feed:
        assert feed.sid_of("trade") is None
        assert await feed.subscribe("trade") == 1 and feed.sid_of("trade") == 1
        await take(feed.events(), 2)  # Reconnected, ResubscribeFailed
        assert feed.sid_of("trade") is None  # refused: the old link's id is not kept (M1.R 1)
        assert await feed.resubscribe("trade") == 9 and feed.sid_of("trade") == 9
        with pytest.raises(FeedError, match="no subscription"):
            await feed.resubscribe("ticker")


async def test_only_subscriptions_still_wanted_are_restored() -> None:
    async def both(ws: ServerConnection) -> None:
        for sid in (1, 2):
            await ws.send(subscribed(await read_command(ws), sid))
        unsub = await read_command(ws)
        await ws.send(json.dumps({"id": unsub["id"], "type": "ok", "msg": {}}))
        await ws.close()

    async with FakeKalshi(both, subscribe_then()) as srv, feed_for(srv) as feed:
        first = await feed.subscribe("trade")
        await feed.subscribe("ticker")
        await feed.unsubscribe(first)
        await take(feed.events(), 1)
        await asyncio.wait_for(_until_subscribed(srv, 1), 5)
    assert [c["params"]["channels"] for c in srv.commands[1] if c["cmd"] == "subscribe"] == [
        ["ticker"]
    ]


async def test_added_markets_are_remembered_for_the_next_reconnect() -> None:
    async def first(ws: ServerConnection) -> None:
        await ws.send(subscribed(await read_command(ws), 1))
        update = await read_command(ws)
        reply = {
            "id": update["id"],
            "type": "ok",
            "sid": 1,
            "seq": 1,
            "msg": {"market_tickers": ["A", "B"]},
        }
        await ws.send(json.dumps(reply))
        await ws.send(trade(1, seq=2))  # the reply used seq 1, so this is not a gap
        await ws.close()

    async with FakeKalshi(first, subscribe_then()) as srv, feed_for(srv) as feed:
        sid = await feed.subscribe("trade", ["A"])
        assert await feed.update_subscription(sid, "add_markets", ["B"]) == ["A", "B"]
        await take(feed.events(), 2)
        assert feed.stats()["sequence_gaps"] == 0
        await asyncio.wait_for(_until_subscribed(srv, 1), 5)
    resubscribe = next(c for c in srv.commands[1] if c["cmd"] == "subscribe")
    assert resubscribe["params"]["market_tickers"] == ["A", "B"]


# --- sequence numbers ---------------------------------------------------------------------------


async def test_a_resubscribe_of_a_restored_subscription_sends_nothing() -> None:
    """M1.R merge review 6: a retry waking after a reconnect restored the channel opened a second
    subscription beside it."""
    async with FakeKalshi(subscribe_then()) as srv, feed_for(srv) as feed:
        sid = await feed.subscribe("trade")
        assert await feed.resubscribe("trade") == sid
    assert [c["cmd"] for c in srv.commands[0]] == ["subscribe"]  # one, not two


async def test_a_jump_in_seq_is_reported_before_the_message_that_shows_it() -> None:
    frames = [trade(1, seq=1), trade(2, seq=2), trade(4, seq=4), trade(5, seq=5)]
    async with FakeKalshi(subscribe_then(*frames)) as srv, feed_for(srv) as feed:
        await feed.subscribe("trade")
        events = await take(feed.events(), 5)
    gap = next(e for e in events if isinstance(e, SequenceGap))
    assert (gap.sid, gap.expected, gap.got) == (1, 3, 4)
    assert events.index(gap) == 2 and numbers(events) == ["t-1", "t-2", "t-4", "t-5"]
    assert feed.stats()["sequence_gaps"] == 1


async def test_a_reply_that_used_a_number_makes_the_next_message_a_gap_if_it_skips_one() -> None:
    async def first(ws: ServerConnection) -> None:
        await ws.send(subscribed(await read_command(ws), 1))
        update = await read_command(ws)
        reply = {
            "id": update["id"],
            "type": "ok",
            "sid": 1,
            "seq": 1,
            "msg": {"market_tickers": ["A"]},
        }
        await ws.send(json.dumps(reply))
        await ws.send(trade(1, seq=3))  # 2 is missing
        await ws.wait_closed()

    async with FakeKalshi(first) as srv, feed_for(srv) as feed:
        sid = await feed.subscribe("trade", ["A"])
        await feed.update_subscription(sid, "add_markets", [])
        events = await take(feed.events(), 2)
    assert isinstance(events[0], SequenceGap) and (events[0].expected, events[0].got) == (2, 3)


async def test_a_reply_that_skips_a_number_is_itself_reported_as_a_gap() -> None:
    """M1.R 2: a gap seen on a command's reply was counted and dropped."""

    async def first(ws: ServerConnection) -> None:
        await ws.send(subscribed(await read_command(ws), 1))
        await ws.send(trade(1, seq=1))
        update = await read_command(ws)
        reply = {
            "id": update["id"],
            "type": "ok",
            "sid": 1,
            "seq": 4,
            "msg": {"market_tickers": []},
        }
        await ws.send(json.dumps(reply))  # 2 and 3 are missing
        await ws.send(trade(2, seq=5))
        await ws.wait_closed()

    async with FakeKalshi(first) as srv, feed_for(srv) as feed:
        sid = await feed.subscribe("trade", ["A"])
        await take(feed.events(), 1)
        await feed.update_subscription(sid, "add_markets", [])
        events = await take(feed.events(), 2)
    assert isinstance(events[0], SequenceGap) and (events[0].expected, events[0].got) == (2, 4)
    assert numbers(events) == ["t-2"] and feed.stats()["sequence_gaps"] == 1


async def test_messages_without_a_seq_are_never_a_gap() -> None:
    frames = [trade(1, seq=None), trade(2, seq=None), trade(3, seq=None)]
    async with FakeKalshi(subscribe_then(*frames)) as srv, feed_for(srv) as feed:
        await feed.subscribe("trade")
        assert numbers(await take(feed.events(), 3)) == ["t-1", "t-2", "t-3"]
    assert feed.stats()["sequence_gaps"] == 0


# --- a slow consumer ----------------------------------------------------------------------------


async def test_a_consumer_that_falls_behind_costs_a_reconnect_and_not_memory() -> None:
    flood = [trade(n, seq=n) for n in range(1, 60)]
    first = subscribe_then(*flood)
    second = subscribe_then(trade(100, seq=1))
    async with (
        FakeKalshi(first, second) as srv,
        feed_for(srv, queue_limit=5, close_timeout=5.0) as feed,
    ):
        await feed.subscribe("trade")
        await asyncio.sleep(0.4)  # the consumer is not reading
        stats = feed.stats()
        assert stats["queue_depth"] <= 5 and stats["high_water"] <= 5 and stats["overflows"] >= 1
        started = asyncio.get_running_loop().time()
        events = await take(feed.events(), 7)
        # the link was cut at once: a polite close could not finish while nothing was reading it
        assert asyncio.get_running_loop().time() - started < 2.0
    assert numbers(events[:5]) == ["t-1", "t-2", "t-3", "t-4", "t-5"]  # kept, in order
    assert isinstance(events[5], Reconnected) and events[5].reason == "overflow"
    assert numbers(events[6:]) == ["t-100"]


# --- a feed that goes quiet ---------------------------------------------------------------------


async def test_a_link_that_stays_up_but_sends_nothing_is_dropped_by_the_watchdog() -> None:
    first = subscribe_then()
    second = subscribe_then(trade(1, seq=1))
    async with FakeKalshi(first, second) as srv, feed_for(srv, idle_timeout=0.3) as feed:
        await feed.subscribe("trade")
        events = await take(feed.events(), 2, limit=5)
    assert isinstance(events[0], Reconnected) and events[0].reason == "stalled"
    assert feed.stats()["stalls"] == 1


async def test_a_feed_with_nothing_subscribed_is_not_a_stall() -> None:
    async with FakeKalshi(subscribe_then()) as srv, feed_for(srv, idle_timeout=0.2) as feed:
        await asyncio.sleep(0.7)
        assert feed.stats()["generation"] == 1 and feed.stats()["stalls"] == 0


async def test_data_keeps_the_watchdog_quiet() -> None:
    async def chatty(ws: ServerConnection) -> None:
        await ws.send(subscribed(await read_command(ws), 1))
        for n in range(12):
            await ws.send(trade(n, seq=n + 1))
            await asyncio.sleep(0.05)
        await ws.wait_closed()

    async with FakeKalshi(chatty) as srv, feed_for(srv, idle_timeout=0.3) as feed:
        await feed.subscribe("trade")
        await take(feed.events(), 12)
        assert feed.stats()["stalls"] == 0


# --- commands that go wrong ---------------------------------------------------------------------


async def test_a_refused_command_raises_with_kalshis_code_and_is_not_remembered() -> None:
    async def refuse(ws: ServerConnection) -> None:
        await ws.send(error_reply(await read_command(ws), 6, "already subscribed"))
        await ws.wait_closed()

    async with FakeKalshi(refuse) as srv, feed_for(srv) as feed:
        with pytest.raises(FeedError, match=r"\[6\] already subscribed") as caught:
            await feed.subscribe("trade")
        assert caught.value.code == 6 and feed._subscriptions == []  # noqa: SLF001
        assert feed._pending == {}  # noqa: SLF001


async def test_a_command_that_is_never_answered_times_out_and_cleans_up() -> None:
    async def silent(ws: ServerConnection) -> None:
        await read_command(ws)
        await ws.wait_closed()

    async with FakeKalshi(silent) as srv, feed_for(srv, command_timeout=0.2) as feed:
        with pytest.raises(FeedError, match="subscribe timed out"):
            await feed.subscribe("trade")
        assert feed._pending == {}  # noqa: SLF001


async def test_a_reply_with_no_subscription_id_is_an_error() -> None:
    async def odd(ws: ServerConnection) -> None:
        command = await read_command(ws)
        await ws.send(json.dumps({"id": command["id"], "type": "subscribed", "msg": {}}))
        await ws.wait_closed()

    async with FakeKalshi(odd) as srv, feed_for(srv) as feed:
        with pytest.raises(FeedError, match="no subscription id"):
            await feed.subscribe("trade")


async def test_an_update_reply_without_the_market_set_is_an_error() -> None:
    async def first(ws: ServerConnection) -> None:
        await ws.send(subscribed(await read_command(ws), 1))
        update = await read_command(ws)
        await ws.send(json.dumps({"id": update["id"], "type": "ok", "msg": {}}))
        await ws.wait_closed()

    async with FakeKalshi(first) as srv, feed_for(srv) as feed:
        sid = await feed.subscribe("trade", ["A"])
        with pytest.raises(FeedError, match="no market set"):
            await feed.update_subscription(sid, "add_markets", ["B"])


# --- frames that are not what they should be ---------------------------------------------------


async def test_bad_frames_are_counted_and_odd_ones_are_passed_on_with_what_is_wrong() -> None:
    ticker = {"market_ticker": "M", "yes_bid_dollars": "0.10", "extra": 1}
    bad_trade = json.loads(trade(7))
    bad_trade["msg"]["no_price_dollars"] = "0.9000"
    frames = [
        "not json",
        '{"no": "type"}',
        json.dumps({"type": "ticker", "sid": 2, "msg": ticker}),
        json.dumps(bad_trade),
    ]
    async with FakeKalshi(subscribe_then(*frames)) as srv, feed_for(srv) as feed:
        await feed.subscribe("trade")
        events = await take(feed.events(), 2)
    assert events[0].frame.unknown == ("extra",) and events[0].frame.error is None
    assert events[1].frame.payload is None and "do not add to $1" in events[1].frame.error
    stats = feed.stats()
    assert stats["bad_frames"] == 2 and stats["parse_errors_by_type"] == {"trade": 1}
    assert stats["unknown_fields_by_type"] == {"ticker.extra": 1}
    assert stats["frames_by_type"]["ticker"] == 1


# --- connecting and closing ---------------------------------------------------------------------


async def test_failed_connects_back_off_and_then_give_up() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError("refused")

    config = FeedConfig(reconnect_base=0.5, reconnect_max=3.0, max_reconnect_attempts=4)
    feed = KalshiFeed(signer(), config, connect=refuse, sleep=sleep, rng=Top())
    with pytest.raises(FeedError, match="reconnect attempts exhausted"):
        await feed.start()
    assert sleeps == [0.5, 1.0, 2.0]  # none before the first try; doubling, capped at 3.0


async def test_the_delay_is_capped() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError("refused")

    config = FeedConfig(reconnect_base=1.0, reconnect_max=3.0, max_reconnect_attempts=6)
    with pytest.raises(FeedError):
        await KalshiFeed(signer(), config, connect=refuse, sleep=sleep, rng=Top()).start()
    assert sleeps == [1.0, 2.0, 3.0, 3.0, 3.0]


async def test_a_link_that_keeps_dropping_is_not_redialled_faster_and_faster() -> None:
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        delays.append(seconds)
        await asyncio.sleep(0.001)

    async def drop(ws: ServerConnection) -> None:
        await ws.close()

    async with FakeKalshi(drop) as srv:
        feed = KalshiFeed(
            signer(),
            FeedConfig(url=srv.url, reconnect_base=0.5, reconnect_max=8.0, idle_timeout=None),
            sleep=sleep,
            rng=Top(),
        )
        async with feed:
            await asyncio.wait_for(_until_connections(srv, 5), 5)
    assert delays[:4] == [0.5, 1.0, 2.0, 4.0]


async def test_a_link_that_lasted_resets_the_delay() -> None:
    now = {"t": 0.0}
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        delays.append(seconds)
        await asyncio.sleep(0.001)

    async def first(ws: ServerConnection) -> None:
        await asyncio.sleep(0.1)  # let the client note when the link opened
        now["t"] += 100.0  # this link has lasted long enough to count as healthy
        await ws.close()

    async def later(ws: ServerConnection) -> None:
        await ws.close()

    async with FakeKalshi(later, first, later, later) as srv:
        feed = KalshiFeed(
            signer(),
            FeedConfig(url=srv.url, reconnect_base=0.5, reconnect_max=60.0, idle_timeout=None),
            sleep=sleep,
            monotonic=lambda: now["t"],
            rng=Top(),
        )
        async with feed:
            await asyncio.wait_for(_until_connections(srv, 5), 5)
    # short link, long link (the delay starts over), short link, short link (growing again)
    assert delays[:4] == [0.5, 0.5, 0.5, 1.0]


async def test_closing_ends_the_event_stream_cleanly() -> None:
    async with FakeKalshi(subscribe_then(trade(1, seq=1))) as srv:
        feed = feed_for(srv)
        await feed.start()
        await feed.subscribe("trade")
        events = feed.events()
        assert numbers(await take(events, 1)) == ["t-1"]
        await feed.close()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(events), 5)


async def test_closing_while_waiting_to_reconnect_returns_promptly() -> None:
    release = asyncio.Event()

    async def hold(seconds: float) -> None:
        await release.wait()

    async def drop(ws: ServerConnection) -> None:
        await ws.close()

    async with FakeKalshi(drop) as srv:
        feed = KalshiFeed(signer(), FeedConfig(url=srv.url, idle_timeout=None), sleep=hold)
        await feed.start()
        await asyncio.sleep(0.2)  # it is now waiting out its delay
        await asyncio.wait_for(feed.close(), 5)


async def test_a_feed_lost_for_good_makes_the_event_stream_raise() -> None:
    async def drop(ws: ServerConnection) -> None:
        await ws.close()

    async with FakeKalshi(drop) as srv:
        config = feed_config(srv, max_reconnect_attempts=1, idle_timeout=None)
        feed = KalshiFeed(signer(), config, rng=Top())
        await feed.start()
        with pytest.raises(FeedError, match="reconnect attempts exhausted"):
            await take(feed.events(), 5)


def test_the_configuration_is_checked() -> None:
    bad_settings: tuple[dict[str, Any], ...] = (
        {"queue_limit": 1},
        {"command_timeout": 0},
        {"reconnect_base": 0},
        {"reconnect_base": 5.0, "reconnect_max": 1.0},
        {"idle_timeout": 0},
        {"max_frame_bytes": 0},
    )
    for bad in bad_settings:
        with pytest.raises(ValueError):
            FeedConfig(**bad)


def test_the_feed_is_on_demo_unless_production_is_asked_for(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert FeedConfig().url == WS_URLS[Environment.DEMO]
    with caplog.at_level(logging.WARNING, logger="ikars_core"):
        KalshiFeed(signer())
        assert "PRODUCTION" not in caplog.text
        KalshiFeed(signer(), FeedConfig(url=WS_URLS[Environment.PRODUCTION]))
        assert "PRODUCTION" in caplog.text


async def test_a_command_in_flight_when_the_link_drops_fails_at_once() -> None:
    async def drop_on_command(ws: ServerConnection) -> None:
        await read_command(ws)
        await ws.close()

    async with (
        FakeKalshi(drop_on_command, subscribe_then()) as srv,
        feed_for(srv, command_timeout=5.0) as feed,
    ):
        started = asyncio.get_running_loop().time()
        with pytest.raises(FeedError, match="connection closed"):
            await asyncio.wait_for(feed.subscribe("trade"), 2.0)
        assert asyncio.get_running_loop().time() - started < 2.0


async def test_a_wait_that_times_out_loses_nothing_and_does_not_end_the_stream() -> None:
    async def slow(ws: ServerConnection) -> None:
        await ws.send(subscribed(await read_command(ws), 1))
        await asyncio.sleep(0.4)
        await ws.send(trade(1, seq=1))
        await asyncio.sleep(0.4)
        await ws.send(trade(2, seq=2))
        await ws.wait_closed()

    async with FakeKalshi(slow) as srv, feed_for(srv) as feed:
        await feed.subscribe("trade")
        events = feed.events()
        for _ in range(2):  # two waits are cut short before anything arrives
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(anext(events), 0.1)
        assert numbers(await take(events, 2)) == ["t-1", "t-2"]  # nothing lost, still going


async def test_a_subscription_id_names_its_channel_on_each_link() -> None:
    async def first(ws: ServerConnection) -> None:
        await ws.send(subscribed(await read_command(ws), 1))
        await ws.send(subscribed(await read_command(ws), 2))
        await ws.close()

    async def second(ws: ServerConnection) -> None:
        await ws.send(subscribed(await read_command(ws), 7))
        await ws.send(subscribed(await read_command(ws), 8))
        await ws.wait_closed()

    async with FakeKalshi(first, second) as srv, feed_for(srv) as feed:
        await feed.subscribe("trade")
        await feed.subscribe("ticker")
        assert (feed.channel_of(1), feed.channel_of(2), feed.channel_of(9)) == (
            "trade",
            "ticker",
            None,
        )
        await take(feed.events(), 1)  # the reconnect
        await asyncio.wait_for(_until_subscribed(srv, 1), 5)
        for _ in range(100):
            if feed.channel_of(7) == "trade" and feed.channel_of(8) == "ticker":
                break
            await asyncio.sleep(0.02)
        assert (feed.channel_of(7), feed.channel_of(8)) == ("trade", "ticker")
        assert feed.channel_of(1) is None  # the old link's ids mean nothing now
