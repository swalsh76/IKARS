"""DPAPI for the current user: a real round trip on Windows, a clear refusal elsewhere."""

import sys

import pytest
from ikars_core import dpapi


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI exists on Windows only")
def test_protect_round_trips_and_hides_the_bytes() -> None:
    secret = b"not a real key" * 4
    sealed = dpapi.protect(secret)
    assert secret not in sealed
    assert dpapi.unprotect(sealed) == secret


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI exists on Windows only")
def test_damaged_data_is_refused() -> None:
    sealed = bytearray(dpapi.protect(b"x" * 32))
    sealed[-1] ^= 0xFF
    with pytest.raises(dpapi.DpapiError):
        dpapi.unprotect(bytes(sealed))


def test_off_windows_it_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(NotImplementedError):
        dpapi.protect(b"x")
