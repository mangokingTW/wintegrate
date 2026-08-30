"""Live coverage for classic Win32 dialogs (`#32770`) and their control set.

The rest of the suite drives Store apps (Notepad, Calculator), which are XAML.
Classic dialogs are a different provider entirely — HWND-based controls, control
ids instead of automation ids, Toggle/Selection/ExpandCollapse patterns — and are
what an application's own settings dialogs actually look like. This exercises that
path against the fixture in `win32_dialog_app.py`.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from win32_dialog_app import (
    DIALOG_TITLE,
    ID_CHECK_ONCE,
    ID_COMBO,
    ID_EDIT,
    ID_LIST,
    ID_OK,
)

from wintegrate import Window
from wintegrate.exceptions import WindowDiscoveryTimeoutError

APP = Path(__file__).parent / "win32_dialog_app.py"


@pytest.fixture
def dialog():
    """Launches the dialog fixture in its own process and guarantees cleanup."""
    proc = subprocess.Popen([sys.executable, str(APP)])
    try:
        win = Window.find(class_name="#32770", title_exact=DIALOG_TITLE, timeout=20.0)
        win.set_foreground(verify=False)
        time.sleep(0.3)
        yield win
    finally:
        try:
            proc.kill()
        except Exception:
            pass


def control(win: Window, ctrl_id: int):
    """Resolves a dialog control by its control id, which UIA exposes as AutomationId."""
    return win.re_resolve_element().find_descendant(automation_id=str(ctrl_id), timeout=10.0)


def test_dialog_is_a_real_win32_dialog(dialog):
    assert dialog.class_name == "#32770"
    assert dialog.exists() is True


def test_controls_resolve_by_control_id(dialog):
    """Every control the fixture declares is reachable by its numeric id."""
    for ctrl_id in (ID_OK, ID_LIST, ID_EDIT, ID_COMBO, ID_CHECK_ONCE):
        assert control(dialog, ctrl_id) is not None, f"control {ctrl_id} not found"


def test_missing_control_returns_none_when_not_required(dialog):
    root = dialog.re_resolve_element()
    assert root.find_descendant(automation_id="9999", timeout=1.0, required=False) is None


def test_edit_accepts_verified_typing(dialog):
    edit = control(dialog, ID_EDIT)
    edit.type_verified("C:/app.exe", verify_contains="C:/app.exe")
    assert "C:/app.exe" in edit.get_value()


def test_checkbox_toggle_pattern(dialog):
    check = control(dialog, ID_CHECK_ONCE)
    assert check.toggle_state == 0

    check.set_toggle_verified(True)
    assert check.toggle_state == 1

    # Already in the target state: Toggle() would flip it back, so this must no-op.
    check.set_toggle_verified(True)
    assert check.toggle_state == 1

    check.set_toggle_verified(False)
    assert check.toggle_state == 0


def test_combobox_expand_collapse_and_selection(dialog):
    combo = control(dialog, ID_COMBO)
    assert combo.expand_collapse_state == 0

    combo.expand_verified(True)
    assert combo.expand_collapse_state == 1

    combo.expand_verified(False)
    assert combo.expand_collapse_state == 0

    selected = combo.get_selection()
    assert selected and selected[0].name == "Alpha layout"


def test_listbox_children_and_selection(dialog):
    lst = control(dialog, ID_LIST)
    items = lst.children()
    assert [i.name for i in items] == ["rule one", "rule two", "rule three"]

    items[1].select_verified()
    assert items[1].is_selected is True
    assert lst.get_selection()[0].name == "rule two"


def test_find_all_filters_by_control_type_and_name(dialog):
    root = dialog.re_resolve_element()
    buttons = root.find_all(class_name="Button")
    names = {b.name for b in buttons}
    assert {"Browse", "Close", "Apply once", "Default enable"} <= names

    # AND semantics: name filter applies on top of the class filter.
    browse = root.find_all(class_name="Button", name_contains="Brow")
    assert [b.name for b in browse] == ["Browse"]


def test_send_keys_reaches_the_dialog(dialog):
    edit = control(dialog, ID_EDIT)
    edit.type_verified("abc", verify_contains="abc")

    # Ctrl+A then Delete clears the field: proves modifiers and named keys both land.
    edit.send_keys("^a")
    edit.send_keys("{DELETE}")
    time.sleep(0.2)
    assert edit.get_value() == ""


def test_buttons_are_invokable(dialog):
    """The Close button (IDOK) exposes InvokePattern and can be driven."""
    close = control(dialog, ID_OK)
    assert close.name == "Close"
    assert close.invoke() is True


def test_closing_the_dialog_is_observable(dialog):
    assert dialog.exists() is True
    dialog.close(force=True)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and dialog.exists():
        time.sleep(0.1)
    assert dialog.exists() is False

    with pytest.raises(WindowDiscoveryTimeoutError):
        Window.find(class_name="#32770", title_exact=DIALOG_TITLE, timeout=1.0)
