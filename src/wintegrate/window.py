"""Window management, discovery, and process lifecycle."""

from __future__ import annotations

import logging
import re
import subprocess
import time

from wintegrate.diagnostics import WindowCensus
from wintegrate.element import UiaElement
from wintegrate.exceptions import ElementNotFoundError, WindowDiscoveryTimeoutError
from wintegrate.interop import (
    SW_RESTORE,
    attach_to_input_desktop,
    get_composition_string,
    get_foreground_window,
    get_ime_status,
    get_keyboard_layout,
    get_process_image_name,
    get_window_class,
    get_window_pid,
    get_window_title,
    kernel32,
    set_ime_conversion,
    set_ime_open,
    user32,
)

logger = logging.getLogger(__name__)

# Locale-independent search ladder for an application's primary text-input element:
# window classes and UIA control types don't change with the UI language, so no
# localized control names are needed.
DEFAULT_TEXT_INPUT_LADDER: tuple[dict, ...] = (
    {"class_name": "RichEditD2DPT"},  # Win11 tabbed Notepad
    {"class_name": "Edit"},  # classic Win32 edit control
    {"control_type_id": 50030},  # UIA Document
    {"control_type_id": 50004},  # UIA Edit
    # Note: "NotepadTextBox" is deliberately absent — it is the *container* hwnd
    # around the RichEdit child, so it wins the race while the child is still
    # materializing, and its get_value() is always empty (verification can never
    # pass against it).
)


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

    def get_ime_status(self) -> dict[str, object]:
        """
        Reads this window's IME state: `has_context`, `is_open`, `conversion`,
        `sentence`, `native_mode`, `full_shape`.

        `has_context` is False for a modern XAML/WinUI control, which routes text
        services through TSF rather than IMM32 — that means "ask TSF", not "IME off".
        """
        return get_ime_status(self.hwnd)

    def set_ime_open(self, is_open: bool) -> bool:
        """Opens or closes this window's IME. False when it has no IMM32 context."""
        return set_ime_open(self.hwnd, is_open)

    def set_ime_conversion(self, conversion: int, sentence: int = 0) -> bool:
        """Sets this window's IME conversion mode (see the IME_CMODE_* flags)."""
        return set_ime_conversion(self.hwnd, conversion, sentence)

    def get_composition_string(self) -> str:
        """Returns the IME's in-progress composition text for this window ("" when idle)."""
        return get_composition_string(self.hwnd)

    @property
    def keyboard_layout(self) -> int:
        """The active keyboard layout (HKL) of the thread that owns this window."""
        return get_keyboard_layout(self.hwnd)

    @property
    def keyboard_language_id(self) -> int:
        """The LANGID (low word of the HKL) of this window's keyboard layout."""
        return self.keyboard_layout & 0xFFFF

    def exists(self, require_visible: bool = True) -> bool:
        """Returns whether this window handle is still a live (and optionally visible) window."""
        if not user32.IsWindow(self.hwnd):
            return False
        return bool(user32.IsWindowVisible(self.hwnd)) if require_visible else True

    def re_resolve_element(self) -> UiaElement:
        """Always resolves a fresh UIA element directly from the HWND."""
        return UiaElement.from_handle(self.hwnd)

    def find_text_input(
        self, timeout: float = 20.0, ladder: tuple[dict, ...] | list[dict] | None = None
    ) -> UiaElement:
        """
        Locates this window's primary text-input element using a locale-independent
        ladder of window classes and UIA control types (see DEFAULT_TEXT_INPUT_LADDER).
        """
        conds = tuple(ladder) if ladder else DEFAULT_TEXT_INPUT_LADDER
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                root = self.re_resolve_element()
                for cond in conds:
                    try:
                        el = root.find_descendant(timeout=0.2, **cond)
                        if el:
                            return el
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(0.2)
        raise ElementNotFoundError(
            f"No text-input element found in window '{self.title}' within {timeout}s"
        )

    def move_to_current_desktop(self):
        """Moves this window to the currently active virtual desktop if pyvda is available."""
        try:
            from pyvda import AppView, VirtualDesktop

            current_desktop = VirtualDesktop.current()
            app_view = AppView(self.hwnd)
            app_view.move(current_desktop)
        except Exception as exc:
            logger.debug(f"move_to_current_desktop skipped: {exc}")

    def set_foreground(self, verify: bool = True, timeout: float = 2.0) -> bool:
        """
        Brings the window to the foreground cleanly using Win32 AttachThreadInput.
        Synchronizes thread input queues to reliably grant foreground activation permission.
        """
        attach_to_input_desktop()
        self.move_to_current_desktop()

        cur_thread = kernel32.GetCurrentThreadId()
        fg_hwnd = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
        target_thread = user32.GetWindowThreadProcessId(self.hwnd, None)

        attached_fg = False
        attached_target = False

        if fg_thread and fg_thread != cur_thread:
            attached_fg = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
        if target_thread and target_thread != cur_thread:
            attached_target = bool(user32.AttachThreadInput(cur_thread, target_thread, True))

        try:
            user32.ShowWindow(self.hwnd, SW_RESTORE)
            user32.SetForegroundWindow(self.hwnd)
            user32.SetActiveWindow(self.hwnd)
            user32.SetFocus(self.hwnd)
            user32.BringWindowToTop(self.hwnd)
        finally:
            if attached_fg:
                user32.AttachThreadInput(cur_thread, fg_thread, False)
            if attached_target:
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
            subprocess.run(
                ["taskkill", "/F", "/PID", str(self.pid)], capture_output=True, check=False
            )

    @classmethod
    def find(
        cls,
        title_exact: str | None = None,
        title_pattern: str | None = None,
        class_name: str | None = None,
        pid: int | None = None,
        timeout: float = 5.0,
    ) -> Window:
        """
        Finds an existing visible top-level window matching ALL supplied criteria.

        Criteria are combined with AND: `find(title_pattern="Settings",
        class_name="#32770")` returns the dialog whose title matches, not merely the
        first `#32770` on the desktop. Pass `pid` to restrict the search to one
        process — the reliable way to target a window whose title is localized,
        empty, or duplicated across applications.
        """
        if not any([title_exact, title_pattern, class_name, pid]):
            raise ValueError("Window.find requires at least one search criterion")

        deadline = time.monotonic() + timeout
        compiled_re = re.compile(title_pattern, re.IGNORECASE) if title_pattern else None

        while time.monotonic() < deadline:
            snapshots = WindowCensus.capture()
            for snap in snapshots:
                if not snap.is_visible:
                    continue
                if title_exact is not None and snap.title != title_exact:
                    continue
                if compiled_re and not compiled_re.search(snap.title):
                    continue
                if class_name and snap.class_name.lower() != class_name.lower():
                    continue
                if pid is not None and snap.pid != pid:
                    continue
                return cls(snap.hwnd, snap.pid)
            time.sleep(0.1)

        raise WindowDiscoveryTimeoutError(
            f"Window not found (title_exact={title_exact}, title_pattern={title_pattern}, "
            f"class_name={class_name}, pid={pid})"
        )

    @classmethod
    def launch_and_discover(
        cls,
        cmd: list[str] | str,
        timeout: float = 10.0,
        title_pattern: str | None = None,
        exclude_hwnds: set[int] | None = None,
        process_names: tuple[str, ...] | list[str] | None = None,
        window_classes: tuple[str, ...] | list[str] | None = None,
    ) -> tuple[subprocess.Popen, Window]:
        """
        Launches an application and discovers its top-level window by diffing pre/post snapshots.
        Solves launcher PID != window PID issue and allows excluding existing HWNDs.

        Matching prefers locale-independent identities: `window_classes` (window class
        names) and `process_names` (executable basenames of the window's owning
        process). `title_pattern` remains as a last-resort fallback; a candidate
        window is accepted when ANY provided criterion matches.
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
        proc_names = {p.lower() for p in process_names} if process_names else None
        classes = set(window_classes) if window_classes else None
        has_criteria = bool(compiled_re or proc_names or classes)

        def matches(snap) -> bool:
            if classes and snap.class_name in classes:
                return True
            if proc_names and get_process_image_name(snap.pid) in proc_names:
                return True
            if compiled_re and compiled_re.search(snap.title):
                return True
            return False

        def is_ignorable_helper(snap) -> bool:
            title = snap.title.lower()
            cls_name = snap.class_name.lower()
            if "gdi+ window" in title or "gdi+ hook window class" in cls_name:
                return True
            if "msctfime ui" in title or "msctfime ui" in cls_name:
                return True
            if "default ime" in title or "ime" == cls_name:
                return True
            return False

        while time.monotonic() < deadline:
            after = WindowCensus.capture()
            diff = WindowCensus.diff(before, after)

            # Look for newly added visible top-level windows
            for snap in diff.added:
                if snap.is_visible and snap.hwnd not in excluded and not is_ignorable_helper(snap):
                    if has_criteria and not matches(snap):
                        continue
                    return proc, cls(snap.hwnd, snap.pid)

            # Fallback: check all currently visible windows matching criteria
            for snap in after:
                if (
                    snap.is_visible
                    and snap.hwnd not in excluded
                    and snap.hwnd not in {b.hwnd for b in before}
                    and not is_ignorable_helper(snap)
                ):
                    if has_criteria and matches(snap):
                        return proc, cls(snap.hwnd, snap.pid)

            time.sleep(0.1)

        # Best-effort: don't leak the launcher on timeout (a late-arriving window
        # can still outlive this — session sanitization sweeps those up).
        try:
            proc.kill()
        except Exception:
            pass
        raise WindowDiscoveryTimeoutError(
            f"Window failed to appear within {timeout}s (cmd={cmd}, pattern={title_pattern})"
        )
