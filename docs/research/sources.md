# Sources: terms of use and how IKARS polls them

A record of each outside source's terms (PLAN G17), checked before its adapter is built, and how the M2.1 polling
framework treats it. Each RSS feed gets a row here when it is added (M2.3 onward).

All the sources here are public: no source gives IKARS material non-public information (G17, Kalshi's rulebook).
Nothing fetched is ever committed to this public repository (D10, D23).

## How every polling source behaves (M2.1)

- **User-Agent:** `IKARS/<version> (+https://github.com/swalsh76/IKARS)`. An email is added only when the local
  settings hold one (`[sources] contact_email`), never in the repository.
- **HTTPS only,** with no redirects followed and a reply size limit (5 MB).
- **About one request a second per host,** across every feed on that host.
- **Conditional GET:** `If-Modified-Since` and `If-None-Match`, from the validators the server gave last time.
- **Never sooner than the server's `Cache-Control: max-age`** for that reply.
- **Waiting:** `Retry-After` is honoured on 429 and 503, and other errors back off up to the feed's interval.

## NWS / NOAA: `api.weather.gov` (M2.2)

Checked 2026-10-28 on the API's documentation page (weather.gov/documentation/services-web-api).

| Item | Finding |
|---|---|
| Terms | "intended to be open data, free to use for any purpose" |
| Key | none needed today ("will be replaced with an API key in the future") |
| User-Agent | required, to identify the application |
| Rate limit | "reasonable rate limits in place to prevent abuse"; the limit itself is not published |
| Caching | cache-friendly by design; a location's grid may be cached but can change, so `/points` is checked again from time to time |

**M2.1 live probe** (2026-10-28):
- **Observations** (`/stations/{id}/observations/latest`) answer with a weak `ETag`, `Last-Modified` and
  `max-age=280`.
- **`If-Modified-Since` gives a 304.** `If-None-Match` alone does not, because the weak `ETag` names the server
  that answered. IKARS sends both.
- **`/points/{lat},{lon}` gives a location's forecast URLs.** Coordinates must have at most four decimal
  places; more gives a redirect.

**What Kalshi settles on (M2.2, 2026-10-28).** Kalshi's weather markets settle on **The Weather Company's**
value, as the series' settlement source and each market's rules say: "the maximum temperature recorded at New
York City (CLINYC) … according to The Weather Company".
- **The Weather Company's portal** (weather.com/kalshi) shows "verified climate observations from official
  government climate stations". It offers no API, feed or terms for automated access, so IKARS does not fetch
  it (G17).
- **IKARS records the NWS climate report for the same station as the proxy** (owner, 2026-10-28). How close it is
  gets measured against Kalshi's settlements in M2.4.
- **The rules name the report's code** (`CLINYC`). The station is `K` plus its location (KNYC, Central Park). All
  24 codes in open markets mapped on 2026-10-28.
- **The latest report, with its text, is at `/products/types/CLI/locations/{location}/latest`** (`max-age=49`):
  - preliminary in the afternoon ("VALID TODAY AS OF ...");
  - final the next morning ("YESTERDAY").

## GDELT: DOC 2.0 API and archives (M2.3, M2.8)

Checked 2026-10-28 on gdeltproject.org/about.html, "Terms of Use".

| Item | Finding |
|---|---|
| Terms | "available for unlimited and unrestricted use for any academic, commercial, or governmental use of any kind without fee" |
| Redistribution | allowed ("redistribute, rehost, republish, and mirror ... in any form") |
| Citation | any use must "include a citation to the GDELT Project and a link to this website": IKARS shows "Source: The GDELT Project (gdeltproject.org)" wherever GDELT items appear (M2.7) |
| Third-party content | GDELT points to articles owned by their publishers. IKARS keeps only the headline, link, source, times and GDELT's own fields, never the article text (D23). |

**How IKARS uses GDELT (M2.3, owner 2026-11-01).** Both routes keep GDELT's terms above.
- **The GKG 15-minute files: the main stream.**
  - `data.gdeltproject.org/gdeltv2/lastupdate.txt` names the newest files. It lists them as `http://`, and IKARS
    fetches them over `https://`, which the host serves.
  - On 2026-11-01 a GKG file was 2.5 MB zipped (7.5 MB unzipped), with `max-age=3600` and an ETag. It listed about
    550 to 1,100 web articles, every one with its title, from 150 to 280 sources.
  - IKARS checks the index's MD5 for the newest file.
- **The DOC 2.0 API: topic searches, when it answers.**
  - It asks for at most one request every 5 seconds, and IKARS sends one every 10 at most.
  - On 2026-11-01 it answered every request with 429, even 90 seconds apart.
  - It refuses a 15-minute span as too short, so IKARS asks for the last hour. A search returns at most 250
    articles.

## US government feeds (M2.3)

US government works have no copyright (17 U.S.C. §105). Each agency's own policy was checked on 2026-11-01 and is
quoted below. IKARS keeps only the headline, link, times and the feed's own summary (D23), and cites the agency
wherever an item appears (M2.7).

| Feeds (settings name) | Agency's terms |
|---|---|
| `fed-press`, `fed-speeches` | Federal Reserve Board, Website Policies: "information on Board's website is in the public domain"; "Please cite to the Board as the source" |
| `bea` | Bureau of Economic Analysis: no copyright statement on its policies page; a US government work, so public domain by law; it publishes "Guidelines for Citing BEA" |
| `census-indicators` | Census Bureau, open data policy: it shares "its public data as open data" and asks for citation; a US government work. This feed reuses one link for several releases, so its ids are its guids |
| `eia-press`, `eia-today` | EIA, Copyrights and Reuse: "U.S. government publications are in the public domain"; cite with "Source: U.S. Energy Information Administration" and the date |
| `nhc-atlantic`, `nhc-east-pacific` | NWS/NOAA disclaimer (covers the National Hurricane Center): "in the public domain, unless specifically noted otherwise"; "may be used without charge for any lawful purpose" |
| `whitehouse-news`, `whitehouse-actions` | whitehouse.gov copyright: "government-produced materials appearing on this site are not copyright protected"; third-party content is CC BY 3.0. Replies are about 550 KB, mostly answered 304 |
| `fda-press` | FDA, Website Policies: "not copyrighted", "may be republished, reprinted and otherwise used freely by anyone"; credit "appreciated but not required" |
| `usgs-quakes` | USGS, Copyrights and Credits: "USGS-authored or produced data and information are considered to be in the U.S. Public Domain"; credit "U.S. Geological Survey" |

**Not in the starting list:**
- **BLS** answers the IKARS User-Agent with 403.
- **SEC** allows no "unclassified" bots and at most 10 requests a second. Both need a contact email in the
  User-Agent (`[sources] contact_email`, set only in the local settings), and can be added once that is set.
- **Treasury's** press feed address needs finding.

Publisher feeds (news organisations) come only by the owner's decision, feed by feed (owner, 2026-11-01).
