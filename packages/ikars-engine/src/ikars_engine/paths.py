"""Where IKARS keeps its files (PLAN D12).

Installed, everything lives under ``%LOCALAPPDATA%\\IKARS`` (the user's own profile, no admin
rights). The portable build keeps everything beside the program instead: a file named
``portable.marker`` next to it switches that on. The program folder itself is never written to
otherwise.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

PORTABLE_MARKER = "portable.marker"
APP_NAME = "IKARS"


@dataclass(frozen=True, slots=True)
class Paths:
    root: Path

    @property
    def settings(self) -> Path:
        return self.root / "settings.toml"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def recordings(self) -> Path:
        return self.root / "data" / "recordings"

    @property
    def ledger(self) -> Path:
        return self.root / "ledger"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    def create(self) -> "Paths":
        for folder in (self.logs, self.data, self.recordings, self.ledger, self.backups):
            folder.mkdir(parents=True, exist_ok=True)
        return self


def program_folder() -> Path:
    """The folder of the running program: the frozen executable's, else the interpreter's."""
    return Path(sys.executable).resolve().parent


def resolve(program: Path | None = None, environ: dict[str, str] | None = None) -> Paths:
    """The data root: beside the program when it is portable, else under the user's local app data
    (``LOCALAPPDATA`` on Windows; ``~/.local/share`` elsewhere, for tests on other systems)."""
    env = dict(os.environ) if environ is None else environ
    folder = program or program_folder()
    if (folder / PORTABLE_MARKER).is_file():
        return Paths(folder / "data-portable")
    base = env.get("LOCALAPPDATA")
    if base:
        return Paths(Path(base) / APP_NAME)
    return Paths(Path.home() / ".local" / "share" / APP_NAME)
