"""Bat Virtual Terminal (ANSI) cho cmd.exe / PowerShell cu.

cmd.exe mac dinh in ma ANSI dang text tho (nhu screenshot).
Goi enable_ansi() mot lan luc start truoc moi print().
Khong phu thuoc thu vien ngoai.
"""
from __future__ import annotations

import os
import re

_ANSI_RE = re.compile(r"\033\[[0-9;]*m|\033\[[HJ2]*")
_enabled: bool | None = None


def enable_ansi() -> bool:
    global _enabled
    if os.name != "nt":
        _enabled = True
        return True
    # Trick pho bien: os.system('') bat VT processing tren Win10+.
    try:
        os.system("")
    except Exception:
        pass
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            ENABLE_VT = 0x0004
            new_mode = mode.value | ENABLE_VT
            if kernel.SetConsoleMode(handle, new_mode):
                _enabled = True
                return True
    except Exception:
        pass
    _enabled = False
    return False


def ansi_ok() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if _enabled is not None:
        return _enabled
    return os.name != "nt"


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)
