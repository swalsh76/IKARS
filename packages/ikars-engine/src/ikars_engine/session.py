"""Browser sessions for the web terminal (PLAN D3 as amended; M1.8a-web-1a).

``ikars open`` asks the running engine for a **one-time code** (with the bearer token from
``api.json``) and opens the terminal with the code in the address's ``#`` fragment, which the
browser never sends to a server. The page posts the code back once; the engine answers with a
**session cookie** (``HttpOnly``, ``SameSite=Strict``), so page scripts never see a credential.

A code works once and only for ``CODE_TTL_S``. A cookie is valid for ``days`` from when it was
issued and is renewed once it is a day old, so a terminal in daily use never has to sign in again.
Cookies are signed (HMAC-SHA256) with a key kept in the data folder, encrypted with DPAPI for this
Windows account (D8), so **an engine restart keeps the terminal signed in** (review 1.R2 item 1).
Where DPAPI does not exist (off Windows) the key lives in memory only, and a restart signs out.
Deleting the key file signs every terminal out.

Browsers scope cookies by host, not port: another server on 127.0.0.1 visited in the same browser
profile would receive the cookie. The terminal's own Edge profile, which visits nothing else, is
what confines it. A cookie also names the port it was issued on and is good on that port only, and
when the engine has to take another port (a program held its own during a restart, and open windows
may have sent it their cookies) the key is replaced, so every cookie issued before is worthless
(M1.R3 D1).
"""

import base64
import hashlib
import hmac
import logging
import secrets
import time
from collections.abc import Callable
from itertools import count
from pathlib import Path

from ikars_core import dpapi

from ikars_engine.files import first_free, write_text_atomic

COOKIE = "ikars_session"
KEY_FILE = "session.key"
CODE_TTL_S = 30
RENEW_AFTER_S = 86_400
MAX_CODES = 16  # unredeemed codes kept at once (each ``ikars open`` makes one)
log = logging.getLogger("ikars_engine.session")


def _save_key(path: Path, key: bytes) -> None:
    """``key``, DPAPI-protected for this account, in ``path`` (raises when it cannot be)."""
    write_text_atomic(path, base64.b64encode(dpapi.protect(key)).decode("ascii"))


def _load_or_create_key(path: Path) -> tuple[bytes, bool]:
    """The signing key, and whether it outlives this process (DPAPI-protected on disk)."""
    if path.exists():
        # a read that fails (a lock, a disk error) is raised: the start fails and the watchdog
        # retries, rather than a new key signing every terminal out (M1.8a-web-1a merge review)
        stored = path.read_text(encoding="ascii")
        try:
            return dpapi.unprotect(base64.b64decode(stored)), True
        except NotImplementedError:
            log.warning("no DPAPI here: terminal sessions end when the engine stops")
            return secrets.token_bytes(32), False
        except (dpapi.DpapiError, ValueError) as exc:  # damaged, or not this account's
            log.warning("session key unreadable: a new one signs every terminal out",
                        extra={"error": type(exc).__name__})  # fmt: skip
            path.replace(first_free(  # kept beside it, never overwriting an earlier copy
                path.with_name(f"{path.name}.unreadable" + ("" if n == 0 else f".{n}"))
                for n in count()
            ))  # fmt: skip
    key = secrets.token_bytes(32)
    try:
        _save_key(path, key)
        return key, True
    except (OSError, NotImplementedError) as exc:
        log.warning("session key not saved: terminal sessions end when the engine stops",
                    extra={"error": type(exc).__name__})  # fmt: skip
        return key, False


class Sessions:
    def __init__(self, folder: Path, days: int, now_s: Callable[[], float] = time.time) -> None:
        self._path = folder / KEY_FILE
        self._key, self.persistent = _load_or_create_key(self._path)
        self._max_age = days * 86_400
        self._now = now_s
        self._codes: dict[bytes, float] = {}  # a code's hash -> when it expires

    # --- one-time codes ------------------------------------------------------------------------

    def issue_code(self) -> str:
        now = self._now()
        self._codes = {h: t for h, t in self._codes.items() if t > now}
        if len(self._codes) >= MAX_CODES:
            del self._codes[min(self._codes, key=self._codes.__getitem__)]
        code = secrets.token_urlsafe(32)
        self._codes[hashlib.sha256(code.encode()).digest()] = now + CODE_TTL_S
        return code

    def redeem(self, code: str, port: int) -> str | None:
        """A new cookie value for ``port`` for a live, unused code, else None; the code is spent
        either way."""
        expires = self._codes.pop(hashlib.sha256(code.encode()).digest(), None)
        if expires is None or expires <= self._now():
            return None
        return self._cookie(int(self._now()), port)

    def rotate(self) -> None:
        """A new key: every cookie issued before is worthless (the terminal's port was taken)."""
        self._key = secrets.token_bytes(32)
        if not self.persistent:
            return
        try:
            _save_key(self._path, self._key)
        except (OSError, NotImplementedError) as exc:  # this run's key still replaces the old one
            self.persistent = False
            log.warning("new session key not saved", extra={"error": type(exc).__name__})

    # --- cookies -------------------------------------------------------------------------------

    def _sign(self, body: str) -> str:
        digest = hmac.new(self._key, body.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _cookie(self, issued: int, port: int) -> str:
        body = f"{issued}.{issued + self._max_age}.{port}"
        return f"{body}.{self._sign(body)}"

    def check(self, value: str, port: int) -> tuple[bool, str | None]:
        """Whether ``value`` is a valid session cookie for ``port``, and a renewed value when it
        is due."""
        body, _, signature = value.rpartition(".")
        # compare_digest raises on non-ASCII text: such a value is no cookie of ours
        if not value.isascii() or not hmac.compare_digest(signature, self._sign(body)):
            return False, None
        try:
            issued, expires, issued_port = (int(part) for part in body.split("."))
        except ValueError:  # not three numbers (a cookie from before M1.R3 has two)
            return False, None
        now = self._now()
        if issued_port != port or not issued <= now < expires:
            return False, None
        return True, self._cookie(int(now), port) if now - issued >= RENEW_AFTER_S else None

    def set_cookie(self, value: str) -> bytes:
        """The ``Set-Cookie`` header value for ``value``."""
        return (
            f"{COOKIE}={value}; Max-Age={self._max_age}; Path=/; HttpOnly; SameSite=Strict"
        ).encode("ascii")
