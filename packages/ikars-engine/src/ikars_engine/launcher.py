"""``IKARS.exe``'s Python side (M1.9a, PLAN D12): what the installed program does when started.

``IKARS.exe`` (a few lines of C#, ``tools/launcher``) starts the bundled ``pythonw.exe`` with this
module and passes its arguments through. First, Velopack's install, update and uninstall steps
are answered (it runs the program with its own arguments and waits at most 30 seconds); a plain
start then opens the terminal, as ``ikars open`` does, starting the engine when it is not running.

Uninstalling removes what IKARS put in Windows (start at sign-in, the watchdog's task, the
notification name) and stops the engine. It never touches the data folder: the ledger, settings
and logs outlive the program (the program has its own folder; BUILD-LOG 1.9).
"""

import sys
import time
from collections.abc import Callable, Sequence

from ikars_engine import autostart, runmode
from ikars_engine.paths import Paths, resolve

STOP_WAIT_S = 20.0  # within Velopack's 30 seconds for a step
MB_ICONERROR = 0x10


def stop_engine(
    paths: Paths, wait_s: float = STOP_WAIT_S, sleep: Callable[[float], None] = time.sleep
) -> None:
    """Stop the engine and wait for it: its Python runs from the program folder Velopack is about to
    replace or remove (merge review 8). Marked as the owner's stop, so the watchdog leaves it; the
    next start (the program, after an update) clears the mark."""
    if runmode.engine_running(paths.data):
        runmode.request_stop(paths.data)
        deadline = time.monotonic() + wait_s
        while runmode.engine_running(paths.data) and time.monotonic() < deadline:
            sleep(0.5)


def before_uninstall(
    paths: Paths,
    remove: Callable[[], object] | None = None,
    wait_s: float = STOP_WAIT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Stop the engine and remove IKARS's Windows entries. The data folder is left as it is."""
    stop_engine(paths, wait_s, sleep)
    if remove is None:
        autostart.remove(autostart.UserRegistry(), autostart.run_quietly)
    else:
        remove()


def say(text: str) -> None:
    """A message box: the launcher has no console to print to."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "IKARS", MB_ICONERROR)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    paths = resolve()
    from velopack import App

    # a Velopack step runs its hook here and ends the process; updates wait for the owner's click
    (
        App()
        .set_args(args)
        .set_auto_apply_on_startup(False)
        .on_before_update_fast_callback(lambda version: stop_engine(paths))
        .on_before_uninstall_fast_callback(lambda version: before_uninstall(paths))
        .run()
    )
    from ikars_engine.open_terminal import OpenError, open_terminal
    from ikars_engine.settings import SettingsError, load

    try:
        settings = load(paths.create().settings)
        open_terminal(paths, settings=settings)
    except (OpenError, SettingsError, OSError) as exc:
        say(f"IKARS could not open: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
