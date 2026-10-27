"""Who besides its owner can read a file, from its Windows ACL (no extra package: ``ctypes``).

POSIX file modes do not exist on Windows, so "readable by its owner alone" is decided from the
file's discretionary ACL instead. A principal counts as a reader when an *allow* entry that applies
to the file itself (not an inherit-only entry) grants it any read right, or the right to change
the ACL or take ownership (which lets it grant itself read). The owner (also as OWNER
RIGHTS), SYSTEM and the Administrators group are expected readers; anyone else (Users, Everyone,
Authenticated Users, another account) is reported. A file with no DACL is readable by everyone.
"""

import sys
from pathlib import Path

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x1
    _DACL_SECURITY_INFORMATION = 0x4
    # ACCESS_ALLOWED and ACCESS_ALLOWED_CALLBACK (conditional): the same layout up to the SID
    _ALLOW_TYPES = frozenset({0x0, 0x9})
    _INHERIT_ONLY_ACE = 0x8
    # FILE_READ_DATA, GENERIC_READ, GENERIC_ALL, MAXIMUM_ALLOWED read directly; WRITE_DAC and
    # WRITE_OWNER let a principal give itself read. Any of them counts as being able to read.
    _READ_RIGHTS = 0x1 | 0x80000000 | 0x10000000 | 0x02000000 | 0x40000 | 0x80000

    class _AceHeader(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    class _AccessAllowedAce(ctypes.Structure):
        _fields_ = [
            ("Header", _AceHeader),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    class _AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]


# SYSTEM and the built-in Administrators group (they can read any file anyway), and OWNER RIGHTS
# (S-1-3-4), which stands for whoever owns the file: the owner, not another principal.
EXPECTED_READERS = frozenset({"S-1-5-18", "S-1-5-32-544", "S-1-3-4"})


class AclError(OSError):
    """The file's ACL could not be read."""


def _sid_string(sid: int) -> str:
    text = ctypes.c_wchar_p()
    if not _advapi32.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(text)):
        raise AclError(ctypes.get_last_error(), "ConvertSidToStringSidW failed")
    try:
        return str(text.value)
    finally:
        _kernel32.LocalFree(text)


def other_readers(path: Path) -> list[str]:
    """The SIDs (as strings) of every principal besides the owner, SYSTEM and Administrators that
    may read ``path``; ``["Everyone (no DACL)"]`` if the file has no DACL. Windows only."""
    if sys.platform != "win32":
        raise NotImplementedError("other_readers reads a Windows ACL")
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = _advapi32.GetNamedSecurityInfoW(
        str(path),
        _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status != 0:
        raise AclError(status, f"cannot read the ACL of {path}")
    try:
        if not dacl.value:
            return ["Everyone (no DACL)"]
        expected = EXPECTED_READERS | {_sid_string(owner.value or 0)}
        info = _AclSizeInformation()
        if not _advapi32.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
            raise AclError(ctypes.get_last_error(), f"cannot read the ACL of {path}")
        readers: list[str] = []
        for index in range(info.AceCount):
            ace = ctypes.c_void_p()
            if not _advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                raise AclError(ctypes.get_last_error(), f"cannot read the ACL of {path}")
            entry = ctypes.cast(ace, ctypes.POINTER(_AccessAllowedAce)).contents
            if entry.Header.AceType not in _ALLOW_TYPES:
                continue  # deny entries only take rights away; other types do not apply to files
            if entry.Header.AceFlags & _INHERIT_ONLY_ACE or not entry.Mask & _READ_RIGHTS:
                continue
            sid = _sid_string((ace.value or 0) + _AccessAllowedAce.SidStart.offset)
            if sid not in expected and sid not in readers:
                readers.append(sid)
        return readers
    finally:
        _kernel32.LocalFree(descriptor)
