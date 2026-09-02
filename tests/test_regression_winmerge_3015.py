"""WinMerge #3015, reproduced against the build that had it.

`PropEditor.cpp` attached a `std::clamp(v, 1, MAX_TABSIZE)` validator to
`OPT_TAB_TYPE` when it was meant for `OPT_TAB_SIZE`. *Insert tabs* is tab type
**0**, which is below the clamp's lower bound, so choosing it wrote 1 — *Insert
spaces*. Fixed by commit edc1597 (2025-10-30); the maintainer closed the
duplicate report #3055 with "fixed in the latest version 2.16.52.2", which
shipped the same day #3015 was closed.

Measured, unpacked builds of the same flavour on both sides:

    2.16.52.2   choose Insert tabs -> TabType=0x0, Options reopens on tabs
    2.16.52     choose Insert tabs -> TabType=0x1, Options reopens on spaces

**The failure is asymmetric, and that is what makes it worth testing.** Choosing
*Insert spaces* writes 1, which is already inside the clamp range, so it works
on both builds. A test that only checked "the setting round-trips" would have
passed on the broken build half the time — so the spaces direction is asserted
too, as a control. Without it, a build that ignored the dialog entirely and
always wrote 1 would look identical to a fixed one.

Two things about driving this dialog are worth knowing:

- **There is no `HMENU`.** WinMerge 2.16 uses an MFC Feature Pack menu bar,
  which is a toolbar, so `GetMenu()` returns NULL and there is nothing to walk.
  UIA does see the items, but `Invoke()` on the top-level one *succeeds and
  opens nothing* — another call that reports success and leaves no result.
  `Alt+E` opens it; the popup is a plain `#32768` whose items UIA reads fine.
- **The page is found by what it contains, not by what it is called.** The
  Options tree has 27 nodes, all localised — `'編輯器'` on a Chinese desktop,
  `'Editor'` on an English one. Each node is clicked until control **1038**
  appears. Resource ids are not translated, which is the same reason Notepad++'s
  Find dialog is testable in any language while its toolbar is not.

  The menu item is reached the same way, and getting it wrong cost a CI round:
  the first version matched `'(O)'`, the parenthesised mnemonic. That reads as
  language-independent on a Chinese desktop and is a CJK convention — English
  Windows writes `&Options...`. What both spellings share is the accelerator
  `Ctrl+,`, so that is what is matched.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from wintegrate import Window
from wintegrate.apps import sweep_processes_verified
from wintegrate.diagnostics import WindowCensus
from wintegrate.interop import send_keys

# Not part of the release gate; see tests/test_regression_notepadpp_16326.py.
pytestmark = [
    pytest.mark.upstream_bug,
    pytest.mark.target_app,
    pytest.mark.skipif(sys.platform != "win32", reason="drives a live Win32 application"),
]

REQUIRE_BUILDS = os.environ.get("WINTEGRATE_REQUIRE_UPSTREAM_BUILDS") == "1"

PORTABLE_ROOT = Path(r"C:\wm")
BUGGY_VERSION = "2.16.52"
FIXED_VERSION = "2.16.52.2"
PROCESS = "WinMergeU.exe"
WINDOW_CLASS = "WinMergeWindowClassW"

SETTINGS_KEY = r"HKCU\Software\Thingamahoochie\WinMerge\Settings"
TAB_TYPE_TABS = "0x0"
TAB_TYPE_SPACES = "0x1"

# Resource ids from PropEditor's dialog template. Numbers are not localised.
IDC_INSERT_TABS = 1038
IDC_INSERT_SPACES = 1040
IDOK = 1

# How long the Options page must hold still before the walk believes it.
PAGE_SETTLE = 0.5

# Part of the Options item's name in every locale; see _open_options.
OPTIONS_ACCELERATOR = "Ctrl+,"

BM_GETCHECK = 0x00F0
BM_CLICK = 0x00F5
CONTROL_TYPE_MENU_ITEM = 50011
CONTROL_TYPE_TREE_ITEM = 50024

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_ENUM_PROC = _WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
    _user32.GetDlgCtrlID.restype = ctypes.c_int
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.SendMessageW.restype = wintypes.LPARAM
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.EnumChildWindows.argtypes = [wintypes.HWND, _ENUM_PROC, wintypes.LPARAM]
else:
    _user32 = None


def _executable(version: str) -> Path:
    exe = PORTABLE_ROOT / version / "WinMerge" / PROCESS
    if not exe.exists():
        message = (
            f"unpacked WinMerge {version} is not at {exe}. This test compares a build that "
            "has upstream bug #3015 against one that does not, so both have to be present; "
            "see the module docstring."
        )
        if REQUIRE_BUILDS:
            pytest.fail(message)
        pytest.skip(message)
    return exe


def _reset_settings() -> None:
    """Both builds share one registry key, so the state is cleared per launch."""
    subprocess.run(["reg", "delete", SETTINGS_KEY, "/f"], capture_output=True, check=False)


def _tab_type() -> str | None:
    result = subprocess.run(
        ["reg", "query", SETTINGS_KEY, "/v", "TabType"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        if "TabType" in line:
            return line.split()[-1]
    return None


def _control(dialog: int, control_id: int) -> int | None:
    """The visible child with this control id.

    Not `GetDlgItem`: a property sheet keeps every page it has visited as a
    child of the same dialog, so the id resolves on pages that are no longer
    showing. Only the visible one is the page the user is looking at.
    """
    found: list[int] = []

    def callback(hwnd, _lparam):
        if _user32.GetDlgCtrlID(hwnd) == control_id and _user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True

    _user32.EnumChildWindows(dialog, _ENUM_PROC(callback), 0)
    return found[0] if found else None


def _is_checked(dialog: int, control_id: int) -> bool:
    control = _control(dialog, control_id)
    assert control, f"control {control_id} is not on the visible page"
    return bool(_user32.SendMessageW(control, BM_GETCHECK, 0, 0))


def _open_options(window: Window) -> int:
    """Opens Options and returns its dialog handle.

    `Alt+E` rather than the menu item's `Invoke()`, which returns success and
    opens nothing, and rather than the `Ctrl+,` accelerator the menu advertises,
    which did not fire when synthesised.
    """
    before = {snap.hwnd for snap in WindowCensus.capture() if snap.is_visible}
    send_keys("%e")
    popup = _settled_popup()
    items = (
        Window(popup, window.pid)
        .re_resolve_element()
        .find_all(control_type_id=CONTROL_TYPE_MENU_ITEM)
    )
    # Matched on the *accelerator*, which is part of the item's name and is the
    # only part of it that reads the same in every locale:
    #
    #   zh-TW   ' 選項 (O)...\tCtrl+,'
    #   en-US   '&Options...\tCtrl+,'
    #
    # The first version matched '(O)', which looks language-independent on a
    # Chinese desktop and is in fact a CJK convention — English Windows puts the
    # mnemonic in an embedded '&'. Every test here failed on the runners with a
    # bare StopIteration.
    names = [item.name or "" for item in items]
    options = next((item for item in items if OPTIONS_ACCELERATOR in (item.name or "")), None)
    assert options is not None, (
        f"no Edit-menu item carries the accelerator {OPTIONS_ACCELERATOR!r}; "
        f"the menu offered {names!r}"
    )
    options.invoke()

    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        time.sleep(0.4)
        new = [
            snap
            for snap in WindowCensus.capture()
            if snap.is_visible and snap.hwnd not in before and snap.class_name == "#32770"
        ]
        if new:
            return new[0].hwnd
    raise AssertionError("the Options dialog did not open within 12s of Alt+E, O")


def _settled_popup() -> int:
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        time.sleep(0.3)
        popup = _user32.FindWindowW("#32768", None)
        if popup and _user32.IsWindowVisible(popup):
            return popup
    raise AssertionError("Alt+E opened no menu popup within 6s")


def _goto_editor_page(dialog: int, pid: int) -> str:
    """Walks the Options tree until the visible page carries the tab-type radios.

    The 27 node names are localised, so none of them can be matched on. What
    cannot change is that exactly one page owns control 1038.

    Navigation is by **arrow key**, not by clicking each node, and that took two
    CI rounds to arrive at:

    - `click()` aims at the middle of an element's bounding rectangle and
      silently returns when there isn't one. Nodes scrolled out of the tree's
      viewport have no rectangle, so they were skipped with no sign at all.
    - The runners have a smaller desktop than the development VM, so *more* of
      the tree is out of view there. Clicking every node worked locally and
      reported "clicked 23 of 27 pages and none carried control 1038" on arm64.

    A tree view scrolls its own caret into view, so Down reaches every node
    regardless of the viewport. One click is still needed first, to put focus in
    the tree — and for that any laid-out node will do.
    """
    root = Window(dialog, pid).re_resolve_element()
    nodes = root.find_all(control_type_id=CONTROL_TYPE_TREE_ITEM)
    assert nodes, "the Options dialog exposed no tree items at all"

    anchored = False
    for node in nodes:
        try:
            left, top, right, bottom = node.bounding_rectangle
            if right > left and bottom > top:
                node.click()
                anchored = True
                break
        except Exception:  # noqa: BLE001 - try the next node
            continue
    assert anchored, (
        f"none of the {len(nodes)} Options tree nodes had a non-empty bounding rectangle, "
        "so the tree could not be given focus and every click would have been a no-op"
    )

    if _wait_for_editor_page(dialog):
        return "the page the tree opened on"

    # One Down per node, plus slack for parents that expand into extra rows.
    for _ in range(len(nodes) * 2):
        send_keys("{DOWN}")
        if _wait_for_editor_page(dialog):
            return "reached by arrow key"

    raise AssertionError(
        f"walked the whole Options tree ({len(nodes)} nodes) and no page carried control "
        f"{IDC_INSERT_TABS}"
    )


def _wait_for_editor_page(dialog: int, timeout: float = 0.8) -> bool:
    """Polls for the tab-type radios, and requires them to stay.

    Polling alone is not enough, and the arm64 runner proved it: the walk found
    control 1038, returned, and by the time the caller looked the control was
    gone again — "control 1038 is not on the visible page", intermittently, from
    a run whose only other change was in an unrelated file.

    The cause is that arrow keys queue. Each Down is sent, polled for briefly,
    and followed by the next one; on a slower machine the earlier keystrokes are
    still being processed, so the Editor page appears and is then carried past
    by presses that were already in flight. Requiring the control to be present
    twice, either side of a settle, means the page has stopped moving before the
    walk stops.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _control(dialog, IDC_INSERT_TABS):
            time.sleep(PAGE_SETTLE)
            return _control(dialog, IDC_INSERT_TABS) is not None
        time.sleep(0.15)
    return False


def _choose_and_confirm(window: Window, control_id: int) -> tuple[str | None, bool]:
    """Picks a tab type, presses OK, and reports what persisted.

    Returns the stored `TabType` and whether Options reopens on *Insert tabs*.
    Both are read, because they are different claims: the registry says what was
    written, the reopened dialog says what the application believes.
    """
    dialog = _open_options(window)
    _goto_editor_page(dialog, window.pid)

    radio = _control(dialog, control_id)
    assert radio, f"control {control_id} is not on the editor page"
    _user32.SendMessageW(radio, BM_CLICK, 0, 0)
    time.sleep(0.4)
    assert _is_checked(dialog, control_id), (
        f"clicking control {control_id} did not check it, so nothing was chosen "
        "and the rest of this test would be measuring the default"
    )

    ok = _control(dialog, IDOK)
    assert ok, "the Options dialog has no visible OK button"
    _user32.SendMessageW(ok, BM_CLICK, 0, 0)
    time.sleep(1.5)

    stored = _tab_type()
    reopened = _open_options(window)
    _goto_editor_page(reopened, window.pid)
    shows_tabs = _is_checked(reopened, IDC_INSERT_TABS)
    send_keys("{ESC}")
    time.sleep(0.6)
    return stored, shows_tabs


def _round_trip(version: str, control_id: int) -> tuple[str | None, bool]:
    exe = _executable(version)
    sweep_processes_verified((PROCESS,), ("WinMerge",))
    time.sleep(1.0)
    _reset_settings()
    process, window = Window.launch_and_discover(
        [str(exe)],
        timeout=90.0,
        process_names=(PROCESS,),
        window_classes=(WINDOW_CLASS,),
        require_all=True,
    )
    try:
        time.sleep(2.0)
        with window.foreground(verify=False):
            return _choose_and_confirm(window, control_id)
    finally:
        process.terminate()
        sweep_processes_verified((PROCESS,), ("WinMerge",))


def test_insert_tabs_survives_on_the_fixed_build():
    stored, shows_tabs = _round_trip(FIXED_VERSION, IDC_INSERT_TABS)
    assert stored == TAB_TYPE_TABS, (
        f"WinMerge {FIXED_VERSION} stored TabType={stored}, expected {TAB_TYPE_TABS}"
    )
    assert shows_tabs, (
        f"WinMerge {FIXED_VERSION} reopened Options on Insert spaces after Insert tabs "
        "was chosen — the fix for #3015 is not in the build under test"
    )


def test_insert_tabs_is_lost_on_the_build_that_had_the_bug():
    stored, shows_tabs = _round_trip(BUGGY_VERSION, IDC_INSERT_TABS)
    assert stored == TAB_TYPE_SPACES, (
        f"WinMerge {BUGGY_VERSION} was expected to reproduce #3015 by clamping tab type 0 "
        f"up to 1, but it stored TabType={stored}. Either this is not the build the issue "
        "was reported against, or the dialog was never reached — the assertions inside "
        "_choose_and_confirm rule the second out."
    )
    assert not shows_tabs, (
        f"WinMerge {BUGGY_VERSION} stored {TAB_TYPE_SPACES} yet reopened Options on Insert "
        "tabs, which would mean the dialog and the stored setting disagree"
    )


@pytest.mark.parametrize("version", [FIXED_VERSION, BUGGY_VERSION])
def test_insert_spaces_round_trips_on_both_builds(version):
    """The control that makes the failure above specific.

    The clamp's lower bound is 1 and *Insert spaces* is 1, so this direction is
    untouched by the bug. Without this, a build that ignored the dialog and
    always wrote 1 would be indistinguishable from a fixed one.
    """
    stored, shows_tabs = _round_trip(version, IDC_INSERT_SPACES)
    assert stored == TAB_TYPE_SPACES, (
        f"WinMerge {version} stored TabType={stored} after Insert spaces was chosen; "
        f"this direction is unaffected by #3015 and should be {TAB_TYPE_SPACES} on both builds"
    )
    assert not shows_tabs, f"WinMerge {version} reopened Options on Insert tabs"
