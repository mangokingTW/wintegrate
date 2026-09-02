"""Live end-to-end integration test for Mouse controller and gestures on Windows."""

from __future__ import annotations

import sys

import pytest

from wintegrate import NOTEPAD, Session, SessionConfig


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Live GUI automation tests require a Windows desktop session",
)
def test_live_mouse_controller_and_gestures():
    """Verifies session.mouse, hover, and position tracking against live Notepad."""
    with Session(SessionConfig()) as session:
        with session.app(NOTEPAD) as app:
            # Query physical cursor position
            pos = session.mouse.position
            assert isinstance(pos, tuple)
            assert len(pos) == 2

            # Smooth interpolated movement
            session.mouse.move(250, 250, steps=4, delay=0.01)
            new_pos = session.mouse.position
            assert abs(new_pos[0] - 250) <= 20
            assert abs(new_pos[1] - 250) <= 20

            # Element & Locator hover
            editor = app.get_by_role("edit").first
            assert editor.is_visible(timeout=5.0)
            editor.hover(steps=3)

            # Wheel scrolling over editor
            session.mouse.wheel(delta_y=120)
            session.mouse.wheel(delta_y=-120)
