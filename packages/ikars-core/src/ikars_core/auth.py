"""Request signing for Kalshi's data key. Ed25519 only: RSA-PSS is deliberately unsupported.

Kalshi signs a request as ``timestamp + METHOD + path`` (the query string is not part of it).
The data key lives on the server host, so what it can sign is limited here and not left to the
caller: a data signer signs only a GET to a read route in the allow-list below, or the WebSocket
handshake. It can never sign a POST, a DELETE, an order route or a portfolio route (balances,
positions and orders), so even a bug elsewhere cannot make the data key place an order or read an
account value. The trading client, when it exists (K7), is a separate class with its own key.

Every key file must be readable by its owner alone, not only the trading key: by file mode on a
POSIX machine, by ACL on Windows (``winacl``).
"""

import base64
import os
import re
import stat
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ikars_core import winacl

HEADER_KEY = "KALSHI-ACCESS-KEY"
HEADER_TIMESTAMP = "KALSHI-ACCESS-TIMESTAMP"
HEADER_SIGNATURE = "KALSHI-ACCESS-SIGNATURE"

API_PREFIX = "/trade-api/v2"
WS_PATH = "/trade-api/ws/v2"

# A path segment may not start with a dot, so ``.`` and ``..`` can never appear in a signed path.
_SEGMENT = r"[A-Za-z0-9_\-][A-Za-z0-9_.\-]*"
_READ_ROUTES = re.compile(
    rf"{re.escape(API_PREFIX)}/(?:(?:markets|events|series|exchange|historical)(?:/{_SEGMENT})*"
    r"|account/limits)"
)


class AuthError(Exception):
    """Credentials are missing or unusable."""


class SigningRefused(AuthError):
    """The data key was asked to sign something it must never sign."""


def read_route(path: str) -> bool:
    """Is ``path`` (query string removed) a route the data key may sign for a GET?"""
    return path == WS_PATH or _READ_ROUTES.fullmatch(path) is not None


def parse_private_key(data: bytes, source: str) -> Ed25519PrivateKey:
    """An unencrypted PEM Ed25519 private key from bytes; ``source`` names it in an error.

    Where the bytes came from decides whether their file mode matters: a key file in a home
    directory must be private to its owner (``load_private_key``); a ``systemd`` credential sits in
    a directory only the service can enter, so the service reads it and calls this directly."""
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except (ValueError, TypeError) as exc:
        raise AuthError(f"{source} is not an unencrypted PEM private key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise AuthError(
            f"unsupported key type {type(key).__name__}: only Ed25519 keys are supported "
            "(create an Ed25519 key; RSA is intentionally not implemented)"
        )
    return key


def _refuse_if_shared(target: Path, shown: Path) -> None:
    """Refuse a key file that anyone but its owner can read: by file mode on POSIX, by ACL on
    Windows (where SYSTEM and Administrators are allowed too, as they can read any file). Checked
    before the key's bytes are read."""
    if sys.platform == "win32":
        try:
            readers = winacl.other_readers(target)
        except OSError as exc:
            raise AuthError(f"cannot check who can read private key file {shown}: {exc}") from exc
        if readers:
            raise AuthError(
                f"{shown} is readable by other users ({', '.join(readers)}); run: "
                f'icacls "{shown}" /inheritance:r /grant:r "%USERNAME%:R"'
            )
        return
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except OSError as exc:
        raise AuthError(f"cannot read private key file {shown}: {exc.strerror}") from exc
    if mode & 0o077:
        raise AuthError(
            f"{shown} is readable by other users (mode {mode:03o}); run: chmod 600 {shown}"
        )


def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Load an unencrypted PEM Ed25519 private key from a file only its owner can read."""
    target = path.expanduser()
    if not target.is_file():
        raise AuthError(f"cannot read private key file {path}: no such file")
    _refuse_if_shared(target, path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise AuthError(f"cannot read private key file {path}: {exc.strerror}") from exc
    return parse_private_key(data, str(path))


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


class DataSigner:
    """Builds the signed headers for read-only requests and the WebSocket handshake.

    ``offset_ms`` is how far the local clock is ahead of Kalshi's (negative: behind), from
    :mod:`ikars_core.clock`; it is subtracted so that the signed timestamp is Kalshi's time.
    """

    def __init__(
        self,
        key_id: str,
        private_key: Ed25519PrivateKey,
        *,
        now_ms: Callable[[], int] = _now_ms,
        offset_ms: int = 0,
    ) -> None:
        if not key_id.strip():
            raise AuthError("the key id is empty")
        self._key_id = key_id.strip()
        self._key = private_key
        self._now_ms = now_ms
        self.offset_ms = offset_ms

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "DataSigner":
        """From ``IKARS_KALSHI_KEY_ID`` and ``IKARS_KALSHI_PRIVATE_KEY_PATH``."""
        env = os.environ if environ is None else environ
        key_id, path = (
            env.get("IKARS_KALSHI_KEY_ID", ""),
            env.get("IKARS_KALSHI_PRIVATE_KEY_PATH", ""),
        )
        if not key_id or not path:
            raise AuthError(
                "IKARS_KALSHI_KEY_ID and IKARS_KALSHI_PRIVATE_KEY_PATH must both be set"
            )
        return cls(key_id, load_private_key(Path(path)))

    def headers(self, method: str, path: str, *, timestamp_ms: int | None = None) -> dict[str, str]:
        """Signed headers for a GET of ``path`` (a URL path with or without a query string)."""
        bare = path.split("?", 1)[0]
        if method.upper() != "GET":
            raise SigningRefused(f"the data key signs GET requests only, not {method.upper()}")
        if not read_route(bare):
            raise SigningRefused(f"the data key does not sign requests to {bare}")
        stamp = self._now_ms() - self.offset_ms if timestamp_ms is None else timestamp_ms
        signature = self._key.sign(f"{stamp}GET{bare}".encode())
        return {
            HEADER_KEY: self._key_id,
            HEADER_TIMESTAMP: str(stamp),
            HEADER_SIGNATURE: base64.b64encode(signature).decode(),
        }
