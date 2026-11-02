"""News items (M2.3; PLAN §4.3 D23 as amended): what GDELT and the curated feeds share.

Every news item is the kind ``news``, and its id is its **cleaned link**: the same article from
GDELT and from a feed, or from two feeds, has one id, and every news feed remembers ids in one scope
(``SEEN``), so it is recorded once, from whichever source delivered it first. Clustering the same
story told by different publishers waits for the embeddings (M2.5).

Kept per item: the headline, the link, the source, the publisher's time, the received time (the
host's), the feed's own summary as plain text, and GDELT's own fields. Never the article: nothing
here fetches one (D23).
"""

import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

KIND = "news"
SEEN = "news"  # the seen scope every news feed shares
SUMMARY_CHARS = 1_000
HEADLINE_CHARS = 500

# query parameters that only track the reader; a link without them is the same article
_TRACKING = re.compile(
    r"^(utm_\w*|mc_cid|mc_eid|fbclid|gclid|dclid|msclkid|igshid|yclid|ocid|cmpid|smid)$",
    re.IGNORECASE,
)


def clean_link(url: str | None) -> str | None:
    """``url`` as an id: https, the host in lower case without ``www.``, no default port, no
    tracking parameters, no fragment, no trailing slash. None when it is not a web link."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower().removeprefix("www.")
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _TRACKING.match(k)
        ]
    )
    return urlunsplit(("https", netloc, parts.path.rstrip("/") or "/", query, ""))


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def plain_text(html: str | None, limit: int = SUMMARY_CHARS) -> str | None:
    """Markup as plain text: tags and scripts dropped, entities read, spaces collapsed, at most
    ``limit`` characters. None when nothing is left."""
    if not html:
        return None
    reader = _Text()
    reader.feed(html)
    reader.close()
    text = " ".join("".join(reader.parts).split())
    return text[:limit] or None
