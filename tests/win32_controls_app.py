"""A dialog holding a real TreeView and a real report-mode ListView, as a fixture.

Run as a subprocess (`python tests/win32_controls_app.py`).

These two common controls are what makes the grid and tree work testable without
a compiler or a WPF toolchain: Windows' own UIA providers expose
`SysListView32` in report mode through Grid / Table / GridItem / TableItem, and
`SysTreeView32` through TreeItem with ExpandCollapse, SelectionItem and
ScrollItem. Those are exactly the patterns the control wrappers drive, so the
tests exercise the real providers rather than a mock of what they might do.

The tree is deliberately three levels deep and the grid deliberately taller than
its own viewport, so navigation has ancestors to expand and cells to scroll to.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
else:  # pragma: no cover - collection-only path
    user32 = kernel32 = comctl32 = None

WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_BORDER = 0x00800000
WS_TABSTOP = 0x00010000
WS_VSCROLL = 0x00200000
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_EX_DLGMODALFRAME = 0x00000001

TVS_HASBUTTONS = 0x0001
TVS_HASLINES = 0x0002
TVS_LINESATROOT = 0x0004
LVS_REPORT = 0x0001
LVS_SINGLESEL = 0x0004

WM_QUIT = 0x0012
PM_REMOVE = 0x0001

# TreeView
TV_FIRST = 0x1100
TVM_INSERTITEMW = TV_FIRST + 50
TVIF_TEXT = 0x0001
# TVI_ROOT and TVI_LAST are negative pointer-sized constants: (ULONG_PTR)-0x10000
# and (ULONG_PTR)-0xFFFE. Writing them as 32-bit literals leaves the high half
# clear on 64-bit Windows, and TVM_INSERTITEMW then rejects the parent handle.
_PTR_MASK = (1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1
TVI_ROOT = (-0x10000) & _PTR_MASK
TVI_LAST = (-0xFFFE) & _PTR_MASK

# ListView
LVM_FIRST = 0x1000
LVM_INSERTCOLUMNW = LVM_FIRST + 97
LVM_INSERTITEMW = LVM_FIRST + 77
LVM_SETITEMW = LVM_FIRST + 76
LVM_SETEXTENDEDLISTVIEWSTYLE = LVM_FIRST + 54
LVS_EX_FULLROWSELECT = 0x00000020
LVS_EX_GRIDLINES = 0x00000001
LVCF_TEXT = 0x0004
LVCF_WIDTH = 0x0002
LVCF_SUBITEM = 0x0008
LVIF_TEXT = 0x0001

ICC_TREEVIEW_CLASSES = 0x00000002
ICC_LISTVIEW_CLASSES = 0x00000001

DIALOG_TITLE = "wintegrate controls fixture"

ID_TREE = 2001
ID_GRID = 2002

# What the fixture builds, so tests assert against one definition.
TREE_DATA = {
    "Root A": {"Category A1": ["Item A1a", "Item A1b"], "Category A2": ["Item A2a"]},
    "Root B": {"Category B1": ["Item B1a"]},
}
GRID_COLUMNS = ["Name", "Kind", "Status"]
GRID_ROWS = [
    ["alpha", "widget", "ready"],
    ["beta", "gadget", "ready"],
    ["gamma", "widget", "failed"],
    ["delta", "gizmo", "ready"],
    ["epsilon", "widget", "pending"],
    ["zeta", "gadget", "ready"],
    ["eta", "gizmo", "failed"],
    ["theta", "widget", "ready"],
    ["iota", "gadget", "pending"],
    ["kappa", "gizmo", "ready"],
    ["lambda", "widget", "ready"],
    ["mu", "gadget", "failed"],
]


class INITCOMMONCONTROLSEX(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("dwICC", wintypes.DWORD)]


class TVITEMW(ctypes.Structure):
    _fields_ = [
        ("mask", wintypes.UINT),
        ("hItem", wintypes.HANDLE),
        ("state", wintypes.UINT),
        ("stateMask", wintypes.UINT),
        ("pszText", wintypes.LPWSTR),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("iSelectedImage", ctypes.c_int),
        ("cChildren", ctypes.c_int),
        ("lParam", ctypes.c_ssize_t),
    ]


class TVINSERTSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hParent", wintypes.HANDLE),
        ("hInsertAfter", wintypes.HANDLE),
        ("item", TVITEMW),
    ]


class LVCOLUMNW(ctypes.Structure):
    _fields_ = [
        ("mask", wintypes.UINT),
        ("fmt", ctypes.c_int),
        ("cx", ctypes.c_int),
        ("pszText", wintypes.LPWSTR),
        ("cchTextMax", ctypes.c_int),
        ("iSubItem", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("iOrder", ctypes.c_int),
    ]


class LVITEMW(ctypes.Structure):
    _fields_ = [
        ("mask", wintypes.UINT),
        ("iItem", ctypes.c_int),
        ("iSubItem", ctypes.c_int),
        ("state", wintypes.UINT),
        ("stateMask", wintypes.UINT),
        ("pszText", wintypes.LPWSTR),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("lParam", ctypes.c_ssize_t),
        ("iIndent", ctypes.c_int),
        ("iGroupId", ctypes.c_int),
        ("cColumns", wintypes.UINT),
        ("puColumns", ctypes.c_void_p),
        ("piColFmt", ctypes.c_void_p),
        ("iGroup", ctypes.c_int),
    ]


def _declare() -> None:
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


def _create(cls: str, style: int, x: int, y: int, w: int, h: int, parent, ctrl_id: int):
    hwnd = user32.CreateWindowExW(
        0,
        cls,
        "",
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


def _insert_tree_item(tree, parent, text: str):
    ins = TVINSERTSTRUCTW()
    ins.hParent = parent
    ins.hInsertAfter = TVI_LAST
    ins.item.mask = TVIF_TEXT
    buf = ctypes.create_unicode_buffer(text)
    ins.item.pszText = ctypes.cast(buf, wintypes.LPWSTR)
    handle = user32.SendMessageW(tree, TVM_INSERTITEMW, 0, ctypes.addressof(ins))
    if not handle:
        raise OSError(f"TVM_INSERTITEMW failed for {text!r}")
    return handle


def _populate_tree(tree) -> None:
    for root_text, categories in TREE_DATA.items():
        root = _insert_tree_item(tree, TVI_ROOT, root_text)
        for cat_text, leaves in categories.items():
            cat = _insert_tree_item(tree, root, cat_text)
            for leaf in leaves:
                _insert_tree_item(tree, cat, leaf)


def _populate_grid(grid) -> None:
    user32.SendMessageW(
        grid, LVM_SETEXTENDEDLISTVIEWSTYLE, 0, LVS_EX_FULLROWSELECT | LVS_EX_GRIDLINES
    )

    for index, title in enumerate(GRID_COLUMNS):
        col = LVCOLUMNW()
        col.mask = LVCF_TEXT | LVCF_WIDTH | LVCF_SUBITEM
        col.cx = 120
        buf = ctypes.create_unicode_buffer(title)
        col.pszText = ctypes.cast(buf, wintypes.LPWSTR)
        col.iSubItem = index
        if user32.SendMessageW(grid, LVM_INSERTCOLUMNW, index, ctypes.addressof(col)) < 0:
            raise OSError(f"LVM_INSERTCOLUMNW failed for {title!r}")

    for row_index, row in enumerate(GRID_ROWS):
        item = LVITEMW()
        item.mask = LVIF_TEXT
        item.iItem = row_index
        item.iSubItem = 0
        buf = ctypes.create_unicode_buffer(row[0])
        item.pszText = ctypes.cast(buf, wintypes.LPWSTR)
        if user32.SendMessageW(grid, LVM_INSERTITEMW, 0, ctypes.addressof(item)) < 0:
            raise OSError(f"LVM_INSERTITEMW failed for row {row_index}")

        for col_index, value in enumerate(row[1:], start=1):
            sub = LVITEMW()
            sub.mask = LVIF_TEXT
            sub.iItem = row_index
            sub.iSubItem = col_index
            sub_buf = ctypes.create_unicode_buffer(value)
            sub.pszText = ctypes.cast(sub_buf, wintypes.LPWSTR)
            user32.SendMessageW(grid, LVM_SETITEMW, 0, ctypes.addressof(sub))


def build() -> int:
    _declare()

    icc = INITCOMMONCONTROLSEX()
    icc.dwSize = ctypes.sizeof(INITCOMMONCONTROLSEX)
    icc.dwICC = ICC_TREEVIEW_CLASSES | ICC_LISTVIEW_CLASSES
    if not comctl32.InitCommonControlsEx(ctypes.byref(icc)):
        raise OSError("InitCommonControlsEx failed; tree and list classes unavailable")

    dlg = user32.CreateWindowExW(
        WS_EX_DLGMODALFRAME,
        "#32770",
        DIALOG_TITLE,
        WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        100,
        100,
        820,
        520,
        None,
        None,
        None,
        None,
    )
    if not dlg:
        raise OSError(f"Could not create #32770 dialog: {ctypes.get_last_error()}")

    tree = _create(
        "SysTreeView32",
        WS_BORDER | WS_TABSTOP | WS_VSCROLL | TVS_HASBUTTONS | TVS_HASLINES | TVS_LINESATROOT,
        20,
        20,
        320,
        440,
        dlg,
        ID_TREE,
    )
    _populate_tree(tree)

    # Shorter than its contents on purpose, so later rows start off-screen and
    # ScrollItemPattern has something to do.
    grid = _create(
        "SysListView32",
        WS_BORDER | WS_TABSTOP | LVS_REPORT | LVS_SINGLESEL,
        360,
        20,
        420,
        200,
        dlg,
        ID_GRID,
    )
    _populate_grid(grid)

    user32.ShowWindow(dlg, 5)  # SW_SHOW
    user32.UpdateWindow(dlg)
    user32.SetForegroundWindow(dlg)
    return dlg


def main() -> int:
    dlg = build()
    print(DIALOG_TITLE, flush=True)

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
