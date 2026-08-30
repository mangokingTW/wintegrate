"""Integrated live GUI automation and verification test:
- Launches Notepad via the managed app-lifecycle API (session.app)
- Resizes and repositions window
- Automates UI text input and focus transitions
- Records action timeline events and continuous screen video
"""

from __future__ import annotations

import time
from pathlib import Path

from wintegrate import (
    NOTEPAD,
    Session,
    SessionConfig,
    TextActionTimelineRecorder,
)


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

        # 3. Launch Notepad with managed lifecycle: locale-independent discovery,
        # fresh-instance sweep on CI, and guaranteed cleanup on context exit.
        timeline.record_action("launch_app", text="Launching notepad.exe")
        with session.app(NOTEPAD, timeout=30.0) as app:
            win = app.window
            timeline.record_action(
                "window_discovered", window=win, details={"hwnd": win.hwnd, "pid": win.pid}
            )

            # 4. Move and resize window
            win.move_and_resize(60, 60, 600, 450)
            win.set_foreground(verify=False)
            time.sleep(0.5)
            timeline.record_action(
                "window_repositioned", window=win, details={"rect": [60, 60, 600, 450]}
            )

            # 5. Locate the editor via the locale-independent text-input ladder
            editor = app.find_text_input()

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

        # 8. Cleanup happened on context exit
        timeline.record_action("app_closed")

    # 9. Dump Timeline logs
    timeline.dump_json(artifacts_dir / "timeline.json")
    timeline.close()

    assert (artifacts_dir / "timeline.log").exists()
    assert (artifacts_dir / "timeline.json").exists()
