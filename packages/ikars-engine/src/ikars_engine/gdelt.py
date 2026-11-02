"""GDELT news (M2.3; PLAN §4.3 D23 as amended), recorded from the first slice.

**The GKG files are the main stream** (owner, 2026-11-01). Every 15 minutes GDELT publishes a Global
Knowledge Graph file listing each article it saw, with its title in the ``Extras`` field. The plugin
reads the index (``lastupdate.txt``) every ``INDEX_EVERY_S`` and fetches each new file once, over
HTTPS. A file the engine missed (stopped, or GDELT unreachable) is fetched afterwards by name, up
to ``BACKFILL_S`` back, a few per read; older ones are logged as unrecoverable. The bookmark (the
last file read) moves on only once a file's items have all gone out.

**DOC 2.0 searches** from the settings run beside it every 15 minutes, at most one request every
``DOC_EVERY_S`` (GDELT asks for one every 5 seconds). On 2026-11-01 the API answered every query
with 429, so these add to the GKG stream when it answers; duplicates between them go by link.

Kept per article: the title, link, source, GDELT's time, the publisher's time, and GDELT's themes,
places, people, organisations and tone. Never quotations or the article's text (D23). GDELT's terms
ask for a citation, shown wherever its items appear (M2.7; docs/research/sources.md).
"""

import asyncio
import contextlib
import hashlib
import io
import re
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import msgspec

from ikars_engine.clocks import exact_ns
from ikars_engine.news import HEADLINE_CHARS, KIND, SEEN, clean_link, plain_text
from ikars_engine.plugins import PluginContext, PluginKind, PluginSpec
from ikars_engine.polling import Feed, Found, Poller, PollError, PollingSource
from ikars_engine.settings import NewsSettings

FILES = "https://data.gdeltproject.org/gdeltv2/"
INDEX = FILES + "lastupdate.txt"
DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
DOC_HOST = "api.gdeltproject.org"
INDEX_EVERY_S = 120.0
FILE_EVERY = timedelta(minutes=15)
DOC_EVERY_S = 900.0
DOC_REACH_S = 3_600.0  # a search reaches back an hour ("timespan": "1h")
DOC_PER_SECOND = 0.1  # one request every 10 seconds, under GDELT's one every 5
BACKFILL_S = 86_400  # missed files are fetched up to a day back (owner, 2026-11-01)
FILES_PER_READ = 4  # a catch-up of a day takes about an hour, in small steps
ZIP_MAX_BYTES = 40_000_000
UNZIPPED_MAX_BYTES = 200_000_000
MAX_RATIO = 30
KEPT = {"themes": 64, "places": 32, "people": 32, "organisations": 32}
BOOKMARK = "gdelt.gkg"
BEHIND = "gdelt.gkg.behind_since"  # the first file of a backlog being fetched
STAMP = "%Y%m%d%H%M%S"

_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.DOTALL)
_PUBLISHED = re.compile(r"<PAGE_PRECISEPUBTIMESTAMP>(\d{14})</PAGE_PRECISEPUBTIMESTAMP>")
_GKG = re.compile(r"/(\d{14})\.gkg\.csv\.zip$")


def stamp_time(stamp: str) -> datetime:
    """GDELT's ``YYYYMMDDHHMMSS``, which is UTC."""
    return datetime.strptime(stamp, STAMP).replace(tzinfo=UTC)


def _iso(stamp: str) -> str | None:
    try:
        return stamp_time(stamp).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def newest_file(index: bytes) -> tuple[str, str] | None:
    """The newest GKG file the index names: its stamp and MD5."""
    for line in index.decode("ascii", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 3 and (found := _GKG.search(parts[2])):
            return found.group(1), parts[1].lower()
    return None


def unzip_one(data: bytes) -> bytes:
    """The one file in a GKG zip, refused when the zip holds more, or would unpack too large."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) != 1:
                raise ValueError(f"the zip holds {len(members)} files, not one")
            member = members[0]
            if member.file_size > min(UNZIPPED_MAX_BYTES, MAX_RATIO * max(member.compress_size, 1)):
                raise ValueError("the zip would unpack too large")
            with archive.open(member) as inner:
                raw = inner.read(UNZIPPED_MAX_BYTES + 1)
    except zipfile.BadZipFile as exc:
        raise ValueError("not a zip file") from exc
    if len(raw) > UNZIPPED_MAX_BYTES:
        raise ValueError("the zip unpacked too large")
    return raw


def _unique(field: str, limit: int) -> list[str]:
    seen: dict[str, None] = {}
    for one in field.split(";"):
        if one and one not in seen:
            seen[one] = None
            if len(seen) == limit:
                break
    return list(seen)


def _places(field: str) -> list[dict[str, str]]:
    """GDELT's locations (``type#name#country#adm1#lat#lon#id``), each place once, without the
    coordinates."""
    out: dict[tuple[str, str, str], None] = {}
    for one in field.split(";"):
        parts = one.split("#")
        if len(parts) >= 4 and parts[1]:
            out.setdefault((parts[1], parts[2], parts[3]), None)
            if len(out) == KEPT["places"]:
                break
    return [{"name": n, "country": c, "adm1": a} for n, c, a in out]


def parse_gkg(raw: bytes) -> list[Found]:
    """A GKG 2.1 file's web articles (27 tab-separated columns a row)."""
    found: list[Found] = []
    for line in raw.decode("utf-8", errors="replace").split("\n"):
        cols = line.rstrip("\r").split("\t")
        if len(cols) < 27 or cols[2] != "1":  # 1: a web article
            continue
        link = cols[4]
        key = clean_link(link)
        if key is None:
            continue
        extras = cols[26]
        title = _TITLE.search(extras)
        published = _PUBLISHED.search(extras)
        published_ns = None  # the publisher's time only, never GDELT's crawl time (merge review)
        if published:
            with contextlib.suppress(ValueError):
                published_ns = exact_ns(stamp_time(published.group(1)))
        payload: dict[str, Any] = {
            # entities read once, by plain_text (merge review 7)
            "headline": plain_text(title.group(1), HEADLINE_CHARS) if title else None,
            "link": link,
            "source": cols[3] or None,
            "gdelt_time": _iso(cols[1]),
            "published": _iso(published.group(1)) if published else None,
            "themes": _unique(cols[7], KEPT["themes"]),
            "places": _places(cols[9]),
            "people": _unique(cols[11], KEPT["people"]),
            "organisations": _unique(cols[13], KEPT["organisations"]),
            "tone": cols[15].split(",", 1)[0] or None,  # as GDELT gives it, never a float
            "gkg_id": cols[0],
            "via": "gdelt",
        }
        found.append(Found(id=key, published_ns=published_ns, payload=payload))
    return found


def read_file(data: bytes, md5: str | None = None) -> list[Found]:
    """One GKG zip's articles, its MD5 checked when the index gave it (the index names only the
    newest file, so a backfilled file is checked only by unzipping it)."""
    if md5 is not None and hashlib.md5(data, usedforsecurity=False).hexdigest() != md5:
        raise ValueError("the GKG file does not match the index's MD5")
    return parse_gkg(unzip_one(data))


def passable(name: str, newest: str, exc: Exception) -> bool:
    """Whether a file that failed is passed (logged as missed) rather than retried: only a file
    at least two slots older than the newest (a fresh one can still be on its way: merge review 6)
    whose failure is GDELT's answer (not found, too large, unreadable), never the network's or a
    busy server's, which a retry fixes (merge review 3)."""
    if stamp_time(newest) - stamp_time(name) < 2 * FILE_EVERY:
        return False
    if isinstance(exc, PollError):
        return exc.status is not None and exc.status < 500 and exc.status != 429
    return True


class _Article(msgspec.Struct):
    url: str = ""
    title: str = ""
    seendate: str = ""
    domain: str = ""
    language: str = ""
    sourcecountry: str = ""


class _Articles(msgspec.Struct):
    articles: list[_Article] = []


def parse_doc(query: str, body: bytes) -> list[Found]:
    """A DOC 2.0 article list. GDELT answers some refusals with a 200 and a line of text, which
    is an error here, not an empty list."""
    if not body.strip():
        return []
    try:
        reply = msgspec.json.decode(body, type=_Articles)
    except msgspec.DecodeError as exc:
        raise ValueError(f"GDELT did not answer with an article list: {body[:80]!r}") from exc
    found: list[Found] = []
    for a in reply.articles:
        key = clean_link(a.url)
        if key is None:
            continue
        seen = a.seendate.replace("T", "").removesuffix("Z")
        payload = {
            "headline": plain_text(a.title, HEADLINE_CHARS),
            "link": a.url,
            "source": a.domain or None,
            "gdelt_time": _iso(seen),
            "language": a.language or None,
            "country": a.sourcecountry or None,
            "query": query,
            "via": "gdelt-doc",
        }
        # GDELT's seen time is in gdelt_time; the search gives no publisher's time
        found.append(Found(id=key, published_ns=None, payload=payload))
    return found


def doc_url(query: str) -> str:
    return DOC + "?" + urlencode({
        "query": query, "mode": "artlist", "format": "json", "maxrecords": 250,
        "timespan": "1h", "sort": "datedesc",  # DOC_REACH_S
    })  # fmt: skip


class GdeltNews(PollingSource):
    spec = PluginSpec(
        name="gdelt", kind=PluginKind.SOURCE, version="1", produces=(KIND,), rate_per_second=1.0
    )
    item_kind = KIND

    def __init__(self) -> None:
        self._read_to: str | None = None  # the last file of the reply going out now
        self._caught_up = False  # that reply reached the newest file
        # windows missed for good, logged once the reply that found them has gone out, so a read
        # that fails and is retried never logs them twice (merge review 4)
        self._pending: list[tuple[datetime, datetime, str]] = []
        self._poller: Poller | None = None
        self._note: dict[str, Any] = {}

    def parse(self, feed: Feed, body: bytes, ctx: PluginContext) -> list[Found]:
        return parse_doc(feed.name.removeprefix("gdelt.q."), body)

    async def read(
        self, feed: Feed, body: bytes, poller: Poller, ctx: PluginContext
    ) -> list[Found]:
        if feed.name != "gdelt.gkg":
            return self.parse(feed, body, ctx)
        newest = newest_file(body)
        if newest is None:
            raise ValueError("the GDELT index names no GKG file")
        stamp, md5 = newest
        self._read_to, self._pending = None, []
        due = self._due(poller, stamp)
        found: list[Found] = []
        for one in due[:FILES_PER_READ]:
            try:
                data = await ctx.call(
                    poller.once(f"{FILES}{one}.gkg.csv.zip", max_bytes=ZIP_MAX_BYTES), 120.0
                )
                # off the event loop: a file is megabytes to unpack and read (merge review 2)
                found.extend(
                    await asyncio.to_thread(read_file, data, md5 if one == stamp else None)
                )
            except (PollError, ValueError) as exc:
                if not passable(one, stamp, exc):
                    raise  # retried at the next read: the network, or the newest file
                when = stamp_time(one)
                self._pending.append((when, when + FILE_EVERY, f"GKG file {one}: {exc}"))
            self._read_to = one
        behind = max(0, len(due) - FILES_PER_READ)
        self._caught_up = behind == 0
        self._note.update({"newest": stamp, "behind": behind})
        return found

    def _due(self, poller: Poller, newest: str) -> list[str]:
        """The files after the bookmark up to ``newest``, oldest first; with no bookmark (the first
        start), the newest only. A window older than ``BACKFILL_S`` is noted as missed, and the
        start of a backlog is kept (in the state store, across restarts) for its backfill entry."""
        mark = poller.bookmark(BOOKMARK)
        last = stamp_time(newest)
        if mark is None:
            return [newest]
        after = stamp_time(str(mark)) + FILE_EVERY
        oldest = last - timedelta(seconds=BACKFILL_S)
        if after < oldest:
            self._pending.append((after, oldest, "GKG files older than a day are not fetched"))
            after = oldest
        # more than two files behind (the gap rule, at the files' pace): a backlog to fetch by name
        if last - after >= 2 * FILE_EVERY and poller.bookmark(BEHIND) is None:
            poller.set_bookmark(BEHIND, int(after.strftime(STAMP)))
        out = []
        while after <= last:
            out.append(after.strftime(STAMP))
            after += FILE_EVERY
        return out

    def delivered(self, feed: Feed) -> None:
        """The reply's items went out: the bookmark moves on, the missed windows are logged, and a
        backlog now caught up is logged once as a backfill (merge review 4)."""
        poller = self._poller
        if feed.name != "gdelt.gkg" or poller is None:
            return
        for start, end, detail in self._pending:
            poller.log_gap("gdelt.gkg", exact_ns(start), exact_ns(end), "unrecoverable", detail)
            self._note["missing"] = self._note.get("missing", 0) + (end - start) // FILE_EVERY
        self._pending = []
        if self._read_to is None:
            return
        poller.set_bookmark(BOOKMARK, int(self._read_to))
        self._note["file"] = self._read_to
        behind_from = poller.bookmark(BEHIND)
        if behind_from is not None and self._caught_up:
            end = stamp_time(self._read_to) + FILE_EVERY
            poller.log_backfill("gdelt.gkg", exact_ns(stamp_time(str(behind_from))),
                                exact_ns(end), "missed GKG files fetched by name")  # fmt: skip
            poller.clear_bookmark(BEHIND)
        self._read_to = None

    async def manage(
        self,
        ctx: PluginContext,
        poller: Poller,
        start: Callable[[Feed], None],
        stop: Callable[[str], None],
    ) -> None:
        if ctx.settings.get("enabled", True) is False:
            ctx.log.info("the gdelt source is turned off in the settings")
            return
        self._poller = poller
        self._note = poller.notes.setdefault(self.spec.name, {})
        poller.limit_host(DOC_HOST, DOC_PER_SECOND)
        start(Feed(name="gdelt.gkg", url=INDEX, every_s=INDEX_EVERY_S, accept="text/plain",
                   kind=KIND, seen=SEEN, conditional=False, backfills=True))  # fmt: skip
        get = ctx.services.get("news")
        settings: NewsSettings = get() if get is not None else NewsSettings(gdelt_queries=[])
        names: set[str] = set()
        for q in settings.gdelt_queries:
            if q.name in names or not q.query.strip():
                ctx.log.warning("a GDELT search was skipped", extra={"search": q.name})
                continue
            names.add(q.name)
            start(Feed(name=f"gdelt.q.{q.name}", url=doc_url(q.query), every_s=DOC_EVERY_S,
                       accept="application/json", kind=KIND, seen=SEEN,
                       reach_s=DOC_REACH_S))  # fmt: skip
        self._note["searches"] = sorted(names)
