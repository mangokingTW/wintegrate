"""Comprehensive multi-window switching and concurrent automation test with recording:
- Launches two distinct Notepad instances (Notepad A & Notepad B)
- Positions them side by side
- Switches foreground focus alternately between Window A and Window B
- Types verified distinct text into each window
- Verifies buffer isolation (Window A text does not leak into Window B)
- Records full timeline events and continuous screen video
"""

import time
from pathlib import Path

from wintegrate import (
    Session,
    SessionConfig,
    TextActionTimelineRecorder,
    Window,
)


def test_multi_window_switching_and_typing():
    artifacts_dir = Path("recording-artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    timeline = TextActionTimelineRecorder(output_path=artifacts_dir / "timeline_multiwindow.log")
    timeline.record_action("multi_window_suite_start", text="Testing multi-window switching & typing")

    config = SessionConfig(
        artifact_dir=artifacts_dir,
        record_video=True,
        fps=30,
        sanitize_runner=True,
        default_timeout=15.0,
    )

    with Session(config) as session:
        # 1. Launch Notepad A
        timeline.record_action("launch_win_a", text="Launching Notepad Window A")
        proc_a, win_a = session.launch_and_discover(
            ["notepad.exe"],
            title_pattern=".*Notepad.*|.*記事本.*",
            timeout=12.0,
        )
        win_a.move_and_resize(50, 50, 500, 400)
        win_a.set_foreground()
        time.sleep(0.5)

        # 2. Launch Notepad B
        timeline.record_action("launch_win_b", text="Launching Notepad Window B")
        proc_b, win_b = Window.launch_and_discover(
            ["notepad.exe"],
            title_pattern=".*Notepad.*|.*記事本.*",
            timeout=12.0,
        )
        win_b.move_and_resize(580, 50, 500, 400)
        win_b.set_foreground()
        time.sleep(0.5)

        try:
            # 3. Locate editors
            root_a = win_a.re_resolve_element()
            try:
                editor_a = root_a.find_descendant(name_contains="Text Editor", timeout=3.0)
            except Exception:
                editor_a = root_a.find_descendant(name_contains="Document", timeout=3.0)

            root_b = win_b.re_resolve_element()
            try:
                editor_b = root_b.find_descendant(name_contains="Text Editor", timeout=3.0)
            except Exception:
                editor_b = root_b.find_descendant(name_contains="Document", timeout=3.0)

            # 4. Focus Window A and Type Text A
            win_a.set_foreground()
            time.sleep(0.3)
            timeline.record_action("focus_win_a", window=win_a)
            text_a = "Window A: First Stream\nLine A2\n"
            editor_a.type_verified(
                text_a,
                verify_contains="Window A: First Stream\nLine A2",
                delay_per_char=0.03,
            )
            timeline.record_action("typed_win_a", target=editor_a, text=text_a)

            # 5. Switch to Window B and Type Text B
            win_b.set_foreground()
            time.sleep(0.3)
            timeline.record_action("focus_win_b", window=win_b)
            text_b = "Window B: Second Stream\nLine B2\n"
            editor_b.type_verified(
                text_b,
                verify_contains="Window B: Second Stream\nLine B2",
                delay_per_char=0.03,
            )
            timeline.record_action("typed_win_b", target=editor_b, text=text_b)

            # 6. Switch back to Window A and append additional text
            win_a.set_foreground()
            time.sleep(0.3)
            timeline.record_action("refocus_win_a", window=win_a)
            text_a_extra = "Line A3: Verified Append\n"
            editor_a.type_verified(
                text_a_extra,
                verify_contains="Line A3: Verified Append",
                delay_per_char=0.03,
            )

            # 7. Assert Final Buffers and Isolation
            val_a = editor_a.get_value()
            val_b = editor_b.get_value()

            assert "Window A: First Stream" in val_a
            assert "Line A3: Verified Append" in val_a
            assert "Window B: Second Stream" not in val_a

            assert "Window B: Second Stream" in val_b
            assert "Window A: First Stream" not in val_b

            timeline.record_action("isolation_verified", details={"val_a_len": len(val_a), "val_b_len": len(val_b)})

        finally:
            # 8. Clean up both windows
            win_a.close(force=True)
            win_b.close(force=True)
            for p in (proc_a, proc_b):
                if p:
                    try:
                        p.kill()
                    except Exception:
                        pass
            timeline.record_action("all_windows_closed")

    timeline.dump_json(artifacts_dir / "timeline_multiwindow.json")
    timeline.close()

    assert (artifacts_dir / "timeline_multiwindow.log").exists()
    assert (artifacts_dir / "timeline_multiwindow.json").exists()
