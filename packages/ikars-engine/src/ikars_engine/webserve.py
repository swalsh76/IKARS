"""Serving the web terminal's files (PLAN D3 as amended; M1.8a-web-1a).

The engine serves the built terminal on its own loopback origin, so the page and the API share one
origin and need no cross-origin rules. Every response carries the browser hardening of D3: a strict
Content Security Policy (scripts and styles only from the engine, no inline script, no ``eval``, no
frames), ``nosniff``, no referrer, and no framing. The terminal's files are public (they hold no
secret); the API behind ``/v1/`` needs a session.

A path that names no file is a screen's address (``/market/KXDEMO``): the app's ``index.html``
answers it and the app routes it. A missing file with an extension (``/assets/x.js``) is a 404.
Content types come from a fixed table, never from the Windows registry, which can map ``.js`` to
``text/plain`` (the browser would then refuse the script under ``nosniff``).

Until the terminal is built (``web/dist``, from M1.8a-web-1b), a small placeholder page signs in and
shows that the engine answers.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, Response

# font-src allows data: (M1.8a-web-2a): AG Grid's CSS theme embeds its icon font as a data: URI. A
# font cannot run code; scripts and styles stay 'self' only (its CSS-file theme is used precisely so
# that no inline style is needed).
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP.encode("ascii")),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
)
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}
PLACEHOLDER = Path(__file__).parent / "placeholder"


def terminal_root(repository: Path | None = None) -> Path:
    """The built terminal (``web/dist`` in the repository) when it exists, else the placeholder."""
    if repository is None:
        repository = Path(__file__).parents[4]  # src/ikars_engine -> the repository's root
    built = repository / "web" / "dist"
    return built if (built / "index.html").is_file() else PLACEHOLDER


def safe_parts(relative: str) -> tuple[str, ...] | None:
    """The path's parts when every one is a plain name, else None. Checked on the text alone,
    before any filesystem call: a UNC path (``\\\\host\\share`` or ``//host/share``) handed to
    ``resolve`` or ``is_file`` would make Windows connect to that host and send the owner's NTLM
    credentials (M1.8a-web-1a security review). No backslash, colon (drives, streams), empty part,
    ``.`` or ``..``."""
    if "\\" in relative or ":" in relative:
        return None
    parts = tuple(relative.removesuffix("/").split("/"))  # a screen's address may end in /
    if any(part in ("", ".", "..") for part in parts):
        return None
    return parts


def files_route(root: Path) -> Callable[[Request], Awaitable[Response]]:
    """The handler for every path outside ``/v1/``: a file under ``root``, or its ``index.html``."""

    async def serve(request: Request) -> Response:
        relative = request.path_params.get("path", "")
        if relative == "v1" or relative.startswith("v1/"):  # an unknown API route, not a screen
            return Response(b'{"error":"not_found","detail":"no such route"}', 404,
                            media_type="application/json")  # fmt: skip
        parts = safe_parts(relative) if relative else ()
        if parts is None:
            return Response(b"not found", 404, media_type="text/plain")
        target = root.joinpath(*parts)  # plain names only: it cannot leave root
        if parts and target.suffix in CONTENT_TYPES and target.is_file():
            return FileResponse(target, media_type=CONTENT_TYPES[target.suffix])
        if "." in relative.rsplit("/", 1)[-1]:  # a file that is not there
            return Response(b"not found", 404, media_type="text/plain")
        return FileResponse(root / "index.html", media_type=CONTENT_TYPES[".html"])

    return serve
