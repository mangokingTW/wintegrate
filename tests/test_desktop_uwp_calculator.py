"""Desktop-exclusive test: UWP Calculator automation and Virtual Desktop isolation.
This test is automatically skipped on Windows Server editions.
"""

from __future__ import annotations

import time
from pathlib import Path

from wintegrate import (
    Session,
    SessionConfig,
    TextActionTimelineRecorder,
    desktop_only,
)


@desktop_only
def test_desktop_uwp_calculator_automation():
    artifacts_dir = Path("recording-artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    timeline = TextActionTimelineRecorder(output_path=artifacts_dir / "timeline_calc.log")
    timeline.record_action("suite_init", text="Starting Desktop UWP Calculator test")

    # Desktop editions (Windows 11) support Virtual Desktop isolation & video recording
    config = SessionConfig(
        artifact_dir=artifacts_dir,
        record_video=True,
        fps=30,
        sanitize_runner=False,
        isolated_virtual_desktop=True,
        default_timeout=15.0,
    )

    with Session(config) as session:
        timeline.record_action("session_entered", details={"fps": 30})

        # Launch UWP Calculator with localized title matching
        timeline.record_action("launch_app", text="Launching calc.exe")
        proc, win = session.launch_and_discover(
            ["calc.exe"],
            timeout=12.0,
            title_pattern="(?i)calculator|小算盤|计算器|calculadora|calculatrice|rechner|電卓",
        )

        timeline.record_action(
            "window_discovered", window=win, details={"hwnd": win.hwnd, "pid": win.pid}
        )

        try:
            win.move_and_resize(60, 60, 420, 520)
            win.set_foreground(verify=False)
            timeline.record_action(
                "window_repositioned", window=win, details={"rect": [60, 60, 420, 520]}
            )

            root = win.re_resolve_element()

            # Perform calculation: 1 + 2 + 3 = 6
            timeline.record_action("calc_start", details={"expression": "1 + 2 + 3"})
            root.find_descendant(automation_id="num1Button").invoke()
            root.find_descendant(automation_id="plusButton").invoke()
            root.find_descendant(automation_id="num2Button").invoke()
            root.find_descendant(automation_id="plusButton").invoke()
            root.find_descendant(automation_id="num3Button").invoke()
            root.find_descendant(automation_id="equalButton").invoke()
            time.sleep(0.3)
            timeline.record_action("calc_success")

            # Assert result buffer
            res_elem = root.find_descendant(automation_id="CalculatorResults")
            res_val = res_elem.name
            assert "6" in res_val
            timeline.record_action("buffer_verified", details={"result": res_val})

        finally:
            win.close(force=True)
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            timeline.record_action("app_closed", window=win)

    timeline.dump_json(artifacts_dir / "timeline_calc.json")
    timeline.close()

    assert (artifacts_dir / "timeline_calc.log").exists()
    assert (artifacts_dir / "timeline_calc.json").exists()
