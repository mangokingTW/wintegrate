"""Where an element's text comes from, and what happens when it comes from nowhere.

`get_value()` used to end with `return self.name`. That is not the element's text
— it is its *label* — and substituting one for the other is silent: the reading
comes back a plausible string and every assertion against it passes on nothing.

The case that found it was a WinUI `TextBox` bound to a `{n}` placeholder, whose
Name is `'n'`. An empty field read back as `'n'`, so "the field is not empty" held
while the field was empty.

Against real controls, not mocks, and the interesting part is that the four
sources are all reachable from fixtures that already exist:

    control                        source
    Win32 EDIT, created empty      ValuePattern   ('' — an answer, not a miss)
    Win32 LISTBOX                  WM_GETTEXT     ('')
    Win32 BUTTON                   WM_GETTEXT     ('Browse')
    WPF DataGridCell               ValuePattern   ('row-0')
    WPF DataGrid itself            Name           <- no handle, no text patterns

The last row is the one that used to lie.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from win32_dialog_app import (
    DIALOG_TITLE,
    ID_BROWSE,
    ID_COMBO,
    ID_EDIT,
    ID_LIST,
)

from wintegrate import Window
from wintegrate.element import UiaElement, ValueReading
from wintegrate.exceptions import ValueUnavailableError, WindowDiscoveryTimeoutError

APP = Path(__file__).parent / "win32_dialog_app.py"
WPF_APP = Path(__file__).parent / "wpf_grid_app.ps1"
WPF_TITLE = "wintegrate grid fixture"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="reads live UIA elements")


@pytest.fixture(scope="module")
def dialog():
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
    """A WPF window, for the one thing Win32 cannot provide: a handle-less element.

    Every Win32 control has an HWND, so `WM_GETTEXT` always answers and the Name
    source is unreachable. WPF elements below the top-level window have no handle
    of their own, which is what makes the fourth source testable at all.
    """
    log = Path(tempfile.gettempdir()) / "wpf_value_reading.stderr.log"
    with log.open("w", encoding="utf-8") as errfile:
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(WPF_APP)],
            stdout=subprocess.DEVNULL,
            stderr=errfile,
        )
        try:
            deadline = time.monotonic() + 90.0
            win = None
            while time.monotonic() < deadline:
                try:
                    win = Window.find(title_exact=WPF_TITLE, timeout=1.0)
                    break
                except WindowDiscoveryTimeoutError:
                    if proc.poll() is not None:
                        pytest.fail(
                            "the WPF fixture exited before its window appeared:\n"
                            + log.read_text(encoding="utf-8", errors="replace")[-800:]
                        )
            if win is None:
                pytest.fail("the WPF fixture window did not appear within 90s")
            win.set_foreground(verify=False)
            time.sleep(1.0)
            yield win
        finally:
            try:
                proc.kill()
            except Exception:
                pass


def _find(win: Window, **kwargs) -> UiaElement:
    return win.re_resolve_element().find_descendant(timeout=15.0, **kwargs)


def test_reading_reports_its_source(dialog):
    reading = _find(dialog, automation_id=str(ID_EDIT)).read_value()
    assert isinstance(reading, ValueReading)
    assert reading.source in {"TextPattern", "ValuePattern", "WM_GETTEXT", "Name"}
    # A NamedTuple, so existing code that unpacked a plain string does not
    # silently start indexing characters.
    text, source = reading
    assert (text, source) == (reading.text, reading.source)


def test_an_empty_edit_reads_as_empty_not_as_its_label(dialog):
    """The regression. This control is created with no text at all."""
    edit = _find(dialog, automation_id=str(ID_EDIT))
    reading = edit.read_value()
    assert reading.text == ""
    assert reading.source != "Name"
    assert edit.get_value() == ""


def test_an_empty_native_listbox_answers_rather_than_falling_through(dialog):
    """WM_GETTEXTLENGTH returning 0 is the answer "empty", not "ask something else".

    Treating 0 as a miss is what used to send an empty native control on to the
    Name fallback. DefWindowProc answers WM_GETTEXTLENGTH for every window, so
    there is no case where 0 means the query failed.
    """
    listbox = _find(dialog, automation_id=str(ID_LIST))
    reading = listbox.read_value()
    assert reading.source == "WM_GETTEXT"
    assert reading.text == ""


def test_a_control_carrying_text_reads_it(dialog):
    """The control for the two above: a reading that is always '' proves nothing."""
    button = _find(dialog, automation_id=str(ID_BROWSE))
    assert button.get_value() == "Browse"
    assert _find(dialog, automation_id=str(ID_COMBO)).get_value() == "Alpha layout"


def test_a_handleless_element_with_no_text_patterns_refuses_to_guess(wpf_window):
    """The WPF DataGrid itself: no HWND, no Value, no Text. Only a Name.

    Before this change `get_value()` returned that Name, and a caller asserting
    on the grid's "text" got a plausible-looking string that was never its
    contents.
    """
    grid = _find(wpf_window, automation_id="wintegrate-grid")
    assert grid.handle == 0, "a handle would make WM_GETTEXT answer and skip this path"

    reading = grid.read_value()
    assert reading.source == "Name"
    assert reading.text == grid.name

    with pytest.raises(ValueUnavailableError) as exc:
        grid.get_value()
    message = str(exc.value)
    # The message has to name the element and say what to do instead, or it just
    # moves the guessing somewhere else.
    assert "Name" in message
    assert "allow_name_fallback" in message
    assert "read_value" in message


def test_the_name_fallback_is_available_when_it_is_what_you_want(wpf_window):
    grid = _find(wpf_window, automation_id="wintegrate-grid")
    assert grid.get_value(allow_name_fallback=True) == grid.name


def test_wpf_grid_cells_have_a_real_source(wpf_window):
    """The cells are not the Name case, so the wrapper below is not relying on it."""
    grid = _find(wpf_window, automation_id="wintegrate-grid").as_data_grid()
    cell = grid.row(0).cell(0)
    reading = cell.element.read_value()
    assert reading.source == "ValuePattern"
    assert reading.text == "row-0"


def test_datagridcell_still_reads_through_the_wrapper(wpf_window):
    """`DataGridCell.value` keeps its own Name fallback, deliberately.

    A grid cell is one of the places UIA genuinely puts the displayed text in
    Name, so the wrapper decides for itself via `read_value()` instead of
    inheriting a default that is wrong everywhere else.
    """
    grid = _find(wpf_window, automation_id="wintegrate-grid").as_data_grid()
    assert grid.row(0).values() == ["row-0", "widget", "ready"]
