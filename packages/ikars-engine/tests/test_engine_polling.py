"""M2.1: polling sources (the base, the shared poller, the gap rule) against a fake HTTP server and
a fake clock. Every feed and item here is made up."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from ikars_engine.clocks import VirtualClock
from ikars_engine.model import Item
from ikars_engine.plugins import PluginContext, PluginKind, PluginSpec
from ikars_engine.polling import (
    GAP_AFTER,
    NS,
    Feed,
    Found,
    Poller,
    PollError,
    PollingSource,
    user_agent,
)
from ikars_engine.settings import VERSION, Settings, migrate
from ikars_engine.sources import ReplaySource, write_recording
from ikars_engine.store import StateStore

T0 = 1_767_225_600 * NS  # 2026-01-01 00:00 UTC
URL = "https://feeds.example.test/news.json"


class Done(Exception):
    """Ends a test's polling loop."""


class FakeClock:
    """Wall time that a sleep moves on; the loop ends after ``sleeps`` sleeps."""

    def __init__(self, sleeps: int = 50) -> None:
        self.now = T0
        self.slept: list[float] = []
        self._left = sleeps

    def now_ns(self) -> int:
        return self.now

    def monotonic_ns(self) -> int:
        return self.now

    async def sleep(self, seconds: float) -> None:
        if self._left == 0:
            raise Done
        self._left -= 1
        self.slept.append(seconds)
        self.now += int(seconds * NS)
        await asyncio.sleep(0)


class Server:
    """A fake feed host: each request takes the next scripted reply (the last one repeats)."""

    def __init__(self, replies: list[httpx.Response]) -> None:
        self.replies = replies
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]


def items(*ids: str, headers: dict[str, str] | None = None) -> httpx.Response:
    body = json.dumps([{"id": i, "title": f"made-up {i}"} for i in ids])
    return httpx.Response(200, content=body.encode(), headers=headers or {})


class News(PollingSource):
    spec = PluginSpec(
        name="test-news", kind=PluginKind.SOURCE, version="1", produces=("news_item",)
    )
    item_kind = "news_item"

    def __init__(self, url: str = URL, every_s: float = 60.0) -> None:
        self.url, self.every_s = url, every_s

    def feeds(self, ctx: PluginContext) -> list[Feed]:
        return [Feed(name="test-news.main", url=self.url, every_s=self.every_s)]

    def parse(self, feed: Feed, body: bytes, ctx: PluginContext) -> list[Found]:
        return [Found(id=r["id"], payload={"title": r["title"]}) for r in json.loads(body)]


def run_source(
    tmp_path: Path, server: Server, *, sleeps: int = 50, source: News | None = None,
    store: StateStore | None = None, clock: FakeClock | None = None, max_bytes: int = 5_000_000,
) -> tuple[list[Item], Poller, FakeClock, StateStore]:  # fmt: skip
    clock = clock or FakeClock(sleeps)
    store = store or StateStore(tmp_path / "state.sqlite")
    poller = Poller(
        store, clock, user_agent("9.9"), transport=httpx.MockTransport(server), max_bytes=max_bytes
    )
    emitted: list[Item] = []

    async def emit(item: Item) -> None:
        emitted.append(item)

    source = source or News()
    ctx = PluginContext(source.spec, clock, {}, emit, 30.0, services={"poller": lambda: poller})

    async def go() -> None:
        try:
            await source.run(ctx)
        except Done:
            pass
        finally:
            await poller.aclose()

    asyncio.run(go())
    return emitted, poller, clock, store


def test_the_user_agent_names_the_repository_and_an_email_only_when_set() -> None:
    assert user_agent("0.2.0") == "IKARS/0.2.0 (+https://github.com/swalsh76/IKARS)"
    assert user_agent("0.2.0", "me@example.test").endswith("; me@example.test)")


def test_a_feed_is_asked_whether_it_changed_and_each_item_is_emitted_once(tmp_path: Path) -> None:
    validators = {"ETag": 'W/"a"', "Last-Modified": "Thu, 01 Jan 2026 00:00:00 GMT"}
    server = Server([
        items("a", "b", "a", headers=validators),  # a listed twice in one reply: once
        httpx.Response(304),
        items("b", "c"),  # b again: only c is new
    ])  # fmt: skip
    emitted, poller, _, store = run_source(tmp_path, server, sleeps=3)
    assert [i.ref.id for i in emitted] == ["a", "b", "c"]
    assert all(i.ref.kind == "news_item" and i.source == "test-news" for i in emitted)
    assert emitted[0].payload == {"title": "made-up a", "feed": "test-news.main"}
    second = server.requests[1].headers
    assert second["If-None-Match"] == 'W/"a"'  # both sent: NWS honours only If-Modified-Since
    assert second["If-Modified-Since"] == "Thu, 01 Jan 2026 00:00:00 GMT"
    assert server.requests[0].headers["User-Agent"].startswith("IKARS/9.9 (+https://github.com/")
    health = poller.health()["test-news.main"]
    assert isinstance(health, dict)
    assert (health["polls"], health["not_modified"], health["emitted"], health["errors"]) == (
        4,
        1,
        3,
        0,
    )  # fmt: skip  (three sleeps: four polls, the last one's reply already seen)
    assert "url" not in health and URL not in json.dumps(health)
    store.close()


def test_a_restart_asks_changed_since_and_records_nothing_twice(tmp_path: Path) -> None:
    first = Server([items("a", "b", headers={"Last-Modified": "Thu, 01 Jan 2026 00:00:00 GMT"})])
    emitted, _, clock, store = run_source(tmp_path, first, sleeps=0)
    assert [i.ref.id for i in emitted] == ["a", "b"]
    again = Server([items("a", "b", "c")])
    later = FakeClock(1)
    later.now = clock.now + 30 * NS  # restarted half an interval later
    emitted, _, _, _ = run_source(tmp_path, again, sleeps=1, store=store, clock=later)
    assert [i.ref.id for i in emitted] == ["c"]
    assert later.slept[0] == pytest.approx(30.0)  # the next poll when it was due, not at once
    assert again.requests[0].headers["If-Modified-Since"] == "Thu, 01 Jan 2026 00:00:00 GMT"
    store.close()


class Flaky(News):
    """Fails to read the first reply it gets."""

    failed = False

    def parse(self, feed: Feed, body: bytes, ctx: PluginContext) -> list[Found]:
        if not self.failed:
            self.failed = True
            raise ValueError("a bad body")
        return super().parse(feed, body, ctx)


def test_a_reply_whose_items_did_not_go_out_is_asked_for_again(tmp_path: Path) -> None:
    # per-commit review: the validators are kept only after the items are emitted
    validators = {"Last-Modified": "Thu, 01 Jan 2026 00:00:00 GMT"}
    server = Server([items("a", headers=validators)])
    emitted, _, _, store = run_source(tmp_path, server, sleeps=1, source=Flaky())
    assert [i.ref.id for i in emitted] == ["a"]  # the retry got the whole reply again
    assert "If-Modified-Since" not in server.requests[1].headers
    store.close()


def test_the_server_says_how_long_to_wait(tmp_path: Path) -> None:
    server = Server([
        items("a", headers={"Cache-Control": "public, max-age=300"}),  # longer than 60 s
        httpx.Response(429, headers={"Retry-After": "120"}),
        httpx.Response(503),
        items("b"),
    ])  # fmt: skip
    _, poller, clock, store = run_source(tmp_path, server, sleeps=4)
    waits = clock.slept  # the first poll needs no wait
    assert waits[0] >= 300 * 0.9  # max-age, less the jitter
    assert waits[1] >= 120 * 0.9  # Retry-After
    assert waits[2] <= 60 * 1.1  # backoff, never longer than the interval
    assert poller.health()["test-news.main"]["errors"] == 2  # type: ignore[index]
    store.close()


def test_a_long_outage_is_a_gap_and_a_blip_is_not(tmp_path: Path) -> None:
    every = 60.0
    blip = Server([items("a"), httpx.Response(500), items("b")])
    _, _, _, store = run_source(tmp_path / "blip", blip, sleeps=3, source=News(every_s=every))
    assert store.gaps() == []  # one failed poll, retried quietly
    store.close()
    down = Server([items("a"), *[httpx.Response(500)] * 8, items("b")])
    _, poller, _, store = run_source(tmp_path / "down", down, sleeps=10, source=News(every_s=every))
    [gap] = store.gaps()
    assert (gap.channel, gap.reason, gap.status, gap.recovery) == (
        "test-news.main", "unreachable", "done", "refetched"
    )  # fmt: skip
    assert gap.end_ns - gap.start_ns > GAP_AFTER * every * NS
    assert "HTTP 500" in gap.detail
    assert poller.health()["test-news.main"]["gaps"] == 1  # type: ignore[index]
    store.close()


def test_an_engine_stopped_too_long_logs_the_missed_window(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.set_poll_feed("test-news.main", None, None, T0 - 3_600 * NS)  # last good an hour ago
    _, _, _, store = run_source(tmp_path, Server([items("a")]), sleeps=0, store=store)
    [gap] = store.gaps()
    assert gap.start_ns == T0 - 3_600 * NS and "the engine was not running" in gap.detail
    store.close()


@pytest.mark.parametrize(
    ("url", "reply", "problem"),
    [
        ("http://feeds.example.test/news.json", items("a"), "only https"),
        (URL, httpx.Response(301, headers={"Location": "https://elsewhere.test/"}), "HTTP 301"),
        (URL, items(*[f"i{n}" for n in range(200)]), "larger than"),
    ],
)
def test_refused_replies(tmp_path: Path, url: str, reply: httpx.Response, problem: str) -> None:
    server = Server([reply])
    emitted, poller, _, store = run_source(
        tmp_path, server, sleeps=1, source=News(url=url), max_bytes=2_000
    )
    assert emitted == []
    error = poller.health()["test-news.main"]["last_error"]  # type: ignore[index]
    assert problem in str(error)
    if url.startswith("http:"):
        assert server.requests == []  # never even asked
    assert len(server.requests) <= 2  # a redirect is never followed
    store.close()


def test_a_poll_error_carries_the_servers_wait() -> None:
    assert PollError("x", 5.0).retry_after_s == 5.0


def test_a_source_without_the_poller_stays_idle(tmp_path: Path) -> None:
    async def emit(item: Item) -> None:
        raise AssertionError

    source = News()
    ctx = PluginContext(source.spec, VirtualClock(T0), {}, emit, 1.0)
    asyncio.run(source.run(ctx))  # returns: a test engine or a replay has no poller


def test_remembered_ids_are_forgotten_after_a_while(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    store.remember("f", ["a", "b"], T0)
    store.remember("f", ["c"], T0 + 10 * 86_400 * NS)
    assert store.unseen("f", ["a", "c", "d", "d"]) == ["d"]
    assert store.forget_seen(T0 + 86_400 * NS) == 2
    assert store.unseen("f", ["a", "c"]) == ["a"]
    store.close()


def test_what_a_source_emits_replays_at_its_received_time(tmp_path: Path) -> None:
    emitted, _, _, store = run_source(tmp_path, Server([items("a", "b")]), sleeps=0)
    store.close()
    stamped = [
        Item(ref=i.ref, source=i.source, received_ns=T0 + n, payload=i.payload)
        for n, i in enumerate(emitted)
    ]  # the host stamps received time live; here by hand
    path = tmp_path / "rec.parquet"
    write_recording(path, stamped)
    clock = VirtualClock(T0 - 1)

    async def read() -> list[Item]:
        return [i async for i in ReplaySource([path], clock).items()]

    back = asyncio.run(read())
    assert [(i.ref.id, i.received_ns) for i in back] == [("a", T0), ("b", T0 + 1)]
    assert clock.now_ns() == T0 + 1


def test_settings_version_6_adds_sources_and_the_owners_ceiling() -> None:
    old: dict[str, Any] = {"version": 5, "budget": {"monthly_e6": 40_000_000}}
    new = migrate(old)
    assert new["version"] == VERSION == 6 and new["budget"]["monthly_e6"] == 20_000_000
    chosen = migrate({"version": 5, "budget": {"monthly_e6": 30_000_000}})
    assert chosen["budget"]["monthly_e6"] == 30_000_000  # a ceiling the owner set stays
    assert migrate({"version": 5})["version"] == 6
    assert Settings().sources.contact_email == "" and Settings().budget.monthly_e6 == 20_000_000
