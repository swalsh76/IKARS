"""``python -m ikars_engine open``: the web terminal in its own Edge app window (PLAN D3, amended).

1. If the engine is not running, start it in the background (as the watchdog does) and wait for its
   API to answer.
2. Ask the engine for a one-time sign-in code, with the per-start token from ``api.json``.
3. Open ``http://127.0.0.1:<port>/#code=<code>`` as an Edge app window with IKARS's own browser
   profile (``<data root>\\browser``: none of the owner's cookies, history or extensions) and
   extensions off.

The code reaches the page in the address's ``#`` fragment, which the browser never sends to a
server. It is also on Edge's command line, visible to this Windows account's own processes for the
instant before the page spends it: a code works once, for 30 seconds.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ikars_engine import runmode
from ikars_engine.api import read_api_file
from ikars_engine.paths import Paths
from ikars_engine.watchdog import engine_command, start_engine

START_WAIT_S = 180.0  # a cold start reads the account's limits and runs discovery first
POLL_S = 1.0


class OpenError(RuntimeError):
    """The terminal could not be opened (said in plain words)."""


def edge_path(environ: dict[str, str] | None = None) -> Path:
    """Microsoft Edge (part of Windows 11) in its usual places."""
    env = os.environ if environ is None else environ
    for base in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        if folder := env.get(base):
            candidate = Path(folder) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.is_file():
                return candidate
    raise OpenError("Microsoft Edge was not found")


def edge_command(edge: Path, url: str, profile: Path) -> list[str]:
    return [
        str(edge),
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
    ]


def launch(command: list[str]) -> None:
    flags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
    subprocess.Popen(  # noqa: S603 (Edge, with our own arguments)
        command, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip


def request_code(port: int, token: str) -> str:
    """A one-time code from the engine on ``port`` (its URL is built by the engine)."""
    request = urllib.request.Request(  # noqa: S310 (a fixed loopback address)
        f"http://127.0.0.1:{port}/v1/session/code",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=10) as answer:  # noqa: S310
        return str(json.loads(answer.read())["url"])


def open_terminal(
    paths: Paths,
    root: Path | None = None,
    *,
    start: Callable[[list[str]], None] = start_engine,
    run: Callable[[list[str]], None] = launch,
    edge: Callable[[], Path] = edge_path,
    code: Callable[[int, str], str] = request_code,
    wait_s: float = START_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    started = False
    if not runmode.engine_running(paths.data):
        start(engine_command(root))
        started = True
    deadline = time.monotonic() + wait_s
    while True:
        record = read_api_file(paths.data)
        if record is not None:
            try:
                url = code(int(record["port"]), str(record["token"]))
                break
            except (OSError, KeyError, ValueError, TypeError):
                pass  # api.json from a stopped engine or malformed, or the API not answering yet
        if time.monotonic() >= deadline:
            raise OpenError("the engine's API did not answer: see ikars status")
        sleep(POLL_S)
    run(edge_command(edge(), url, paths.root / "browser"))
    return {"opened": True, "started_engine": started, "port": int(record["port"])}
