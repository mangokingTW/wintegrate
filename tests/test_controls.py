"""Grid and tree control wrappers, against real Windows common controls.

The fixture builds a `SysListView32` in report mode and a `SysTreeView32`, whose
UIA providers ship with Windows and expose exactly the patterns these wrappers
drive — Grid / Table / GridItem / TableItem, and TreeItem with ExpandCollapse,
SelectionItem and ScrollItem. Testing against the real providers is the point: a
mocked pattern would only confirm what I assumed UIA does.

The grid is taller than its viewport and the tree three levels deep, so scrolling
and multi-level expansion are exercised rather than described.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from win32_controls_app import DIALOG_TITLE, ID_TREE, TREE_DATA

from wintegrate import Window
from wintegrate.element import UiaElement
from wintegrate.exceptions import ElementNotFoundError, WindowDiscoveryTimeoutError

APP = Path(__file__).parent / "win32_controls_app.py"
WPF_APP = Path(__file__).parent / "wpf_grid_app.ps1"
WPF_TITLE = "wintegrate grid fixture"

# Mirrors the fixture's generator, so the expected value of any row is computed
# rather than duplicated — 200 rows are not worth hard-coding.
GRID_COLUMNS = ["Name", "Kind", "Status"]
GRID_ROW_COUNT = 200
_KINDS = ["widget", "gadget", "gizmo"]
_STATES = ["ready", "failed", "pending"]


def expected_row(i: int) -> list[str]:
    return [f"row-{i}", _KINDS[i % 3], _STATES[i % 3]]


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="drives live Win32 common controls")


@pytest.fixture(scope="module")
def dialog():
    """One fixture process for the module: these controls are read far more than mutated."""
    proc = subprocess.Popen([sys.executable, str(APP)])
    try:
        win = Window.find(class_name="#32770", title_exact=DIALOG_TITLE, timeout=20.0)
        win.set_foreground(verify=False)
        time.sleep(0.5)
        yield win
    finally:
        try:
            proc.kill()
        except Exception:
            pass


@pytest.fixture(scope="module")
def wpf_window():
    """A real WPF DataGrid.

    The Win32 report-mode ListView is a UIA *List* — Selection and Scroll only,
    no GridPattern, its rows plain list items. GridPattern comes from WPF/WinUI,
    and a WPF DataGrid also virtualizes its rows, which is what makes the
    virtualization countermeasures testable rather than merely present.
    """
    # 90s, not the 40s this used to be. A cold Windows Server runner loading
    # PresentationFramework, PresentationCore and WindowsBase for the first time
    # exceeded 40s on test-x64 (3.14) while the other three Python versions on the
    # same image were fine — a timeout tuned on a warm machine failing on a cold
    # one, which reads as a flaky test rather than as a timeout.
    launch_timeout = 90.0
    log = Path(tempfile.gettempdir()) / "wpf_grid_app.stderr.log"
    with log.open("w", encoding="utf-8") as errfile:
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(WPF_APP)],
            stdout=subprocess.DEVNULL,
            stderr=errfile,
        )
        try:
            win = _await_fixture_window(proc, log, launch_timeout)
            win.set_foreground(verify=False)
            time.sleep(1.0)
            yield win
        finally:
            try:
                proc.kill()
            except Exception:
                pass


def _await_fixture_window(proc, log: Path, timeout: float) -> Window:
    """Waits for the fixture window, and says something useful when it never comes.

    Waiting out the whole timeout and reporting "window not found" hides the two
    failures that actually happen: PowerShell exited (a syntax error, a missing
    assembly, an execution policy) or it is still starting. Polling the process
    turns the first into an immediate, informative failure instead of a 90-second
    mystery, and puts its stderr in the message.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return Window.find(title_exact=WPF_TITLE, timeout=1.0)
        except WindowDiscoveryTimeoutError:
            pass
        code = proc.poll()
        if code is not None:
            detail = log.read_text(encoding="utf-8", errors="replace").strip()[-800:]
            raise RuntimeError(
                f"The WPF grid fixture exited with code {code} before its window "
                f"appeared.\nPowerShell stderr:\n{detail or '(empty)'}"
            )
    detail = log.read_text(encoding="utf-8", errors="replace").strip()[-800:]
    raise WindowDiscoveryTimeoutError(
        f"The WPF grid fixture window {WPF_TITLE!r} did not appear within {timeout}s, "
        f"and the PowerShell process is still running (pid {proc.pid}) — so it is "
        f"starting slowly rather than broken.\nPowerShell stderr:\n{detail or '(empty)'}"
    )


@pytest.fixture
def grid(wpf_window):
    element = wpf_window.re_resolve_element().find_descendant(
        automation_id="wintegrate-grid", timeout=15.0
    )
    return element.as_data_grid()


@pytest.fixture
def tree(dialog):
    element = dialog.re_resolve_element().find_descendant(automation_id=str(ID_TREE), timeout=10.0)
    return element.as_tree_view()


# --- DataGrid ---------------------------------------------------------------


def test_grid_reports_its_shape(grid):
    assert grid.row_count == GRID_ROW_COUNT
    assert grid.column_count == len(GRID_COLUMNS)


def test_grid_column_headers(grid):
    assert grid.get_column_headers() == GRID_COLUMNS


def test_cell_lookup_by_coordinates(grid):
    cell = grid.get_cell(0, 0)
    assert cell.value == expected_row(0)[0]
    assert (cell.row, cell.column) == (0, 0)


def test_cell_lookup_by_column_header(grid):
    """A header is the readable way to address a column, and survives reordering."""
    cell = grid.get_cell(2, "Status")
    assert cell.value == expected_row(2)[2]


def test_unknown_column_header_names_the_alternatives(grid):
    with pytest.raises(ElementNotFoundError) as excinfo:
        grid.get_cell(0, "Nonexistent")
    assert "Nonexistent" in str(excinfo.value)
    assert "Name" in str(excinfo.value)


def test_out_of_range_cell_is_an_error_not_an_empty_result(grid):
    with pytest.raises(ElementNotFoundError):
        grid.get_cell(grid.row_count, 0)


def test_reaching_a_row_below_the_viewport(grid):
    """The last row starts off-screen; GetItem addresses data, not pixels."""
    last = GRID_ROW_COUNT - 1
    cell = grid.get_cell(last, 0)
    assert cell.value == expected_row(last)[0]


def test_row_values(grid):
    assert grid.row(1).values() == expected_row(1)


def test_find_row_by_cell_value(grid):
    """Searches every row, including ones never scrolled to."""
    target = expected_row(GRID_ROW_COUNT - 1)[0]
    row = grid.find_row_by_cell_value("Name", target)
    assert row.index == GRID_ROW_COUNT - 1
    assert row.cell("Name").value == target


def test_find_row_by_cell_value_reports_the_search_when_missing(grid):
    with pytest.raises(ElementNotFoundError) as excinfo:
        grid.find_row_by_cell_value("Name", "no-such-value")
    assert str(GRID_ROW_COUNT) in str(excinfo.value)


def test_select_cell_verified(grid):
    assert grid.select_cell_verified(1, 0) is True
    assert grid.get_cell(1, 0).element.is_selected is True


# --- TreeView ---------------------------------------------------------------


def test_tree_root_items(tree):
    assert [item.name for item in tree.root_items] == list(TREE_DATA)


def test_expanding_a_node_reveals_its_children(tree):
    root_name = next(iter(TREE_DATA))
    root = tree.find_root(root_name)
    assert root is not None

    assert root.expand_verified() is True
    assert root.is_expanded is True
    assert [c.name for c in root.children_items()] == list(TREE_DATA[root_name])


def test_collapse_verified(tree):
    root = tree.find_root(next(iter(TREE_DATA)))
    root.expand_verified()
    assert root.collapse_verified() is True
    assert root.is_expanded is False


def test_leaf_reports_itself_as_a_leaf(tree):
    root_name = next(iter(TREE_DATA))
    category_name = next(iter(TREE_DATA[root_name]))
    leaf_name = TREE_DATA[root_name][category_name][0]

    leaf = tree.navigate_path_verified([root_name, category_name, leaf_name])
    assert leaf.is_leaf is True
    # A leaf has nothing to expand, so expanding it is a success, not an error.
    assert leaf.expand_verified() is True


def test_navigate_path_expands_every_ancestor_and_selects_the_target(tree):
    root_name = next(iter(TREE_DATA))
    category_name = next(iter(TREE_DATA[root_name]))
    leaf_name = TREE_DATA[root_name][category_name][0]

    item = tree.navigate_path_verified(f"{root_name}/{category_name}/{leaf_name}")

    assert item.name == leaf_name
    assert item.element.is_selected is True
    assert tree.find_root(root_name).is_expanded is True


def test_navigate_path_accepts_a_list(tree):
    root_name, categories = next(iter(TREE_DATA.items()))
    category_name = next(iter(categories))
    item = tree.navigate_path_verified([root_name, category_name])
    assert item.name == category_name


def test_navigate_path_names_the_missing_segment_and_the_alternatives(tree):
    root_name = next(iter(TREE_DATA))
    with pytest.raises(ElementNotFoundError) as excinfo:
        tree.navigate_path_verified(f"{root_name}/no-such-category")

    message = str(excinfo.value)
    assert "no-such-category" in message
    # "not found" without the alternatives is the least useful failure available.
    assert next(iter(TREE_DATA[root_name])) in message


# --- Virtualization helpers -------------------------------------------------


def test_virtualization_helpers_are_transparent(grid):
    """The countermeasures must be safe to call and leave the element usable.

    An earlier version asserted realize() returns False here, which was true of
    the Win32 ListView that materializes everything. On a WPF DataGrid even the
    first row supports VirtualizedItemPattern, so pinning a particular return
    value tested the fixture rather than the behaviour. What callers rely on is
    that these are safe on anything and change nothing observable.
    """
    cell = grid.get_cell(0, 0)
    assert isinstance(cell.element.realize(), bool)
    assert isinstance(cell.element.scroll_into_view(), bool)
    assert cell.element.ensure_available() is cell.element
    # Still usable afterwards: the countermeasures must be transparent.
    assert cell.value == expected_row(0)[0]


def test_scroll_into_view_reports_support(grid):
    """ScrollItemPattern is what moves an off-screen row into the viewport."""
    cell = grid.get_cell(GRID_ROW_COUNT - 1, 0)
    assert isinstance(cell.element.scroll_into_view(), bool)


def test_report_what_the_providers_actually_expose(dialog):
    """Prints each control's real identity and patterns into the CI log.

    Not an assertion about behaviour — a standing diagnostic. UIA providers differ
    between Windows builds and control versions, and when a wrapper says a control
    "is not a grid", the next question is always what it *is*. Having that in the
    same run that failed removes a round trip.
    """
    root = dialog.re_resolve_element()
    print("\n--- provider report ---")
    for child in root.children():
        print(" ", child.describe())
        for grandchild in child.children()[:3]:
            print("    ", grandchild.describe())
    print("--- end provider report ---")


def test_far_rows_are_genuinely_virtualized(grid):
    """The row is not merely off-screen — it has no UIA peer until realized.

    This is the assertion the Win32 ListView could not support: it materializes
    every item, so the virtualization path was implemented but never exercised. A
    WPF DataGrid creates peers only for the rows near the viewport, so a row 200
    deep is a real virtualized item.
    """
    # Ask the raw element straight from the grid pattern: get_cell() calls
    # ensure_available() for you, and once an item is materialized its
    # VirtualizedItem pattern is gone — so a check made after that is a race,
    # not an assertion.
    raw = UiaElement(grid._grid().GetItem(GRID_ROW_COUNT - 1, 0))
    assert "VirtualizedItem" in raw.supported_patterns()

    cell = grid.get_cell(GRID_ROW_COUNT - 1, 0)
    assert cell.value == expected_row(GRID_ROW_COUNT - 1)[0]


def test_column_headers_survive_a_provider_that_hides_them(grid):
    """WPF answers TablePattern.GetCurrentColumnHeaders with an empty collection.

    The headers live on HeaderItem children instead. Reading only the pattern
    reports a grid with no headers, which reads like the grid has none rather than
    like the query looked in the wrong place — so header lookup falls back.
    """
    assert grid.get_column_headers() == GRID_COLUMNS
