"""E2E verification tests for wintegrate using standard Windows Notepad."""

import os
import time
import pytest
from wintegrate import Session, SessionConfig, Window


def test_notepad_launch_type_verified(tmp_path):
    config = SessionConfig(
        artifact_dir=tmp_path / "artifacts",
        record_video=True,
        fps=30,
        sanitize_runner=False,
        default_timeout=15.0,
    )

    with Session(config) as session:
        # Launch modern tabbed Notepad (which exhibits launcher PID != window HWND PID)
        proc, win = session.launch_and_discover(["notepad.exe"], title_pattern=".*Notepad.*")
        try:
            assert win.hwnd != 0
            assert win.is_visible

            # Re-resolve fresh UIA element
            root_elem = win.re_resolve_element()

            # Find the main edit document control
            edit_elem = root_elem.find_descendant(name_contains="Text Editor", timeout=5.0)
            if not edit_elem:
                # Fallback to any edit/document control
                edit_elem = root_elem.find_descendant(name_contains="Document", timeout=2.0)

            # Test hardware typing and newline verification
            # Typing 'First line\nSecond line' should result in 2 lines with verified content
            edit_elem.type_verified(
                "First line\nSecond line\n",
                expected_line_count_delta=2,
                verify_contains="First line\nSecond line",
                delay_per_char=0.01,
            )

        finally:
            win.close(force=True)
            try:
                proc.kill()
            except Exception:
                pass

    # Verify session artifacts created
    assert (tmp_path / "artifacts" / "window_census.json").exists()
    assert (tmp_path / "artifacts" / "session_events.json").exists()
