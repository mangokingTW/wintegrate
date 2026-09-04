"""Win32 structures, constants, and ctypes foreign function interface."""

from __future__ import annotations

import ctypes
import enum
import logging
import os
import sys
import time
from ctypes import wintypes
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Native DLL handles.
#
# Deliberately NOT ctypes.windll: that is a process-wide cache handing every
# importer the same WinDLL, whose function pointers are also cached — so the
# argtypes pinned below would be visible to every other ctypes user in the
# process. Since ctypes checks pointer arguments by type identity rather than
# layout, an unrelated library passing its own byte-identical INPUT struct to
# SendInput would then fail with "expected LP_INPUT instance instead of
# LP_Input", raised from inside that library and looking like its bug.
# (Reported against 0.1.0 by a caller using pydirectinput alongside wintegrate.)
#
# Private WinDLL instances get their own function-pointer cache, so these pins
# stay inside wintegrate while still persisting across our own calls.
if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    imm32 = ctypes.WinDLL("imm32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
else:

    class _UnsupportedPlatformFunc:
        """Importable placeholder for a Win32 API: any call fails fast off-Windows."""

        def __init__(self, dll_name: str, func_name: str):
            self._label = f"{dll_name}.{func_name}"
            self.argtypes = []
            self.restype = None

        def __call__(self, *args, **kwargs):
            raise RuntimeError(
                f"wintegrate requires Windows: {self._label} is unavailable on this platform"
            )

    class _UnsupportedPlatformDll:
        def __init__(self, name: str):
            self._name = name
            self._funcs: dict[str, _UnsupportedPlatformFunc] = {}

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            return self._funcs.setdefault(name, _UnsupportedPlatformFunc(self._name, name))

    user32 = _UnsupportedPlatformDll("user32")
    kernel32 = _UnsupportedPlatformDll("kernel32")
    imm32 = _UnsupportedPlatformDll("imm32")
    gdi32 = _UnsupportedPlatformDll("gdi32")
    ole32 = _UnsupportedPlatformDll("ole32")
    dwmapi = _UnsupportedPlatformDll("dwmapi")

# Window Show / Sizing Constants
SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_MAXIMIZE = 3
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_MINIMIZE = 6
SW_SHOWMINNOACTIVE = 7
SW_SHOWNA = 8
SW_RESTORE = 9

# SetWindowPos Flags
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040

# Window Messages
WM_DESTROY = 0x0002
WM_SETFOCUS = 0x0007
WM_KILLFOCUS = 0x0008
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_SETTINGCHANGE = 0x001A
WM_CHAR = 0x0102
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_IME_CONTROL = 0x0283
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_QUIT = 0x0012

# Low-level hooks
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

# Low-level keyboard flags & special VK
VK_PACKET = 0xE7
LLKHF_EXTENDED = 0x00000001
LLKHF_INJECTED = 0x00000010
LLKHF_ALTDOWN = 0x00000020
LLKHF_UP = 0x00000080

# GetCursorInfo / DrawIconEx
CURSOR_SHOWING = 0x00000001
DI_NORMAL = 0x0003

# IME Constants
IMC_GETCONVERSIONMODE = 0x0001
IMC_SETCONVERSIONMODE = 0x0002
IMC_GETOPENSTATUS = 0x0005
IMC_SETOPENSTATUS = 0x0006

# DwmGetWindowAttribute
DWMWA_CLOAKED = 14


class CloakReason(enum.IntFlag):
    """Why DWM is hiding a window, as an IntFlag so the reason prints as itself.

    Cloaking is not the same thing as `IsWindowVisible` returning False, and the
    distinction matters because **`IsWindowVisible` answers True for a cloaked
    window**. A WinUI or UWP app that has hidden itself, and a window sitting on
    another virtual desktop, are both still "visible" by that measure.

    The reason is worth having rather than just a boolean: `SHELL` usually means
    the window is on another virtual desktop, which `Window.move_to_current_desktop`
    can fix, while `APP` means the application itself put it away and only the
    application will bring it back.
    """

    APP = 0x0000_0001
    SHELL = 0x0000_0002
    INHERITED = 0x0000_0004


def get_window_cloak_reason(hwnd: int) -> CloakReason | None:
    """
    Returns why DWM is hiding `hwnd`, `CloakReason(0)` when it is not, or None
    when the attribute cannot be read (a dead handle, or a platform without DWM).

    None and `CloakReason(0)` are deliberately different: "not cloaked" is an
    answer, "could not ask" is not, and collapsing them is how a caller ends up
    treating a window it cannot see as being on screen.
    """
    if not hwnd:
        return None
    value = wintypes.DWORD(0)
    try:
        hresult = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_CLOAKED),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except Exception as exc:  # noqa: BLE001 - no DWM at all is a None, not a crash
        logger.debug(f"DwmGetWindowAttribute failed ({type(exc).__name__}): {exc}")
        return None
    if hresult != 0:
        logger.debug(f"DwmGetWindowAttribute returned 0x{hresult & 0xFFFFFFFF:08X} for {hwnd:#x}")
        return None
    return CloakReason(value.value)


class DisplayAffinity(enum.IntEnum):
    """Whether a window has asked Windows to keep it out of screen captures.

    A window sets this on itself with `SetWindowDisplayAffinity`, and Windows
    then honours it in **every** capture path: GDI `BitBlt`, DXGI Desktop
    Duplication, `Windows.Graphics.Capture`, DWM thumbnails. There is no flag on
    the capturing side that overrides it, and the call only works on windows of
    the calling process, so nothing outside the application can clear it.

    This is a third, separate reason a window can be missing from a capture, and
    it is the one every other instrument answers wrongly:

    | reason | `IsWindowVisible` | cloak reason | in a capture |
    |---|---|---|---|
    | off-screen coordinates | True | 0 | yes, just not where you looked |
    | DWM cloaked | True | non-zero | no |
    | **excluded from capture** | **True** | **0** | **no** |

    So the window is visible, uncloaked, on screen, and the user can see it --
    only the recording cannot. Password managers do this deliberately, which is
    where it was found: a session recording that showed the credential prompt,
    the keystroke HUD and every step of the run, with the application under test
    simply absent from the frame.
    """

    NONE = 0x00
    MONITOR = 0x01
    EXCLUDE_FROM_CAPTURE = 0x11


def get_window_display_affinity(hwnd: int) -> DisplayAffinity | None:
    """Returns the window's display affinity, or None when it cannot be read.

    None and `DisplayAffinity.NONE` are deliberately different, for the same
    reason as in `get_window_cloak_reason`: "nothing is excluding this window"
    is an answer, "could not ask" is not, and a caller that collapses them ends
    up promising a recording it cannot produce. An affinity value Windows has
    added since this was written is also None -- it is readable but not something
    this code can claim to understand.
    """
    if not hwnd:
        return None
    value = wintypes.DWORD(0)
    try:
        ok = user32.GetWindowDisplayAffinity(wintypes.HWND(hwnd), ctypes.byref(value))
    except Exception as exc:  # noqa: BLE001 - an older platform is a None, not a crash
        logger.debug(f"GetWindowDisplayAffinity failed ({type(exc).__name__}): {exc}")
        return None
    if not ok:
        logger.debug(
            f"GetWindowDisplayAffinity failed for {hwnd:#x} (error {ctypes.get_last_error()})"
        )
        return None
    try:
        return DisplayAffinity(value.value)
    except ValueError:
        logger.debug(f"unknown display affinity {value.value:#x} for {hwnd:#x}")
        return None


# GetSystemMetrics indices
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CXCURSOR = 13
SM_CYCURSOR = 14

# PrintWindow flags
PW_RENDERFULLCONTENT = 0x00000002

SRCCOPY = 0x00CC0020

# Desktop / Access Rights
DESKTOP_ALL = 0x01FF
HWND_BROADCAST = 0xFFFF

# SendInput Constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
MOUSEEVENTF_ABSOLUTE = 0x8000
# Maps absolute coordinates onto the whole virtual desktop instead of the primary
# monitor, which is what ABSOLUTE alone does.
MOUSEEVENTF_VIRTUALDESK = 0x4000
WHEEL_DELTA = 120

# Virtual Key Codes
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_SPACE = 0x20
VK_BACK = 0x08
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C

# IME control keys. These reach the IME itself rather than the focused control.
VK_KANA = 0x15  # also VK_HANGUL
VK_IME_ON = 0x16
VK_KANJI = 0x19  # also VK_HANJA
VK_IME_OFF = 0x1A
VK_CONVERT = 0x1C
VK_NONCONVERT = 0x1D
VK_PROCESSKEY = 0xE5  # what an IME reports while it owns a keystroke


# ImmGetConversionStatus mode flags
VK_CAPITAL = 0x14


class ImeConversion(enum.IntFlag):
    """IME conversion-mode flags, as an IntFlag so a mode prints as what it means.

    The raw API deals in integers, and `conversion=9` tells a reader nothing. As
    an IntFlag the same value reprs as `ImeConversion.NATIVE|FULLSHAPE`, which is
    the difference between a diagnostic artifact you can read and one you have to
    decode. IntFlag members are ints, so anything that took the old constants
    still works.
    """

    ALPHANUMERIC = 0x0000
    NATIVE = 0x0001
    KATAKANA = 0x0002
    FULLSHAPE = 0x0008
    ROMAN = 0x0010


# The original names, kept because they are the ones the Win32 docs use.
IME_CMODE_ALPHANUMERIC = ImeConversion.ALPHANUMERIC
IME_CMODE_NATIVE = ImeConversion.NATIVE
IME_CMODE_KATAKANA = ImeConversion.KATAKANA
IME_CMODE_FULLSHAPE = ImeConversion.FULLSHAPE
IME_CMODE_ROMAN = ImeConversion.ROMAN

# ImmGetCompositionString index
GCS_COMPSTR = 0x0008
GCS_RESULTSTR = 0x0800

MAPVK_VK_TO_VSC = 0
#: Scan code back to a virtual key, distinguishing left from right modifiers.
#: The plain MAPVK_VSC_TO_VK (1) collapses them, which loses which Shift it was.
MAPVK_VSC_TO_VK_EX = 3

# Named keys accepted inside braces by send_keys, e.g. "{ENTER}", "{TAB 3}".
# Names are matched case-insensitively.
KEY_NAMES: dict[str, int] = {
    "ENTER": VK_RETURN,
    "RETURN": VK_RETURN,
    "TAB": VK_TAB,
    "SPACE": VK_SPACE,
    "BACKSPACE": VK_BACK,
    "BS": VK_BACK,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "DELETE": 0x2E,
    "DEL": 0x2E,
    "INSERT": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PGUP": 0x21,
    "PGDN": 0x22,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "SHIFT": VK_SHIFT,
    "CTRL": VK_CONTROL,
    "CONTROL": VK_CONTROL,
    "ALT": VK_MENU,
    "WIN": VK_LWIN,
    "LWIN": VK_LWIN,
    "RWIN": VK_RWIN,
    "APPS": 0x5D,  # context-menu key
    "PRTSC": 0x2C,
    # IME control keys
    "IME_ON": VK_IME_ON,
    "IME_OFF": VK_IME_OFF,
    "KANA": VK_KANA,
    "HANGUL": VK_KANA,
    "KANJI": VK_KANJI,
    "HANJA": VK_KANJI,
    "CONVERT": VK_CONVERT,
    "NONCONVERT": VK_NONCONVERT,
    **{f"F{i}": 0x6F + i for i in range(1, 25)},  # F1..F24 -> 0x70..0x87
}

# Keys that must carry KEYEVENTF_EXTENDEDKEY to be delivered correctly. Both Win
# keys belong here: their scan codes (0xE05B/0xE05C) are in the extended set, and
# without the flag the shell does not recognise a Win chord at all.
_EXTENDED_VKS = {
    0x2E,
    0x2D,
    0x24,
    0x23,
    0x21,
    0x22,
    0x25,
    0x26,
    0x27,
    0x28,
    0x5D,
    0x2C,
    VK_LWIN,
    VK_RWIN,
}

# A repeat count above this is a typo rather than an intent: even at the default
# 20 ms per key, a thousand keystrokes already takes 20 seconds to send.
MAX_KEY_REPEAT = 1000

# Modifier prefixes, following the pywinauto/SendKeys convention.
#
# There is deliberately no Win-key prefix here. AutoHotkey spells it `#`, but this
# grammar sends everything it does not recognise as literal text, so claiming `#`
# would silently change what `send_keys("issue #123")` types. Win chords go
# through `send_hotkey()` instead, which is a separate grammar for a separate job:
# this one is for typing, that one is for pressing a chord.
_MODIFIER_PREFIXES = {"^": VK_CONTROL, "+": VK_SHIFT, "%": VK_MENU}

# Names accepted as modifiers by `parse_hotkey`. Aliases are the spellings people
# actually write in a hotkey, not the Win32 ones: nobody types "menu" for Alt.
_HOTKEY_MODIFIERS = {
    "CTRL": VK_CONTROL,
    "CONTROL": VK_CONTROL,
    "SHIFT": VK_SHIFT,
    "ALT": VK_MENU,
    "MENU": VK_MENU,
    "WIN": VK_LWIN,
    "LWIN": VK_LWIN,
    "RWIN": VK_RWIN,
    "SUPER": VK_LWIN,
    "META": VK_LWIN,
}


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", POINT),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


if sys.platform == "win32":
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
else:
    # Importable stand-in; EnumWindows callbacks are never invoked off-Windows.
    WNDENUMPROC = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

# Explicit signatures
user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenInputDesktop.restype = wintypes.HANDLE

user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
user32.SetThreadDesktop.restype = wintypes.BOOL

user32.EnumDesktopWindows.argtypes = [wintypes.HANDLE, WNDENUMPROC, wintypes.LPARAM]
user32.EnumDesktopWindows.restype = wintypes.BOOL

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL

user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL

user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL

user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
# Declared like everything else here, and for the usual reason: an undeclared
# out-pointer is marshalled as a C int, and the address of a DWORD overflows it.
user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype = wintypes.BOOL
user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
user32.GetDlgCtrlID.restype = ctypes.c_int
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
# `SendMessageW` gets a restype but deliberately no argtypes. The default
# restype is a 32-bit int, which truncates any message that answers with a
# pointer or a packed pair — `WM_MENUCHAR` returns its command in the high word.
#
# argtypes are left off because lParam is genuinely polymorphic: `WM_GETTEXT`
# wants a buffer there and `SCI_*` wants an integer, and declaring either one
# rejects the other. Callers pass explicit `wintypes.*` instances instead, which
# is where the width is decided anyway.
user32.SendMessageW.restype = wintypes.LPARAM
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short

user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.ActivateKeyboardLayout.argtypes = [wintypes.HKL, wintypes.UINT]
user32.ActivateKeyboardLayout.restype = wintypes.HKL
user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
user32.LoadKeyboardLayoutW.restype = wintypes.HKL
user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(wintypes.HKL)]
user32.GetKeyboardLayoutList.restype = ctypes.c_int

imm32.ImmGetContext.argtypes = [wintypes.HWND]
imm32.ImmGetContext.restype = wintypes.HANDLE
imm32.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
imm32.ImmReleaseContext.restype = wintypes.BOOL
imm32.ImmGetOpenStatus.argtypes = [wintypes.HANDLE]
imm32.ImmGetOpenStatus.restype = wintypes.BOOL
imm32.ImmSetOpenStatus.argtypes = [wintypes.HANDLE, wintypes.BOOL]
imm32.ImmSetOpenStatus.restype = wintypes.BOOL
imm32.ImmGetConversionStatus.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
imm32.ImmGetConversionStatus.restype = wintypes.BOOL
imm32.ImmSetConversionStatus.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
imm32.ImmSetConversionStatus.restype = wintypes.BOOL
imm32.ImmGetCompositionStringW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
]
imm32.ImmGetCompositionStringW.restype = ctypes.c_long
imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND
imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND

user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

# Declared because it was not, and that cost three CI runs. GetModuleHandleW
# returns an HMODULE; undeclared, ctypes converts it as c_int and the top 32
# bits go. Handing that truncated value to RegisterClassW and CreateWindowExW
# crashed inside window creation with an access violation -- and the crash
# reached neither pytest's exit code nor its summary line.
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p

user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND

user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.restype = wintypes.HWND

user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND

kernel32.GetCurrentThreadId.restype = wintypes.DWORD

# GDI handles are pointer-width. Left unpinned, ctypes converts them as c_int and
# a handle above 2^31 raises "int too long to convert" -- which is exactly what
# GetIconInfo's bitmaps did here. The existing capture path got away with it
# because GDI hands out small values in practice; that is luck, not a contract.
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HBITMAP,
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFOHEADER),
    ctypes.c_uint,
]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.BitBlt.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]
gdi32.BitBlt.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int

user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorInfo.restype = wintypes.BOOL
user32.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wintypes.BOOL
user32.DrawIconEx.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
    wintypes.HANDLE,
    ctypes.c_uint,
]
user32.DrawIconEx.restype = wintypes.BOOL
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(RECT), wintypes.HANDLE]
user32.FillRect.restype = ctypes.c_int
gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
gdi32.CreateSolidBrush.restype = wintypes.HANDLE
# LPARAM, not int: a truncated hook parameter raises inside the hook callback,
# where ctypes prints the traceback and returns None. The event is still seen but
# never reaches the next hook in the chain, and nothing fails loudly.
user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = ctypes.c_long
# argtypes are deliberately left alone: the hook procedure's WINFUNCTYPE is
# defined by the caller, and pinning one here would reject every other shape.
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = wintypes.BOOL


class CursorState(NamedTuple):
    """Where the pointer is and which cursor is being shown for it."""

    handle: int
    position: tuple[int, int]
    hotspot: tuple[int, int]


def get_cursor_state() -> CursorState | None:
    """The visible cursor, or None when nothing is being shown.

    A screen grab through BitBlt does not include the pointer -- the cursor is
    composited by the system, not stored in the desktop bitmap -- so anything that
    wants a pointer in its output has to ask for it separately and draw it.
    """
    info = CURSORINFO()
    info.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(info)):
        return None
    if not (info.flags & CURSOR_SHOWING) or not info.hCursor:
        return None

    hotspot = (0, 0)
    icon = ICONINFO()
    if user32.GetIconInfo(info.hCursor, ctypes.byref(icon)):
        hotspot = (int(icon.xHotspot), int(icon.yHotspot))
        # GetIconInfo hands over two bitmaps that the caller owns; leaking one per
        # frame would exhaust the GDI object quota partway through a recording.
        for bitmap in (icon.hbmMask, icon.hbmColor):
            if bitmap:
                gdi32.DeleteObject(bitmap)

    return CursorState(
        handle=int(info.hCursor),
        position=(int(info.ptScreenPos.x), int(info.ptScreenPos.y)),
        hotspot=hotspot,
    )


def _send_input_checked(arr, label: str) -> bool:
    """
    Sends an INPUT array and reports events the system refused to inject.

    SendInput returns how many events it actually queued; a lower count means the
    input was blocked (UIPI from a higher-integrity foreground window, a locked
    workstation, or the thread not being attached to the input desktop). Ignoring
    the return value turns that into silently missing keystrokes.
    """
    count = len(arr)
    sent = user32.SendInput(count, arr, ctypes.sizeof(INPUT))
    if sent != count:
        err = ctypes.get_last_error()
        logger.warning(
            f"SendInput injected {sent}/{count} events for {label} (GetLastError={err}); "
            "input was blocked — the foreground window may run at a higher integrity level"
        )
        return False
    return True


def send_char_input(char: str):
    """
    Sends character input reliably using native Win32 SendInput (KEYEVENTF_UNICODE).

    Note: KEYEVENTF_UNICODE injects the character directly and does NOT pass through
    an IME. Automating IME composition requires scan-code input instead.
    """
    if char == "\n" or char == "\r":
        # Press Enter key via VK_RETURN
        inp_down = INPUT(
            type=INPUT_KEYBOARD,
            u=_INPUT_UNION(
                ki=KEYBDINPUT(wVk=VK_RETURN, wScan=0x1C, dwFlags=0, time=0, dwExtraInfo=0)
            ),
        )
        inp_up = INPUT(
            type=INPUT_KEYBOARD,
            u=_INPUT_UNION(
                ki=KEYBDINPUT(
                    wVk=VK_RETURN, wScan=0x1C, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0
                )
            ),
        )
        arr = (INPUT * 2)(inp_down, inp_up)
        _send_input_checked(arr, "VK_RETURN")
    else:
        val = ord(char)
        inp_down = INPUT(
            type=INPUT_KEYBOARD,
            u=_INPUT_UNION(
                ki=KEYBDINPUT(wVk=0, wScan=val, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)
            ),
        )
        inp_up = INPUT(
            type=INPUT_KEYBOARD,
            u=_INPUT_UNION(
                ki=KEYBDINPUT(
                    wVk=0,
                    wScan=val,
                    dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )
        arr = (INPUT * 2)(inp_down, inp_up)
        _send_input_checked(arr, repr(char))


def parse_key_spec(spec: str) -> list[tuple[str, object, tuple[int, ...]]]:
    """
    Parses a SendKeys-style spec into `(kind, value, modifier_vks)` actions.

    - `"{ENTER}"` / `"{ESC}"` / `"{F5}"` -> named virtual keys (`kind="vk"`)
    - `"{TAB 3}"` repeats a named key three times
    - `"^a"`, `"+{TAB}"`, `"%{F4}"` -> Ctrl / Shift / Alt applied to the next key
    - anything else is literal text (`kind="char"`), sent as Unicode
    - `"{{"` and `"}}"` are literal braces

    Pure function: no Win32 calls, so the grammar is testable on any platform.
    Raises ValueError on an unterminated brace or an unknown key name.
    """
    actions: list[tuple[str, object, tuple[int, ...]]] = []
    pending: list[int] = []
    i = 0
    while i < len(spec):
        ch = spec[i]

        if ch in _MODIFIER_PREFIXES:
            pending.append(_MODIFIER_PREFIXES[ch])
            i += 1
            continue

        if ch == "{":
            if spec.startswith("{{", i):
                actions.append(("char", "{", tuple(pending)))
                pending, i = [], i + 2
                continue
            end = spec.find("}", i + 1)
            if end == -1:
                raise ValueError(f"Unterminated '{{' in key spec: {spec!r}")
            body = spec[i + 1 : end].strip()
            if not body:
                raise ValueError(f"Empty '{{}}' in key spec: {spec!r}")
            name, _, count_str = body.partition(" ")
            repeat = 1
            count_str = count_str.strip()
            if count_str:
                # Deliberately not int(): it accepts "+3", "-5" and "1_000_000",
                # inheriting Python's literal syntax into a grammar that never
                # promised it. A negative count also used to parse cleanly and
                # then expand to nothing — a keystroke silently not sent.
                if not count_str.isdigit():
                    raise ValueError(
                        f"Invalid repeat count {count_str!r} in {body!r}: expected digits only"
                    )
                repeat = int(count_str)
                if repeat < 1:
                    raise ValueError(f"Repeat count in {body!r} must be at least 1")
                if repeat > MAX_KEY_REPEAT:
                    # Unbounded, this builds one action per repeat: "{TAB 99999999999}"
                    # exhausts memory instead of reporting a typo.
                    raise ValueError(
                        f"Repeat count {repeat} in {body!r} exceeds the maximum of {MAX_KEY_REPEAT}"
                    )
            vk = KEY_NAMES.get(name.upper())
            if vk is None:
                raise ValueError(
                    f"Unknown key name {name!r}. Known names: {', '.join(sorted(KEY_NAMES))}"
                )
            for _ in range(repeat):
                actions.append(("vk", vk, tuple(pending)))
            pending, i = [], end + 1
            continue

        if spec.startswith("}}", i):
            actions.append(("char", "}", tuple(pending)))
            pending, i = [], i + 2
            continue

        actions.append(("char", ch, tuple(pending)))
        pending, i = [], i + 1

    return actions


def _key_input(vk: int, keyup: bool) -> INPUT:
    flags = KEYEVENTF_KEYUP if keyup else 0
    if vk in _EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    return INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUT_UNION(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)),
    )


def send_vk_input(vk: int, modifiers: tuple[int, ...] = ()) -> bool:
    """Presses a virtual key, holding `modifiers` (Ctrl/Shift/Alt VKs) around it."""
    events = [_key_input(m, False) for m in modifiers]
    events += [_key_input(vk, False), _key_input(vk, True)]
    events += [_key_input(m, True) for m in reversed(modifiers)]
    arr = (INPUT * len(events))(*events)
    return _send_input_checked(arr, f"vk=0x{vk:02X} modifiers={modifiers}")


def parse_hotkey(spec: str) -> tuple[tuple[int, ...], int | str]:
    """
    Parses a chord like `"win+alt+space"` or `"ctrl+,"` into `(modifiers, key)`.

    The rule is simply that **the last token is the key and everything before it
    must be a modifier**. That makes `"win"` (press the Win key alone, opening
    Start) and `"win+alt+space"` the same grammar rather than two special cases,
    and it rejects `"space+ctrl"` instead of quietly sending something else.

    The key comes back as an `int` when it can be resolved without asking the
    system — a name from `KEY_NAMES`, or a single ASCII letter or digit, whose
    virtual key equals its uppercase codepoint. Anything else (`","`, `"/"`,
    whose virtual key depends on the active keyboard layout) comes back as the
    one-character `str` for `send_hotkey` to map through `VkKeyScanW`.

    Pure function: no Win32 calls, so the grammar is testable on any platform.
    Raises ValueError on an empty spec, an unknown name, or a non-modifier in a
    non-final position.

    `+` separates tokens here, and is *not* the Shift prefix it is in
    `parse_key_spec`. These are two grammars for two jobs: that one types text,
    this one presses a chord. Shift is spelled `"shift"`, and `"shift++"` is a
    Shift-plus chord because only the final token is read as the key.
    """
    if not spec or not spec.strip():
        raise ValueError("Empty hotkey spec")

    stripped = spec.strip()
    if stripped.endswith("+"):
        # The key itself is "+". Both "ctrl+" and "ctrl++" mean Ctrl-plus; the
        # separator before it is optional because requiring it would make "+"
        # alone unspellable.
        key_token = "+"
        head = stripped[:-1]
        if head.endswith("+"):
            head = head[:-1]
    else:
        head, _, key_token = stripped.rpartition("+")
        key_token = key_token.strip()

    tokens = [t.strip() for t in head.split("+")] if head else []

    modifiers: list[int] = []
    for token in tokens:
        if not token:
            raise ValueError(
                f"Empty token in hotkey spec {spec!r}. Only modifiers may precede "
                f"the key; known modifiers: {', '.join(sorted(_HOTKEY_MODIFIERS))}"
            )
        vk = _HOTKEY_MODIFIERS.get(token.upper())
        if vk is None:
            raise ValueError(
                f"{token!r} is not a modifier, so it cannot come before the key in "
                f"{spec!r}. The last token is the key. Known modifiers: "
                f"{', '.join(sorted(_HOTKEY_MODIFIERS))}"
            )
        if vk not in modifiers:
            modifiers.append(vk)

    named = KEY_NAMES.get(key_token.upper())
    if named is not None:
        return tuple(modifiers), named
    if len(key_token) == 1:
        if key_token.isascii() and key_token.isalnum():
            return tuple(modifiers), ord(key_token.upper())
        return tuple(modifiers), key_token
    raise ValueError(
        f"Unknown key {key_token!r} in hotkey spec {spec!r}. Use a single character "
        f"or one of: {', '.join(sorted(KEY_NAMES))}"
    )


def send_hotkey(spec: str) -> bool:
    """
    Presses a chord: `send_hotkey("win+alt+space")`, `send_hotkey("ctrl+shift+p")`.

    This exists because `send_keys` cannot express a Win chord and should not be
    taught to: its grammar sends unrecognised characters as literal text, so
    giving `#` a meaning would change what `send_keys("issue #123")` types.

    Returns False if the system refused to inject the events. Raises ValueError on
    a spec that does not parse — see `parse_hotkey`.
    """
    modifiers, key = parse_hotkey(spec)
    if isinstance(key, str):
        scan = user32.VkKeyScanW(key)
        if scan == -1:
            raise ValueError(
                f"{key!r} has no virtual key on the active keyboard layout "
                f"(0x{get_keyboard_layout():08X}), so {spec!r} cannot be sent"
            )
        # The layout may need Shift for this character (e.g. "?" on a US layout).
        # That Shift is part of producing the key, so it joins the modifiers.
        if scan & 0x0100 and VK_SHIFT not in modifiers:
            modifiers = (*modifiers, VK_SHIFT)
        key = scan & 0xFF
    return send_vk_input(key, modifiers)


def send_keys(spec: str, delay_per_key: float = 0.02) -> bool:
    """
    Sends a SendKeys-style spec: named keys in braces, `^`/`+`/`%` modifiers,
    everything else as literal Unicode text. See `parse_key_spec` for the grammar.

    There is no Win-key modifier in this grammar; use `send_hotkey("win+...")`.

    Returns False if the system refused to inject any of the events.
    """
    ok = True
    for kind, value, modifiers in parse_key_spec(spec):
        if kind == "vk":
            ok = send_vk_input(int(value), modifiers) and ok
        elif modifiers:
            # A modified letter is a shortcut (e.g. ^a), so send the letter's
            # virtual key rather than a Unicode codepoint, which ignores modifiers.
            vk_scan = user32.VkKeyScanW(str(value))
            ok = send_vk_input(vk_scan & 0xFF, modifiers) and ok
        else:
            send_char_input(str(value))
        if delay_per_key > 0:
            time.sleep(delay_per_key)
    return ok


def send_scan_key(vk: int, keyup: bool = False, extended: bool = False) -> INPUT:
    """
    Builds a keystroke carried by its scan code rather than its virtual key.

    An IME sits below the virtual-key layer: it watches physical key events and
    maps them through its own layout. Synthesizing a Unicode codepoint
    (KEYEVENTF_UNICODE) or a bare virtual key hands the character straight to the
    focused control, so composition never starts. Scan codes are the only input
    path an IME actually processes.
    """
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    flags = KEYEVENTF_SCANCODE
    if keyup:
        flags |= KEYEVENTF_KEYUP
    if extended or vk in _EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    return INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)),
    )


def send_physical_keys(text: str, delay_per_key: float = 0.03) -> bool:
    """
    Types `text` as physical key presses, so an active IME sees and composes them.

    Each character is mapped to a virtual key for the current keyboard layout
    (VkKeyScanW) and delivered by scan code, with Shift held where the layout
    requires it. Use this — not `send_char_input` — whenever the IME is the thing
    under test; use `send_char_input` when you only need the characters to land.

    Returns False if the system refused to inject any of the events.
    """
    ok = True
    for ch in text:
        vk_scan = user32.VkKeyScanW(ch)
        if vk_scan == -1:
            logger.warning(
                f"Character {ch!r} is not reachable on the active keyboard layout; "
                "sending it as Unicode instead, which bypasses the IME"
            )
            send_char_input(ch)
            continue
        vk = vk_scan & 0xFF
        shift_state = (vk_scan >> 8) & 0xFF
        modifiers = []
        if shift_state & 0x01:
            modifiers.append(VK_SHIFT)
        if shift_state & 0x02:
            modifiers.append(VK_CONTROL)
        if shift_state & 0x04:
            modifiers.append(VK_MENU)

        events = [send_scan_key(m) for m in modifiers]
        events += [send_scan_key(vk), send_scan_key(vk, keyup=True)]
        events += [send_scan_key(m, keyup=True) for m in reversed(modifiers)]
        arr = (INPUT * len(events))(*events)
        ok = _send_input_checked(arr, f"physical {ch!r}") and ok
        if delay_per_key > 0:
            time.sleep(delay_per_key)
    return ok


WM_IME_CONTROL = 0x0283
IMC_GETCONVERSIONMODE = 0x0001
IMC_SETCONVERSIONMODE = 0x0002
IMC_GETOPENSTATUS = 0x0005
IMC_SETOPENSTATUS = 0x0006


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def get_ime_status(hwnd: int) -> dict[str, object]:
    """
    Reads the IME state attached to a window: open/closed and conversion mode.

    `has_context` is False when the window has no IMM32 context at all — which is
    the normal answer for a modern XAML/WinUI control, where text services run
    through TSF instead. Treat a False there as "ask TSF", not as "IME is off".

    `has_context` is False whenever the control routes text services through TSF
    rather than IMM32 — which modern controls do — so it cannot distinguish "no
    IME here" from "an IME is running and swallowing your keystrokes". There is
    deliberately no companion field claiming to answer that: `ImmIsIME` looked
    like the answer but reports whether an HKL is *loaded*, returning true for a
    plain en-GB layout the moment it is loaded alongside an IME. Switch the
    layout to get deterministic input; do not try to detect an IME. A Bopomofo layout swallows
    unshifted letters into composition whether or not IMM32 hands out a context,
    so "no context" and "no IME" are different facts and a caller needs both.
    """
    himc = imm32.ImmGetContext(hwnd)
    if not himc:
        return {
            "has_context": False,
            "is_open": None,
            "conversion": None,
            "sentence": None,
        }
    try:
        is_open = bool(imm32.ImmGetOpenStatus(himc))
        conversion = wintypes.DWORD()
        sentence = wintypes.DWORD()
        if imm32.ImmGetConversionStatus(himc, ctypes.byref(conversion), ctypes.byref(sentence)):
            conv, sent = conversion.value, sentence.value
        else:
            conv, sent = None, None
        return {
            "has_context": True,
            "is_open": is_open,
            "conversion": conv,
            "sentence": sent,
            "native_mode": None if conv is None else bool(conv & IME_CMODE_NATIVE),
            "full_shape": None if conv is None else bool(conv & IME_CMODE_FULLSHAPE),
        }
    finally:
        imm32.ImmReleaseContext(hwnd, himc)


def _ime_control_targets(hwnd: int) -> list[int]:
    """The windows worth addressing when driving another process's IME.

    The IME follows keyboard focus, and in a dialog that focus is on a child
    control, not the window you were handed. GetGUIThreadInfo names the focused
    child; try it first and fall back to the window itself.
    """
    targets = [hwnd]
    try:
        tid = user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), None)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
            focus = int(info.hwndFocus)
            if focus and focus != hwnd:
                targets.insert(0, focus)
    except Exception as exc:
        logger.debug(f"GetGUIThreadInfo failed ({type(exc).__name__}): {exc}")
    return targets


def _ime_control(hwnd: int, command: int, value: int) -> int | None:
    """Sends one WM_IME_CONTROL to the default IME window, or None if there is none."""
    ime_wnd = imm32.ImmGetDefaultIMEWnd(wintypes.HWND(hwnd))
    if not ime_wnd:
        return None
    return int(user32.SendMessageW(ime_wnd, WM_IME_CONTROL, command, value))


def set_ime_open(hwnd: int, is_open: bool) -> bool:
    """Opens or closes the IME for a window.

    Goes through WM_IME_CONTROL rather than ImmSetOpenStatus. ImmGetContext
    returns nothing for a window in another process — and for anything routing
    text services through TSF — so the context-based call silently does nothing
    in exactly the case an automation tool cares about. The default IME window
    accepts the request across process boundaries.
    """
    sent = False
    for target in _ime_control_targets(hwnd):
        if _ime_control(target, IMC_SETOPENSTATUS, int(bool(is_open))) is not None:
            sent = True
    return sent


def set_ime_conversion(hwnd: int, conversion: int, sentence: int = 0) -> bool:
    """Sets the IME conversion mode (see the IME_CMODE_* flags).

    `sentence` is accepted for symmetry with get_ime_status and is not sent:
    WM_IME_CONTROL carries the conversion mode only.

    Measured on a zh-TW Windows 11 ARM64 desktop, driving a dialog in another
    process: with IME_CMODE_ALPHANUMERIC, send_physical_keys("hello") lands
    "hello"; with IME_CMODE_NATIVE the same call lands "" because the IME takes
    the keystrokes into composition. That is the switch this function exists to
    give a test.
    """
    sent = False
    for target in _ime_control_targets(hwnd):
        if _ime_control(target, IMC_SETCONVERSIONMODE, int(conversion)) is not None:
            sent = True
    return sent


def get_toggle_key_state(vk: int) -> bool:
    """Whether a toggle key (Caps Lock, Num Lock, Scroll Lock) is currently latched.

    The low bit of GetKeyState is the toggle, not the pressed state.
    """
    return bool(user32.GetKeyState(vk) & 1)


def set_caps_lock(on: bool) -> bool:
    """Latches or clears Caps Lock, returning whether it ended up as asked.

    Caps Lock is desktop-global and survives everything: a stray press hours ago
    turns every subsequent scan-code test into an assertion about `HELLO` instead
    of `hello`. Nothing in a test suite normally owns this state, which is exactly
    why a test that types letters has to establish it.
    """
    if get_toggle_key_state(VK_CAPITAL) == bool(on):
        return True
    user32.keybd_event(VK_CAPITAL, 0, 0, 0)
    user32.keybd_event(VK_CAPITAL, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.15)
    return get_toggle_key_state(VK_CAPITAL) == bool(on)


def get_ime_conversion(hwnd: int) -> ImeConversion | None:
    """The IME conversion mode as the IME itself reports it, or None if unavailable.

    None means "no IME window answered", which is not the same as alphanumeric —
    a caller restoring state must not treat it as a value to restore to.
    """
    for target in _ime_control_targets(hwnd):
        got = _ime_control(target, IMC_GETCONVERSIONMODE, 0)
        if got is not None:
            return ImeConversion(got)
    return None


def get_composition_string(hwnd: int, index: int = GCS_COMPSTR) -> str:
    """
    Returns the IME's in-progress composition string for a window ("" when idle).

    This is the direct evidence that keystrokes reached the IME rather than the
    control: mid-composition the characters live here, not in the control's value.
    """
    himc = imm32.ImmGetContext(hwnd)
    if not himc:
        return ""
    try:
        size = imm32.ImmGetCompositionStringW(himc, index, None, 0)
        if size <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(size // 2 + 1)
        imm32.ImmGetCompositionStringW(himc, index, buf, size)
        return buf.value
    finally:
        imm32.ImmReleaseContext(hwnd, himc)


def get_keyboard_layout(hwnd: int | None = None) -> int:
    """
    Returns the active keyboard layout (HKL) for a window's thread, or this thread's.

    Per-window IME state is a property of the *thread* owning the window, which is
    why this takes an hwnd rather than assuming the caller's thread.
    """
    if hwnd is None:
        return int(user32.GetKeyboardLayout(0) or 0)
    thread_id = user32.GetWindowThreadProcessId(hwnd, None)
    return int(user32.GetKeyboardLayout(thread_id) or 0)


def get_keyboard_layout_list() -> list[int]:
    """Returns every keyboard layout (HKL) currently loaded in the session."""
    count = user32.GetKeyboardLayoutList(0, None)
    if count <= 0:
        return []
    arr = (wintypes.HKL * count)()
    got = user32.GetKeyboardLayoutList(count, arr)
    return [int(arr[i]) for i in range(got)]


def activate_keyboard_layout(hkl: int, flags: int = 0) -> int:
    """Activates a keyboard layout for the calling thread; returns the previous HKL."""
    return int(user32.ActivateKeyboardLayout(wintypes.HKL(hkl), flags) or 0)


def load_keyboard_layout(layout_id: str, flags: int = 1) -> int:
    """Loads a layout by identifier (e.g. "00000404" for zh-TW); returns its HKL."""
    return int(user32.LoadKeyboardLayoutW(layout_id, flags) or 0)


def send_mouse_click(x: int, y: int, move_event: bool = True):
    """
    Positions the cursor and performs a standard left mouse click.

    `SetCursorPos` moves the pointer without producing an input event, so anything
    watching the mouse — a low-level hook, an overlay drawing where clicks land,
    an application tracking hover — never learns the pointer moved. It sees a
    click at whatever position it last knew about.

    So an absolute `MOUSEEVENTF_MOVE` is injected as well, which is also closer to
    what a real click looks like: a user's click is always preceded by movement,
    and some applications only update hover state on `WM_MOUSEMOVE` before acting
    on the button. `move_event=False` returns to the older behaviour for a caller
    that specifically does not want the extra event.

    `SetCursorPos` still runs, and last, because the normalized coordinates the
    move event carries are quantized to 1/65535 of the virtual desktop — close
    enough to be invisible, but this way the final pointer position is exactly
    the one that was asked for rather than the rounded one.
    """
    if move_event:
        # Normalize to the virtual desktop rather than the primary monitor:
        # MOUSEEVENTF_ABSOLUTE alone maps 0..65535 onto the primary display, so
        # on a multi-monitor runner a click meant for a secondary monitor lands
        # on the primary one instead.
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) or 1
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) or 1
        nx = int(round((x - vx) * 65535 / vw))
        ny = int(round((y - vy) * 65535 / vh))
        inp_move = INPUT(
            type=INPUT_MOUSE,
            u=_INPUT_UNION(
                mi=MOUSEINPUT(
                    dx=max(0, min(65535, nx)),
                    dy=max(0, min(65535, ny)),
                    mouseData=0,
                    dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )
        move_arr = (INPUT * 1)(inp_move)
        user32.SendInput(1, move_arr, ctypes.sizeof(INPUT))

    user32.SetCursorPos(x, y)
    inp_down = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(
            mi=MOUSEINPUT(
                dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_LEFTDOWN, time=0, dwExtraInfo=0
            )
        ),
    )
    inp_up = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(
            mi=MOUSEINPUT(
                dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_LEFTUP, time=0, dwExtraInfo=0
            )
        ),
    )
    arr = (INPUT * 2)(inp_down, inp_up)
    user32.SendInput(2, arr, ctypes.sizeof(INPUT))


def _send_mouse_move(x: int, y: int):
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) or 1
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) or 1
    nx = int(round((x - vx) * 65535 / vw))
    ny = int(round((y - vy) * 65535 / vh))
    inp_move = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(
            mi=MOUSEINPUT(
                dx=max(0, min(65535, nx)),
                dy=max(0, min(65535, ny)),
                mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    move_arr = (INPUT * 1)(inp_move)
    user32.SendInput(1, move_arr, ctypes.sizeof(INPUT))
    user32.SetCursorPos(x, y)


def send_mouse_right_click(x: int, y: int, move_event: bool = True):
    """Positions the cursor and performs a standard right mouse click."""
    if move_event:
        _send_mouse_move(x, y)
    else:
        user32.SetCursorPos(x, y)

    inp_down = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(
            mi=MOUSEINPUT(
                dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_RIGHTDOWN, time=0, dwExtraInfo=0
            )
        ),
    )
    inp_up = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(
            mi=MOUSEINPUT(
                dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_RIGHTUP, time=0, dwExtraInfo=0
            )
        ),
    )
    arr = (INPUT * 2)(inp_down, inp_up)
    user32.SendInput(2, arr, ctypes.sizeof(INPUT))


def send_mouse_middle_click(x: int, y: int, move_event: bool = True):
    """Positions the cursor and performs a middle mouse button click."""
    if move_event:
        _send_mouse_move(x, y)
    else:
        user32.SetCursorPos(x, y)

    inp_down = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(
            mi=MOUSEINPUT(
                dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_MIDDLEDOWN, time=0, dwExtraInfo=0
            )
        ),
    )
    inp_up = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(
            mi=MOUSEINPUT(
                dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_MIDDLEUP, time=0, dwExtraInfo=0
            )
        ),
    )
    arr = (INPUT * 2)(inp_down, inp_up)
    user32.SendInput(2, arr, ctypes.sizeof(INPUT))


def send_mouse_down(button: str = "left", x: int | None = None, y: int | None = None):
    """Presses and holds the specified mouse button ('left', 'right', 'middle')."""
    if x is not None and y is not None:
        _send_mouse_move(x, y)

    btn = button.lower().strip()
    if btn == "left":
        flag = MOUSEEVENTF_LEFTDOWN
    elif btn == "right":
        flag = MOUSEEVENTF_RIGHTDOWN
    elif btn == "middle":
        flag = MOUSEEVENTF_MIDDLEDOWN
    else:
        raise ValueError(f"Unknown mouse button {button!r}. Choose 'left', 'right', or 'middle'.")

    inp = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(mi=MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0)),
    )
    user32.SendInput(1, (INPUT * 1)(inp), ctypes.sizeof(INPUT))


def send_mouse_up(button: str = "left", x: int | None = None, y: int | None = None):
    """Releases the specified mouse button ('left', 'right', 'middle')."""
    if x is not None and y is not None:
        _send_mouse_move(x, y)

    btn = button.lower().strip()
    if btn == "left":
        flag = MOUSEEVENTF_LEFTUP
    elif btn == "right":
        flag = MOUSEEVENTF_RIGHTUP
    elif btn == "middle":
        flag = MOUSEEVENTF_MIDDLEUP
    else:
        raise ValueError(f"Unknown mouse button {button!r}. Choose 'left', 'right', or 'middle'.")

    inp = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(mi=MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0)),
    )
    user32.SendInput(1, (INPUT * 1)(inp), ctypes.sizeof(INPUT))


def send_mouse_move(x: int, y: int, steps: int = 1, delay: float = 0.0):
    """Moves the cursor to (x, y) with optional intermediate interpolation steps."""
    if steps <= 1:
        _send_mouse_move(x, y)
        if delay > 0:
            time.sleep(delay)
        return

    # Read current position for interpolation
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        start_x, start_y = x, y
    else:
        start_x, start_y = pt.x, pt.y

    for i in range(1, steps + 1):
        cur_x = int(round(start_x + (x - start_x) * (i / steps)))
        cur_y = int(round(start_y + (y - start_y) * (i / steps)))
        _send_mouse_move(cur_x, cur_y)
        if delay > 0:
            time.sleep(delay)


def send_mouse_hwheel(
    delta: int, x: int | None = None, y: int | None = None, move_event: bool = True
):
    """Sends a horizontal mouse wheel event (positive = scroll right, negative = scroll left)."""
    if x is not None and y is not None:
        if move_event:
            _send_mouse_move(x, y)
        else:
            user32.SetCursorPos(x, y)

    inp_wheel = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(
            mi=MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=int(delta),
                dwFlags=MOUSEEVENTF_HWHEEL,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    user32.SendInput(1, (INPUT * 1)(inp_wheel), ctypes.sizeof(INPUT))


def send_mouse_double_click(x: int, y: int, move_event: bool = True, interval: float = 0.05):
    """Positions the cursor and performs a double left mouse click."""
    send_mouse_click(x, y, move_event=move_event)
    time.sleep(interval)
    send_mouse_click(x, y, move_event=False)


def send_mouse_wheel(
    delta: int, x: int | None = None, y: int | None = None, move_event: bool = True
):
    """
    Sends a vertical mouse wheel event (positive = scroll up, negative = scroll down).

    Each standard notch is WHEEL_DELTA (120).
    """
    if x is not None and y is not None:
        if move_event:
            _send_mouse_move(x, y)
        else:
            user32.SetCursorPos(x, y)

    inp_wheel = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(
            mi=MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=int(delta),
                dwFlags=MOUSEEVENTF_WHEEL,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    arr = (INPUT * 1)(inp_wheel)
    user32.SendInput(1, arr, ctypes.sizeof(INPUT))


def send_mouse_drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    steps: int = 10,
    delay: float = 0.01,
):
    """Smoothly drags the mouse from start coordinates to end coordinates with the left button held."""
    _send_mouse_move(start_x, start_y)
    time.sleep(delay)

    # Press left button down
    inp_down = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(
            mi=MOUSEINPUT(
                dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_LEFTDOWN, time=0, dwExtraInfo=0
            )
        ),
    )
    user32.SendInput(1, (INPUT * 1)(inp_down), ctypes.sizeof(INPUT))
    time.sleep(delay)

    # Interpolate movement
    for i in range(1, steps + 1):
        cur_x = int(round(start_x + (end_x - start_x) * (i / steps)))
        cur_y = int(round(start_y + (end_y - start_y) * (i / steps)))
        _send_mouse_move(cur_x, cur_y)
        time.sleep(delay)

    # Release left button
    inp_up = INPUT(
        type=INPUT_MOUSE,
        u=_INPUT_UNION(
            mi=MOUSEINPUT(
                dx=0, dy=0, mouseData=0, dwFlags=MOUSEEVENTF_LEFTUP, time=0, dwExtraInfo=0
            )
        ),
    )
    user32.SendInput(1, (INPUT * 1)(inp_up), ctypes.sizeof(INPUT))


def get_input_desktop_handle() -> wintypes.HANDLE | None:
    """Returns handle to active Input Desktop, or None if unavailable."""
    try:
        h = user32.OpenInputDesktop(0, False, DESKTOP_ALL)
        if h:
            return h
    except Exception:
        pass
    return None


def attach_to_input_desktop() -> bool:
    """
    Attaches current thread to the active Input Desktop (e.g. 'Default').
    Crucial in CI / agent environments where the runner thread may spawn attached to an isolated desktop.
    """
    try:
        input_desk = user32.OpenInputDesktop(0, False, DESKTOP_ALL)
        if input_desk:
            return bool(user32.SetThreadDesktop(input_desk))
    except Exception as exc:
        logger.debug(f"SetThreadDesktop failed ({type(exc).__name__}): {exc}")
    return False


def get_window_title(hwnd: int) -> str:
    """Gets the window text/title for a given HWND."""
    length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(wintypes.HWND(hwnd), buf, length + 1)
    return buf.value


def get_window_class(hwnd: int) -> str:
    """Gets the window class name for a given HWND."""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
    return buf.value


def find_child_by_control_id(hwnd: int, control_id: int, visible_only: bool = True) -> int | None:
    """A child window by its numeric control id.

    Not `GetDlgItem`, and the difference matters on a property sheet: it keeps
    every page it has visited as a child of the same dialog, so an id resolves
    on pages that are no longer showing. `visible_only` is what makes "the
    control the user is looking at" answerable.

    Numeric control ids are also the one identifier on a Win32 dialog that is
    not translated, which makes them the only reliable handle on a localised
    build — the button captions are not.
    """
    if not hwnd or not user32.IsWindow(hwnd):
        return None

    found: list[int] = []

    def enum_proc(child, _lparam):
        if user32.GetDlgCtrlID(child) == control_id:
            if not visible_only or user32.IsWindowVisible(child):
                found.append(child)
                return False
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(enum_proc), 0)
    return found[0] if found else None


def find_child_windows(hwnd: int, class_substrings: tuple[str, ...]) -> list[int]:
    """Child HWNDs of `hwnd` whose class name contains any of `class_substrings`.

    Substring rather than exact match: the framework-owned child window classes
    this is used for carry versioned or namespaced names, and only the namespace
    prefix is stable across releases.
    """
    found: list[int] = []

    def enum_proc(child, _lparam):
        try:
            cls_name = get_window_class(child)
            if any(s in cls_name for s in class_substrings):
                found.append(child)
        except Exception:
            pass
        return True

    user32.EnumChildWindows(wintypes.HWND(hwnd), WNDENUMPROC(enum_proc), 0)
    return found


def describe_dialog_contents(hwnd: int, limit: int = 14) -> list[str]:
    """The text of a dialog's child controls, as "ClassName: text" lines.

    A window census answers "what appeared"; this answers "what does it say".
    For an unexpected dialog sitting on a CI machine — an update prompt, a
    security warning, a licence agreement — the title is rarely enough to act on
    and nobody can look at the screen.

    Read with `WM_GETTEXT` rather than through UIA. It is a *system* message, so
    USER32 marshals the buffer across the process boundary; a custom message
    carrying a pointer would not survive the trip. It also needs no COM, which
    matters here because this runs while something has already gone wrong.

    Controls with no text are skipped: a dialog is mostly invisible layout, and
    the lines worth reading are the static text and the buttons.
    """
    # `EnumChildWindows(NULL, ...)` enumerates every top-level window on the
    # desktop, so a handle that has already been destroyed would come back as a
    # census of the whole machine rather than as nothing.
    if not hwnd or not user32.IsWindow(hwnd):
        return []

    found: list[str] = []

    def enum_proc(child, _lparam):
        if len(found) >= limit:
            return False
        try:
            cls_name = get_window_class(child)
            length = user32.GetWindowTextLengthW(wintypes.HWND(child))
            if length <= 0:
                # A control can refuse GetWindowTextLength and still answer
                # WM_GETTEXT — an owner-drawn SysLink is the usual case.
                buf = ctypes.create_unicode_buffer(512)
                user32.SendMessageW(wintypes.HWND(child), WM_GETTEXT, 512, buf)
                text = buf.value
            else:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(wintypes.HWND(child), buf, length + 1)
                text = buf.value
            text = " ".join(text.split())
            if text:
                found.append(f"{cls_name}: {text[:120]}")
        except Exception:
            pass
        return True

    try:
        user32.EnumChildWindows(wintypes.HWND(hwnd), WNDENUMPROC(enum_proc), 0)
    except Exception as exc:
        logger.debug(f"describe_dialog_contents failed for {hwnd:#x}: {exc}")
    return found


def get_window_pid(hwnd: int) -> int:
    """Gets the process ID associated with a given HWND."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return pid.value


kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def get_process_image_name(pid: int) -> str:
    """
    Returns the executable basename (lowercase, e.g. 'notepad.exe') for a PID,
    or "" if the process cannot be opened. Locale-independent, unlike window titles.
    """
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    except Exception:
        return ""
    finally:
        kernel32.CloseHandle(handle)


TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def get_parent_pid_map() -> dict[int, int]:
    """Returns {pid: parent_pid} for every process, via a Toolhelp snapshot."""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        raise OSError(f"CreateToolhelp32Snapshot failed (GetLastError={ctypes.get_last_error()})")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        parents: dict[int, int] = {}
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise OSError(f"Process32FirstW failed (GetLastError={ctypes.get_last_error()})")
        while True:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return parents
    finally:
        kernel32.CloseHandle(snapshot)


def find_pids_by_image_name(names: tuple[str, ...] | list[str]) -> set[int]:
    """PIDs of every running process whose image name is in `names`, case-insensitive.

    A packaged app outlives its windows: measured on Windows 11 ARM64, Notepad's
    windows disappear roughly 45ms before its processes do, and the singleton
    identity belongs to the process. A sweep that only waits for windows can hand
    back control while the dying instance can still absorb the next launch.
    """
    wanted = {n.lower() for n in names}
    if not wanted:
        return set()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        raise OSError(f"CreateToolhelp32Snapshot failed (GetLastError={ctypes.get_last_error()})")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        found: set[int] = set()
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return found
        while True:
            if entry.szExeFile.lower() in wanted:
                found.add(entry.th32ProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return found
    finally:
        kernel32.CloseHandle(snapshot)


def get_ancestor_pids(pid: int | None = None) -> set[int]:
    """
    Returns `pid` plus every ancestor PID, walking parent links from a snapshot.

    PIDs are recycled, so a stale parent link can point at an unrelated process and
    in principle form a cycle; the walk is bounded and cycle-guarded rather than
    trusting the chain to terminate.
    """
    pid = os.getpid() if pid is None else pid
    parents = get_parent_pid_map()
    chain = {pid}
    current = pid
    for _ in range(64):
        parent = parents.get(current)
        if not parent or parent in chain:
            break
        chain.add(parent)
        current = parent
    return chain


def get_foreground_window() -> int:
    """Gets the HWND of the current foreground window."""
    return user32.GetForegroundWindow()


# --- Touch injection ---------------------------------------------------------
#
# Injected touch goes through a synthetic pointer device: Windows creates a
# virtual digitizer on request and forwards the contacts we describe to it as if
# a real one had reported them. Two APIs do this. `CreateSyntheticPointerDevice`
# (Windows 10 1809+) is the current one and also handles pen;
# `InitializeTouchInjection` (Windows 8+) is the older touch-only path and is
# kept as a fallback, since both were measured working on every host that
# delivers touch at all.
#
# What the return values do *not* tell you is whether anything received the
# contact. On a runner whose desktop is covered by a full-screen onboarding
# window, all three injection calls report success and no window sees a thing --
# which is why `touch.py` verifies delivery rather than trusting these.

PT_TOUCH = 2
PT_PEN = 3

#: Feedback modes for a synthetic pointer device. NONE draws no visual touch
#: indicator, which is what automation wants: the indicator is the OS drawing on
#: top of the app under test.
POINTER_FEEDBACK_DEFAULT = 1
POINTER_FEEDBACK_INDIRECT = 2
POINTER_FEEDBACK_NONE = 3

TOUCH_FEEDBACK_DEFAULT = 0x1
TOUCH_FEEDBACK_INDIRECT = 0x2
TOUCH_FEEDBACK_NONE = 0x3

POINTER_FLAG_NONE = 0x00000000
POINTER_FLAG_NEW = 0x00000001
POINTER_FLAG_INRANGE = 0x00000002
POINTER_FLAG_INCONTACT = 0x00000004
POINTER_FLAG_DOWN = 0x00010000
POINTER_FLAG_UPDATE = 0x00020000
POINTER_FLAG_UP = 0x00040000

TOUCH_FLAG_NONE = 0x00000000
TOUCH_MASK_NONE = 0x00000000
TOUCH_MASK_CONTACTAREA = 0x00000001
TOUCH_MASK_ORIENTATION = 0x00000002
TOUCH_MASK_PRESSURE = 0x00000004

#: Pressure is 0..1024 in the API. Real digitizers report a wide range; this is
#: a firm, unremarkable press.
TOUCH_DEFAULT_PRESSURE = 512

#: Half-width of the square contact patch, in pixels. A contact with no area is
#: accepted but describes a fingertip infinitely small, and some gesture
#: recognisers use the area.
TOUCH_DEFAULT_CONTACT_RADIUS = 4

#: The most contacts a synthetic device is asked for. Ten is what Windows
#: reports for its own touch hardware and more than any gesture here needs.
TOUCH_MAX_CONTACTS = 10


class POINTER_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerType", wintypes.DWORD),
        ("pointerId", ctypes.c_uint32),
        ("frameId", ctypes.c_uint32),
        ("pointerFlags", ctypes.c_uint32),
        ("sourceDevice", wintypes.HANDLE),
        ("hwndTarget", wintypes.HWND),
        ("ptPixelLocation", POINT),
        ("ptHimetricLocation", POINT),
        ("ptPixelLocationRaw", POINT),
        ("ptHimetricLocationRaw", POINT),
        ("dwTime", wintypes.DWORD),
        ("historyCount", ctypes.c_uint32),
        ("InputData", ctypes.c_int32),
        ("dwKeyStates", wintypes.DWORD),
        ("PerformanceCount", ctypes.c_uint64),
        ("ButtonChangeType", ctypes.c_int),
    ]


class POINTER_TOUCH_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerInfo", POINTER_INFO),
        ("touchFlags", ctypes.c_uint32),
        ("touchMask", ctypes.c_uint32),
        ("rcContact", RECT),
        ("rcContactRaw", RECT),
        ("orientation", ctypes.c_uint32),
        ("pressure", ctypes.c_uint32),
    ]


class POINTER_PEN_INFO(ctypes.Structure):
    """Not used for touch, but the union below has to be the size Windows expects."""

    _fields_ = [
        ("pointerInfo", POINTER_INFO),
        ("penFlags", ctypes.c_uint32),
        ("penMask", ctypes.c_uint32),
        ("pressure", ctypes.c_uint32),
        ("rotation", ctypes.c_uint32),
        ("tiltX", ctypes.c_int32),
        ("tiltY", ctypes.c_int32),
    ]


class _POINTER_TYPE_INFO_UNION(ctypes.Union):
    _fields_ = [("touchInfo", POINTER_TOUCH_INFO), ("penInfo", POINTER_PEN_INFO)]


class POINTER_TYPE_INFO(ctypes.Structure):
    """What `InjectSyntheticPointerInput` takes -- not `POINTER_TOUCH_INFO` itself."""

    _fields_ = [("type", wintypes.DWORD), ("u", _POINTER_TYPE_INFO_UNION)]
    _anonymous_ = ("u",)


if sys.platform == "win32":
    # Handle-returning calls are declared, without exception. An undeclared
    # restype makes ctypes convert a 64-bit handle as c_int, and above 2^31 that
    # is an OverflowError or an access violation rather than a wrong number --
    # this project has lost three separate debugging sessions to it.
    #
    # These four are looked up softly: `CreateSyntheticPointerDevice` does not
    # exist before Windows 10 1809, and its absence is a capability answer, not
    # an import error.
    for _fn_name, _fn_argtypes, _fn_restype in (
        (
            "CreateSyntheticPointerDevice",
            [wintypes.DWORD, ctypes.c_ulong, ctypes.c_int],
            ctypes.c_void_p,
        ),
        (
            "InjectSyntheticPointerInput",
            [ctypes.c_void_p, ctypes.POINTER(POINTER_TYPE_INFO), ctypes.c_uint32],
            wintypes.BOOL,
        ),
        ("DestroySyntheticPointerDevice", [ctypes.c_void_p], None),
        ("InitializeTouchInjection", [ctypes.c_uint32, wintypes.DWORD], wintypes.BOOL),
        (
            "InjectTouchInput",
            [ctypes.c_uint32, ctypes.POINTER(POINTER_TOUCH_INFO)],
            wintypes.BOOL,
        ),
    ):
        try:
            _fn = getattr(user32, _fn_name)
        except AttributeError:
            logger.debug(f"{_fn_name} is not exported by this user32; touch may be limited")
            continue
        _fn.argtypes = _fn_argtypes
        _fn.restype = _fn_restype


def touch_injection_api_available() -> bool:
    """Whether either injection entry point exists at all.

    A `True` here means the API is present, nothing more. Whether an injected
    contact reaches a window is a separate question with a different answer on
    some hosts, and `Touch.available()` is the one that measures it.
    """
    if sys.platform != "win32":
        return False
    return hasattr(user32, "CreateSyntheticPointerDevice") or hasattr(
        user32, "InitializeTouchInjection"
    )


def create_touch_device(
    max_contacts: int = TOUCH_MAX_CONTACTS, feedback: int = POINTER_FEEDBACK_NONE
) -> int | None:
    """Creates a synthetic touch digitizer, or returns None with the reason logged.

    `feedback` defaults to NONE so Windows draws no touch indicator: the
    indicator is the OS painting over the application under test, which is
    exactly what a recording should not contain.
    """
    if sys.platform != "win32" or not hasattr(user32, "CreateSyntheticPointerDevice"):
        return None
    ctypes.set_last_error(0)
    handle = user32.CreateSyntheticPointerDevice(PT_TOUCH, max_contacts, feedback)
    if not handle:
        logger.debug(
            f"CreateSyntheticPointerDevice failed (GetLastError={ctypes.get_last_error()})"
        )
        return None
    return int(handle)


def destroy_touch_device(handle: int | None) -> None:
    if sys.platform == "win32" and handle and hasattr(user32, "DestroySyntheticPointerDevice"):
        user32.DestroySyntheticPointerDevice(ctypes.c_void_p(handle))


def initialize_legacy_touch_injection(max_contacts: int = TOUCH_MAX_CONTACTS) -> bool:
    """Sets up the Windows 8 touch injection path, used when no device handle exists."""
    if sys.platform != "win32" or not hasattr(user32, "InitializeTouchInjection"):
        return False
    ctypes.set_last_error(0)
    if user32.InitializeTouchInjection(max_contacts, TOUCH_FEEDBACK_NONE):
        return True
    logger.debug(f"InitializeTouchInjection failed (GetLastError={ctypes.get_last_error()})")
    return False


def make_touch_contact(
    x: int,
    y: int,
    flags: int,
    pointer_id: int = 0,
    pressure: int = TOUCH_DEFAULT_PRESSURE,
    radius: int = TOUCH_DEFAULT_CONTACT_RADIUS,
) -> POINTER_TOUCH_INFO:
    """One contact in one frame. `flags` is the POINTER_FLAG_* combination."""
    contact = POINTER_TOUCH_INFO()
    contact.pointerInfo.pointerType = PT_TOUCH
    contact.pointerInfo.pointerId = pointer_id
    contact.pointerInfo.pointerFlags = flags
    contact.pointerInfo.ptPixelLocation.x = x
    contact.pointerInfo.ptPixelLocation.y = y
    contact.touchFlags = TOUCH_FLAG_NONE
    contact.touchMask = TOUCH_MASK_CONTACTAREA | TOUCH_MASK_PRESSURE
    contact.rcContact.left = x - radius
    contact.rcContact.right = x + radius
    contact.rcContact.top = y - radius
    contact.rcContact.bottom = y + radius
    contact.pressure = pressure
    return contact


def inject_touch_frame(device: int | None, contacts: list[POINTER_TOUCH_INFO]) -> bool:
    """Sends one frame describing every contact currently on the digitizer.

    A frame is the complete picture, not a delta: a contact left out of it is a
    finger lifted. Returns whether the system accepted the frame -- which is not
    the same as anything having received it.
    """
    if not contacts:
        return True
    if sys.platform != "win32":
        return False
    ctypes.set_last_error(0)
    if device is not None:
        typed = (POINTER_TYPE_INFO * len(contacts))()
        for i, contact in enumerate(contacts):
            typed[i].type = PT_TOUCH
            typed[i].touchInfo = contact
        ok = bool(user32.InjectSyntheticPointerInput(ctypes.c_void_p(device), typed, len(typed)))
        if not ok:
            logger.debug(
                f"InjectSyntheticPointerInput refused {len(typed)} contact(s) "
                f"(GetLastError={ctypes.get_last_error()})"
            )
        return ok
    array = (POINTER_TOUCH_INFO * len(contacts))(*contacts)
    ok = bool(user32.InjectTouchInput(len(array), array))
    if not ok:
        logger.debug(
            f"InjectTouchInput refused {len(array)} contact(s) "
            f"(GetLastError={ctypes.get_last_error()})"
        )
    return ok
