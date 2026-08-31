"""DB Browser for SQLite #3735, reproduced against the build that had it.

Copying a cell from Browse Data put an extra end-of-line on the clipboard, so
pasting it into the app's own "Find in table" box matched nothing. Reported
against v3.13.0 with the note that v3.12.2 was fine, fixed by 20f481a1 — which
`git compare` places behind v3.13.1.

Measured, same win64 (Qt 5.15.2) build on both sides so nothing but the fix
differs:

    3.13.1   cell (0,1) = '1'   clipboard = '1'
    3.13.0   cell (0,1) = '1'   clipboard = '1\\r\\n'

The post-condition is **outside the application**: the clipboard. Nothing here
reads the grid to decide whether the copy was right — the grid is only used to
find something to click. That matters because Qt's accessibility surface is the
least dependable of the four applications this project drives, and a check that
does not depend on it cannot be undermined by it.

Two prompts have to be off before any of this is observable, and both cost a
round of debugging:

- **The update check.** `checkversion/enabled` defaults to true, so an old build
  opens a modal announcing a newer version. The window is unreachable behind it
  and the data grid never appears at all — the symptom is "no Table in the UIA
  tree", which reads like an accessibility problem rather than a dialog.
- **Shared settings.** Both portable builds write to
  `HKCU\\Software\\sqlitebrowser`, so this is not per-directory the way
  Notepad++'s `doLocalConf.xml` is. The setting is written before each launch
  rather than once.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from tests_support_clipboard import clear_clipboard, clipboard_text
from waits import settled

from wintegrate import Window
from wintegrate.apps import sweep_processes_verified
from wintegrate.interop import get_foreground_window, get_window_class, get_window_title, send_keys

# Not part of the release gate; see tests/test_regression_notepadpp_16326.py.
pytestmark = [
    pytest.mark.upstream_bug,
    pytest.mark.target_app,
    pytest.mark.skipif(sys.platform != "win32", reason="drives a live Qt application"),
]

REQUIRE_BUILDS = os.environ.get("WINTEGRATE_REQUIRE_UPSTREAM_BUILDS") == "1"

PORTABLE_ROOT = Path(r"C:\db4s")
BUGGY_VERSION = "3.13.0"
FIXED_VERSION = "3.13.1"
PROCESS = "DB Browser for SQLite.exe"

MAIN_TAB_BAR = "centralwidget.mainTab.qt_tabwidget_tabbar"
CONTROL_TYPE_TAB = 50018
CONTROL_TYPE_TAB_ITEM = 50019
CONTROL_TYPE_TABLE = 50036

CELL_VALUE = "1"


def _portable(version: str) -> Path:
    exe = PORTABLE_ROOT / version / f"{PROCESS[:-4]}.exe"
    if not exe.exists():
        message = (
            f"portable DB Browser for SQLite {version} is not at {exe}. This test compares "
            "a build that has upstream bug #3735 against one that does not, so both have "
            "to be present; see the module docstring."
        )
        if REQUIRE_BUILDS:
            pytest.fail(message)
        pytest.skip(message)
    return exe


def _disable_update_check() -> None:
    """An old build otherwise opens a modal announcing a newer version."""
    subprocess.run(
        [
            "reg",
            "add",
            r"HKCU\Software\sqlitebrowser\sqlitebrowser\checkversion",
            "/v",
            "enabled",
            "/t",
            "REG_SZ",
            "/d",
            "false",
            "/f",
        ],
        capture_output=True,
        check=False,
    )


def _sample_database(tmp_path: Path) -> Path:
    # A fresh file per run: the app reopens the last database, and a locked one
    # from a previous run fails the next with a permission error rather than
    # anything about the test.
    path = tmp_path / f"clip-{uuid.uuid4().hex[:8]}.db"
    connection = sqlite3.connect(path)
    with connection:
        connection.execute("CREATE TABLE widgets(id INTEGER PRIMARY KEY, name TEXT)")
        connection.executemany("INSERT INTO widgets(name) VALUES(?)", [("alpha",), ("beta",)])
    connection.close()
    return path


def _main_tab_bar(win: Window):
    for bar in win.re_resolve_element().find_all(
        control_type_id=CONTROL_TYPE_TAB, class_name="QTabBar"
    ):
        if bar.automation_id.endswith(MAIN_TAB_BAR):
            return bar
    return None


def _browse_data_grid(win: Window):
    """The data grid, reached by selecting whichever tab materialises a Table.

    Not by name (localized) and not by index (observed to vary). Each tab is
    given time before moving on: an earlier version of this walked all four in
    1.5s each and ended up sitting on the wrong one.
    """
    bar = _main_tab_bar(win)
    assert bar is not None, "the document tab bar never appeared"
    count = len(bar.find_all(control_type_id=CONTROL_TYPE_TAB_ITEM))
    for index in range(count):
        tabs = _main_tab_bar(win).find_all(control_type_id=CONTROL_TYPE_TAB_ITEM)
        tabs[index].click()
        tables = settled(
            lambda: win.re_resolve_element().find_all(control_type_id=CONTROL_TYPE_TABLE),
            lambda found: bool(found),
            timeout=10.0,
        )
        if tables:
            return tables[0].as_data_grid()
    pytest.fail(f"none of the {count} tabs produced a data grid")


def _clipboard_after_copying_a_cell(exe: Path) -> str | None:
    _disable_update_check()
    sweep_processes_verified((PROCESS,), ("DB Browser",))
    database = _sample_database(Path(exe).parent)
    proc, win = Window.launch_and_discover(
        [str(exe), str(database)], timeout=120.0, process_names=(PROCESS,)
    )
    try:
        win.ensure_onscreen()
        with win.foreground(verify=False):
            deadline_ok = settled(
                lambda: get_foreground_window() == win.hwnd, lambda ok: ok, timeout=15.0
            )
            foreground = get_foreground_window()
            assert deadline_ok, (
                f"the window never came forward; <class={get_window_class(foreground)!r} "
                f"title={get_window_title(foreground)!r}> holds the foreground, so the "
                "click and Ctrl+C below would have gone there"
            )

            grid = _browse_data_grid(win)
            cell = grid.get_cell(0, 1)
            assert cell.value == CELL_VALUE, (
                f"expected the first data cell to read {CELL_VALUE!r}, got {cell.value!r}"
            )

            # Emptied first, so "nothing was copied" cannot read as a stale value
            # left by the other half of this comparison.
            assert clear_clipboard(), "could not empty the clipboard before copying"
            target = getattr(cell, "element", cell)
            target.click()
            send_keys("^c")
            return settled(clipboard_text, lambda text: bool(text), timeout=10.0)
    finally:
        proc.terminate()
        sweep_processes_verified((PROCESS,), ("DB Browser",))


def test_fixed_build_copies_the_cell_without_a_line_ending():
    copied = _clipboard_after_copying_a_cell(_portable(FIXED_VERSION))
    assert copied == CELL_VALUE, (
        f"DB Browser {FIXED_VERSION} put {copied!r} on the clipboard, expected "
        f"{CELL_VALUE!r} — upstream #3735 has regressed"
    )


def test_buggy_build_appends_a_line_ending():
    """Asserting the old build is still broken keeps the pair meaningful.

    Without it, a download URL that quietly started serving a fixed build would
    turn both tests green while comparing nothing.
    """
    copied = _clipboard_after_copying_a_cell(_portable(BUGGY_VERSION))
    assert copied == CELL_VALUE + "\r\n", (
        f"DB Browser {BUGGY_VERSION} was expected to reproduce #3735 by appending a line "
        f"ending, but put {copied!r} on the clipboard — is this really the pre-20f481a1 build?"
    )


def test_the_two_builds_are_the_same_qt():
    """The comparison is only about the fix, so nothing else may differ.

    Both are the win64 package, which carries Qt 5.15.2. If one of these ever
    became the arm64 (Qt 6) build, the accessibility surface would change
    underneath the test and a difference in the clipboard would no longer be
    attributable to the fix.
    """
    for version in (FIXED_VERSION, BUGGY_VERSION):
        directory = _portable(version).parent
        assert (directory / "Qt5Core.dll").exists(), f"{version} is not the Qt 5 build"
        assert not (directory / "Qt6Core.dll").exists(), f"{version} carries Qt 6"
