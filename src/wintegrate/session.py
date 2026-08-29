"""Session management, CI environment sanitization, and artifact flushing."""

from __future__ import annotations
import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wintegrate.interop import (
    user32,
    kernel32,
    get_window_title,
    get_window_class,
    SW_MINIMIZE,
    WNDENUMPROC,
)
from wintegrate.diagnostics import (
    ContinuousRecorder,
    WindowCensus,
    WindowSnapshot,
    capture_screen_image,
)
from wintegrate.element import UiaElement
from wintegrate.window import Window

logger = logging.getLogger(__name__)


@dataclass
class SessionConfig:
    artifact_dir: str | Path = "artifacts"
    record_video: bool = True
    fps: int = 30
    sanitize_runner: bool = True
    default_timeout: float = 15.0
    dismiss_oobe: bool = True


def sanitize_ci_runner_environment():
    """
    Cleans up known GitHub Actions CI runner hazards:
    1. Disables orphaned WSL auto-update scheduled task that pops prompt windows every 30s.
    2. Minimizes background console/terminal windows without killing the host runner terminal (titled 'Default').
    """
    # 1. Disable WSL scheduled task by matching action string if task exists
    try:
        ps_cmd = (
            "Get-ScheduledTask | Where-Object { $_.Actions.Execute -like '*wsl.exe*' "
            "-and $_.Actions.Arguments -like '*--prompt-before-exit*' } | "
            "Disable-ScheduledTask -ErrorAction SilentlyContinue"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, timeout=5)
    except Exception as exc:
        logger.debug(f"Runner task sanitization skipped ({type(exc).__name__}): {exc}")

    # 2. Minimize background windows (preserves host terminal 'Default')
    try:
        console_hwnd = kernel32.GetConsoleWindow()
        if console_hwnd:
            user32.ShowWindow(console_hwnd, SW_MINIMIZE)

        def enum_proc(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                title = get_window_title(hwnd).lower()
                cls = get_window_class(hwnd)
                # Match terminal windows, but NEVER close or minimize the runner host window if titled 'default'
                if ("cmd" in title or "powershell" in title) and "default" not in title:
                    user32.ShowWindow(hwnd, SW_MINIMIZE)
                elif cls == "ConsoleWindowClass" and "default" not in title:
                    user32.ShowWindow(hwnd, SW_MINIMIZE)
            return True

        user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
    except Exception as exc:
        logger.debug(f"Window minimization skipped ({type(exc).__name__}): {exc}")


def try_dismiss_oobe_privacy_screen() -> bool:
    """
    Dismisses Windows First-Sign-in / OOBE privacy screens using UIA focus fallback
    and AutomationId substring matching.
    """
    try:
        # Check if an OOBE / CoreWindow is active
        oobe_win = None
        for snap in WindowCensus.capture():
            if "oobe" in snap.class_name.lower() or "oobe" in snap.title.lower():
                oobe_win = snap
                break

        if not oobe_win:
            return False

        logger.info(f"Detected OOBE / Privacy screen: {oobe_win}")
        # Use FocusedElement fallback
        try:
            elem = UiaElement.get_focused()
            logger.info(f"Focused OOBE element: '{elem.name}', ID: '{elem.automation_id}'")
            # Try to find Next/Accept buttons
            parent = UiaElement.from_handle(oobe_win.hwnd)
            btn = parent.find_descendant(automation_id="OobeSettingsAcceptButton", timeout=2.0)
            btn.invoke()
            return True
        except Exception:
            pass
    except Exception as exc:
        logger.debug(f"OOBE dismissal check skipped ({type(exc).__name__}): {exc}")
    return False


class Session:
    """
    Orchestrates test execution environment, continuous video recording,
    pre/post window census diffing, and automatic failure artifact generation.
    """

    def __init__(self, config: SessionConfig | None = None):
        self.config = config or SessionConfig()
        self.artifact_dir = Path(self.config.artifact_dir)
        self.recorder: ContinuousRecorder | None = None
        self.initial_census: list[WindowSnapshot] = []
        self.final_census: list[WindowSnapshot] = []
        self.logs: list[dict[str, Any]] = []

    def log_event(self, event_type: str, message: str, **kwargs):
        """Records a structured event in session log."""
        entry = {
            "timestamp": time.time(),
            "type": event_type,
            "message": message,
            **kwargs,
        }
        self.logs.append(entry)
        logger.info(f"[{event_type}] {message}")

    def __enter__(self) -> Session:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.log_event("SESSION_START", "Starting wintegrate automation session")

        if self.config.sanitize_runner:
            sanitize_ci_runner_environment()
            if self.config.dismiss_oobe:
                try_dismiss_oobe_privacy_screen()

        self.initial_census = WindowCensus.capture()

        if self.config.record_video:
            video_file = self.artifact_dir / "session_recording.mp4"
            self.recorder = ContinuousRecorder(video_file, fps=self.config.fps)
            self.recorder.start()

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.final_census = WindowCensus.capture()
            diff = WindowCensus.diff(self.initial_census, self.final_census)

            if self.recorder:
                self.recorder.stop()

            # If an error occurred, capture failure snapshot immediately
            if exc_type is not None:
                self.log_event(
                    "SESSION_FAILURE",
                    f"Session failed with {exc_type.__name__}: {exc_val}",
                    exc_type=exc_type.__name__,
                )
                try:
                    img = capture_screen_image()
                    img.save(self.artifact_dir / "failure_screenshot.png")
                except Exception as exc:
                    logger.error(f"Failed to save failure screenshot: {exc}")

            # Write census diff and logs to artifact dir
            census_data = {
                "initial_count": len(self.initial_census),
                "final_count": len(self.final_census),
                "added": [w.__dict__ for w in diff.added],
                "removed": [w.__dict__ for w in diff.removed],
            }
            with open(self.artifact_dir / "window_census.json", "w", encoding="utf-8") as f:
                json.dump(census_data, f, indent=2)

            with open(self.artifact_dir / "session_events.json", "w", encoding="utf-8") as f:
                json.dump(self.logs, f, indent=2)

            self.log_event("SESSION_END", "Session completed and artifacts flushed")
        except Exception as exc:
            logger.error(f"Error during session teardown ({type(exc).__name__}): {exc}")

        return False  # Do not suppress original exception

    def find_window(
        self,
        title_exact: str | None = None,
        title_pattern: str | None = None,
        class_name: str | None = None,
        timeout: float | None = None,
    ) -> Window:
        timeout = timeout or self.config.default_timeout
        return Window.find(
            title_exact=title_exact,
            title_pattern=title_pattern,
            class_name=class_name,
            timeout=timeout,
        )

    def launch_and_discover(
        self,
        cmd: list[str] | str,
        title_pattern: str | None = None,
        timeout: float | None = None,
    ) -> tuple[subprocess.Popen, Window]:
        timeout = timeout or self.config.default_timeout
        return Window.launch_and_discover(cmd, timeout=timeout, title_pattern=title_pattern)
