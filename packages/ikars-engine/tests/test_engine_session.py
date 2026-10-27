"""Browser sessions: one-time codes, signed sliding cookies, and a key that outlives a restart."""

import base64
from pathlib import Path

import pytest
from ikars_engine.session import CODE_TTL_S, KEY_FILE, MAX_CODES, RENEW_AFTER_S, Sessions

T0 = 1_767_225_600.0


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


def sessions(tmp_path: Path, clock: Clock | None = None, days: int = 30) -> Sessions:
    return Sessions(tmp_path, days, clock or Clock())


def test_a_code_works_once(tmp_path: Path) -> None:
    s = sessions(tmp_path)
    code = s.issue_code()
    cookie = s.redeem(code)
    assert cookie is not None and s.check(cookie) == (True, None)
    assert s.redeem(code) is None


def test_a_code_expires(tmp_path: Path) -> None:
    clock = Clock()
    s = sessions(tmp_path, clock)
    code = s.issue_code()
    clock.now += CODE_TTL_S
    assert s.redeem(code) is None


def test_an_unknown_code_is_refused(tmp_path: Path) -> None:
    assert sessions(tmp_path).redeem("made-up") is None


def test_unredeemed_codes_are_capped(tmp_path: Path) -> None:
    s = sessions(tmp_path)
    first = s.issue_code()
    for _ in range(MAX_CODES):
        s.issue_code()
    assert s.redeem(first) is None  # the oldest made room


def test_a_cookie_is_renewed_once_a_day_old_and_ends_after_its_days(tmp_path: Path) -> None:
    clock = Clock()
    s = sessions(tmp_path, clock, days=2)
    cookie = s.redeem(s.issue_code())
    assert cookie is not None
    clock.now += RENEW_AFTER_S
    valid, renewed = s.check(cookie)
    assert valid and renewed is not None and renewed != cookie
    clock.now += RENEW_AFTER_S  # two days after the first: it has ended, the renewed one lives
    assert s.check(cookie) == (False, None)
    assert s.check(renewed)[0]


@pytest.mark.parametrize("change", ["signature", "times", "shape"])
def test_a_forged_cookie_is_refused(tmp_path: Path, change: str) -> None:
    s = sessions(tmp_path)
    cookie = s.redeem(s.issue_code())
    assert cookie is not None
    issued, expires, signature = cookie.split(".")
    forged = {
        "signature": f"{issued}.{expires}.{signature[:-2]}AA",
        "times": f"{issued}.{int(expires) + 10**9}.{signature}",
        "shape": "x",
    }[change]
    assert s.check(forged) == (False, None)


def test_a_cookie_from_the_future_is_refused(tmp_path: Path) -> None:
    clock = Clock()
    s = sessions(tmp_path, clock)
    clock.now += 3_600
    cookie = s.redeem(s.issue_code())
    clock.now -= 7_200
    assert cookie is not None and s.check(cookie) == (False, None)


def test_a_restart_keeps_the_terminal_signed_in(tmp_path: Path) -> None:
    first = sessions(tmp_path)
    if not first.persistent:
        pytest.skip("no DPAPI here")
    cookie = first.redeem(first.issue_code())
    assert cookie is not None
    assert sessions(tmp_path).check(cookie)[0]  # a new engine, the same key file


def test_the_key_on_disk_is_not_the_key(tmp_path: Path) -> None:
    s = sessions(tmp_path)
    if not s.persistent:
        pytest.skip("no DPAPI here")
    stored = base64.b64decode((tmp_path / KEY_FILE).read_text(encoding="ascii"))
    assert s._key not in stored


def test_an_unreadable_key_is_set_aside_and_replaced(tmp_path: Path) -> None:
    (tmp_path / KEY_FILE).write_text("not base64 of anything DPAPI made", encoding="ascii")
    s = sessions(tmp_path)
    assert (tmp_path / (KEY_FILE + ".unreadable")).exists()
    cookie = s.redeem(s.issue_code())
    assert cookie is not None and s.check(cookie)[0]


def test_without_dpapi_sessions_last_only_while_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(data: bytes) -> bytes:
        raise NotImplementedError

    monkeypatch.setattr("ikars_core.dpapi.protect", missing)
    s = sessions(tmp_path)
    assert not s.persistent and not (tmp_path / KEY_FILE).exists()
    cookie = s.redeem(s.issue_code())
    assert cookie is not None and s.check(cookie)[0]


def test_the_cookie_header_is_http_only_and_strict(tmp_path: Path) -> None:
    header = sessions(tmp_path, days=30).set_cookie("v").decode()
    assert header == "ikars_session=v; Max-Age=2592000; Path=/; HttpOnly; SameSite=Strict"
