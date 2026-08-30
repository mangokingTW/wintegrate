"""Comprehensive multi-window switching and concurrent automation test with recording:
- Launches two distinct top-level windows (Notepad + Calculator on Desktop, dual Notepad on Server)
- Positions them side by side
- Switches foreground focus alternately between Window A and Window B
- Types verified distinct text into each window
- Verifies buffer isolation (Window A text does not leak into Window B)
- Records full timeline events and continuous screen video
"""

from __future__ import annotations

import time
from pathlib import Path

from wintegrate import (
    CALCULATOR,
    NOTEPAD,
    Session,
    SessionConfig,
    TextActionTimelineRecorder,
    env,
)


def test_multi_window_switching_and_typing():
    artifacts_dir = Path("recording-artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    timeline = TextActionTimelineRecorder(output_path=artifacts_dir / "timeline_multiwindow.log")
    timeline.record_action(
        "multi_window_suite_start", text="Testing multi-window switching & typing"
    )

    config = SessionConfig(
        artifact_dir=artifacts_dir,
        record_video=True,
        fps=30,
        default_timeout=15.0,
    )

    with Session(config) as session:
        # 1. Launch Notepad A (managed lifecycle: cleanup guaranteed on context exit)
        timeline.record_action("launch_win_a", text="Launching Notepad Window A")
        with session.app(NOTEPAD, timeout=30.0) as app_a:
            win_a = app_a.window
            win_a.move_and_resize(50, 50, 500, 400)
            win_a.set_foreground(verify=False)
            time.sleep(0.5)
            editor_a = app_a.find_text_input()

            if env.is_desktop:
                # 2. On Desktop (Win11), launch Calculator as distinct top-level Window B
                timeline.record_action("launch_win_b", text="Launching Calculator Window B")
                with session.app(CALCULATOR, timeout=30.0, exclude_hwnds={win_a.hwnd}) as app_b:
                    win_b = app_b.window
                    win_b.move_and_resize(580, 50, 420, 500)
                    win_b.set_foreground(verify=False)
                    time.sleep(0.5)

                    # 3. Focus Window A and type
                    win_a.set_foreground(verify=False)
                    time.sleep(0.3)
                    editor_a.type_verified(
                        "Window A Text\n",
                        expected_line_count_delta=1,
                        verify_contains="Window A Text",
                    )

                    # 4. Switch to Window B, drive it, and verify its own state changed
                    win_b.set_foreground(verify=False)
                    time.sleep(0.3)
                    calc_root = win_b.re_resolve_element()
                    calc_root.find_descendant(automation_id="num7Button").invoke()
                    display = calc_root.find_descendant(
                        automation_id="CalculatorResults", timeout=5.0
                    )
                    assert "7" in display.name

                    # 5. Switch back to Window A and assert isolation in both directions
                    win_a.set_foreground(verify=False)
                    time.sleep(0.3)
                    res_a = editor_a.get_value()
                    assert "Window A Text" in res_a
                    assert "7" not in res_a

                    timeline.record_action(
                        "isolation_verified",
                        details={"res_a": res_a, "calc_display": display.name},
                    )
            else:
                # 2. On Server, launch second Win32 Notepad Window B. fresh=False:
                # sweeping leftover instances here would kill Window A.
                timeline.record_action("launch_win_b", text="Launching Notepad Window B")
                with session.app(
                    NOTEPAD, timeout=30.0, fresh=False, exclude_hwnds={win_a.hwnd}
                ) as app_b:
                    win_b = app_b.window
                    win_b.move_and_resize(580, 50, 500, 400)
                    win_b.set_foreground(verify=False)
                    time.sleep(0.5)
                    editor_b = app_b.find_text_input()

                    # 3. Focus Window A and Type
                    win_a.set_foreground(verify=False)
                    time.sleep(0.3)
                    editor_a.type_verified(
                        "Window A Text\n",
                        expected_line_count_delta=1,
                        verify_contains="Window A Text",
                    )

                    # 4. Switch to Window B and Type
                    win_b.set_foreground(verify=False)
                    time.sleep(0.3)
                    editor_b.type_verified(
                        "Window B Text\n",
                        expected_line_count_delta=1,
                        verify_contains="Window B Text",
                    )

                    # 5. Assert Buffers
                    res_a = editor_a.get_value()
                    res_b = editor_b.get_value()
                    assert "Window A Text" in res_a
                    assert "Window B Text" in res_b
                    assert "Window B Text" not in res_a
                    assert "Window A Text" not in res_b

                    timeline.record_action(
                        "isolation_verified", details={"res_a": res_a, "res_b": res_b}
                    )

    timeline.dump_json(artifacts_dir / "timeline_multiwindow.json")
    timeline.close()

    assert (artifacts_dir / "timeline_multiwindow.log").exists()
    assert (artifacts_dir / "timeline_multiwindow.json").exists()
