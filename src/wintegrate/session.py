"""Session management, CI environment sanitization, and artifact flushing."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wintegrate.diagnostics import (
    ContinuousRecorder,
    WindowCensus,
    WindowSnapshot,
    capture_screen_image,
)
from wintegrate.element import UiaElement
from wintegrate.interop import (
    SW_HIDE,
    SW_MINIMIZE,
    WNDENUMPROC,
    attach_to_input_desktop,
    get_window_class,
    get_window_title,
    kernel32,
    user32,
)
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
    2. Terminates background Edge browser welcome prompts and WSL popups.
    3. Minimizes background console/terminal windows without killing the host runner terminal (titled 'Default').
    """
    attach_to_input_desktop()

    # 1. Disable WSL scheduled task and kill noisy background prompts
    try:
        ps_cmd = (
            "Get-ScheduledTask | Where-Object { $_.Actions.Execute -like '*wsl.exe*' "
            "-or $_.TaskName -like '*wsl*' } | "
            "Disable-ScheduledTask -ErrorAction SilentlyContinue; "
            "Get-Process -Name 'wsl','msedge' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, timeout=5)
    except Exception as exc:
        logger.debug(f"Runner task sanitization skipped ({type(exc).__name__}): {exc}")

    # 2. Minimize/hide noisy background windows (preserves host terminal 'Default')
    try:
        console_hwnd = kernel32.GetConsoleWindow()
        if console_hwnd:
            user32.ShowWindow(console_hwnd, SW_MINIMIZE)

        def enum_proc(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                title = get_window_title(hwnd).lower()
                cls = get_window_class(hwnd)
                # Match WSL, Edge welcome screens, search popups
                if "wsl" in title:
                    user32.ShowWindow(hwnd, SW_HIDE)
                elif "edge" in title and ("welcome" in title or "first run" in title):
                    user32.ShowWindow(hwnd, SW_HIDE)
                elif "search" in title and cls == "Windows.UI.Core.CoreWindow":
                    user32.ShowWindow(hwnd, SW_HIDE)
                elif ("cmd" in title or "powershell" in title) and "default" not in title:
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
        logger.info(f"[{event_type}] {message} ({kwargs})")

    def __enter__(self) -> Session:
        logger.info("Starting Wintegrate UI automation session...")
        attach_to_input_desktop()

        if self.config.sanitize_runner:
            sanitize_ci_runner_environment()

        if self.config.dismiss_oobe:
            try_dismiss_oobe_privacy_screen()

        # Capture baseline window state
        self.initial_census = WindowCensus.capture()
        self.log_event("session_start", "Baseline window census captured", count=len(self.initial_census))

        # Start continuous video recording if requested
        if self.config.record_video:
            video_path = self.artifact_dir / "session_recording.mp4"
            self.recorder = ContinuousRecorder(output_path=video_path, fps=self.config.fps)
            if self.recorder.start():
                self.log_event("video_recording_started", f"Streaming to {video_path}")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logger.info("Tearing down Wintegrate UI automation session...")

        # Stop recorder
        if self.recorder:
            frames = self.recorder.stop()
            self.log_event("video_recording_stopped", "Recorder finalized", frames=frames)

        # Capture final census and compute diff
        self.final_census = WindowCensus.capture()
        diff = WindowCensus.diff(self.initial_census, self.final_census)
        self.log_event(
            "session_end",
            "Final window census captured",
            added=len(diff.added),
            removed=len(diff.removed),
            persisted=len(diff.persisted),
        )

        # Always save window census
        self._save_census_dump(diff)

        # If an exception occurred during the test, take failure snapshot
        if exc_type is not None:
            logger.error(f"Test failed with {exc_type.__name__}: {exc_val}. Capturing failure artifact.")
            self._capture_failure_screenshot()

        # Flush session logs
        self._flush_session_logs()

        return False  # Do not suppress exceptions

    def _save_census_dump(self, diff):
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            census_path = self.artifact_dir / "window_census.json"
            census_data = {
                "initial_count": len(self.initial_census),
                "final_count": len(self.final_census),
                "added": [s.__dict__ for s in diff.added],
                "removed": [s.__dict__ for s in diff.removed],
            }
            with open(census_path, "w", encoding="utf-8") as f:
                json.dump(census_data, f, indent=2)
            logger.info(f"Saved window census diff to {census_path}")
        except Exception as exc:
            logger.error(f"Failed to dump window census ({type(exc).__name__}): {exc}")

    def _capture_failure_screenshot(self):
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = self.artifact_dir / "failure_screenshot.png"
            img = capture_screen_image()
            img.save(screenshot_path)
            logger.info(f"Saved failure screenshot to {screenshot_path}")
        except Exception as exc:
            logger.error(f"Failed to capture failure screenshot ({type(exc).__name__}): {exc}")

    def _flush_session_logs(self):
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            events_path = self.artifact_dir / "session_events.json"
            with open(events_path, "w", encoding="utf-8") as f:
                json.dump(self.logs, f, indent=2)
        except Exception as exc:
            logger.error(f"Failed to flush session events ({type(exc).__name__}): {exc}")

    def find_window(
        self,
        title_exact: str | None = None,
        title_pattern: str | None = None,
        class_name: str | None = None,
        timeout: float | None = None,
    ) -> Window:
        """Finds an existing top-level window matching criteria."""
        to = timeout or self.config.default_timeout
        return Window.find(title_exact=title_exact, title_pattern=title_pattern, class_name=class_name, timeout=to)

    def launch_and_discover(
        self,
        cmd: list[str] | str,
        timeout: float | None = None,
        title_pattern: str | None = None,
        exclude_hwnds: set[int] | None = None,
    ) -> tuple[subprocess.Popen, Window]:
        """Wrapper around Window.launch_and_discover with session logging."""
        to = timeout or self.config.default_timeout
        self.log_event("launch_app", f"Launching {cmd}")
        proc, win = Window.launch_and_discover(cmd, timeout=to, title_pattern=title_pattern, exclude_hwnds=exclude_hwnds)
        self.log_event("window_discovered", f"Window '{win.title}' (HWND: {win.hwnd}, PID: {win.pid})")
        return proc, win
