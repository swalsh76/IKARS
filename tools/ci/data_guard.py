"""Fails if the repository tracks data it must never hold (docs/TESTING.md, PLAN D10 and D21-4).

Checks every file git tracks (the index, so it also guards a commit before it is made): no market
data, databases, ledger exports or keys by extension, and nothing over the size limit under a
``tests`` folder, where only small synthetic or hand-written fixtures belong.

    python tools/ci/data_guard.py
"""

import re
import subprocess
import sys
from pathlib import PurePosixPath

# Matched against every suffix of the name, so "x.duckdb.wal" and "x.db-journal" are caught too.
FORBIDDEN_SUFFIXES = frozenset(
    {
        # market data and recordings
        ".parquet",
        ".arrow",
        ".feather",
        ".jsonl",
        # databases and their side files
        ".duckdb",
        ".sqlite",
        ".sqlite3",
        ".db",
        ".wal",
        ".db-wal",
        ".db-shm",
        ".db-journal",
        ".sqlite-wal",
        ".sqlite-shm",
        ".sqlite-journal",
        ".sqlite3-wal",
        ".sqlite3-shm",
        ".sqlite3-journal",
        # keys and certificates
        ".pem",
        ".key",
        ".pfx",
        ".p12",
        # browser network recordings (they can hold live replies)
        ".har",
    }
)
# Output folders that can hold live data or secrets (Playwright's reports, traces and screenshots;
# coverage; builds; installed packages): nothing under them is committed (PLAN D11, 2026-10-27).
FORBIDDEN_FOLDERS = frozenset(
    {"playwright-report", "test-results", "blob-report", "node_modules", "coverage", "dist"}
)
ENV_FILE = re.compile(r"\.env(\..+)?")  # .env, .env.local, ...: settings files that hold secrets
# The ledger's tax-year package (PLAN D18): IKARS-<year>-summary.pdf, -form8949.csv, -6781.csv,
# -manifest.txt, -ledger.jsonl. Only that exact shape, so ordinary files named ikars-... still pass.
LEDGER_EXPORT = re.compile(r"ikars-\d{4}-[a-z0-9]+\.(pdf|csv|txt|jsonl)")
TEST_FILE_LIMIT_BYTES = 1_000_000


def is_forbidden(name: str) -> bool:
    name = name.lower()
    suffixes = {s.lower() for s in PurePosixPath(name).suffixes}
    return bool(suffixes & FORBIDDEN_SUFFIXES) or LEDGER_EXPORT.fullmatch(name) is not None


def problems(paths: list[str], size_of: dict[str, int]) -> list[str]:
    found: list[str] = []
    for path in paths:
        parts = PurePosixPath(path).parts
        if is_forbidden(parts[-1]) or ENV_FILE.fullmatch(parts[-1].lower()):
            found.append(f"{path}: this kind of file is never committed")
        elif FORBIDDEN_FOLDERS & set(parts[:-1]):
            found.append(f"{path}: output folders are never committed")
        elif "tests" in PurePosixPath(path).parts and size_of.get(path, 0) > TEST_FILE_LIMIT_BYTES:
            found.append(f"{path}: over {TEST_FILE_LIMIT_BYTES:,} bytes in a tests folder")
    return found


def git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed arguments, no shell
        ["git", *args],  # noqa: S607 - git is resolved from PATH on purpose
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")


def indexed_sizes() -> dict[str, int]:
    """Every tracked path with its size **as staged in the index**, not as it is on disk, so a
    staged file cannot pass by being changed in the working tree afterwards. Paths are relative to
    the repository's top level, wherever the script is run from."""
    top = git("rev-parse", "--show-toplevel").strip()
    listing = git("-C", top, "ls-files", "-z", "--format=%(objectsize) %(path)")
    sizes = {}
    for entry in filter(None, listing.split("\0")):
        size, _, path = entry.partition(" ")
        sizes[path] = int(size) if size.isdigit() else 0  # a submodule has no object size
    return sizes


def main() -> int:
    size_of = indexed_sizes()
    paths = sorted(size_of)
    found = problems(paths, size_of)
    for line in found:
        print(f"data guard: {line}", file=sys.stderr)
    print(f"data guard: {len(paths)} tracked files checked, {len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
