"""Comprehensive multi-window switching and concurrent automation test with recording:
- Launches two distinct Notepad instances (Notepad A & Notepad B)
- Positions them side by side
- Switches foreground focus alternately between Window A and Window B
- Types verified distinct text into each window
- Verifies buffer isolation (Window A text does not leak into Window B)
- Records full timeline events and continuous screen video
- Uses Windows 11 isolated clean-room virtual desktop
"""

import time
from pathlib import Path

from wintegrate import (
    Session,
    SessionConfig,
    TextActionTimelineRecorder,
    UiaElement,
)


def find_editor(target: Window | UiaElement, timeout: float = 12.0) -> UiaElement:
    """Helper to locate Notepad editor control on any OS version/locale."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            root = target.re_resolve_element() if isinstance(target, Window) else target
            for cond in [
                {"control_type_id": 50030},  # Edit control (Win32 Notepad / Server)
                {"control_type_id": 50004},  # Document/Text Editor control (Win11)
                {"name_contains": "Text Editor"},
                {"name_contains": "Document"},
                {"name_contains": "文字編輯器"},
                {"name_contains": "文本编辑器"},
            ]:
                try:
                    editor = root.find_descendant(**cond, timeout=0.3)
                    if editor:
                        return editor
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError("Could not locate Notepad editor control")


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
        # 1. Launch Notepad A
        timeline.record_action("launch_win_a", text="Launching Notepad Window A")
        proc_a, win_a = session.launch_and_discover(
            ["notepad.exe"],
            timeout=12.0,
            title_pattern=".*Notepad.*|.*記事本.*|.*记事本.*",
        )
        win_a.move_and_resize(50, 50, 500, 400)
        win_a.set_foreground(verify=False)
        time.sleep(0.5)

        # 2. Launch Notepad B (exclude win_a.hwnd so discovery never confuses the two)
        timeline.record_action("launch_win_b", text="Launching Notepad Window B")
        proc_b, win_b = session.launch_and_discover(
            ["notepad.exe"],
            timeout=12.0,
            title_pattern=".*Notepad.*|.*記事本.*|.*记事本.*",
            exclude_hwnds={win_a.hwnd},
        )
        win_b.move_and_resize(580, 50, 500, 400)
        win_b.set_foreground(verify=False)
        time.sleep(0.5)

        try:
            root_a = win_a.re_resolve_element()
            root_b = win_b.re_resolve_element()

            editor_a = find_editor(root_a)
            editor_b = find_editor(root_b)

            # 3. Focus Window A and Type: "Window A Text\n"
            win_a.set_foreground()
            time.sleep(0.3)
            timeline.record_action("focus_win_a", window=win_a)

            editor_a.type_verified(
                "Window A Text\n",
                expected_line_count_delta=1,
                verify_contains="Window A Text",
            )
            time.sleep(0.3)
            timeline.record_action("typed_win_a", text="Window A Text")

            # 4. Switch to Window B and Type: "Window B Text\n"
            win_b.set_foreground()
            time.sleep(0.3)
            timeline.record_action("focus_win_b", window=win_b)

            editor_b.type_verified(
                "Window B Text\n",
                expected_line_count_delta=1,
                verify_contains="Window B Text",
            )
            time.sleep(0.3)
            timeline.record_action("typed_win_b", text="Window B Text")

            # 5. Assert Final Buffers and Isolation
            res_a = editor_a.get_value()
            res_b = editor_b.get_value()

            assert "Window A Text" in res_a
            assert "Window B Text" in res_b
            assert "Window B Text" not in res_a
            assert "Window A Text" not in res_b

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
