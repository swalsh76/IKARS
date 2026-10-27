"""``python -m ikars_engine open``: the web terminal in its own Edge app window (PLAN D3, amended).

1. If the engine is not running, start it in the background (as the watchdog does) and wait for its
   API to answer.
2. Ask the engine for a one-time sign-in code, with the per-start token from ``api.json``.
3. Open ``http://127.0.0.1:<port>/#code=<code>`` as an Edge app window with IKARS's own browser
   profile (``<data root>\\browser``: none of the owner's cookies, history or extensions) and
   extensions off.

With a saved layout (``--layout NAME``, or else the one chosen to open at start), each of its
windows opens on its own screen, at its place and size, with a code of its own (M1.8b-2).

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
from ikars_engine.settings import LayoutWindow, Settings
from ikars_engine.settings_api import SettingsRefused, checked_layout
from ikars_engine.watchdog import engine_command, start_engine

START_WAIT_S = 180.0  # a cold start reads the account's limits and runs discovery first
POLL_S = 1.0
LAUNCH_GAP_S = 1.0  # between windows: the first starts Edge, the rest join it


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


def edge_command(
    edge: Path, url: str, profile: Path, window: LayoutWindow | None = None
) -> list[str]:
    command = [
        str(edge),
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if window is not None:
        command += [
            f"--window-position={window.x},{window.y}",
            f"--window-size={window.width},{window.height}",
        ]
    return command


def windows_for(settings: Settings, name: str | None) -> list[LayoutWindow]:
    """The windows to open: the layout named, else the one chosen for the start; none (one window
    where Edge puts it) when there is no such choice or the name is empty."""
    wanted = settings.terminal.start_layout if name is None else name.strip()
    if not wanted:
        return []
    for layout in settings.terminal.layouts:
        if layout.name.casefold() == wanted.casefold():
            try:  # the file may be edited by hand: its screens go on Edge's command line
                return checked_layout(layout).windows
            except SettingsRefused as exc:
                raise OpenError(f"the layout {layout.name!r} cannot be used: {exc}") from exc
    if name is None:
        return []  # the start layout was deleted by hand: one window
    raise OpenError(f"no layout is named {wanted!r} (Settings, Terminal lists them)")


def launch(command: list[str]) -> None:
    flags = subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
    subprocess.Popen(  # noqa: S603 (Edge, with our own arguments)
        command, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip


def request_code(port: int, token: str) -> str:
    """A one-time code from the engine on ``port``."""
    request = urllib.request.Request(  # noqa: S310 (a fixed loopback address)
        f"http://127.0.0.1:{port}/v1/session/code",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=10) as answer:  # noqa: S310
        return str(json.loads(answer.read())["code"])


def open_terminal(
    paths: Paths,
    root: Path | None = None,
    *,
    settings: Settings | None = None,
    layout: str | None = None,
    start: Callable[[list[str]], None] = start_engine,
    run: Callable[[list[str]], None] = launch,
    edge: Callable[[], Path] = edge_path,
    code: Callable[[int, str], str] = request_code,
    wait_s: float = START_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    windows = windows_for(settings or Settings(), layout)  # before anything starts
    started = False
    if not runmode.engine_running(paths.data):
        start(engine_command(root))
        started = True
    deadline = time.monotonic() + wait_s
    while True:
        record = read_api_file(paths.data)
        if record is not None:
            try:
                port, token = int(record["port"]), str(record["token"])
                first = code(port, token)
                break
            except (OSError, KeyError, ValueError, TypeError):
                pass  # api.json from a stopped engine or malformed, or the API not answering yet
        if time.monotonic() >= deadline:
            raise OpenError("the engine's API did not answer: see ikars status")
        sleep(POLL_S)
    browser, profile = edge(), paths.root / "browser"
    if not windows:
        run(edge_command(browser, f"http://127.0.0.1:{port}/#code={first}", profile))
    for n, window in enumerate(windows):
        if n:
            sleep(LAUNCH_GAP_S)
        spent = first if n == 0 else code(port, token)  # each window spends a code of its own
        url = f"http://127.0.0.1:{port}{window.screen}#code={spent}"
        run(edge_command(browser, url, profile, window))
    return {"opened": True, "started_engine": started, "port": port, "windows": len(windows) or 1}
