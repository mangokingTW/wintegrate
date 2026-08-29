"""Integrated live GUI automation and verification test:
- Discovers or launches Notepad
- Resizes and repositions window
- Types text using verified unicode hardware simulation (verified line counts & buffer delta)
- Records action timeline events and continuous screen video
- Uses Windows 11 isolated clean-room virtual desktop
"""

from __future__ import annotations

from pathlib import Path

from wintegrate import (
    Session,
    SessionConfig,
    TextActionTimelineRecorder,
)
from wintegrate.exceptions import WindowDiscoveryTimeoutError


def test_live_gui_automation_with_recording():
    artifacts_dir = Path("recording-artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # 1. Initialize text action timeline recorder
    timeline = TextActionTimelineRecorder(output_path=artifacts_dir / "timeline.log")
    timeline.record_action("suite_init", text="Starting live GUI automation & recording test")

    # 2. Configure session with continuous screen recording, window census, and virtual desktop isolation
    config = SessionConfig(
        artifact_dir=artifacts_dir,
        record_video=True,
        fps=30,
        sanitize_runner=True,
        isolated_virtual_desktop=True,
        default_timeout=15.0,
    )

    with Session(config) as session:
        timeline.record_action("session_entered", details={"fps": 30})

        # 3. Discover or launch live Notepad process
        timeline.record_action("launch_app", text="Launching notepad.exe")
        try:
            win = session.find_window(title_pattern=".*Notepad.*|.*記事本.*", timeout=2.0)
            proc = None
        except WindowDiscoveryTimeoutError:
            proc, win = session.launch_and_discover(
                ["notepad.exe"],
                title_pattern=".*Notepad.*|.*記事本.*",
                timeout=12.0,
            )

        timeline.record_action("window_discovered", window=win, details={"hwnd": win.hwnd, "pid": win.pid})

        try:
            # 4. Move and resize window
            win.move_and_resize(60, 60, 600, 450)
            win.set_foreground()
            timeline.record_action("window_repositioned", window=win, details={"rect": [60, 60, 600, 450]})

            # 5. Direct UIA Element resolution
            root = win.re_resolve_element()
            try:
                editor = root.find_descendant(control_type_id=50004, timeout=5.0)
            except Exception:
                try:
                    editor = root.find_descendant(name_contains="Text Editor", timeout=5.0)
                except Exception:
                    editor = root.find_descendant(name_contains="Document", timeout=5.0)

            timeline.record_action("editor_located", target=editor)

            # 6. Verified Hardware Keystroke Input
            input_text = "wintegrate ci automation\nline 2: verified keystrokes\n"
            timeline.record_action("type_verified_start", target=editor, text=input_text)
            editor.type_verified(
                input_text,
                verify_contains="wintegrate ci automation\nline 2: verified keystrokes",
                delay_per_char=0.03,
            )
            timeline.record_action("type_verified_success", target=editor)

            # 7. Assert ValuePattern / TextPattern Buffer
            final_value = editor.get_value()
            assert "wintegrate ci automation" in final_value
            assert "line 2: verified keystrokes" in final_value
            timeline.record_action("buffer_verified", target=editor, details={"buffer_length": len(final_value)})

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
