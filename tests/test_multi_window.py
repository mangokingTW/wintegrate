"""Comprehensive multi-window switching and concurrent automation test with recording:
- Launches two distinct Calculator instances (Calculator A & Calculator B)
- Positions them side by side
- Switches foreground focus alternately between Window A and Window B
- Interacts with controls in each window
- Verifies calculation and state isolation (Window A state does not leak into Window B)
- Records full timeline events and continuous screen video
- Uses Windows 11 isolated clean-room virtual desktop
"""

import time
from pathlib import Path

from wintegrate import (
    Session,
    SessionConfig,
    TextActionTimelineRecorder,
)


def test_multi_window_switching_and_typing():
    artifacts_dir = Path("recording-artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    timeline = TextActionTimelineRecorder(output_path=artifacts_dir / "timeline_multiwindow.log")
    timeline.record_action(
        "multi_window_suite_start", text="Testing multi-window switching & interaction"
    )

    config = SessionConfig(
        artifact_dir=artifacts_dir,
        record_video=True,
        fps=30,
        sanitize_runner=True,
        isolated_virtual_desktop=True,
        default_timeout=15.0,
    )

    with Session(config) as session:
        # 1. Launch Calculator A
        timeline.record_action("launch_win_a", text="Launching Calculator Window A")
        proc_a, win_a = session.launch_and_discover(
            ["calc.exe"],
            timeout=12.0,
        )
        win_a.move_and_resize(50, 50, 420, 520)
        win_a.set_foreground()
        time.sleep(0.5)

        # 2. Launch Calculator B (exclude win_a.hwnd so discovery never confuses the two)
        timeline.record_action("launch_win_b", text="Launching Calculator Window B")
        proc_b, win_b = session.launch_and_discover(
            ["cmd.exe", "/c", "start", "calc.exe"],
            timeout=12.0,
            exclude_hwnds={win_a.hwnd},
        )
        win_b.move_and_resize(500, 50, 420, 520)
        win_b.set_foreground()
        time.sleep(0.5)

        try:
            root_a = win_a.re_resolve_element()
            root_b = win_b.re_resolve_element()

            # 3. Focus Window A and Perform Calculation: 7 + 8 = 15
            win_a.set_foreground()
            time.sleep(0.3)
            timeline.record_action("focus_win_a", window=win_a)

            root_a.find_descendant(automation_id="num7Button").click()
            root_a.find_descendant(automation_id="plusButton").click()
            root_a.find_descendant(automation_id="num8Button").click()
            root_a.find_descendant(automation_id="equalButton").click()
            timeline.record_action("calc_win_a", text="7 + 8")

            # 4. Switch to Window B and Perform Calculation: 9 + 9 = 18
            win_b.set_foreground()
            time.sleep(0.3)
            timeline.record_action("focus_win_b", window=win_b)

            root_b.find_descendant(automation_id="num9Button").click()
            root_b.find_descendant(automation_id="plusButton").click()
            root_b.find_descendant(automation_id="num9Button").click()
            root_b.find_descendant(automation_id="equalButton").click()
            timeline.record_action("calc_win_b", text="9 + 9")

            time.sleep(0.3)

            # 5. Assert Final Buffers and Isolation
            res_a = root_a.find_descendant(automation_id="CalculatorResults").name
            res_b = root_b.find_descendant(automation_id="CalculatorResults").name

            assert "15" in res_a
            assert "18" in res_b

            timeline.record_action("isolation_verified", details={"res_a": res_a, "res_b": res_b})

        finally:
            # 6. Clean up both windows
            win_a.close(force=True)
            win_b.close(force=True)
            for p in (proc_a, proc_b):
                if p:
                    try:
                        p.kill()
                    except Exception:
                        pass
