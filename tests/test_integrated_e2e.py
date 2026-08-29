"""Integrated live GUI automation and verification test:
- Launches Calculator application
- Resizes and repositions window
- Automates UI calculations using reliable UIA interactions
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

        # 3. Discover or launch live Calculator process
        timeline.record_action("launch_app", text="Launching calc.exe")
        proc, win = session.launch_and_discover(
            ["calc.exe"],
            timeout=12.0,
        )

        timeline.record_action(
            "window_discovered", window=win, details={"hwnd": win.hwnd, "pid": win.pid}
        )

        try:
            # 4. Move and resize window
            win.move_and_resize(60, 60, 420, 520)
            win.set_foreground()
            timeline.record_action(
                "window_repositioned", window=win, details={"rect": [60, 60, 420, 520]}
            )

            # 5. Direct UIA Element resolution
            root = win.re_resolve_element()

            # 6. Perform verified calculation: 1 + 2 + 3 = 6
            timeline.record_action("calc_start", details={"expression": "1 + 2 + 3"})
            root.find_descendant(automation_id="num1Button").click()
            root.find_descendant(automation_id="plusButton").click()
            root.find_descendant(automation_id="num2Button").click()
            root.find_descendant(automation_id="plusButton").click()
            root.find_descendant(automation_id="num3Button").click()
            root.find_descendant(automation_id="equalButton").click()
            timeline.record_action("calc_success")

            # 7. Assert Calculation Result Buffer
            res_elem = root.find_descendant(automation_id="CalculatorResults")
            res_val = res_elem.name
            assert "6" in res_val
            timeline.record_action("buffer_verified", details={"result": res_val})

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
