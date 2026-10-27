"""Windows DPAPI for the current user (PLAN D8; no extra package: ``ctypes``).

``protect`` encrypts bytes so that only this Windows account on this machine can decrypt them;
``unprotect`` reverses it. Nothing is ever shown to the user (``CRYPTPROTECT_UI_FORBIDDEN``). Off
Windows both raise ``NotImplementedError``, so a caller can fall back (for example to a key kept in
memory only).
"""

import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _BLOB_P = ctypes.POINTER(_Blob)
    _crypt32.CryptProtectData.argtypes = [
        _BLOB_P, wintypes.LPCWSTR, _BLOB_P, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        _BLOB_P,
    ]  # fmt: skip
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        _BLOB_P, ctypes.c_void_p, _BLOB_P, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        _BLOB_P,
    ]  # fmt: skip
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _UI_FORBIDDEN = 0x1


class DpapiError(OSError):
    """DPAPI refused (another account's data, or damaged data)."""


def _blob(data: bytes) -> "_Blob":
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _call(name: str, data: bytes) -> bytes:
    if sys.platform != "win32":
        raise NotImplementedError("DPAPI exists on Windows only")
    source, out = _blob(data), _Blob()
    function = getattr(_crypt32, name)
    if not function(ctypes.byref(source), None, None, None, None, _UI_FORBIDDEN, ctypes.byref(out)):
        raise DpapiError(ctypes.get_last_error(), f"{name} failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(out.pbData)


def protect(data: bytes) -> bytes:
    """``data`` encrypted for this Windows account on this machine."""
    return _call("CryptProtectData", data)


def unprotect(data: bytes) -> bytes:
    """What ``protect`` encrypted (raises ``DpapiError`` for anything else)."""
    return _call("CryptUnprotectData", data)
