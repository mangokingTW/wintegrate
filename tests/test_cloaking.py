"""Cloaking: the difference between "IsWindowVisible" and "somebody could see it".

`IsWindowVisible` answers True for a window DWM has cloaked. So a WinUI or UWP app
that has hidden itself, and a window sitting on another virtual desktop, both look
visible to it. A test waiting for one of those to disappear waits forever, and a
test asserting one is gone passes while it is still there.

The case that found this: Command Palette dismisses itself on Esc, and
`IsWindowVisible` stayed True across the dismissal — so a probe's *control*
failed, reporting that a keystroke which demonstrably worked had done nothing.

Against real windows. The genuinely-cloaked case is produced with a second
virtual desktop rather than described: a window left behind on another desktop is
shell-cloaked by Windows itself, which is the only way to get a true reading here
without a UWP app to drive.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest
from win32_dialog_app import DIALOG_TITLE

from wintegrate import CloakReason, Window, get_window_cloak_reason
from wintegrate.interop import SW_HIDE, SW_SHOW, user32

APP = __import__("pathlib").Path(__file__).parent / "win32_dialog_app.py"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="reads DWM window attributes")


@pytest.fixture
def dialog():
    """A plain Win32 window. Function-scoped: some tests here hide or move it."""
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


def test_a_normal_window_is_not_cloaked(dialog):
    assert dialog.cloak_reason == CloakReason(0)
    assert dialog.is_cloaked is False
    assert dialog.is_visible is True
    assert dialog.is_on_screen is True


def test_a_dead_handle_cannot_be_asked(dialog):
    """None, not False. "Could not ask" and "not cloaked" are different answers.

    Collapsing them is how a caller ends up treating a window it cannot see as
    being on screen.
    """
    assert get_window_cloak_reason(0) is None
    assert get_window_cloak_reason(0xDEAD_BEEF) is None


def test_hiding_a_window_shows_up_in_both_measures(dialog):
    """SW_HIDE is the case IsWindowVisible does catch, so both agree here."""
    user32.ShowWindow(dialog.hwnd, SW_HIDE)
    time.sleep(0.3)
    assert dialog.is_visible is False
    assert dialog.is_on_screen is False

    user32.ShowWindow(dialog.hwnd, SW_SHOW)
    time.sleep(0.3)
    assert dialog.is_visible is True
    assert dialog.is_on_screen is True


def test_exists_keeps_its_old_meaning_by_default(dialog):
    """The default is IsWindowVisible, unchanged.

    Tightening it silently would change what every existing caller measures, so
    the on-screen check is opt-in.
    """
    assert dialog.exists() is True
    assert dialog.exists(require_visible=False) is True
    assert dialog.exists(require_on_screen=True) is True


def test_a_window_on_another_virtual_desktop_is_cloaked(dialog):
    """The reading that matters, produced rather than asserted from documentation.

    Windows shell-cloaks a window that is not on the current desktop. Throughout
    this, `IsWindowVisible` keeps answering True — which is the whole point.
    """
    pyvda = pytest.importorskip("pyvda", reason="virtual desktop control needs pyvda")

    original = pyvda.VirtualDesktop.current()
    scratch = None
    try:
        scratch = pyvda.VirtualDesktop.create()
        scratch.go()
        time.sleep(1.0)

        # The dialog stayed on the original desktop, so it is now cloaked.
        assert dialog.is_visible is True, "IsWindowVisible still says visible — that is the trap"
        reason = dialog.cloak_reason
        assert reason is not None, "could not read the cloak state at all"
        assert reason != CloakReason(0), f"expected a cloaked window, got {reason!r}"
        assert CloakReason.SHELL in reason, (
            f"expected SHELL cloaking for another desktop, got {reason!r}"
        )
        assert dialog.is_cloaked is True
        assert dialog.is_on_screen is False
        assert dialog.exists() is True, "exists() defaults to IsWindowVisible"
        assert dialog.exists(require_on_screen=True) is False
    finally:
        try:
            original.go()
            time.sleep(0.8)
        except Exception:
            pass
        if scratch is not None:
            try:
                scratch.remove()
            except Exception:
                pass

    time.sleep(0.5)
    assert dialog.is_cloaked is False, "back on the original desktop, nothing should be cloaked"
    assert dialog.is_on_screen is True


def test_cloak_reason_reprs_as_what_it_means():
    """An IntFlag so a diagnostic says SHELL rather than 2."""
    assert "SHELL" in repr(CloakReason.SHELL)
    assert CloakReason(0) == 0
    assert bool(CloakReason(0)) is False
    assert bool(CloakReason.APP) is True
    # Members are ints, so anything comparing against the raw attribute value works.
    assert int(CloakReason.APP) == 1
    assert int(CloakReason.SHELL) == 2
    assert int(CloakReason.INHERITED) == 4
