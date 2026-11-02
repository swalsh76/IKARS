"""M2.3: news from GDELT and the curated feeds. Every reply here is hand-written and made up (the
test-data policy), shaped like the real ones."""

import asyncio
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from ikars_engine.gdelt import (
    BEHIND,
    BOOKMARK,
    FILES_PER_READ,
    GdeltNews,
    doc_url,
    newest_file,
    parse_doc,
    parse_gkg,
    passable,
    unzip_one,
)
from ikars_engine.model import Item
from ikars_engine.news import clean_link, plain_text
from ikars_engine.plugins import PluginContext
from ikars_engine.polling import Feed, Found, Poller, PollError, user_agent
from ikars_engine.rss import RssFeeds, accepted, feed_time, parse_feed
from ikars_engine.settings import (
    GOVERNMENT_FEEDS,
    VERSION,
    GdeltQuery,
    NewsFeed,
    NewsSettings,
    Settings,
    migrate,
)
from ikars_engine.store import StateStore

NS = 1_000_000_000
T0 = 1_767_225_600 * NS  # 2026-01-01 00:00 UTC


class FakeClock:
    """Made-up time that runs until ``until_s`` from the start, then stops the run (sleeps are
    counted by time, not by number: the host budget sleeps too)."""

    def __init__(self, until_s: float = 0.0, now: int = T0) -> None:
        self.now, self.end = now, now + int(until_s * NS)

    def now_ns(self) -> int:
        return self.now

    def monotonic_ns(self) -> int:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += int(seconds * NS)
        if self.now > self.end:
            raise asyncio.CancelledError
        await asyncio.sleep(0)


def run(source: Any, ctx: PluginContext, poller: Poller) -> None:
    async def go() -> None:
        try:
            await source.run(ctx)
        except (asyncio.CancelledError, ExceptionGroup):
            pass
        finally:
            await poller.aclose()

    asyncio.run(go())


# --- shared ---------------------------------------------------------------------------------


def test_a_link_is_cleaned_into_one_id() -> None:
    same = [
        "https://www.made-up.example/a/story/?utm_source=x&id=7#top",
        "http://made-up.example/a/story?id=7&fbclid=abc",
        "HTTPS://Made-Up.Example:443/a/story?id=7",
    ]
    assert {clean_link(u) for u in same} == {"https://made-up.example/a/story?id=7"}
    assert clean_link("https://made-up.example:8443/") == "https://made-up.example:8443/"
    assert clean_link("ftp://made-up.example/x") is None
    assert clean_link("not a link") is None and clean_link(None) is None
    assert clean_link("https://made-up.example:99999/") is None  # a port that cannot be


def test_markup_becomes_plain_text() -> None:
    html = "<p>Rates <b>held</b> &amp; steady</p><script>bad()</script>\n<style>x{}</style> today"
    assert plain_text(html) == "Rates held & steady today"
    assert plain_text("x" * 2_000) == "x" * 1_000
    assert plain_text("<p> </p>") is None and plain_text(None) is None


# --- rss ------------------------------------------------------------------------------------

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<title>Made-up Agency</title>
<item><title>Rates held</title><link>https://www.made-up.example/r/1?utm_medium=rss</link>
<guid>r-1</guid><pubDate>Thu, 01 Jan 2026 08:30:00 EST</pubDate>
<description>&lt;p&gt;The board &lt;b&gt;held&lt;/b&gt; rates.&lt;/p&gt;</description></item>
<item><title>Second release</title><link>https://made-up.example/indicators/</link>
<guid>r-2</guid><dc:date>2026-01-01T15:00:00Z</dc:date></item>
<item><title>Third release</title><link>https://made-up.example/indicators</link>
<guid>r-3</guid><pubDate>Thu, 01 Jan 2026 16:00:00 -0000</pubDate></item>
<item><title>No link or guid</title></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Made-up Survey</title>
<entry><title type="html">M 5.0 - Made-up Ridge</title>
<link rel="self" href="https://made-up.example/self/1"/>
<link href="https://made-up.example/event/1"/><id>urn:made-up:1</id>
<updated>2026-01-01T02:12:33.040Z</updated>
<summary type="html">&lt;b&gt;Depth&lt;/b&gt; 10 km</summary>
</entry></feed>"""

RDF = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>Made-up Bureau</title></channel>
<item><title>Index up</title><link>https://made-up.example/rdf/1</link><dc:date>2026-01-01T13:30:00-05:00</dc:date></item>
</rdf:RDF>"""


def test_an_rss_feed_is_read_with_its_times_and_summary() -> None:
    found, shared = parse_feed("rss.made-up", RSS)
    assert [f.id for f in found] == [
        "https://made-up.example/r/1",
        "https://made-up.example/indicators",
        "https://made-up.example/indicators",
    ]  # the one without a link or guid is skipped: nothing to know it by again
    first = found[0]
    assert first.payload["headline"] == "Rates held"
    assert first.payload["summary"] == "The board held rates."
    assert first.payload["source"] == "Made-up Agency" and first.payload["via"] == "rss"
    assert first.published_ns == T0 + 13 * 3_600 * NS + 30 * 60 * NS  # 08:30 EST is 13:30 UTC
    assert found[1].published_ns == T0 + 15 * 3_600 * NS  # Dublin Core, ISO 8601
    assert found[2].published_ns == T0 + 16 * 3_600 * NS  # RFC 822's -0000 is UTC
    assert shared == 2  # two items, one link: the feed wants ids = "guid"
    by_guid, _ = parse_feed("rss.made-up", RSS, ids="guid")
    assert [f.id for f in by_guid] == ["rss.made-up:r-1", "rss.made-up:r-2", "rss.made-up:r-3"]


def test_atom_and_rss_1_read_alike() -> None:
    [entry], _ = parse_feed("rss.atom", ATOM)
    assert entry.id == "https://made-up.example/event/1"  # the alternate link, not self
    assert entry.payload["headline"] == "M 5.0 - Made-up Ridge"
    assert entry.payload["summary"] == "Depth 10 km" and entry.payload["source"] == "Made-up Survey"
    assert entry.published_ns == T0 + 2 * 3_600 * NS + (12 * 60 + 33) * NS + 40_000_000
    [item], _ = parse_feed("rss.rdf", RDF)
    assert item.id == "https://made-up.example/rdf/1"
    assert item.published_ns == T0 + (18 * 60 + 30) * 60 * NS


def test_feed_times_are_exact_and_never_guessed() -> None:
    assert feed_time("Thu, 01 Jan 2026 00:00:00 GMT") == T0
    assert feed_time("2026-01-01T00:00:00+00:00") == T0
    assert feed_time("Thu, 01 Jan 2026 00:00:00") is None  # no zone: not read as local time
    assert feed_time("2026-01-01T00:00:00") is None
    assert feed_time("yesterday") is None and feed_time(None) is None


def test_hostile_xml_is_refused() -> None:
    laughs = b"""<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>
<rss><channel><item><title>&b;</title><link>https://made-up.example/x</link></item></channel></rss>"""
    outside = b"""<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///c:/windows/win.ini">]>
<rss><channel><item><title>&x;</title></item></channel></rss>"""
    for body in (laughs, outside, b"<rss><channel><item>", b"not xml"):
        with pytest.raises(ValueError, match="not safe, well-formed XML"):
            parse_feed("rss.bad", body)


def test_a_feed_without_its_terms_check_is_refused() -> None:
    ok = NewsFeed(name="ok", url="https://made-up.example/f", terms="public domain")
    feeds = [
        ok,
        NewsFeed(name="no-terms", url="https://made-up.example/g"),
        NewsFeed(name="ok", url="https://made-up.example/h", terms="x"),
        NewsFeed(name="Bad Name!", url="https://made-up.example/i", terms="x"),
        NewsFeed(name="plain", url="http://made-up.example/j", terms="x"),
    ]
    kept, refused = accepted(feeds)
    assert kept == [ok]
    assert refused == {
        "no-terms": "no terms check recorded (terms)",
        "ok": "another feed has this name",
        "Bad Name!": "the name must be letters, digits, - and _",
        "plain": "only https feeds are polled",
    }
    assert all(f.terms and f.url.startswith("https://") for f in GOVERNMENT_FEEDS)
    assert accepted(list(GOVERNMENT_FEEDS))[1] == {}


def test_two_feeds_listing_one_story_record_it_once(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    clock = FakeClock(until_s=60)
    other = RSS.replace(b"<title>Made-up Agency</title>", b"<title>Made-up Mirror</title>")

    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=RSS if request.url.path == "/a" else other)

    poller = Poller(store, clock, user_agent("9.9"), transport=httpx.MockTransport(reply))
    emitted: list[Item] = []

    async def emit(item: Item) -> None:
        emitted.append(item)
        await asyncio.sleep(0)  # the other feed's poll runs while this one is still sending

    news = NewsSettings(
        feeds=[
            NewsFeed(name="a", url="https://made-up.example/a", terms="made up"),
            NewsFeed(name="b", url="https://made-up.example/b", terms="made up", ids="link"),
            NewsFeed(name="c", url="https://made-up.example/c"),  # no terms: never polled
        ]
    )
    source = RssFeeds()
    ctx = PluginContext(source.spec, clock, {}, emit, 30.0,
                        services={"poller": lambda: poller, "news": lambda: news})  # fmt: skip
    run(source, ctx, poller)
    links = [i.ref.id for i in emitted]
    assert sorted(links) == ["https://made-up.example/indicators", "https://made-up.example/r/1"]
    assert {i.ref.kind for i in emitted} == {"news"}
    note = poller.notes["rss"]
    assert note["feeds"] == 2 and note["refused"] == {"c": "no terms check recorded (terms)"}
    assert note["shared_links"] == {"rss.a": 2, "rss.b": 2}
    store.close()


# --- gdelt ----------------------------------------------------------------------------------


def gkg_row(n: int, *, kind: str = "1", title: str | None = "Made-up headline &amp; more") -> str:
    cols = [""] * 27
    cols[0] = f"20260101000000-{n}"
    cols[1] = "20260101000000"
    cols[2] = kind
    cols[3] = "made-up.example"
    cols[4] = f"https://www.made-up.example/story/{n}?utm_source=gdelt"
    cols[7] = "ECON_INFLATION;TAX_FNCACT;ECON_INFLATION"
    cols[9] = "1#Made-up Land#XX##1.5#2.5#XX;3#Made-up Town, Made-up Land#XX#XX01#1.5#2.5#-1"
    cols[11] = "made-up person;made-up person"
    cols[13] = "made-up bank"
    cols[15] = "-2.5,1.0,3.5,4.5,20.0,0.5,300"
    cols[22] = "a quotation never kept"
    extras = "<PAGE_PRECISEPUBTIMESTAMP>20251231234500</PAGE_PRECISEPUBTIMESTAMP>"
    if title is not None:
        extras += f"<PAGE_TITLE>{title}</PAGE_TITLE>"
    cols[26] = extras
    return "\t".join(cols)


def gkg_zip(rows: list[str], members: int = 1) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for m in range(members):
            z.writestr(f"made-up-{m}.gkg.csv", "\n".join(rows) + "\n")
    return out.getvalue()


def test_a_gkg_file_keeps_gdelts_fields_and_never_quotations() -> None:
    raw = "\n".join([gkg_row(1), gkg_row(2, kind="2"), "too\tfew", gkg_row(3, title=None)]).encode()
    first, third = parse_gkg(raw)
    assert first.id == "https://made-up.example/story/1"
    p = first.payload
    assert p["headline"] == "Made-up headline & more"
    assert p["source"] == "made-up.example" and p["via"] == "gdelt"
    assert p["gdelt_time"] == "2026-01-01T00:00:00Z" and p["published"] == "2025-12-31T23:45:00Z"
    assert first.published_ns == T0 - 15 * 60 * NS  # the publisher's time when GDELT has it
    assert p["themes"] == ["ECON_INFLATION", "TAX_FNCACT"]
    assert p["places"] == [
        {"name": "Made-up Land", "country": "XX", "adm1": ""},
        {"name": "Made-up Town, Made-up Land", "country": "XX", "adm1": "XX01"},
    ]
    assert p["people"] == ["made-up person"] and p["organisations"] == ["made-up bank"]
    assert p["tone"] == "-2.5"  # as text, never a float
    assert "a quotation never kept" not in json.dumps(p)
    assert third.payload["headline"] is None


def test_a_gkg_zip_is_opened_safely() -> None:
    assert unzip_one(gkg_zip([gkg_row(1)])).startswith(b"20260101000000-1")
    with pytest.raises(ValueError, match="holds 2 files"):
        unzip_one(gkg_zip([gkg_row(1)], members=2))
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.csv", b"\0" * 5_000_000)
    with pytest.raises(ValueError, match="too large"):
        unzip_one(bomb.getvalue())
    with pytest.raises(ValueError, match="not a zip"):
        unzip_one(b"not a zip")


def test_the_index_names_the_newest_file() -> None:
    index = (
        b"40000 aaa http://data.gdeltproject.org/gdeltv2/20260101000000.export.CSV.zip\n"
        b"2450000 BBB http://data.gdeltproject.org/gdeltv2/20260101000000.gkg.csv.zip\n"
    )
    assert newest_file(index) == ("20260101000000", "bbb")
    assert newest_file(b"nothing here") is None


def test_a_doc_search_reads_its_articles_and_refuses_text() -> None:
    body = json.dumps({"articles": [
        {"url": "https://www.made-up.example/story/1", "title": "Made-up <b>rates</b>",
         "seendate": "20260101T001500Z", "domain": "made-up.example", "language": "English",
         "sourcecountry": "Made-up Land"},
        {"url": "not a link", "title": "skipped"},
    ]}).encode()  # fmt: skip
    [one] = parse_doc("economy", body)
    assert one.id == "https://made-up.example/story/1"
    assert one.published_ns is None  # a search gives GDELT's time only, never as the publisher's
    assert one.payload["gdelt_time"] == "2026-01-01T00:15:00Z"
    assert one.payload["headline"] == "Made-up rates" and one.payload["query"] == "economy"
    assert parse_doc("economy", b"") == [] and parse_doc("economy", b"{}") == []
    with pytest.raises(ValueError, match="article list"):
        parse_doc("economy", b"Please limit requests to one every 5 seconds")
    assert "query=inflation+sourcelang%3Aenglish" in doc_url("inflation sourcelang:english")


def made_up_gdelt(files: dict[str, bytes], newest: str, *, md5: str | None = None) -> Any:
    def reply(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/gdeltv2/lastupdate.txt":
            digest = md5 or hashlib.md5(files[newest], usedforsecurity=False).hexdigest()
            line = f"1 {digest} http://data.gdeltproject.org/gdeltv2/{newest}.gkg.csv.zip\n"
            return httpx.Response(200, content=line.encode())
        name = path.rsplit("/", 1)[-1].removesuffix(".gkg.csv.zip")
        if path.startswith("/gdeltv2/") and name in files:
            return httpx.Response(200, content=files[name])
        if request.url.host == "api.gdeltproject.org":
            return httpx.Response(429, text="Please limit requests to one every 5 seconds")
        return httpx.Response(404)

    return reply


def gdelt_run(
    tmp_path: Path,
    reply: Any,
    *,
    mark: int | None = None,
    behind: int | None = None,
    until_s: float = 60,
    queries: list[GdeltQuery] | None = None,
) -> tuple[list[Item], Poller, StateStore]:
    store = StateStore(tmp_path / "state.sqlite")
    if mark is not None:
        store.set_bookmark(BOOKMARK, mark)
    if behind is not None:  # a backlog begun before a restart
        store.set_bookmark(BEHIND, behind)
    clock = FakeClock(until_s=until_s)
    poller = Poller(store, clock, user_agent("9.9"), transport=httpx.MockTransport(reply))
    emitted: list[Item] = []

    async def emit(item: Item) -> None:
        emitted.append(item)

    news = NewsSettings(gdelt_queries=queries or [], feeds=[])
    source = GdeltNews()
    ctx = PluginContext(source.spec, clock, {}, emit, 30.0,
                        services={"poller": lambda: poller, "news": lambda: news})  # fmt: skip
    run(source, ctx, poller)
    return emitted, poller, store


def stamps(start: str, count: int) -> list[str]:
    from datetime import timedelta

    from ikars_engine.gdelt import STAMP, stamp_time

    t = stamp_time(start)
    return [(t + timedelta(minutes=15 * k)).strftime(STAMP) for k in range(count)]


def test_the_first_start_reads_the_newest_file_and_bookmarks_it(tmp_path: Path) -> None:
    files = {"20260101000000": gkg_zip([gkg_row(1), gkg_row(2)])}
    emitted, poller, store = gdelt_run(tmp_path, made_up_gdelt(files, "20260101000000"))
    assert [i.ref.id for i in emitted] == [
        "https://made-up.example/story/1", "https://made-up.example/story/2"
    ]  # fmt: skip
    assert store.bookmark(BOOKMARK) == 20260101000000
    assert poller.notes["gdelt"]["file"] == "20260101000000"
    store.close()


def test_missed_files_are_backfilled_a_few_at_a_time(tmp_path: Path) -> None:
    names = stamps("20260101000000", 7)  # the bookmark, then six missed files
    files = {n: gkg_zip([gkg_row(k)]) for k, n in enumerate(names)}
    newest = names[-1]
    emitted, poller, store = gdelt_run(tmp_path, made_up_gdelt(files, newest), mark=int(names[0]),
                                       until_s=200)  # fmt: skip
    # two reads of the index (about 120 s apart): four files, then the last two
    assert len(emitted) == 6 and store.bookmark(BOOKMARK) == int(newest)
    [gap] = store.gaps()  # logged once, though two reads caught up
    assert (gap.recovery, gap.status) == ("backfill", "done") and "by name" in gap.detail
    assert FILES_PER_READ == 4
    store.close()


def test_a_file_gdelt_never_published_is_logged_apart_from_the_backfill(tmp_path: Path) -> None:
    names = stamps("20260101000000", 4)
    files = {n: gkg_zip([gkg_row(k)]) for k, n in enumerate(names)}
    del files[names[1]]  # two slots older than the newest: GDELT never published it
    emitted, poller, store = gdelt_run(tmp_path, made_up_gdelt(files, names[3]), mark=int(names[0]))
    assert [i.ref.id for i in emitted] == [
        "https://made-up.example/story/2", "https://made-up.example/story/3"
    ]  # fmt: skip
    lost, after = store.gaps()  # the lost file, then the backfill around it: never both ways
    assert (lost.recovery, lost.status) == ("unrecoverable", "unrecoverable")
    assert "20260101001500" in lost.detail and "HTTP 404" in lost.detail
    assert after.recovery == "backfill" and after.start_ns == lost.end_ns
    assert store.bookmark(BOOKMARK) == int(names[3]) and store.bookmark(BEHIND) is None
    assert poller.notes["gdelt"]["missing"] == 1
    store.close()


def test_a_recent_file_not_there_yet_is_retried_not_passed(tmp_path: Path) -> None:
    names = stamps("20260101000000", 3)
    files = {names[0]: b"", names[2]: gkg_zip([gkg_row(9)])}  # one slot older: may still come
    emitted, poller, store = gdelt_run(tmp_path, made_up_gdelt(files, names[2]), mark=int(names[0]),
                                       until_s=30)  # fmt: skip
    assert emitted == [] and store.gaps() == []
    assert store.bookmark(BOOKMARK) == int(names[0])  # the read is tried again
    assert poller.feeds["gdelt.gkg"].last_error == "HTTP 404"
    store.close()


def test_a_damaged_old_file_is_passed_but_the_network_is_retried() -> None:
    newest = "20260101010000"
    assert passable("20260101003000", newest, ValueError("not a zip file"))
    assert passable("20260101003000", newest, PollError("too large", status=200))
    assert not passable("20260101003000", newest, PollError("ConnectTimeout"))
    assert not passable("20260101003000", newest, PollError("HTTP 503", status=503))
    assert not passable("20260101003000", newest, PollError("HTTP 429", status=429))
    assert not passable("20260101004500", newest, ValueError("not a zip file"))  # too recent


def test_files_older_than_a_day_are_logged_not_fetched(tmp_path: Path) -> None:
    newest = "20260103000000"
    files = {newest: gkg_zip([gkg_row(1)])}
    for n in stamps("20260102000000", 96):
        files.setdefault(n, gkg_zip([]))
    emitted, poller, store = gdelt_run(tmp_path, made_up_gdelt(files, newest), mark=20260101000000)
    [old] = store.gaps()  # the backfill is logged once it has caught up
    assert old.recovery == "unrecoverable" and "older than a day" in old.detail
    assert poller.notes["gdelt"]["behind"] > 0  # still catching up the day it can fetch
    assert poller.notes["gdelt"]["missing"] == 95
    assert store.bookmark(BOOKMARK) == 20260102004500  # one read: the first four files of the day
    assert store.bookmark(BEHIND) == 20260102000000  # kept for the backfill entry, across restarts
    store.close()


def test_a_backlog_begun_before_a_restart_is_logged_from_its_start(tmp_path: Path) -> None:
    names = stamps("20260101000000", 4)
    files = {n: gkg_zip([gkg_row(k)]) for k, n in enumerate(names)}
    emitted, poller, store = gdelt_run(tmp_path, made_up_gdelt(files, names[3]),
                                       mark=int(names[1]), behind=20251231230000)  # fmt: skip
    [gap] = store.gaps()
    assert gap.recovery == "backfill" and gap.start_ns == T0 - 3_600 * NS
    assert store.bookmark(BEHIND) is None
    store.close()


def test_a_file_that_does_not_match_the_index_is_refused(tmp_path: Path) -> None:
    files = {"20260101000000": gkg_zip([gkg_row(1)])}
    emitted, poller, store = gdelt_run(
        tmp_path, made_up_gdelt(files, "20260101000000", md5="0" * 32), until_s=30
    )
    assert emitted == [] and store.bookmark(BOOKMARK) is None
    assert poller.feeds["gdelt.gkg"].errors == 1
    store.close()


def test_a_search_that_gdelt_refuses_leaves_the_files_running(tmp_path: Path) -> None:
    files = {"20260101000000": gkg_zip([gkg_row(1)])}
    queries = [GdeltQuery(name="economy", query="inflation"), GdeltQuery(name="economy", query="x")]
    emitted, poller, store = gdelt_run(tmp_path, made_up_gdelt(files, "20260101000000"),
                                       queries=queries)  # fmt: skip
    assert len(emitted) == 1  # from the GKG file
    search = poller.feeds["gdelt.q.economy"]
    assert search.errors >= 1 and search.last_error == "HTTP 429"
    assert poller.notes["gdelt"]["searches"] == ["economy"]  # the duplicate name skipped
    store.close()


# --- the polling base -----------------------------------------------------------------------


def test_feeds_of_one_scope_never_send_an_item_twice(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    poller = Poller(store, FakeClock(), user_agent("9.9"))
    a = Feed(name="x.a", url="https://made-up.example/a", every_s=60, seen="news")
    b = Feed(name="x.b", url="https://made-up.example/b", every_s=60, seen="news")
    item = [Found(id="https://made-up.example/1")]
    assert poller.fresh(a, item) == item
    assert poller.fresh(b, item) == []  # claimed: a is still sending it
    poller.emitted(a, [], ["https://made-up.example/1"])  # a failed to send it
    assert poller.fresh(b, item) == item
    poller.emitted(b, ["https://made-up.example/1"], ["https://made-up.example/1"])
    assert poller.fresh(a, item) == []  # remembered in the shared scope
    other = [Found(id="https://made-up.example/2")]
    assert poller.fresh(a, other) == other  # a claims it
    poller.emitted(b, ["https://made-up.example/2"], [])  # b listed it too, and sent all it had
    poller.emitted(a, [], ["https://made-up.example/2"])  # but a failed to send it
    assert poller.fresh(b, other) == other  # so it was never remembered as sent: not lost
    alone = Feed(name="x.c", url="https://made-up.example/c", every_s=60)
    assert poller.fresh(alone, item) == item  # its own scope
    asyncio.run(poller.aclose())
    store.close()


def test_an_index_is_read_whole_and_sizes_and_hosts_have_limits(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    asked: list[httpx.Headers] = []

    def reply(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers)
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * 2_000)
        return httpx.Response(200, content=b"ok", headers={"etag": '"v1"'})

    clock = FakeClock(until_s=60)
    poller = Poller(store, clock, user_agent("9.9"), transport=httpx.MockTransport(reply))
    index = Feed(name="x.i", url="https://made-up.example/i", every_s=60, conditional=False)
    store.set_poll_feed("x.i", '"v1"', "Thu, 01 Jan 2026 00:00:00 GMT", T0, 60.0)

    async def go() -> None:
        await poller.fetch(index)
        with pytest.raises(PollError, match="larger than 1000 bytes"):
            await poller.once("https://made-up.example/big", max_bytes=1_000)
        assert await poller.once("https://made-up.example/big", max_bytes=5_000) == b"x" * 2_000
        poller.limit_host("slow.example", 0.1)
        assert poller._buckets["slow.example"]._rate == 0.1
        await poller.aclose()

    asyncio.run(go())
    assert "if-none-match" not in asked[0] and "if-modified-since" not in asked[0]
    store.close()


def test_a_poll_error_carries_the_status(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")

    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if request.url.path == "/gone" else 429)

    poller = Poller(
        store, FakeClock(until_s=60), user_agent("9.9"), transport=httpx.MockTransport(reply)
    )

    async def go() -> None:
        with pytest.raises(PollError) as gone:
            await poller.once("https://made-up.example/gone")
        assert gone.value.status == 404 and not gone.value.throttled
        with pytest.raises(PollError) as slow:
            await poller.once("https://made-up.example/slow")
        assert slow.value.status == 429 and slow.value.throttled
        await poller.aclose()

    asyncio.run(go())
    store.close()


def test_a_backfilling_feed_logs_its_own_gaps(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    clock = FakeClock()
    poller = Poller(store, clock, user_agent("9.9"))
    feed = Feed(name="x.g", url="https://made-up.example/g", every_s=60, backfills=True)
    health = poller.started(feed)
    health.last_ok_ns = T0 - 3_600 * NS
    poller.succeeded(feed, health, None, 60.0)
    assert store.gaps() == [] and health.gaps == 0  # the source logs what it fetched and missed
    asyncio.run(poller.aclose())
    store.close()


# --- settings -------------------------------------------------------------------------------


def test_settings_version_7_adds_the_news_list() -> None:
    assert VERSION == 7 and migrate({"version": 6})["version"] == 7
    news = Settings().news
    assert [f.name for f in news.feeds][:2] == ["fed-press", "fed-speeches"]
    assert next(f for f in news.feeds if f.name == "census-indicators").ids == "guid"
    assert [q.name for q in news.gdelt_queries] == [
        "economy", "government", "elections", "weather", "markets"
    ]  # fmt: skip


def test_only_a_feed_on_link_ids_is_flagged_for_shared_links() -> None:
    source = RssFeeds()
    source._ids = {"rss.l": "link", "rss.g": "guid"}
    feed = Feed(name="rss.l", url="https://made-up.example/l", every_s=300)
    ctx = PluginContext(source.spec, FakeClock(), {}, None, 30.0)  # type: ignore[arg-type]
    source.parse(feed, RSS, ctx)
    source.parse(Feed(name="rss.g", url="https://made-up.example/g", every_s=300), RSS, ctx)
    assert source._note["shared_links"] == {"rss.l": 2}
