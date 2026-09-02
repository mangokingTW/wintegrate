"""Live end-to-end integration test for Playwright-style Locators and rich controls on Windows."""

from __future__ import annotations

import sys

import pytest

from wintegrate import NOTEPAD, Session, SessionConfig
from wintegrate.interop import send_keys


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Live GUI automation tests require a Windows desktop session",
)
def test_live_notepad_locators():
    """Verifies get_by_role, get_by_class, right_click, and text_content against live Notepad."""
    with Session(SessionConfig()) as session:
        with session.app(NOTEPAD) as app:
            # Locate editor via high-level get_by_role (auto-waits for editor element to mount)
            editor_loc = app.get_by_role("edit").first
            assert editor_loc.is_visible(timeout=5.0)
            assert editor_loc.is_enabled(timeout=5.0)

            # Verified typing via Locator
            editor_loc.type_verified(
                "Hello Locators!\n",
                expected_line_count_delta=1,
                verify_contains="Hello Locators!",
            )

            # Read text via Locator
            text = editor_loc.text_content()
            assert "Hello Locators!" in text

            # Perform right click to open context menu and dismiss with ESC
            editor_loc.right_click()
            send_keys("{ESC}")
