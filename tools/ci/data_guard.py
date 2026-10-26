"""Fails if the repository tracks data it must never hold (docs/TESTING.md, PLAN D10 and D21-4).

Checks every file git tracks (the index, so it also guards a commit before it is made): no market
data, databases, ledger exports or keys by extension, and nothing over the size limit under a
``tests`` folder, where only small synthetic or hand-written fixtures belong.

    python tools/ci/data_guard.py
"""

import subprocess
import sys
from pathlib import Path, PurePosixPath

FORBIDDEN_SUFFIXES = frozenset(
    {".parquet", ".duckdb", ".sqlite", ".db", ".arrow", ".jsonl", ".pem", ".key", ".pfx"}
)
FORBIDDEN_INFIXES = (".sqlite-",)  # SQLite's -wal and -shm side files
TEST_FILE_LIMIT_BYTES = 1_000_000


def problems(paths: list[str], size_of: dict[str, int]) -> list[str]:
    found = []
    for path in paths:
        name = PurePosixPath(path).name.lower()
        if PurePosixPath(name).suffix in FORBIDDEN_SUFFIXES or any(
            s in name for s in FORBIDDEN_INFIXES
        ):
            found.append(f"{path}: this kind of file is never committed")
        elif "tests" in PurePosixPath(path).parts and size_of.get(path, 0) > TEST_FILE_LIMIT_BYTES:
            found.append(f"{path}: over {TEST_FILE_LIMIT_BYTES:,} bytes in a tests folder")
    return found


def main() -> int:
    listing = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        ["git", "ls-files", "-z"],  # noqa: S607 - git is resolved from PATH on purpose
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    paths = [p for p in listing.split("\0") if p]
    size_of = {p: Path(p).stat().st_size for p in paths if Path(p).is_file()}
    found = problems(paths, size_of)
    for line in found:
        print(f"data guard: {line}", file=sys.stderr)
    print(f"data guard: {len(paths)} tracked files checked, {len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
