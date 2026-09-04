"""A window can opt out of being captured, and every other instrument says it is fine.

`SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` makes Windows withhold a
window from *every* capture path -- GDI `BitBlt`, DXGI Desktop Duplication,
`Windows.Graphics.Capture`, DWM thumbnails. Meanwhile `IsWindowVisible` is True,
the cloak reason is 0, the window is on screen, and a person looking at the
monitor sees it. So a recording comes back with a hole in exactly the shape of
the thing under test, and nothing in the run reports a problem.

The case that found this: a session recording of a password manager showed the
credential prompt, the keystroke HUD and every step of the run, with the
application itself absent from the frame. Two explanations were tried and
measured wrong first -- that the app had hidden itself, and that `BitBlt` was
missing `CAPTUREBLT` (identical pixels with and without it) -- before the
hypervisor's own screenshot of the same moment, which did contain the window,
pointed at the window attribute rather than at the capture code.

The excluded state is produced rather than described: the affinity can only be
set by the process that owns the window, which is why nothing outside an
application can clear it, and why the fixture app has to do it to itself.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest
from win32_dialog_app import DIALOG_TITLE

from wintegrate import DisplayAffinity, Window, capture_window_image, get_window_display_affinity

APP = __import__("pathlib").Path(__file__).parent / "win32_dialog_app.py"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="reads a window attribute")


def _dialog(exclude: bool):
    env = None
    if exclude:
        import os

        env = dict(os.environ, WINTEGRATE_TEST_EXCLUDE_FROM_CAPTURE="1")
    proc = subprocess.Popen([sys.executable, str(APP)], env=env)
    try:
        win = Window.find(class_name="#32770", title_exact=DIALOG_TITLE, timeout=20.0)
        time.sleep(0.3)
        yield win
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def plain_dialog():
    yield from _dialog(exclude=False)


@pytest.fixture
def excluded_dialog():
    yield from _dialog(exclude=True)


def test_an_ordinary_window_is_not_excluded(plain_dialog):
    """The control. Without it, a reading that always says 'excluded' would pass."""
    assert plain_dialog.display_affinity is DisplayAffinity.NONE
    assert plain_dialog.is_excluded_from_capture is False


def test_a_window_that_opted_out_is_reported_as_excluded(excluded_dialog):
    assert excluded_dialog.display_affinity is DisplayAffinity.EXCLUDE_FROM_CAPTURE
    assert excluded_dialog.is_excluded_from_capture is True


def test_the_other_instruments_all_say_the_window_is_fine(excluded_dialog):
    """Why this needed its own reading: nothing already here answers it.

    This is the assertion that explains the feature. A caller checking
    visibility, cloaking or on-screen-ness gets three answers that are correct
    and useless, and then trusts a screenshot that cannot contain the window.
    """
    assert excluded_dialog.is_visible is True
    assert not excluded_dialog.is_cloaked
    assert excluded_dialog.is_on_screen is True


def test_capture_of_an_excluded_window_says_so(excluded_dialog, caplog):
    """The capture still returns an image; the point is that it is explained.

    `capture_window_image` falls back to cropping the desktop when PrintWindow
    gives it nothing, and for an excluded window that crop is a picture of the
    wallpaper. Raising instead would take a diagnostic and turn it into a test
    failure, so it stays an image plus a warning that names the reason -- the
    same bargain the recorder makes everywhere else.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="wintegrate.diagnostics"):
        img = capture_window_image(excluded_dialog.hwnd)
    assert img.size[0] > 0 and img.size[1] > 0
    assert any("excluded from capture" in record.message for record in caplog.records), (
        f"no warning named the exclusion; records were {[r.message for r in caplog.records]}"
    )


def test_a_dead_handle_is_unknown_not_none_of_the_above():
    """None means 'could not ask', which is not the same answer as NONE.

    Collapsing them is how a caller ends up promising a recording it cannot
    produce: 'not excluded' is a fact, 'no idea' is not.
    """
    assert get_window_display_affinity(0) is None
    assert get_window_display_affinity(0xDEAD_BEEF) is None
