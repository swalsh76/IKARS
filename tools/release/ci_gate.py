"""Whether a tagged commit may be released (M1.R3 E3): the checks ``main`` requires passed on it.

    python tools/release/ci_gate.py --repo owner/name --sha <commit> [--wait-s 2400]

The release workflow runs this before it builds. It reads the commit's check runs through ``gh api``
(the job's read-only token) and waits while any required check is still running, up to
``--wait-s``: a tag pushed with its commit lands before CI has finished. A test release from a pull
request's branch works too: CI runs on every pushed commit of an open pull request.
"""

import argparse
import json
import subprocess
import sys
import time
from typing import Any

REQUIRED = ("python", "hygiene", "web", "CodeQL")  # the main protection ruleset's checks
POLL_S = 30


def verdict(runs: list[dict[str, Any]]) -> tuple[str, str]:
    """("pass" | "wait" | "fail", why) for a commit's check runs; a check run again counts by its
    newest run."""
    newest: dict[str, dict[str, Any]] = {}
    for run in runs:
        name = run["name"]
        if name not in newest or run["id"] > newest[name]["id"]:
            newest[name] = run
    # only the required checks: the release workflow's own jobs are check runs on the commit too,
    # and a failed earlier attempt must not block a new tag (per-commit review); CodeQL is the
    # aggregate of every language's analysis
    failed = [
        name
        for name in REQUIRED
        if name in newest
        and newest[name]["status"] == "completed"
        and newest[name]["conclusion"] not in ("success", "skipped", "neutral")
    ]
    if failed:
        return "fail", f"failed: {', '.join(failed)}"
    missing = [name for name in REQUIRED if name not in newest]
    running = [
        name for name in REQUIRED if name in newest and newest[name]["status"] != "completed"
    ]
    if missing or running:
        return "wait", f"not finished: {', '.join(missing + running)}"
    return "pass", f"passed: {', '.join(REQUIRED)}"


def check_runs(repo: str, sha: str) -> list[dict[str, Any]]:
    route = f"repos/{repo}/commits/{sha}/check-runs"
    out = subprocess.run(["gh", "api", "--paginate", route, "--jq", ".check_runs[]"],  # noqa: S603, S607
                         check=True, capture_output=True, text=True).stdout  # fmt: skip
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--wait-s", type=float, default=2400.0)
    args = parser.parse_args(argv)
    deadline = time.monotonic() + args.wait_s
    while True:
        state, why = verdict(check_runs(args.repo, args.sha))
        print(f"{state}: {why}", flush=True)
        if state != "wait":
            return 0 if state == "pass" else 1
        if time.monotonic() >= deadline:
            print("CI did not finish in time: nothing is released")
            return 1
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
