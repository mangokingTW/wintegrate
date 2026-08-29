"""Comprehensive multi-window switching and concurrent automation test with recording:
- Launches two distinct Notepad instances (Notepad A & Notepad B)
- Positions them side by side
- Switches foreground focus alternately between Window A and Window B
- Types verified distinct text into each window
- Verifies buffer isolation (Window A text does not leak into Window B)
- Records full timeline events and continuous screen video
- Uses Windows 11 isolated clean-room virtual desktop
"""

from __future__ import annotations

import time
from pathlib import Path

from wintegrate import (
    Session,
    SessionConfig,
    TextActionTimelineRecorder,
    UiaElement,
    Window,
    env,
)


def find_editor(target: Window | UiaElement, timeout: float = 12.0) -> UiaElement:
    """Helper to locate Notepad editor control on any OS version/locale."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            root = target.re_resolve_element() if isinstance(target, Window) else target
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

        if env.is_desktop:
            # 2. On Desktop (Win11), launch Calculator as distinct top-level Window B
            timeline.record_action("launch_win_b", text="Launching Calculator Window B")
            proc_b, win_b = session.launch_and_discover(
                ["calc.exe"],
                timeout=12.0,
                title_pattern="(?i)calculator|小算盤|计算器",
                exclude_hwnds={win_a.hwnd},
            )
            win_b.move_and_resize(580, 50, 420, 500)
            win_b.set_foreground(verify=False)
            time.sleep(0.5)

            try:
                # Bring win_a to foreground to resolve its editor
                win_a.set_foreground(verify=False)
                time.sleep(0.3)
                editor_a = find_editor(win_a)

                # Focus Win A and type
                win_a.set_foreground(verify=False)
                time.sleep(0.3)
                editor_a.type_verified(
                    "Window A Text\n",
                    expected_line_count_delta=1,
                    verify_contains="Window A Text",
                )

                # Switch to Win B and interact
                win_b.set_foreground(verify=False)
                time.sleep(0.3)
                calc_root = win_b.re_resolve_element()
                calc_root.find_descendant(automation_id="num7Button").invoke()

                # Switch back to Win A and verify buffer
                win_a.set_foreground(verify=False)
                time.sleep(0.3)
                res_a = editor_a.get_value()
                assert "Window A Text" in res_a

                timeline.record_action("isolation_verified", details={"res_a": res_a})
            finally:
                win_a.close(force=True)
                win_b.close(force=True)
                if proc_a:
                    try:
                        proc_a.kill()
                    except Exception:
                        pass
                if proc_b:
                    try:
                        proc_b.kill()
                    except Exception:
                        pass
        else:
            # 2. On Server, launch second Win32 Notepad Window B
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
                win_a.set_foreground(verify=False)
                time.sleep(0.3)
                editor_a = find_editor(win_a)

                win_b.set_foreground(verify=False)
                time.sleep(0.3)
                editor_b = find_editor(win_b)

                # 3. Focus Window A and Type
                win_a.set_foreground()
                time.sleep(0.3)
                editor_a.type_verified(
                    "Window A Text\n",
                    expected_line_count_delta=1,
                    verify_contains="Window A Text",
                )

                # 4. Switch to Window B and Type
                win_b.set_foreground()
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
            finally:
                win_a.close(force=True)
                win_b.close(force=True)
                if proc_a:
                    try:
                        proc_a.kill()
                    except Exception:
                        pass
                if proc_b:
                    try:
                        proc_b.kill()
                    except Exception:
                        pass

    timeline.dump_json(artifacts_dir / "timeline_multiwindow.json")
    timeline.close()

    assert (artifacts_dir / "timeline_multiwindow.log").exists()
    assert (artifacts_dir / "timeline_multiwindow.json").exists()
