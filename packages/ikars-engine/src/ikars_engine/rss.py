"""Curated RSS and Atom feeds (M2.3; PLAN §4.3 D23 as amended), recorded from the first slice.

The list lives in the settings (``[news] feeds``): US government feeds to start (owner, 2026-11-01),
publisher feeds only by the owner's decision, feed by feed. A feed is polled only when its terms
check is recorded (``terms``; G17): one without is refused and health says why.

Feeds are read with ``defusedxml`` (no entity expansion, no external entities or DTD fetches), by
the tags' local names, so RSS 2.0, RSS 1.0 (RDF) and Atom read alike. Kept per item: the headline,
the link, the publisher's time, and the feed's own summary as plain text (D23). The id is the
cleaned link, so a story two feeds list goes out once; a feed that reuses one link for many items
(the Census indicators) is set to ``ids = "guid"``.
"""

import re
from collections.abc import Callable
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree.ElementTree import Element, ParseError

import defusedxml.ElementTree as SafeXml  # type: ignore[import-untyped]  # no stubs; typed below
from defusedxml import DefusedXmlException

from ikars_engine.clocks import exact_ns
from ikars_engine.news import HEADLINE_CHARS, KIND, SEEN, clean_link, plain_text
from ikars_engine.plugins import PluginContext, PluginKind, PluginSpec
from ikars_engine.polling import Feed, Found, Poller, PollingSource, iso_ns
from ikars_engine.settings import NewsFeed, NewsSettings

ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9"
MIN_EVERY_S = 60
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_ITEMS = ("item", "entry")
_TIMES = ("pubDate", "published", "updated", "date", "issued", "modified")


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(element: Element | None) -> str | None:
    if element is None:
        return None
    text = "".join(element.itertext()).strip()  # Atom's xhtml title or summary has child tags
    return text or None


def _link(item: Element) -> str | None:
    """RSS's ``<link>text</link>``, or Atom's alternate ``<link href>`` (rel omitted: alternate)."""
    fallback = None
    for child in item:
        if _local(child.tag) != "link":
            continue
        href = child.get("href")
        if href is None:
            return _text(child)
        if child.get("rel", "alternate") == "alternate":
            return href.strip()
        fallback = fallback or href.strip()
    return fallback


def feed_time(text: str | None) -> int | None:
    """A feed's time in exact nanoseconds: RFC 822 (RSS) or ISO 8601 (Atom, Dublin Core). None
    when it has no zone; RFC 822's ``-0000`` is UTC, as the RFC says."""
    if not text:
        return None
    text = text.strip()
    found = iso_ns(text)
    if found is not None:
        return found
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        if not text.endswith("-0000"):
            return None
        when = when.replace(tzinfo=UTC)
    return exact_ns(when)


def parse_feed(name: str, body: bytes, ids: str = "link") -> tuple[list[Found], int]:
    """The items of one RSS or Atom reply, and how many of them shared a link with another (a
    sign the feed wants ``ids = "guid"``)."""
    try:
        root: Element = SafeXml.fromstring(body)
    except (ParseError, DefusedXmlException) as exc:
        raise ValueError(f"the feed is not safe, well-formed XML: {type(exc).__name__}") from exc
    channel = next((e for e in root.iter() if _local(e.tag) in ("channel", "feed")), root)
    publisher = _text(next((c for c in channel if _local(c.tag) == "title"), None))
    found: list[Found] = []
    links: dict[str, int] = {}
    for item in (e for e in root.iter() if _local(e.tag) in _ITEMS):
        kids: dict[str, Element] = {}
        for child in item:
            kids.setdefault(_local(child.tag), child)
        link = _link(item)
        cleaned = clean_link(link)
        guid = _text(kids.get("guid")) or _text(kids.get("id"))
        if cleaned is not None:
            links[cleaned] = links.get(cleaned, 0) + 1
        if ids == "guid" and guid:
            key = f"{name}:{guid}"
        elif cleaned is not None:
            key = cleaned
        elif guid:
            key = f"{name}:{guid}"
        else:
            continue  # nothing stable to know it by again
        stamp = next((_text(kids[t]) for t in _TIMES if t in kids), None)
        payload: dict[str, Any] = {
            "headline": plain_text(_text(kids.get("title")), HEADLINE_CHARS),
            "link": link,
            "source": publisher,
            "published": stamp,
            "summary": plain_text(_text(kids.get("description")) or _text(kids.get("summary"))),
            "via": "rss",
        }
        found.append(Found(id=key, published_ns=feed_time(stamp), payload=payload))
    shared = sum(n for n in links.values() if n > 1)
    return found, shared


class RssFeeds(PollingSource):
    spec = PluginSpec(
        name="rss", kind=PluginKind.SOURCE, version="1", produces=(KIND,), rate_per_second=1.0
    )
    item_kind = KIND

    def __init__(self) -> None:
        self._ids: dict[str, str] = {}
        self._note: dict[str, Any] = {}

    def parse(self, feed: Feed, body: bytes, ctx: PluginContext) -> list[Found]:
        ids = self._ids.get(feed.name, "link")
        found, shared = parse_feed(feed.name, body, ids)
        notes = self._note.setdefault("shared_links", {})
        if shared and ids == "link":  # a feed already on guid ids is fine as it is
            notes[feed.name] = shared  # one link for several items: set ids = "guid"
        else:
            notes.pop(feed.name, None)
        return found

    async def manage(
        self,
        ctx: PluginContext,
        poller: Poller,
        start: Callable[[Feed], None],
        stop: Callable[[str], None],
    ) -> None:
        if ctx.settings.get("enabled", True) is False:
            ctx.log.info("the rss source is turned off in the settings")
            return
        get = ctx.services.get("news")
        settings: NewsSettings = get() if get is not None else NewsSettings(feeds=[])
        self._note = poller.notes.setdefault(self.spec.name, {})
        feeds, refused = accepted(settings.feeds)
        self._note.update({"feeds": len(feeds), "refused": refused, "shared_links": {}})
        for one in feeds:
            self._ids[f"rss.{one.name}"] = one.ids
            every_s = float(max(MIN_EVERY_S, one.every_s))
            start(Feed(name=f"rss.{one.name}", url=one.url, every_s=every_s, accept=ACCEPT,
                       kind=KIND, seen=SEEN))  # fmt: skip
        for name, why in refused.items():
            ctx.log.warning("a news feed was refused", extra={"feed": name, "why": why})


def accepted(feeds: list[NewsFeed]) -> tuple[list[NewsFeed], dict[str, str]]:
    """The feeds that may be polled, and why each other one may not (G17: no terms note)."""
    ok: list[NewsFeed] = []
    refused: dict[str, str] = {}
    names: set[str] = set()
    for one in feeds:
        if not _NAME.match(one.name):
            refused[one.name[:48]] = "the name must be letters, digits, - and _"
        elif one.name in names:
            refused[one.name] = "another feed has this name"
        elif not one.terms.strip():
            refused[one.name] = "no terms check recorded (terms)"
        elif not one.url.startswith("https://"):
            refused[one.name] = "only https feeds are polled"
        else:
            names.add(one.name)
            ok.append(one)
    return ok, refused
