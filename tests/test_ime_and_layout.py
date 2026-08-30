"""IMM32 state, keyboard layout, and scan-code input.

These cover the machinery an IME-mode test needs: reading the IME state attached
to a window, querying the thread's keyboard layout, and typing through the
physical-key path an IME can actually intercept.

What they cannot cover on a CI runner is *composition itself* — that needs a
Chinese/Japanese IME installed and selected, which the hosted images do not have.
The layout-dependent assertions here are therefore about the API being wired
correctly, not about a specific IME's behaviour.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from win32_dialog_app import DIALOG_TITLE, ID_EDIT

from wintegrate import Window
from wintegrate.interop import (
    IME_CMODE_NATIVE,
    get_keyboard_layout,
    get_keyboard_layout_list,
)

APP = Path(__file__).parent / "win32_dialog_app.py"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="drives a live Win32 dialog")


@pytest.fixture
def dialog():
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


def edit_of(win: Window):
    return win.re_resolve_element().find_descendant(automation_id=str(ID_EDIT), timeout=10.0)


def test_ime_status_shape(dialog):
    """A classic Win32 window answers the IMM32 query with a complete status dict."""
    status = dialog.get_ime_status()
    assert set(status) >= {"has_context", "is_open", "conversion", "sentence"}
    assert isinstance(status["has_context"], bool)
    if status["has_context"]:
        assert isinstance(status["is_open"], bool)
        assert isinstance(status["conversion"], int)


def test_ime_open_state_round_trips(dialog):
    """Opening and closing the IME is observable through the same status read."""
    if not dialog.get_ime_status()["has_context"]:
        pytest.skip("window has no IMM32 context (TSF-only text services)")

    assert dialog.set_ime_open(True) is True
    assert dialog.get_ime_status()["is_open"] is True

    assert dialog.set_ime_open(False) is True
    assert dialog.get_ime_status()["is_open"] is False


def test_ime_conversion_mode_is_settable(dialog):
    if not dialog.get_ime_status()["has_context"]:
        pytest.skip("window has no IMM32 context (TSF-only text services)")

    dialog.set_ime_conversion(IME_CMODE_NATIVE)
    status = dialog.get_ime_status()
    assert status["conversion"] is not None
    assert isinstance(status["native_mode"], bool)


def test_composition_string_is_empty_when_idle(dialog):
    assert dialog.get_composition_string() == ""


def test_keyboard_layout_is_reported_per_window_thread(dialog):
    hkl = dialog.keyboard_layout
    assert hkl != 0
    assert dialog.keyboard_language_id == hkl & 0xFFFF
    # Same window, asked two ways: the thread's layout, not the caller's.
    assert get_keyboard_layout(dialog.hwnd) == hkl


def test_keyboard_layout_list_contains_the_active_layout(dialog):
    layouts = get_keyboard_layout_list()
    assert layouts, "session reports no keyboard layouts"
    assert dialog.keyboard_layout in layouts


def test_physical_keys_deliver_text(dialog):
    """The scan-code path types real characters — the path an IME can intercept."""
    edit = edit_of(dialog)
    assert edit.send_physical_keys("hello") is True
    time.sleep(0.3)
    assert edit.get_value() == "hello"


def test_physical_keys_handle_shifted_characters(dialog):
    """Shift state comes from the layout, so capitals must survive the round trip."""
    edit = edit_of(dialog)
    edit.send_physical_keys("Ab")
    time.sleep(0.3)
    assert edit.get_value() == "Ab"
