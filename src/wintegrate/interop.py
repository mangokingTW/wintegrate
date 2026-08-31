"""Win32 structures, constants, and ctypes foreign function interface."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes

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
WM_IME_CONTROL = 0x0283
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202

# IME Constants
IMC_GETCONVERSIONMODE = 0x0001
IMC_SETCONVERSIONMODE = 0x0002
IMC_GETOPENSTATUS = 0x0005
IMC_SETOPENSTATUS = 0x0006

# GetSystemMetrics indices
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

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
MOUSEEVENTF_ABSOLUTE = 0x8000

# Virtual Key Codes
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_SPACE = 0x20
VK_BACK = 0x08
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt

# IME control keys. These reach the IME itself rather than the focused control.
VK_KANA = 0x15  # also VK_HANGUL
VK_IME_ON = 0x16
VK_KANJI = 0x19  # also VK_HANJA
VK_IME_OFF = 0x1A
VK_CONVERT = 0x1C
VK_NONCONVERT = 0x1D
VK_PROCESSKEY = 0xE5  # what an IME reports while it owns a keystroke

# ImmGetConversionStatus mode flags
IME_CMODE_ALPHANUMERIC = 0x0000
IME_CMODE_NATIVE = 0x0001
IME_CMODE_KATAKANA = 0x0002
IME_CMODE_FULLSHAPE = 0x0008
IME_CMODE_ROMAN = 0x0010

# ImmGetCompositionString index
GCS_COMPSTR = 0x0008
GCS_RESULTSTR = 0x0800

MAPVK_VK_TO_VSC = 0

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

# Keys that must carry KEYEVENTF_EXTENDEDKEY to be delivered correctly.
_EXTENDED_VKS = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x5D, 0x2C}

# A repeat count above this is a typo rather than an intent: even at the default
# 20 ms per key, a thousand keystrokes already takes 20 seconds to send.
MAX_KEY_REPEAT = 1000

# Modifier prefixes, following the pywinauto/SendKeys convention.
_MODIFIER_PREFIXES = {"^": VK_CONTROL, "+": VK_SHIFT, "%": VK_MENU}


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

user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

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


def send_keys(spec: str, delay_per_key: float = 0.02) -> bool:
    """
    Sends a SendKeys-style spec: named keys in braces, `^`/`+`/`%` modifiers,
    everything else as literal Unicode text. See `parse_key_spec` for the grammar.

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


def get_ime_conversion(hwnd: int) -> int | None:
    """The IME conversion mode as the IME itself reports it, or None if unavailable."""
    for target in _ime_control_targets(hwnd):
        got = _ime_control(target, IMC_GETCONVERSIONMODE, 0)
        if got is not None:
            return got
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


def send_mouse_click(x: int, y: int):
    """Positions cursor and performs a standard left mouse click."""
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
