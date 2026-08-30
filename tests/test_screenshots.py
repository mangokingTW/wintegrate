"""Screenshot capture: desktop, single window, single element, and via a Session.

Every assertion here checks that the image has *content*. A capture API that
returns a correctly-sized black rectangle is the worst possible outcome — the
artifact looks like evidence and shows nothing — so "the file exists" and "the
dimensions are right" are not enough on their own.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from win32_dialog_app import DIALOG_TITLE, ID_LIST

from wintegrate import (
    Session,
    SessionConfig,
    Window,
    capture_screen_image,
    capture_window_image,
)

APP = Path(__file__).parent / "win32_dialog_app.py"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="captures a live Win32 window")


@pytest.fixture
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


def assert_has_content(img, label: str):
    """An image with no non-black pixel is a failed capture wearing a costume."""
    assert img.size[0] > 0 and img.size[1] > 0, f"{label}: empty image"
    assert img.getbbox() is not None, f"{label}: image is entirely black"


def test_desktop_capture_has_content():
    img = capture_screen_image()
    assert_has_content(img, "primary display")


def test_virtual_desktop_capture_covers_at_least_the_primary_display():
    primary = capture_screen_image(all_monitors=False)
    virtual = capture_screen_image(all_monitors=True)
    assert_has_content(virtual, "virtual desktop")
    assert virtual.size[0] >= primary.size[0]
    assert virtual.size[1] >= primary.size[1]


def test_window_capture_matches_the_window_rect(dialog):
    img = capture_window_image(dialog.hwnd)
    assert_has_content(img, "window")

    # The fixture asks for a 520x420 window; the frame makes the real rect differ,
    # so compare against the window's own reported size rather than that request.
    root = dialog.re_resolve_element()
    left, top, right, bottom = root.bounding_rectangle
    assert abs(img.size[0] - (right - left)) <= 2
    assert abs(img.size[1] - (bottom - top)) <= 2


def test_window_capture_saves_to_a_path(dialog, tmp_path):
    out = tmp_path / "nested" / "dialog.png"
    img = dialog.capture(out)
    assert out.exists() and out.stat().st_size > 0
    assert_has_content(img, "saved window capture")


def test_element_capture_is_the_size_of_the_element(dialog, tmp_path):
    listbox = dialog.re_resolve_element().find_descendant(automation_id=str(ID_LIST))
    left, top, right, bottom = listbox.bounding_rectangle

    out = tmp_path / "listbox.png"
    img = listbox.capture(out)

    assert out.exists()
    assert_has_content(img, "element capture")
    assert abs(img.size[0] - (right - left)) <= 2
    assert abs(img.size[1] - (bottom - top)) <= 2
    # An element is a fraction of the desktop; if these matched, the crop did not
    # happen and the "element" capture is really a full screenshot.
    assert img.size[0] < capture_screen_image().size[0]


def test_session_screenshot_lands_in_the_artifact_dir(tmp_path):
    config = SessionConfig(artifact_dir=tmp_path / "artifacts", record_video=False)
    with Session(config) as session:
        path = session.capture_screenshot("checkpoint")

    assert path == tmp_path / "artifacts" / "checkpoint.png"
    assert path.exists() and path.stat().st_size > 0


def test_session_screenshot_can_target_one_window(dialog, tmp_path):
    config = SessionConfig(artifact_dir=tmp_path / "artifacts", record_video=False)
    with Session(config) as session:
        path = session.capture_screenshot("dialog-only.png", window=dialog)

    assert path.name == "dialog-only.png"
    assert path.exists()

    from PIL import Image

    with Image.open(path) as img:
        assert_has_content(img, "session window screenshot")
        assert img.size[0] < capture_screen_image().size[0]
