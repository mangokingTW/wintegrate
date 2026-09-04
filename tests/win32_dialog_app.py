"""A standalone classic Win32 dialog, used as a test fixture.

Run as a subprocess (`python tests/win32_dialog_app.py`), it puts up a `#32770`
dialog holding the classic control set — Edit, ComboBox, CheckBox, ListBox,
Buttons — addressed by the same control IDs real dialog resources use.

Why a fixture rather than a system dialog: system dialogs are localized, differ
between Windows builds, and change without notice. This one is ours, so the
tests can assert on control ids and names without a locale dependency, and it
runs identically on x64 and ARM64.

It runs in a separate process on purpose: that is how UIA is used in practice
(cross-process), and it keeps the UI thread from being the same thread the
automation client blocks on.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

# Guarded so the module's constants stay importable off-Windows, where test
# collection still has to succeed even though the dialog itself cannot run.
if sys.platform == "win32":
    # Private handles for the same reason wintegrate uses them: this module
    # pins argtypes, and ctypes.windll would publish those to the whole process.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # pragma: no cover - collection-only path
    user32 = kernel32 = None

# Window styles
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_BORDER = 0x00800000
WS_TABSTOP = 0x00010000
WS_VSCROLL = 0x00200000
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_EX_DLGMODALFRAME = 0x00000001

# Control styles
BS_AUTOCHECKBOX = 0x00000003
BS_PUSHBUTTON = 0x00000000
CBS_DROPDOWNLIST = 0x00000003
ES_AUTOHSCROLL = 0x00000080
LBS_NOTIFY = 0x00000001

# Messages
CB_ADDSTRING = 0x0143
CB_SETCURSEL = 0x014E
LB_ADDSTRING = 0x0180
WM_QUIT = 0x0012
PM_REMOVE = 0x0001

WDA_EXCLUDEFROMCAPTURE = 0x11

DIALOG_TITLE = "wintegrate test dialog"

# Control ids mirror the shape of a real dialog resource.
ID_OK = 1
ID_LIST = 1001
ID_EDIT = 1002
ID_COMBO = 1003
ID_BROWSE = 1004
ID_CHECK_ONCE = 1011
ID_CHECK_DEFAULT = 1013
ID_COMBO_LANG = 1014


def _add_string(hwnd, msg: int, text: str) -> None:
    """Adds an item to a combo/list box. The lParam must be a pointer to the string."""
    buf = ctypes.create_unicode_buffer(text)
    user32.SendMessageW(hwnd, msg, 0, ctypes.addressof(buf))


def _create(cls: str, text: str, style: int, x: int, y: int, w: int, h: int, parent, ctrl_id: int):
    hwnd = user32.CreateWindowExW(
        0,
        cls,
        text,
        WS_CHILD | WS_VISIBLE | style,
        x,
        y,
        w,
        h,
        parent,
        ctypes.c_void_p(ctrl_id),
        None,
        None,
    )
    if not hwnd:
        raise OSError(f"CreateWindowExW({cls!r}) failed: {ctypes.get_last_error()}")
    return hwnd


def build_dialog() -> int:
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]

    # "#32770" is the system dialog class, so the container is a genuine dialog
    # window rather than something merely dialog-shaped.
    dlg = user32.CreateWindowExW(
        WS_EX_DLGMODALFRAME,
        "#32770",
        DIALOG_TITLE,
        WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        120,
        120,
        520,
        420,
        None,
        None,
        None,
        None,
    )
    if not dlg:
        raise OSError(f"Could not create #32770 dialog: {ctypes.get_last_error()}")

    _create("EDIT", "", WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL, 20, 20, 300, 24, dlg, ID_EDIT)
    _create("BUTTON", "Browse", WS_TABSTOP | BS_PUSHBUTTON, 340, 20, 120, 26, dlg, ID_BROWSE)

    combo = _create(
        "COMBOBOX", "", WS_TABSTOP | WS_VSCROLL | CBS_DROPDOWNLIST, 20, 60, 200, 200, dlg, ID_COMBO
    )
    for item in ("Alpha layout", "Beta layout", "Gamma layout"):
        _add_string(combo, CB_ADDSTRING, item)
    user32.SendMessageW(combo, CB_SETCURSEL, 0, 0)

    lang = _create(
        "COMBOBOX",
        "",
        WS_TABSTOP | WS_VSCROLL | CBS_DROPDOWNLIST,
        240,
        60,
        220,
        200,
        dlg,
        ID_COMBO_LANG,
    )
    for item in ("English", "Chinese", "Japanese"):
        _add_string(lang, CB_ADDSTRING, item)
    user32.SendMessageW(lang, CB_SETCURSEL, 0, 0)

    _create(
        "BUTTON", "Apply once", WS_TABSTOP | BS_AUTOCHECKBOX, 20, 100, 200, 24, dlg, ID_CHECK_ONCE
    )
    _create(
        "BUTTON",
        "Default enable",
        WS_TABSTOP | BS_AUTOCHECKBOX,
        240,
        100,
        220,
        24,
        dlg,
        ID_CHECK_DEFAULT,
    )

    lst = _create(
        "LISTBOX",
        "",
        WS_BORDER | WS_TABSTOP | WS_VSCROLL | LBS_NOTIFY,
        20,
        140,
        440,
        160,
        dlg,
        ID_LIST,
    )
    for item in ("rule one", "rule two", "rule three"):
        _add_string(lst, LB_ADDSTRING, item)

    _create("BUTTON", "Close", WS_TABSTOP | BS_PUSHBUTTON, 340, 320, 120, 30, dlg, ID_OK)

    user32.ShowWindow(dlg, 5)  # SW_SHOW
    user32.UpdateWindow(dlg)
    user32.SetForegroundWindow(dlg)
    return dlg


def _exclude_self_from_capture(dlg) -> None:
    """Asks Windows to withhold this window from screen captures.

    Done here rather than from the test, because `SetWindowDisplayAffinity` only
    works on windows of the calling process -- which is the whole reason nothing
    outside an application can undo it. Producing the state is the only way to
    test the reading of it.
    """
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    ok = user32.SetWindowDisplayAffinity(dlg, WDA_EXCLUDEFROMCAPTURE)
    # Printed either way: a silent failure here would make the test look like a
    # bug in the reading rather than an unsupported build. WDA_EXCLUDEFROMCAPTURE
    # needs Windows 10 2004 or later.
    print(f"EXCLUDE_FROM_CAPTURE={bool(ok)} error={ctypes.get_last_error()}", flush=True)


def main() -> int:
    dlg = build_dialog()
    if os.environ.get("WINTEGRATE_TEST_EXCLUDE_FROM_CAPTURE"):
        _exclude_self_from_capture(dlg)
    print(DIALOG_TITLE, flush=True)

    # Pump with PeekMessage rather than blocking in GetMessage: the system dialog
    # class posts no WM_QUIT when the window closes, so a blocking loop would
    # outlive the dialog and leave an orphan process behind on the runner.
    msg = wintypes.MSG()
    while user32.IsWindow(dlg):
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            if msg.message == WM_QUIT:
                return 0
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)
    return 0


if __name__ == "__main__":
    sys.exit(main())
