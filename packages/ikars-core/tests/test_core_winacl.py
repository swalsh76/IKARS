"""The Windows ACL check on key files, against real files whose ACLs the tests set with icacls."""

import getpass
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ikars_core import winacl
from ikars_core.auth import AuthError, load_private_key

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs")

USERS = "S-1-5-32-545"
EVERYONE = "S-1-1-0"


def icacls(path: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed program, test-controlled arguments
        ["icacls", str(path), *args],  # noqa: S607 - icacls is a Windows system tool
        check=True,
        capture_output=True,
    )


def owner_only(path: Path) -> Path:
    icacls(path, "/inheritance:r", "/grant:r", f"{getpass.getuser()}:(F)")
    return path


@pytest.fixture
def private_file(tmp_path: Path) -> Path:
    path = tmp_path / "key.pem"
    path.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return owner_only(path)


def readers(path: Path) -> list[str]:
    return winacl.other_readers(path)


def test_an_owner_only_file_has_no_other_readers(private_file: Path) -> None:
    assert readers(private_file) == []


def test_a_group_with_read_access_is_reported(private_file: Path) -> None:
    icacls(private_file, "/grant", f"*{USERS}:(R)")
    assert readers(private_file) == [USERS]


def test_system_and_administrators_are_expected(private_file: Path) -> None:
    icacls(private_file, "/grant", "*S-1-5-18:(R)", "/grant", "*S-1-5-32-544:(R)")
    assert readers(private_file) == []


def test_owner_rights_is_the_owner(private_file: Path) -> None:
    icacls(private_file, "/grant", "*S-1-3-4:(R)")
    assert readers(private_file) == []


def test_write_without_read_is_not_reading(private_file: Path) -> None:
    icacls(private_file, "/grant", f"*{EVERYONE}:(WD)")
    assert readers(private_file) == []


def test_a_deny_entry_grants_nothing(private_file: Path) -> None:
    icacls(private_file, "/deny", f"*{USERS}:(R)")
    assert readers(private_file) == []


def test_a_shared_key_is_refused_with_the_fix(private_file: Path) -> None:
    icacls(private_file, "/grant", f"*{EVERYONE}:(R)")
    with pytest.raises(AuthError, match=r"readable by other users \(S-1-1-0\).*icacls"):
        load_private_key(private_file)


def test_a_private_key_loads(private_file: Path) -> None:
    assert isinstance(load_private_key(private_file), Ed25519PrivateKey)


def test_a_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(AuthError, match="no such file"):
        load_private_key(tmp_path / "absent.pem")


def test_an_unreadable_acl_is_reported(tmp_path: Path) -> None:
    with pytest.raises(winacl.AclError):
        winacl.other_readers(tmp_path / "absent.pem")


def test_the_right_to_change_the_acl_counts_as_reading(private_file: Path) -> None:
    icacls(private_file, "/grant", f"*{USERS}:(WDAC)")
    assert readers(private_file) == [USERS]


def test_the_right_to_take_ownership_counts_as_reading(private_file: Path) -> None:
    icacls(private_file, "/grant", f"*{USERS}:(WO)")
    assert readers(private_file) == [USERS]
