"""DB Browser for SQLite (Qt), where "it did not crash" is the assertion.

Qt bridges QAccessible to UIA, and the coverage is generous — but two things it
offers are traps:

- The window class is `Qt683QWindowIcon`, which carries the Qt version. Matching
  it exactly would break on a Qt upgrade, exactly like WinMerge's class carrying
  a module base address.
- `automation_id` is a full object path
  (`Application.MainWindow.centralwidget.mainTab.qt_tabwidget_tabbar`), which is
  language-independent and stable — but a container and its children share the
  container's path, so an id identifies a *group* rather than one element.

The tab labels are localized (`瀏覽資料`, not `Browse Data`) and their order has
been observed to differ between runs, so nothing here matches a tab by name or by
index. What it does assert is what the project's own bug reports are about: the
Windows issues cited in the investigation notes (#3705, #2288, #1878) are all
crashes on tab switching or on running SQL. "Select every tab, still alive"
covers them without reading a single cell.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from target_apps import find_executable

from wintegrate import Window
from wintegrate.apps import sweep_processes_verified

pytestmark = [
    pytest.mark.target_app,
    pytest.mark.skipif(sys.platform != "win32", reason="drives a live Qt application"),
]

SQLITEBROWSER_CANDIDATES = (
    Path(r"C:\Program Files\DB Browser for SQLite\DB Browser for SQLite.exe"),
    Path(r"C:\Program Files (x86)\DB Browser for SQLite\DB Browser for SQLite.exe"),
    Path(os.environ.get("ProgramData", ""))
    / "chocolatey"
    / "lib"
    / "sqlitebrowser"
    / "tools"
    / "DB Browser for SQLite.exe",
)

PROCESS = "DB Browser for SQLite.exe"
# The four tabs of the main document area. Dock widgets and the QMainWindow tab
# bar publish tabs too — 11 in total — and only these four are the ones a user
# would call the app's tabs.
MAIN_TAB_BAR = "centralwidget.mainTab.qt_tabwidget_tabbar"
EXPECTED_MAIN_TABS = 4
CONTROL_TYPE_TAB_ITEM = 50019
CONTROL_TYPE_TABLE = 50036


def _sample_database(path: Path) -> Path:
    connection = sqlite3.connect(path)
    with connection:
        connection.execute("CREATE TABLE widgets(id INTEGER PRIMARY KEY, name TEXT, state TEXT)")
        connection.executemany(
            "INSERT INTO widgets(name, state) VALUES(?, ?)",
            [(f"row-{n}", ("ready", "failed", "pending")[n % 3]) for n in range(30)],
        )
    connection.close()
    return path


def _is_running() -> bool:
    result = subprocess.run(
        ["tasklist", "/fi", f"IMAGENAME eq {PROCESS}"], capture_output=True, text=True
    )
    # tasklist exits 0 and prints an informational line when nothing matched, so
    # the process name has to be looked for rather than the exit code trusted.
    return PROCESS.lower() in result.stdout.lower()


@pytest.fixture
def browser(tmp_path):
    """The app opened on a fresh database.

    A new database per test is this app's equivalent of `/noprefs`: it restores
    the last opened file and the last selected tab otherwise.
    """
    exe = find_executable("DB Browser for SQLite", SQLITEBROWSER_CANDIDATES)
    database = _sample_database(tmp_path / "probe.db")
    sweep_processes_verified((PROCESS,), ("DB Browser",))
    proc, win = Window.launch_and_discover(
        [str(exe), str(database)], timeout=90.0, process_names=(PROCESS,)
    )
    try:
        with win.foreground(verify=False):
            yield win
    finally:
        proc.terminate()
        sweep_processes_verified((PROCESS,), ("DB Browser",))


def _main_tabs(win: Window) -> list:
    return [
        tab
        for tab in win.re_resolve_element().find_all(control_type_id=CONTROL_TYPE_TAB_ITEM)
        if tab.automation_id.endswith(MAIN_TAB_BAR)
    ]


def test_window_class_carries_the_qt_version(browser):
    """Characterisation: records why the class name is not used for matching.

    A failure here means the Qt version moved, and is a prompt to check that no
    matching anywhere depends on the class — not a defect in the app.
    """
    assert browser.class_name.startswith("Qt"), browser.class_name
    assert browser.class_name.endswith("QWindowIcon"), browser.class_name


def test_main_tabs_are_found_by_object_path(browser):
    """Object paths survive translation; names and positions do not."""
    tabs = _main_tabs(browser)
    assert len(tabs) == EXPECTED_MAIN_TABS, (
        f"expected {EXPECTED_MAIN_TABS} tabs on the main tab bar, found {len(tabs)}: "
        f"{[t.name for t in tabs]}"
    )


def test_every_tab_can_be_selected_and_the_app_survives(browser):
    """The crash coverage. Selecting a tab is what the reported bugs do.

    Re-resolving the tab list on every iteration rather than holding the elements:
    switching tabs rebuilds part of the tree, and a held COM wrapper goes stale
    without raising.
    """
    for index in range(EXPECTED_MAIN_TABS):
        tabs = _main_tabs(browser)
        assert len(tabs) == EXPECTED_MAIN_TABS, (
            f"tab {index}: the tab bar lost tabs during the walk ({len(tabs)} left)"
        )
        tab = tabs[index]
        name = tab.name
        assert tab.select_verified(timeout=10.0), f"tab {index} ({name!r}) did not become selected"
        assert _is_running(), f"the app died after selecting tab {index} ({name!r})"


def test_some_tab_renders_a_table(browser):
    """The grid is built, not merely described.

    Which tab produces it is deliberately not asserted: two of the four do, and
    the mapping is neither stable nor language-independent. What matters is that
    selecting tabs materialises a Table somewhere — before any selection there
    are none, because Qt builds the view lazily.
    """
    assert not browser.re_resolve_element().find_all(control_type_id=CONTROL_TYPE_TABLE), (
        "a Table existed before any tab was selected — the laziness this test "
        "relies on is gone, so it no longer proves anything"
    )
    for index in range(EXPECTED_MAIN_TABS):
        tabs = _main_tabs(browser)
        tabs[index].select_verified(timeout=10.0)
        if browser.re_resolve_element().find_all(control_type_id=CONTROL_TYPE_TABLE):
            return
    pytest.fail("no tab produced a Table control — the data grid never rendered")
