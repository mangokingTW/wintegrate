"""Integrated live GUI automation and verification test:
- Launches Notepad application
- Resizes and repositions window
- Automates UI text input and focus transitions
- Records action timeline events and continuous screen video
- Uses Windows 11 isolated clean-room virtual desktop
"""

from __future__ import annotations

import time
from pathlib import Path

from wintegrate import (
    Session,
    SessionConfig,
    TextActionTimelineRecorder,
)


def find_editor(win: Window, timeout: float = 12.0) -> UiaElement:
    """Helper to locate Notepad editor control on any OS version/locale."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            root = win.re_resolve_element()
            for cond in [
                {"class_name": "RichEditD2DPT", "timeout": 0.5},  # Win11 Tabbed Notepad
                {"class_name": "Edit", "timeout": 0.5},  # Win32 Classic Notepad
                {"control_type_id": 50030, "timeout": 0.5},  # Edit ControlType
                {"control_type_id": 50004, "timeout": 0.5},  # Document ControlType
                {"name_contains": "Text Editor", "timeout": 0.5},
                {"name_contains": "Document", "timeout": 0.5},
                {"name_contains": "文字編輯器", "timeout": 0.5},
                {"name_contains": "文本编辑器", "timeout": 0.5},
            ]:
                try:
                    editor = root.find_descendant(**cond)
                    if editor:
                        return editor
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError("Could not locate Notepad editor control")


def test_live_gui_automation_with_recording():
    artifacts_dir = Path("recording-artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # 1. Initialize text action timeline recorder
    timeline = TextActionTimelineRecorder(output_path=artifacts_dir / "timeline.log")
    timeline.record_action("suite_init", text="Starting live GUI automation & recording test")

    # 2. Configure session with continuous screen recording and adaptive platform auto-isolation
    config = SessionConfig(
        artifact_dir=artifacts_dir,
        record_video=True,
        fps=30,
        default_timeout=15.0,
    )

    with Session(config) as session:
        timeline.record_action("session_entered", details={"fps": 30})

        # 3. Discover or launch live Notepad process
        timeline.record_action("launch_app", text="Launching notepad.exe")
        proc, win = session.launch_and_discover(
            ["notepad.exe"],
            timeout=12.0,
            title_pattern=".*Notepad.*|.*記事本.*|.*记事本.*",
        )

        timeline.record_action(
            "window_discovered", window=win, details={"hwnd": win.hwnd, "pid": win.pid}
        )

        try:
            # 4. Move and resize window
            win.move_and_resize(60, 60, 600, 450)
            win.set_foreground(verify=False)
            time.sleep(0.5)
            timeline.record_action(
                "window_repositioned", window=win, details={"rect": [60, 60, 600, 450]}
            )

            # 5. Direct UIA Element resolution & Find Editor
            editor = find_editor(win)

            # 6. Perform verified typing
            timeline.record_action("type_start", details={"text": "wintegrate ci\n"})
            editor.type_verified(
                "wintegrate ci\n",
                expected_line_count_delta=1,
                verify_contains="wintegrate ci",
            )
            timeline.record_action("type_success")

            # 7. Assert text value
            val = editor.get_value()
            assert "wintegrate ci" in val
            timeline.record_action("buffer_verified", details={"result": val})

        finally:
            # 8. Clean up
            win.close(force=True)
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            timeline.record_action("app_closed", window=win)

    # 9. Dump Timeline logs
    timeline.dump_json(artifacts_dir / "timeline.json")
    timeline.close()

    assert (artifacts_dir / "timeline.log").exists()
    assert (artifacts_dir / "timeline.json").exists()
