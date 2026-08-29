"""Window discovery and management with snapshot diffing and fresh handle re-resolution."""

from __future__ import annotations
import re
import subprocess
import time
import logging
from wintegrate.interop import (
    user32,
    get_window_title,
    get_window_class,
    get_window_pid,
    get_foreground_window,
    attach_to_input_desktop,
    SW_RESTORE,
)
from wintegrate.diagnostics import WindowCensus
from wintegrate.element import UiaElement
from wintegrate.exceptions import WindowDiscoveryTimeoutError

logger = logging.getLogger(__name__)


class Window:
    """
    Represents a top-level native window.
    Always re-resolves UIA elements on demand to prevent stale COM pointers.
    """

    def __init__(self, hwnd: int, pid: int = 0):
        self.hwnd = hwnd
        self._pid = pid or get_window_pid(hwnd)

    @property
    def pid(self) -> int:
        if not self._pid:
            self._pid = get_window_pid(self.hwnd)
        return self._pid

    @property
    def title(self) -> str:
        return get_window_title(self.hwnd)

    @property
    def class_name(self) -> str:
        return get_window_class(self.hwnd)

    @property
    def is_visible(self) -> bool:
        return bool(user32.IsWindowVisible(self.hwnd))

    def re_resolve_element(self) -> UiaElement:
        """Always resolves a fresh UIA element directly from the HWND."""
        return UiaElement.from_handle(self.hwnd)

    def set_foreground(self, verify: bool = True, timeout: float = 2.0) -> bool:
        """Brings the window to the foreground and verifies active status."""
        attach_to_input_desktop()
        user32.ShowWindow(self.hwnd, SW_RESTORE)
        user32.SetForegroundWindow(self.hwnd)
        user32.BringWindowToTop(self.hwnd)

        if not verify:
            return True

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            fg = get_foreground_window()
            if fg == self.hwnd:
                return True
            time.sleep(0.05)
        return False

    def move_and_resize(self, x: int, y: int, width: int, height: int, repaint: bool = True):
        """Reposition window on screen."""
        user32.MoveWindow(self.hwnd, x, y, width, height, repaint)

    def close(self, force: bool = False, timeout: float = 3.0):
        """Closes window gracefully via WM_CLOSE or forces termination."""
        WM_CLOSE = 0x0010
        user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not user32.IsWindow(self.hwnd):
                return
            time.sleep(0.05)

        if force and self.pid:
            subprocess.run(["taskkill", "/F", "/PID", str(self.pid)], capture_output=True)

    @classmethod
    def find(
        cls,
        title_exact: str | None = None,
        title_pattern: str | None = None,
        class_name: str | None = None,
        timeout: float = 5.0,
    ) -> Window:
        """Finds an existing top-level window by title, regex pattern, or class name."""
        deadline = time.monotonic() + timeout
        compiled_re = re.compile(title_pattern, re.IGNORECASE) if title_pattern else None

        while time.monotonic() < deadline:
            snapshots = WindowCensus.capture()
            for snap in snapshots:
                if not snap.is_visible:
                    continue
                if title_exact and snap.title == title_exact:
                    return cls(snap.hwnd, snap.pid)
                if compiled_re and compiled_re.search(snap.title):
                    return cls(snap.hwnd, snap.pid)
                if class_name and snap.class_name.lower() == class_name.lower():
                    return cls(snap.hwnd, snap.pid)
            time.sleep(0.1)

        raise WindowDiscoveryTimeoutError(
            f"Window not found (title_exact={title_exact}, title_pattern={title_pattern}, class_name={class_name})"
        )

    @classmethod
    def launch_and_discover(
        cls,
        cmd: list[str] | str,
        timeout: float = 10.0,
        title_pattern: str | None = None,
    ) -> tuple[subprocess.Popen, Window]:
        """
        Launches an application and discovers its top-level window by diffing pre/post snapshots.
        Solves launcher PID != window PID issue (e.g. Modern Windows Notepad).
        """
        attach_to_input_desktop()
        before = WindowCensus.capture()

        if isinstance(cmd, str):
            proc = subprocess.Popen(cmd, shell=True)
        else:
            proc = subprocess.Popen(cmd)

        deadline = time.monotonic() + timeout
        compiled_re = re.compile(title_pattern, re.IGNORECASE) if title_pattern else None

        while time.monotonic() < deadline:
            after = WindowCensus.capture()
            diff = WindowCensus.diff(before, after)

            # 1. Check newly added windows
            for snap in diff.added:
                if not snap.is_visible:
                    continue
                if compiled_re:
                    if compiled_re.search(snap.title):
                        return proc, cls(snap.hwnd, snap.pid)
                else:
                    if snap.pid == proc.pid or snap.title:
                        return proc, cls(snap.hwnd, snap.pid)

            # 2. Check all currently visible windows if title pattern matched
            if compiled_re:
                for snap in after:
                    if snap.is_visible and compiled_re.search(snap.title):
                        return proc, cls(snap.hwnd, snap.pid)

            time.sleep(0.1)

        raise WindowDiscoveryTimeoutError(
            f"Failed to discover window launched via {cmd} (title_pattern={title_pattern})"
        )
