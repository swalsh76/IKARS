"""The release gate (M1.R3 E3): a tag publishes only what CI passed."""

from typing import Any

from ci_gate import REQUIRED, verdict


def run(
    name: str, status: str = "completed", conclusion: str | None = "success", id_: int = 1
) -> dict[str, Any]:
    return {"name": name, "status": status, "conclusion": conclusion, "id": id_}


def test_every_required_check_passed() -> None:
    runs = [run(name) for name in REQUIRED] + [run("analyze (python)")]
    assert verdict(runs)[0] == "pass"


def test_a_failed_required_check_stops_the_release() -> None:
    runs = [run(name) for name in REQUIRED[1:]] + [run("python", conclusion="failure")]
    assert verdict(runs) == ("fail", "failed: python")


def test_a_failed_earlier_release_on_the_commit_does_not_block_a_new_tag() -> None:
    runs = [run(name) for name in REQUIRED] + [run("build", conclusion="failure")]
    assert verdict(runs)[0] == "pass"


def test_a_check_still_running_or_not_started_waits() -> None:
    running = [run(name) for name in REQUIRED[:-1]] + [run(REQUIRED[-1], "in_progress", None)]
    assert verdict(running)[0] == "wait"
    assert verdict([run("python")]) == ("wait", "not finished: hygiene, web, CodeQL")


def test_a_check_run_again_counts_by_its_newest_run() -> None:
    runs = [run(name, id_=5) for name in REQUIRED] + [run("web", conclusion="failure", id_=2)]
    assert verdict(runs)[0] == "pass"
    runs.append(run("web", conclusion="failure", id_=9))
    assert verdict(runs)[0] == "fail"
