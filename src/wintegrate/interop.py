"""Win32 structures, constants, and ctypes foreign function interface."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

logger = logging.getLogger(__name__)

# Native DLL handles
if sys.platform == "win32":
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    imm32 = ctypes.windll.imm32
    gdi32 = ctypes.windll.gdi32
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


def send_char_input(char: str):
    """
    Sends character input reliably using native Win32 SendInput (KEYEVENTF_UNICODE).
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
        user32.SendInput(2, arr, ctypes.sizeof(INPUT))
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
        user32.SendInput(2, arr, ctypes.sizeof(INPUT))


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


def get_foreground_window() -> int:
    """Gets the HWND of the current foreground window."""
    return user32.GetForegroundWindow()
