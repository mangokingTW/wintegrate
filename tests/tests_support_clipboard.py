"""Reading the Windows clipboard, for tests whose post-condition lands there.

Kept in the test suite rather than in the library, on the same judgement as
`tests_support_pixels`: this is ordinary Win32, not knowledge that is hard to
rediscover. The one non-obvious part is the retry — the clipboard is a single
system-wide resource and `OpenClipboard` fails outright while another process
holds it, which on a busy desktop is often the application you just told to copy.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

CF_UNICODETEXT = 13
CF_HDROP = 15

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]


def _open(timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _user32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def clipboard_text(timeout: float = 3.0) -> str | None:
    """The clipboard's Unicode text, or None if it holds none or cannot be read."""
    if not _open(timeout):
        return None
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.c_wchar_p(pointer).value
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def clipboard_has_format(fmt: int) -> bool:
    """Whether a clipboard format is on offer, without taking the data.

    Which *format* is present says more than what the text says. A shell copy of
    a file offers CF_HDROP; a text box offers CF_UNICODETEXT. Asking "is there
    text" cannot tell the two apart when the answer is no for one and yes for
    the other only after an async copy settles.
    """
    return bool(_user32.IsClipboardFormatAvailable(fmt))


def clear_clipboard(timeout: float = 3.0) -> bool:
    """Empties the clipboard, so a later read cannot return a stale value.

    Without this a test that copies nothing at all still reads whatever the
    previous one left, and passes.
    """
    if not _open(timeout):
        return False
    try:
        return bool(_user32.EmptyClipboard())
    finally:
        _user32.CloseClipboard()
