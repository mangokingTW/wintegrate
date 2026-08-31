"""The ergonomics layer: context managers that establish state and give it back.

These exist because the underlying mechanisms are easy to get wrong in ways that
fail silently — an IME mode left switched, a foreground window never returned —
and a `with` block is the one construct Python guarantees will run the teardown.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from waits import settled
from win32_dialog_app import DIALOG_TITLE, ID_EDIT

from wintegrate import ImeConversion, Session, SessionConfig, Window
from wintegrate.interop import get_foreground_window, get_ime_conversion

APP = Path(__file__).parent / "win32_dialog_app.py"


@pytest.fixture
def dialog():
    proc = subprocess.Popen([sys.executable, str(APP)])
    try:
        win = Window.find(title_exact=DIALOG_TITLE, timeout=20.0)
        win.set_foreground(verify=False)
        time.sleep(0.4)
        yield win
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_ime_mode_restores_the_previous_mode(dialog):
    """The block leaves the desktop as it found it."""
    before = get_ime_conversion(dialog.hwnd)
    with dialog.ime_mode(ImeConversion.ALPHANUMERIC):
        pass
    assert get_ime_conversion(dialog.hwnd) == before


def test_ime_mode_does_not_invent_a_state_to_restore(dialog, monkeypatch):
    """A None reading means "no IME answered", not "alphanumeric".

    Restoring a guess would leave the machine in a state the caller never asked
    for, so an unreadable initial mode must mean "restore nothing".
    """
    calls = []
    monkeypatch.setattr("wintegrate.window.get_ime_conversion", lambda hwnd: None)
    monkeypatch.setattr(
        Window, "set_ime_conversion", lambda self, c, sentence=0: calls.append(c) or True
    )
    with dialog.ime_mode(ImeConversion.NATIVE):
        pass
    assert calls == [int(ImeConversion.NATIVE)], "must not call set again on exit"


def test_foreground_gives_the_window_back(dialog):
    """The foreground window is shared state; a block that takes it must return it."""
    other = subprocess.Popen([sys.executable, str(APP)])
    try:
        # Two dialogs share a title, so find the one that is not ours.
        time.sleep(2)
        outsider = next(
            w for w in (Window(s.hwnd, s.pid) for s in _visible_dialogs()) if w.hwnd != dialog.hwnd
        )
        outsider.set_foreground(verify=False)
        assert settled(get_foreground_window, lambda h: h == outsider.hwnd) == outsider.hwnd

        with dialog.foreground(verify=False):
            assert settled(get_foreground_window, lambda h: h == dialog.hwnd) == dialog.hwnd
        assert settled(get_foreground_window, lambda h: h == outsider.hwnd) == outsider.hwnd
    finally:
        other.terminate()
        other.wait(timeout=5)


def _visible_dialogs():
    from wintegrate.diagnostics import WindowCensus

    return [s for s in WindowCensus.capture() if s.is_visible and s.title == DIALOG_TITLE]


def test_step_names_the_failure(tmp_path):
    """A failure inside a step says which step, in the message and the artifacts."""
    with Session(SessionConfig(artifact_dir=tmp_path, record_video=False)) as session:
        with pytest.raises(ValueError) as excinfo:
            with session.step("submit the form"):
                raise ValueError("boom")
        assert "[submit the form]" in str(excinfo.value)
        assert "boom" in str(excinfo.value)

        kinds = [e["type"] for e in session.logs]
        assert "step_start" in kinds and "step_failed" in kinds


def test_step_records_success_with_a_duration(tmp_path):
    with Session(SessionConfig(artifact_dir=tmp_path, record_video=False)) as session:
        with session.step("a step that works"):
            pass
        ok = [e for e in session.logs if e["type"] == "step_ok"]
        assert ok and ok[0]["message"] == "a step that works"
        assert isinstance(ok[0]["seconds"], float)


def test_ime_conversion_prints_as_what_it_means():
    """An IntFlag makes a diagnostic artifact readable without a lookup table."""
    combined = ImeConversion.NATIVE | ImeConversion.FULLSHAPE
    assert int(combined) == 0x0009
    assert "NATIVE" in repr(combined) and "FULLSHAPE" in repr(combined)
    assert ImeConversion.ALPHANUMERIC == 0  # still an int, old code keeps working


def test_element_equality_asks_uia(dialog):
    """Two resolved references to one element are equal; `is` would say no."""
    a = dialog.re_resolve_element().find_descendant(automation_id=str(ID_EDIT), timeout=10.0)
    b = dialog.re_resolve_element().find_descendant(automation_id=str(ID_EDIT), timeout=10.0)
    assert a is not b
    assert a == b


def test_elements_are_unhashable(dialog):
    """A stale-able remote handle has no stable hash, so refuse to pretend."""
    elem = dialog.re_resolve_element()
    with pytest.raises(TypeError):
        {elem}


def test_reprs_are_useful_and_never_raise(dialog):
    assert f"{dialog.hwnd:#x}" in repr(dialog)
    assert "Window" in repr(dialog)
    elem = dialog.re_resolve_element()
    assert "UiaElement" in repr(elem)
    assert repr(Window(0xDEAD_BEEF))  # a dead hwnd must still format


def test_step_records_windows_that_came_and_went(tmp_path):
    """A step's census delta covers what the session-level pair cannot see.

    The session census runs once at start and once at end, so a window that
    appears during the run and is gone before the end leaves no trace at all —
    and that is exactly the window worth knowing about. Per-step boundaries catch
    it.
    """
    import subprocess as sp

    with Session(SessionConfig(artifact_dir=tmp_path, record_video=False)) as session:
        proc = sp.Popen([sys.executable, str(APP)])
        try:
            with session.step("a window appears"):
                Window.find(title_exact=DIALOG_TITLE, timeout=20.0)
            added = [e for e in session.logs if e["type"] == "step_ok" and e.get("windows_added")]
            assert added, "the step should have recorded the new window"
            titles = [w["title"] for w in added[0]["windows_added"]]
            assert any(DIALOG_TITLE in t for t in titles), titles
        finally:
            proc.terminate()
            proc.wait(timeout=5)


def test_discovery_timeout_says_what_was_on_the_desktop():
    """\"Window failed to appear\" is only half an answer; the other half is what did.

    Only the census itself is asserted. The first version of this test also
    required the "nothing new appeared at all" line, and CI produced a counter-
    example within the hour: a Windows Widgets panel opened by itself during the
    three-second wait, so something new *had* appeared and the line was correctly
    absent. Asserting that a live desktop stays quiet is the same environment
    dependency this helper exists to expose.
    """
    from wintegrate.exceptions import WindowDiscoveryTimeoutError

    with pytest.raises(WindowDiscoveryTimeoutError) as excinfo:
        Window.launch_and_discover(
            [sys.executable, "-c", "pass"],  # exits at once, opens nothing
            timeout=3.0,
            window_classes=("NoSuchWindowClass",),
        )
    message = str(excinfo.value)
    assert "Visible windows at the moment discovery gave up" in message
    # The census must name real windows, whatever else the desktop is doing.
    assert "class=" in message and "pid=" in message
    # And it must not claim the class we were looking for was among them.
    assert "NoSuchWindowClass" not in message.split("Visible windows")[1]
