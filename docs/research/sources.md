# Sources: terms of use and how IKARS polls them

A record of each outside source's terms (PLAN G17), checked before its adapter is built, and how the M2.1 polling
framework treats it. Each RSS feed in M2.3 gets a row here when it is added.

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
- **`/points/{lat},{lon}` gives a location's forecast URLs.**

## GDELT: DOC 2.0 API and archives (M2.3, M2.8)

Checked 2026-10-28 on gdeltproject.org/about.html, "Terms of Use".

| Item | Finding |
|---|---|
| Terms | "available for unlimited and unrestricted use for any academic, commercial, or governmental use of any kind without fee" |
| Redistribution | allowed ("redistribute, rehost, republish, and mirror ... in any form") |
| Citation | any use must "include a citation to the GDELT Project and a link to this website": IKARS shows "Source: The GDELT Project (gdeltproject.org)" wherever GDELT items appear (M2.7) |
| Third-party content | GDELT points to articles owned by their publishers. IKARS keeps only the headline, link, source, times and GDELT's own fields, never the article text (D23). |
