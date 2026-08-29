"""Comprehensive live GUI automation test with verified interactions and recording:
- Launches live Notepad window
- Starts continuous streaming screen recording (MP4)
- Records action timeline events (TextActionTimelineRecorder)
- Repositions and focuses window
- Performs verified multi-line typing and assertions
- Reads back text buffer
- Closes window and verifies census diff
- Generates artifacts for GitHub CI Summary
"""

import time
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

    # 2. Configure session with continuous screen recording & window census
    config = SessionConfig(
        artifact_dir=artifacts_dir,
        record_video=True,
        fps=30,
        sanitize_runner=True,
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
            time.sleep(0.5)

            # 5. Resolve UIA root and find editor control
            root = win.re_resolve_element()
            try:
                editor = root.find_descendant(name_contains="Text Editor", timeout=3.0)
            except Exception:
                editor = root.find_descendant(name_contains="Document", timeout=3.0)

            timeline.record_action("editor_located", target=editor)

            # 6. Type verified multi-line text with hardware keystrokes (alphanumeric for universal CI runner support)
            test_content = "wintegrate ci automation\nline 2: verified keystrokes\n"
            timeline.record_action("type_verified_start", target=editor, text=test_content)
            editor.type_verified(
                test_content,
                verify_contains="wintegrate ci automation\nline 2: verified keystrokes",
                delay_per_char=0.04,
            )
            timeline.record_action("type_verified_done", target=editor, text=test_content)

            # 7. Read and assert final buffer text
            val = editor.get_value()
            assert "wintegrate ci automation" in val
            timeline.record_action("text_read_verified", text=val)
            time.sleep(0.5)

        finally:
            # 8. Close window and process
            win.close(force=True)
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            timeline.record_action("app_closed")

    timeline.dump_json(artifacts_dir / "timeline.json")
    timeline.close()

    # Verify all artifacts exist
    assert (artifacts_dir / "timeline.log").exists()
    assert (artifacts_dir / "timeline.json").exists()
    assert (artifacts_dir / "window_census.json").exists()
    assert (artifacts_dir / "session_events.json").exists()

    # Video artifact check
    mp4_file = artifacts_dir / "session_recording.mp4"
    if session.recorder and session.recorder._ffmpeg_exe:
        assert mp4_file.exists()
        assert mp4_file.stat().st_size > 0
