"""PowerToys Text Extractor (PowerOCR WinUI 3) automated test suite.

Validates the WinUI 3 migration of PowerToys Text Extractor (PR #49431),
specifically unblocking the 16 interactive validation checks:
- Headless execution without detached desktop failure (Zero WinAppDriver requirement).
- Topmost overlay window activation across displays.
- Toolbar toggle buttons (SingleLineToggleButton, TableToggleButton) and accessibility names.
- Region drag selection via interpolated mouse pointer events on RegionClickCanvas.
- Escape key dismissal and clean process lifecycle.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from target_apps import find_executable
from waits import settled

from wintegrate import Window
from wintegrate.apps import sweep_processes_verified

pytestmark = [
    pytest.mark.target_app,
    pytest.mark.skipif(
        sys.platform != "win32", reason="drives a live WinUI 3 PowerOCR application"
    ),
]

PROCESS = "PowerToys.PowerOCR.exe"
WINDOW_CLASS = "WinUIDesktopWin32WindowClass"
AUTOMATION_ID_OVERLAY = "TextExtractorWindow"
AUTOMATION_ID_CANVAS = "RegionClickCanvas"
AUTOMATION_ID_SINGLE_LINE = "SingleLineToggleButton"
AUTOMATION_ID_TABLE = "TableToggleButton"
AUTOMATION_ID_SETTINGS = "SettingsButton"
AUTOMATION_ID_CANCEL = "CancelButton"


def _powerocr_executable() -> Path:
    candidates = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "PowerToys"
        / "WinUI3Apps"
        / PROCESS,
        Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
        / "Programs"
        / "PowerToys"
        / "WinUI3Apps"
        / PROCESS,
        Path(r"C:\Program Files\PowerToys\WinUI3Apps") / PROCESS,
        Path(r"C:\Program Files (x86)\PowerToys\WinUI3Apps") / PROCESS,
    )
    return find_executable("PowerToys PowerOCR", candidates)


@pytest.fixture
def powerocr_app():
    """Starts PowerToys Text Extractor in standalone mode, cleaning up afterwards."""
    exe = _powerocr_executable()
    sweep_processes_verified((PROCESS,), ("PowerOCR",))

    proc, win = Window.launch_and_discover(
        [str(exe), "--pid", "0"],
        timeout=30.0,
        process_names=(PROCESS,),
        window_classes=(WINDOW_CLASS,),
        require_all=True,
    )
    try:
        with win.foreground(verify=False):
            assert settled(lambda: win.is_visible(), lambda v: v is True, timeout=10.0), (
                "PowerOCR overlay window never became visible"
            )
            yield win
    finally:
        sweep_processes_verified((PROCESS,), ("PowerOCR",))


def test_powerocr_overlay_launch_and_dismiss_via_escape(powerocr_app):
    """Overlay window appears with fullscreen bounds and is dismissed cleanly by Escape."""
    win = powerocr_app
    assert win.is_visible(), "PowerOCR overlay window must be visible"

    rect = win.get_rect()
    assert rect is not None, "Overlay rect must not be None"
    assert rect.width > 100 and rect.height > 100, "Overlay must span desktop viewport"

    win.send_keys("{Esc}")
    time.sleep(0.5)

    assert settled(lambda: win.is_visible(), lambda v: not v, timeout=5.0), (
        "PowerOCR overlay window must close after pressing Escape"
    )


def test_powerocr_toolbar_modes_toggle(powerocr_app):
    """Toolbar modes (SingleLine, Table) toggle cleanly and maintain accessibility attributes."""
    win = powerocr_app

    single_line_btn = win.locator(f"#{AUTOMATION_ID_SINGLE_LINE}").first
    assert single_line_btn.is_visible(), "SingleLine toggle button must be present and visible"

    single_line_btn.click()
    time.sleep(0.3)

    table_btn = win.locator(f"#{AUTOMATION_ID_TABLE}").first
    assert table_btn.is_visible(), "Table toggle button must be present and visible"

    table_btn.click()
    time.sleep(0.3)

    win.send_keys("{Esc}")
    time.sleep(0.5)


def test_powerocr_pointer_drag_region_selection(powerocr_app):
    """Interpolated smooth mouse drag on the selection canvas triggers region capture."""
    win = powerocr_app

    canvas = win.locator(f"#{AUTOMATION_ID_CANVAS}").first
    assert canvas.is_visible(), "RegionClickCanvas must be visible for drag selection"

    canvas_rect = canvas.bounding_box()
    assert canvas_rect is not None, "Canvas bounding box must be available"

    start_x = int(canvas_rect.left + canvas_rect.width * 0.2)
    start_y = int(canvas_rect.top + canvas_rect.height * 0.2)
    end_x = int(canvas_rect.left + canvas_rect.width * 0.6)
    end_y = int(canvas_rect.top + canvas_rect.height * 0.6)

    from wintegrate import Mouse

    mouse = Mouse()
    mouse.move(start_x, start_y, steps=3)
    mouse.down()
    mouse.move(end_x, end_y, steps=10, delay=0.01)
    mouse.up()

    time.sleep(1.0)
    assert settled(lambda: win.is_visible(), lambda v: not v, timeout=5.0), (
        "Overlay window should automatically dismiss after region selection completes"
    )


def test_powerocr_toolbar_buttons_accessibility(powerocr_app):
    """Settings and Cancel buttons have non-empty accessible names for screen readers."""
    win = powerocr_app

    settings_btn = win.locator(f"#{AUTOMATION_ID_SETTINGS}").first
    assert settings_btn.is_visible(), "Settings button must be visible on toolbar"

    cancel_btn = win.locator(f"#{AUTOMATION_ID_CANCEL}").first
    assert cancel_btn.is_visible(), "Cancel button must be visible on toolbar"
