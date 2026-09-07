"""microsoft/terminal #20593, reproduced against the current release.

Esc closes the terminal pane's context menu, but keyboard focus stays on the
menu button that had it. Enter then invokes that hidden button: from the Split
pane submenu it splits the pane, from the top level it re-opens the submenu at
the screen origin, anchored to a button that is no longer on screen.

The pane menu is a `CommandBarFlyout` (TerminalPage.cpp, `_PopulateContextMenu`)
whose `Closed` handler only clears the items it added. The tab header's menu is a
`MenuFlyout` whose `Closed` handler hands focus back to the terminal (GH#5750),
and it is the control here: same keys, focus ends up where it should.

Measured on 1.24.11911.0 (Windows 11 ARM64 VM), UIA focused element after Esc:

    pane menu, submenu open    <menu item 'Duplicate Windows PowerShell'> rect=(0,0,0,0)
    pane menu, top level       <menu item 'Split pane'>                  12x12 px
    tab menu                   <terminal 'Windows PowerShell'>

The issue is open, so the assertions below state the defect as measured today,
the same way the other regression tests assert that the old build is still
broken. The day one of them fails, a build has started handing focus back and
the test turns into the regression guard for it.

Not `xfail(strict=True)`, deliberately: pytest records a test whose *fixture*
failed as XFAIL when the test carries that marker, and strict only catches XPASS.
A broken fixture would then read exactly like "the bug is still there".
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
from waits import settled

from wintegrate import Mouse, UiaElement, Window, WindowCensus
from wintegrate.interop import PROCESS_QUERY_LIMITED_INFORMATION, kernel32, send_keys

# Not part of the release gate; see tests/test_regression_notepadpp_16326.py.
pytestmark = [
    pytest.mark.upstream_bug,
    pytest.mark.target_app,
    pytest.mark.skipif(sys.platform != "win32", reason="drives a live Windows Terminal"),
]

REQUIRE_BUILDS = os.environ.get("WINTEGRATE_REQUIRE_UPSTREAM_BUILDS") == "1"

# The portable zip from the release page, extracted by the CI step with a
# `.portable` marker beside the exe so it keeps its settings there. Locally,
# WINTEGRATE_TERMINAL_EXE can point at any wt.exe, the installed package included.
VERSION = "1.24.11911.0"
FILE_VERSION = "1.24.2607.10001"  # what WindowsTerminal.exe in that package reports
PORTABLE_ROOT = Path(r"C:\wt")
PROCESS = "WindowsTerminal.exe"
WINDOW_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"
POPUP_CLASS = "Xaml_WindowedPopupClass"
UIA_TAB_ITEM = 50019


def _wt_exe() -> Path:
    override = os.environ.get("WINTEGRATE_TERMINAL_EXE")
    exe = Path(override) if override else PORTABLE_ROOT / VERSION / f"terminal-{VERSION}" / "wt.exe"
    if exe.exists():
        return exe
    message = (
        f"Windows Terminal is not at {exe}. The CI step extracts the portable zip there; "
        "locally, set WINTEGRATE_TERMINAL_EXE to a wt.exe."
    )
    if REQUIRE_BUILDS:
        pytest.fail(message)
    pytest.skip(message)


def _focused() -> UiaElement:
    return UiaElement.get_focused()


def _is_terminal(element: UiaElement) -> bool:
    return element.class_name == "TermControl"


def _panes(win: Window) -> list[UiaElement]:
    return UiaElement.from_handle(win.hwnd).find_all(class_name="TermControl")


def _popups(win: Window) -> list:
    return [
        w
        for w in WindowCensus.capture()
        if w.is_visible and w.pid == win.pid and w.class_name == POPUP_CLASS
    ]


def _focus_settles(matches, timeout: float = 3.0) -> UiaElement:
    return settled(_focused, matches, timeout=timeout)


def _walk_down_to(name_part: str, steps: int = 14) -> UiaElement:
    """Down-arrows through the open menu until an item whose name contains `name_part`
    has focus. Returns the focused element either way; the caller asserts."""
    focused = _focused()
    for _ in range(steps):
        if name_part.casefold() in focused.name.casefold():
            return focused
        before = focused.name
        send_keys("{DOWN}")
        focused = _focus_settles(lambda e, b=before: e.name != b, timeout=2.0)
    return focused


def _terminal_windows() -> list:
    return [w for w in WindowCensus.capture() if w.class_name == WINDOW_CLASS]


def _image_path(pid: int) -> str:
    """The full image path of a process; wintegrate's helper returns the basename."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _is_ours(pid: int, exe: Path) -> bool:
    """Only the Terminal we extracted. On windows-latest the runner's own console
    lives inside a Windows Terminal window (class CASCADIA_HOSTING_WINDOW_CLASS,
    titled with the hosted-compute-agent path); killing WindowsTerminal.exe by name
    there cancels the job. Image path is what tells the two apart."""
    image = _image_path(pid)
    return bool(image) and Path(image).parent.resolve() == exe.parent.resolve()


# Only processes this module launched are ever killed. Sweeping by name would
# also hit whatever else runs in a Windows Terminal, the runner's own console
# included (see _is_ours).
_LAUNCHED: set[int] = set()


def _kill_launched() -> None:
    alive = {w.pid for w in _terminal_windows() if w.pid in _LAUNCHED}
    for pid in alive:
        subprocess.run(["taskkill", "/f", "/pid", str(pid)], capture_output=True, check=False)
    left = settled(
        lambda: [w for w in _terminal_windows() if w.pid in alive], lambda ws: not ws, timeout=10.0
    )
    assert not left, f"Terminal windows we launched survived the sweep: {left}"


@pytest.fixture
def terminal():
    """A fresh Terminal window per test: every scenario here changes menu state."""
    exe = _wt_exe()
    _kill_launched()
    others = {w.hwnd for w in _terminal_windows()}
    proc, win = Window.launch_and_discover(
        [str(exe), "-w", "new"],
        timeout=90.0,
        process_names=(PROCESS,),
        window_classes=(WINDOW_CLASS,),
        exclude_hwnds=others,
    )
    _LAUNCHED.add(win.pid)
    try:
        # An App Execution Alias (the local override) does not live beside the
        # exe it starts, so the path check is for the portable layout only.
        assert os.environ.get("WINTEGRATE_TERMINAL_EXE") or _is_ours(win.pid, exe), (
            f"discovered {win!r}, whose image is {_image_path(win.pid)!r}, "
            f"not the Terminal under {exe.parent}"
        )
        assert win.set_foreground(timeout=10.0), f"{win!r} never became the foreground window"
        focused = _focus_settles(_is_terminal, timeout=15.0)
        assert _is_terminal(focused), f"focus is on {focused.describe()}, not the terminal"
        assert len(_panes(win)) == 1, "expected exactly one pane in a new window"
        yield win
    finally:
        win.close(force=True)
        proc.terminate()
        _kill_launched()


def _open_pane_menu(win: Window) -> UiaElement:
    """The Menu key with the terminal focused; the flyout puts focus on Paste."""
    send_keys("{APPS}")
    focused = _focus_settles(lambda e: e.class_name == "AppBarButton", timeout=5.0)
    assert focused.class_name == "AppBarButton", (
        f"the pane context menu did not take focus; focus is on {focused.describe()}"
    )
    assert _popups(win), "the Menu key opened no popup window"
    return focused


def _open_split_submenu(win: Window) -> UiaElement:
    _open_pane_menu(win)
    entry = _walk_down_to("Split pane")
    assert "split pane" in entry.name.casefold(), f"never reached Split pane: {entry.describe()}"
    send_keys("{RIGHT}")
    item = _focus_settles(lambda e: "duplicate" in e.name.casefold(), timeout=3.0)
    assert "duplicate" in item.name.casefold(), (
        f"Right did not open the Split pane submenu; focus is on {item.describe()}"
    )
    return item


def _press_esc_and_wait_for_the_flyout(win: Window, opened: int) -> None:
    send_keys("{ESC}")
    settled(lambda: len(_popups(win)), lambda n: n < opened, timeout=3.0)


def test_the_measurement_can_see_a_split(terminal):
    """Positive control: Enter on the submenu item, with the menu open, does split.

    Without this, `len(_panes(...)) == 1` below could hold because the probe
    cannot see panes at all, and the xfail would be about the probe, not the bug.
    """
    _open_split_submenu(terminal)
    send_keys("{ENTER}")
    panes = settled(lambda: len(_panes(terminal)), lambda n: n == 2, timeout=10.0)
    assert panes == 2, f"Enter on 'Duplicate ...' produced {panes} pane(s), expected a split"


def test_esc_from_the_submenu_leaves_focus_on_the_hidden_item(terminal):
    """The defect as reported. Fails the day Esc hands focus back to the terminal."""
    item = _open_split_submenu(terminal)
    opened = len(_popups(terminal))
    _press_esc_and_wait_for_the_flyout(terminal, opened)
    focused = _focus_settles(_is_terminal, timeout=3.0)
    assert not _is_terminal(focused), "focus returned to the terminal after Esc: fixed upstream?"
    assert focused.name == item.name and not focused.is_visible(), (
        f"after Esc, focus is on {focused.describe()} rect={focused.bounding_rectangle}; "
        f"expected the dismissed {item.name!r} with an empty rectangle"
    )


def test_enter_after_esc_invokes_the_dismissed_item(terminal):
    """The consequence: Enter runs 'Duplicate ...' from a menu that is no longer
    on screen, and the pane splits. Fails the day Enter reaches the shell instead."""
    _open_split_submenu(terminal)
    opened = len(_popups(terminal))
    _press_esc_and_wait_for_the_flyout(terminal, opened)
    send_keys("{ENTER}")
    # Waits for the split to finish, not for the count to move: the tree reads 0
    # panes for a moment while the new one is being built.
    panes = settled(lambda: len(_panes(terminal)), lambda n: n == 2, timeout=5.0)
    time.sleep(1.0)  # hold the result for the recording
    assert panes == 2, (
        f"Enter after Esc left {panes} pane(s); the dismissed 'Duplicate ...' item was "
        "expected to be invoked and split the pane — fixed upstream?"
    )


def test_esc_from_the_top_level_leaves_focus_on_split_pane(terminal):
    """Same defect without the submenu, not in the report: Esc closes the whole
    menu, focus stays on the 'Split pane' button, and Enter re-opens its submenu
    anchored to a button that is no longer on screen."""
    _open_pane_menu(terminal)
    entry = _walk_down_to("Split pane")
    assert "split pane" in entry.name.casefold(), f"never reached Split pane: {entry.describe()}"
    opened = len(_popups(terminal))
    _press_esc_and_wait_for_the_flyout(terminal, opened)
    assert not _popups(terminal), "Esc at the top level did not close the menu"
    focused = _focus_settles(_is_terminal, timeout=3.0)
    assert not _is_terminal(focused), "focus returned to the terminal after Esc: fixed upstream?"
    assert focused.name == entry.name, (
        f"after Esc, focus is on {focused.describe()}; expected the dismissed {entry.name!r}"
    )
    send_keys("{ENTER}")
    reopened = settled(lambda: len(_popups(terminal)), lambda n: n > 0, timeout=3.0)
    time.sleep(1.0)  # hold the result for the recording
    assert reopened > 0, "Enter after Esc opened nothing; the dismissed button was not invoked"


def test_the_tab_menu_hands_focus_back_on_esc(terminal):
    """The control: the tab header's MenuFlyout returns focus on Esc (GH#5750),
    so Enter afterwards goes to the shell and nothing splits."""
    tabs = UiaElement.from_handle(terminal.hwnd).find_all(control_type_id=UIA_TAB_ITEM)
    assert tabs, "no tab item in the window"
    left, top, right, bottom = tabs[0].bounding_rectangle
    Mouse().right_click((left + right) // 2, (top + bottom) // 2)
    focused = _focus_settles(lambda e: e.class_name.startswith("MenuFlyout"), timeout=5.0)
    assert focused.class_name.startswith("MenuFlyout"), (
        f"the tab context menu did not take focus; focus is on {focused.describe()}"
    )
    entry = _walk_down_to("Split tab")
    assert "split tab" in entry.name.casefold(), f"never reached Split tab: {entry.describe()}"
    opened = len(_popups(terminal))
    _press_esc_and_wait_for_the_flyout(terminal, opened)
    focused = _focus_settles(_is_terminal, timeout=3.0)
    assert _is_terminal(focused), f"after Esc, focus is on {focused.describe()}, not the terminal"
    send_keys("{ENTER}")
    time.sleep(1.0)
    assert len(_panes(terminal)) == 1, "Enter after Esc split the pane from the tab menu"
    assert not _popups(terminal), "Enter after Esc re-opened a menu"


def test_portable_build_is_the_pinned_version():
    """The exe under test is the one the docstring talks about."""
    exe = _wt_exe()
    if os.environ.get("WINTEGRATE_TERMINAL_EXE"):
        pytest.skip("an explicit WINTEGRATE_TERMINAL_EXE is not version-pinned")
    out = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-Item '{exe.parent / PROCESS}').VersionInfo.FileVersion",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    ).stdout.strip()
    assert out == FILE_VERSION, (
        f"{exe.parent / PROCESS} is {out!r}, the test is about package {VERSION} "
        f"(file version {FILE_VERSION})"
    )
