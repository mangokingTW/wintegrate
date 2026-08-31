"""IMM32 state, keyboard layout, and scan-code input.

These cover the machinery an IME-mode test needs: reading the IME state attached
to a window, querying the thread's keyboard layout, and typing through the
physical-key path an IME can actually intercept.

What they cannot cover on a CI runner is *composition itself* — that needs a
Chinese/Japanese IME installed and selected, which the hosted images do not have.
The layout-dependent assertions here are therefore about the API being wired
correctly, not about a specific IME's behaviour.

Nor do they assert interception, though it is real: on a zh-TW ARM64 desktop,
send_physical_keys("hello") into the fixture's EDIT produces "" under layout
0x04040404 and "hello" under 0x04090409. Whether the Bopomofo layout actually
swallows those letters depends on its conversion mode at that moment, and the
mode cannot be forced from here — the control routes text services through TSF,
so IMM32 hands out no context for set_ime_conversion to act on. An assertion
about it would be a coin flip on the machine's current IME state, which is the
environment dependency `latin_dialog` exists to remove.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from waits import value_in, value_of
from win32_dialog_app import DIALOG_TITLE, ID_EDIT

from wintegrate import ImeConversion, Window, requires_ime
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


@pytest.fixture
def latin_dialog(dialog):
    """The fixture dialog with a plain Latin layout active.

    A scan code means whatever the active layout says it means. On a zh-TW
    desktop the Bopomofo layout reads unshifted letters as phonetic keys and
    swallows them into composition, so send_physical_keys("hello") leaves the
    field empty — the IME working as designed, not the injection failing.
    Pinning the layout keeps the two tests below about "do scan codes arrive"
    rather than "does this machine happen to have no IME installed".
    """
    with dialog.ime_mode(ImeConversion.ALPHANUMERIC):
        yield dialog


def test_physical_keys_deliver_text(latin_dialog):
    """The scan-code path types real characters — the path an IME can intercept."""
    latin_dialog.set_foreground(verify=False)
    edit = edit_of(latin_dialog)
    assert edit.send_physical_keys("hello") is True
    assert value_of(edit, "hello") == "hello"


def test_physical_keys_handle_shifted_characters(latin_dialog):
    """Shift state comes from the layout, so capitals must survive the round trip."""
    latin_dialog.set_foreground(verify=False)
    edit = edit_of(latin_dialog)
    edit.send_physical_keys("Ab")
    assert value_of(edit, "Ab") == "Ab"


def test_status_does_not_claim_to_detect_an_ime(dialog):
    """get_ime_status must not carry a field that claims "an IME is active".

    `ImmIsIME` looks like that answer and is not: on a zh-TW desktop it returns
    true for a plain en-GB layout as soon as that layout is loaded, so a
    `layout_has_ime` field would read True on every loaded layout. A field that
    is always True is worse than no field, because callers branch on it.
    """
    status = dialog.get_ime_status()
    assert "has_context" in status
    assert "layout_has_ime" not in status
    assert not hasattr(dialog, "keyboard_layout_has_ime")


@requires_ime
def test_native_mode_intercepts_unshifted_scan_codes(dialog):
    """The behaviour `latin_dialog` exists to neutralise, asserted head-on.

    This is what separates send_physical_keys from type_verified: scan codes
    reach the IME. Both directions are asserted, because only the pair rules out
    "this machine never types anything" as the explanation for the empty field.
    """
    edit = edit_of(dialog)

    def type_hello() -> str:
        edit.send_keys("{HOME}+{END}{DELETE}")
        value_of(edit, "")
        edit.send_physical_keys("hello")
        # Either outcome is a legitimate end state here — this waits for whichever
        # one this mode produces and stops as soon as it has.
        return value_in(edit, ("hello", ""))

    with dialog.ime_mode(ImeConversion.NATIVE):
        swallowed = type_hello()

    with dialog.ime_mode(ImeConversion.ALPHANUMERIC):
        delivered = type_hello()

    assert delivered == "hello", "alphanumeric mode must let scan codes through"
    assert swallowed == "", "native mode must take the same keystrokes into composition"
