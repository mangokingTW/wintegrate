"""DB Browser for SQLite (Qt), where "it did not crash" is the assertion.

Qt bridges QAccessible to UIA, and the coverage is generous — but two things it
offers are traps:

- The window class is `Qt683QWindowIcon`, which carries the Qt version. Matching
  it exactly would break on a Qt upgrade, exactly like WinMerge's class carrying
  a module base address.
- `automation_id` is a full object path
  (`Application.MainWindow.centralwidget.mainTab.qt_tabwidget_tabbar`), which is
  language-independent — but a container and its children share the container's
  path, so an id identifies a *group* rather than one element. And it is not
  dependable: on a Windows Server 2025 runner every tab item reported
  `automation_id=''` while the same build on a Windows 11 client reported the full
  path, with 71 other elements carrying ids on both. `_main_tab_bar` therefore
  falls back to Qt class names.

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
from target_apps import assert_version, find_executable, installed_file_version
from waits import settled

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

# The build every assertion below was measured against, including the Qt version
# in the window class and the object paths. Chocolatey package
# `sqlitebrowser 3.13.1` installs this file version — the vendor writes it as
# '3.13.1.' with a trailing dot, hence the four-part numeric form.
VERIFIED_VERSION = "3.13.1.0"

PROCESS = "DB Browser for SQLite.exe"
# The four tabs of the main document area. Dock widgets and the QMainWindow tab
# bar publish tabs too — 11 in total — and only these four are the ones a user
# would call the app's tabs.
MAIN_TAB_BAR = "centralwidget.mainTab.qt_tabwidget_tabbar"
EXPECTED_MAIN_TABS = 4
CONTROL_TYPE_TAB = 50018
CONTROL_TYPE_TAB_ITEM = 50019
CONTROL_TYPE_TABLE = 50036
CONTROL_TYPE_BUTTON = 50000


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


def _ancestor_classes(element, depth: int = 8) -> list[str]:
    classes = []
    node = element
    for _ in range(depth):
        node = node.get_parent()
        if node is None:
            break
        try:
            classes.append(node.class_name)
        except Exception:
            break
    return classes


def _main_tab_bar(win: Window):
    """The document tab bar, found without relying on automation ids.

    Two routes, because the ids are not dependable. On a Windows 11 client each
    tab item reports its parent tab bar's object path
    (`…centralwidget.mainTab.qt_tabwidget_tabbar`), and the endswith() on that path
    is precise. On a Windows Server 2025 runner the same build of the same
    application reports `automation_id=''` for every tab item while 71 other
    elements still have ids — the tree is otherwise complete, 201 descendants
    including all 11 tab items, so this is not a missing accessibility bridge.

    The fallback uses what is stable on both: Qt class names. There are three tab
    bars — the document one and the Remote dock's are `QTabBar`, and the dock
    title strip is `QMainWindowTabBar` — so the document tab bar is the `QTabBar`
    that is not inside a dock.
    """
    candidates = win.re_resolve_element().find_all(
        control_type_id=CONTROL_TYPE_TAB, class_name="QTabBar"
    )
    for bar in candidates:
        try:
            if bar.automation_id.endswith(MAIN_TAB_BAR):
                return bar
        except Exception:
            continue
    for bar in candidates:
        if not any("Dock" in name for name in _ancestor_classes(bar)):
            return bar
    return None


def _main_tabs(win: Window) -> list:
    bar = _main_tab_bar(win)
    if bar is None:
        return []
    return bar.find_all(control_type_id=CONTROL_TYPE_TAB_ITEM)


def _describe_tree(win: Window, limit: int = 12) -> str:
    """What the UIA tree actually contains, for a failure message.

    "found 0 tabs" says what is missing; the next question is what is there
    instead. Qt builds its UIA tree by bridging QAccessible, and that bridge can
    be absent rather than slow — in which case the answer is a nearly empty tree,
    not a tree with the tabs somewhere else.
    """
    try:
        elements = win.re_resolve_element().find_all()
    except Exception as exc:
        return f"\n  (could not walk the tree: {type(exc).__name__}: {exc})"

    census: dict[str, int] = {}
    for element in elements:
        try:
            census[element.control_type_name] = census.get(element.control_type_name, 0) + 1
        except Exception:
            continue
    lines = [f"\n  UIA descendants: {len(elements)}"]
    if census:
        ranked = sorted(census.items(), key=lambda kv: -kv[1])[:limit]
        lines.append("\n  control types: " + ", ".join(f"{n}={c}" for n, c in ranked))
    else:
        lines.append("\n  control types: none — the QAccessible bridge is not exposing anything")
    ids = []
    for element in elements:
        try:
            if element.automation_id:
                ids.append(element.automation_id.split(".")[-1])
        except Exception:
            continue
    lines.append(f"\n  automation id tails ({len(ids)} total): {sorted(set(ids))[:limit]}")

    # The full path of every tab item, not just the tail. The filter this fixture
    # uses is an endswith() on the tab bar's object path, so when it matches
    # nothing the question is what the paths actually are.
    for element in elements:
        try:
            if element.control_type_id != CONTROL_TYPE_TAB_ITEM:
                continue
            lines.append(f"\n    TabItem name={element.name!r} id={element.automation_id!r}")
        except Exception:
            continue
    return "".join(lines)


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
            # Qt publishes the window before QAccessible has built the tab bar,
            # so the tab list is empty for a moment after discovery. Every test
            # below starts by counting tabs, and all of them failed with 0 on a
            # slower machine until this wait existed. A positive signal, not a
            # sleep: the count is what the tests need, so wait for it.
            found = settled(
                lambda: len(_main_tabs(win)), lambda n: n == EXPECTED_MAIN_TABS, timeout=60.0
            )
            assert found == EXPECTED_MAIN_TABS, (
                f"the main tab bar never reached {EXPECTED_MAIN_TABS} tabs (saw {found})"
                f"{_describe_tree(win)}"
            )
            yield win
    finally:
        proc.terminate()
        sweep_processes_verified((PROCESS,), ("DB Browser",))


def test_window_class_carries_the_qt_version(browser):
    """Characterisation: records why the class name is not used for matching.

    A failure here means the Qt version moved, and is a prompt to check that no
    matching anywhere depends on the class — not a defect in the app.
    """
    assert browser.class_name.startswith("Qt"), browser.class_name
    assert browser.class_name.endswith("QWindowIcon"), browser.class_name


def test_main_tabs_are_found_without_reading_their_names(browser):
    """Neither names nor positions are usable; ids are usable but not dependable.

    The names are translated and their order has been observed to differ between
    runs, so `_main_tab_bar` uses the object-path id where it exists and Qt class
    names where it does not.
    """
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


def test_the_window_can_be_brought_onscreen(browser):
    """This app restores its saved geometry, which may not fit this screen.

    Measured on an 800x600 machine: the window came back at `(0,0,820,620)`,
    wider and taller than the display. Everything that does not involve a pointer
    kept working — the window was visible and foreground, its UIA tree resolved,
    and `select_verified()` succeeded through the SelectionItem pattern — while
    any click was silently discarded.
    """
    assert browser.ensure_onscreen(), "the window could not be moved onto the screen"


def test_toolbar_buttons_are_not_addressable_by_id(browser):
    """Characterisation, and the reason there is no toolbar-button test here.

    Qt's object paths are excellent for containers and useless for the toolbar:
    every QToolButton reports the *class name* as the last path segment rather
    than the widget's own name, so 19 of the 38 buttons share the id
    `…QToolButton`. Their `name` is the translated tooltip, so neither handle
    identifies one button portably.

    The buttons that do have unique ids — `buttonLogClear`, `buttonApply`,
    `butSavePlot`, and the record-navigation set — live in dock widgets that are
    hidden by default. Qt reports off-screen rectangles for hidden widgets
    (`(-701, -525, -494, -469)` for the log editor while the window sat at
    `(0, 0, 800, 600)`), so clicking them lands nowhere.

    Record navigation is addressable and visible, and still not usable: with 30
    rows there is only one page, so `buttonNext`, `buttonEnd` and `buttonBegin`
    all leave `editGoto` reading `1`.

    A failure here means Qt started exposing widget names for tool buttons, and a
    real toolbar test becomes possible.
    """
    buttons = browser.re_resolve_element().find_all(control_type_id=CONTROL_TYPE_BUTTON)
    assert buttons, "no buttons at all — the toolbar did not render"
    tails = [b.automation_id.split(".")[-1] for b in buttons]
    shared = [t for t in tails if t == "QToolButton"]
    assert len(shared) > 1, (
        "tool buttons no longer share the 'QToolButton' id — they may now be "
        "addressable individually, so a toolbar-button test is worth adding"
    )


def test_sqlitebrowser_is_the_verified_version():
    """Pins the build.

    The window class carries the Qt version and the automation ids are object
    paths, so both are tied to this build more tightly than usual.
    """
    exe = find_executable("DB Browser for SQLite", SQLITEBROWSER_CANDIDATES)
    assert_version(
        "DB Browser for SQLite",
        installed_file_version(exe, "DB Browser for SQLite"),
        VERIFIED_VERSION,
    )
