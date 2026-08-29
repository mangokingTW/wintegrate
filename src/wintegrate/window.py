"""Window management, discovery, and process lifecycle."""

from __future__ import annotations

import logging
import re
import subprocess
import time

from wintegrate.diagnostics import WindowCensus
from wintegrate.element import UiaElement
from wintegrate.exceptions import WindowDiscoveryTimeoutError
from wintegrate.interop import (
    HWND_NOTOPMOST,
    HWND_TOPMOST,
    SW_RESTORE,
    SWP_NOMOVE,
    SWP_NOSIZE,
    SWP_SHOWWINDOW,
    attach_to_input_desktop,
    dismiss_popup_or_search,
    get_foreground_window,
    get_window_class,
    get_window_pid,
    get_window_title,
    kernel32,
    user32,
)

logger = logging.getLogger(__name__)


class Window:
    """Represents a top-level native OS window."""

    def __init__(self, hwnd: int, pid: int | None = None):
        self.hwnd = hwnd
        self.pid = pid or get_window_pid(hwnd)

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
        """
        Forcefully brings the window to the foreground across all Windows platforms (x64 / ARM64).
        Uses thread input attachment + SWP_TOPMOST pulse to bypass Foreground Lock Timeout.
        """
        attach_to_input_desktop()
        dismiss_popup_or_search()

        cur_thread = kernel32.GetCurrentThreadId()
        fg_hwnd = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
        target_thread = user32.GetWindowThreadProcessId(self.hwnd, None)

        # 1. Attach thread input to bypass foreground lock restrictions
        attached = False
        if fg_thread and fg_thread != cur_thread:
            attached = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
        elif target_thread and target_thread != cur_thread:
            attached = bool(user32.AttachThreadInput(cur_thread, target_thread, True))

        try:
            user32.ShowWindow(self.hwnd, SW_RESTORE)
            # Pulse TOPMOST to break through background apps
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            user32.SetWindowPos(self.hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            user32.SetForegroundWindow(self.hwnd)
            user32.BringWindowToTop(self.hwnd)
        finally:
            if attached:
                if fg_thread and fg_thread != cur_thread:
                    user32.AttachThreadInput(cur_thread, fg_thread, False)
                elif target_thread and target_thread != cur_thread:
                    user32.AttachThreadInput(cur_thread, target_thread, False)

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
            subprocess.run(["taskkill", "/F", "/PID", str(self.pid)], capture_output=True, check=False)

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
        exclude_hwnds: set[int] | None = None,
    ) -> tuple[subprocess.Popen, Window]:
        """
        Launches an application and discovers its top-level window by diffing pre/post snapshots.
        Solves launcher PID != window PID issue and allows excluding existing HWNDs.
        """
        attach_to_input_desktop()
        before = WindowCensus.capture()
        excluded = exclude_hwnds or set()

        if isinstance(cmd, str):
            proc = subprocess.Popen(cmd, shell=True)
        else:
            proc = subprocess.Popen(cmd)

        deadline = time.monotonic() + timeout
        compiled_re = re.compile(title_pattern, re.IGNORECASE) if title_pattern else None

        while time.monotonic() < deadline:
            after = WindowCensus.capture()
            diff = WindowCensus.diff(before, after)

            # Look for newly added visible top-level windows
            for snap in diff.added:
                if snap.is_visible and snap.hwnd not in excluded:
                    if compiled_re and not compiled_re.search(snap.title):
                        continue
                    return proc, cls(snap.hwnd, snap.pid)

            # Fallback: check all currently visible windows matching criteria
            for snap in after:
                if snap.is_visible and snap.hwnd not in excluded:
                    if compiled_re and compiled_re.search(snap.title):
                        return proc, cls(snap.hwnd, snap.pid)

            time.sleep(0.1)

        raise WindowDiscoveryTimeoutError(
            f"Window failed to appear within {timeout}s (cmd={cmd}, pattern={title_pattern})"
        )
