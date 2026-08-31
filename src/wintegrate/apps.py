"""Well-known application specs and a managed application lifecycle handle.

These package the launch/discovery pitfalls of modern Windows apps so callers
don't have to rediscover them:

- Titles are localized; process image names, window classes, and UIA control
  types are not. AppSpec matching prefers the locale-independent identities and
  keeps ``title_pattern`` only as a last-resort fallback (this is also where
  localized variants live, once, instead of in every caller).
- Store apps are single-instance: a leaked instance makes the next launch open
  a tab in the old window instead of a new top-level window, breaking window
  discovery. ``fresh`` sweeps leftover instances before launching.
- Cold Store-app starts regularly exceed 10s on CI runners (ARM64 especially),
  so managed launches default to a generous discovery timeout.
- A launch that times out must not leak: AppHandle is a context manager that
  guarantees window close + process kill on exit.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from wintegrate.element import UiaElement
from wintegrate.interop import get_process_image_name
from wintegrate.window import Window

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppSpec:
    """Locale-independent identity of an application for launch-and-discover."""

    name: str
    command: tuple[str, ...]
    process_names: tuple[str, ...] = ()
    window_classes: tuple[str, ...] = ()
    title_pattern: str | None = None
    text_input_ladder: tuple[dict, ...] | None = None
    # A packaged app keeps its session on disk, so terminating it is not enough to
    # get a clean start: the next launch restores the tabs the last test left.
    # These name the package and the LocalState subfolders holding that state, so
    # a fresh launch can clear them.
    package_family_name: str | None = None
    session_state_dirs: tuple[str, ...] = ()


NOTEPAD = AppSpec(
    name="notepad",
    command=("notepad.exe",),
    process_names=("notepad.exe",),
    window_classes=("Notepad",),  # same class for classic and Store Notepad
    # Store Notepad reopens the previous session, so a document that looks empty
    # in a fresh launch is not: the text the last test typed comes back, and an
    # assertion on content then fails for a reason three tests old.
    package_family_name="Microsoft.WindowsNotepad_8wekyb3d8bbwe",
    session_state_dirs=("TabState", "WindowState"),
)

CALCULATOR = AppSpec(
    name="calculator",
    command=("calc.exe",),
    # Win11 WinUI / Win10 UWP / legacy Server builds
    process_names=("calculatorapp.exe", "calculator.exe", "win32calc.exe"),
    # UWP windows can be owned by ApplicationFrameHost rather than the app
    # process, so keep a title fallback (localized variants live here, once).
    title_pattern="(?i)calculator|小算盤|计算器|電卓",
)


class AppHandle:
    """Owns one launched application: window + launcher process + guaranteed cleanup."""

    def __init__(
        self,
        proc: subprocess.Popen | None,
        window: Window,
        spec: AppSpec | None = None,
    ):
        self.proc = proc
        self.window = window
        self.spec = spec

    def find_text_input(self, timeout: float = 20.0) -> UiaElement:
        ladder = self.spec.text_input_ladder if self.spec else None
        return self.window.find_text_input(timeout=timeout, ladder=ladder)

    def close(self) -> None:
        try:
            self.window.close(force=True)
        except Exception as exc:
            logger.debug(f"AppHandle window close failed ({type(exc).__name__}): {exc}")
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass

    def __enter__(self) -> AppHandle:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False


def kill_processes(names: tuple[str, ...] | list[str]) -> None:
    """Best-effort force-kill by image name (fresh-launch sweep for single-instance apps)."""
    for name in names:
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, check=False)


def clear_package_session_state(
    package_family_name: str,
    state_dirs: tuple[str, ...] | list[str],
    retries: int = 3,
) -> int:
    """Deletes a packaged app's persisted session, returning how many files went.

    Terminating a packaged app does not give you a clean start: Store Notepad
    keeps its open tabs under LocalState\\TabState and reopens them next launch,
    so a "fresh" window arrives holding the previous test's text.

    Best-effort by design. A file the dying process still has open raises
    PermissionError, so this retries briefly and then gives up rather than
    failing a run over cleanup — and a single surviving file does not stop the
    next launch from coming up empty.
    """
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return 0
    root = Path(local) / "Packages" / package_family_name / "LocalState"
    removed = 0
    for attempt in range(retries):
        stuck = False
        for sub in state_dirs:
            directory = root / sub
            if not directory.is_dir():
                continue
            for entry in directory.iterdir():
                try:
                    entry.unlink()
                    removed += 1
                except PermissionError:
                    stuck = True
                except OSError as exc:
                    logger.debug(f"Could not clear {entry.name} ({type(exc).__name__}): {exc}")
        if not stuck:
            break
        if attempt < retries - 1:
            time.sleep(0.4)
    return removed


def sweep_processes_verified(
    names: tuple[str, ...] | list[str],
    window_classes: tuple[str, ...] | list[str] = (),
    timeout: float = 10.0,
    package_family_name: str | None = None,
    session_state_dirs: tuple[str, ...] | list[str] = (),
) -> bool:
    """Kills leftover instances and waits until their windows are actually gone.

    Terminating a packaged app is asynchronous and package-level: taskkill
    returns long before the last window has been torn down. Sleeping a fixed
    interval and hoping is the failure this replaces — a leaked window makes the
    next launch of a single-instance app produce no new window at all, so
    discovery times out on something no timeout value can fix.

    Returns True when nothing matching remains, False when something outlived the
    wait. Callers should treat False as "this launch may see a stale window"
    rather than as a hard error: a sweep is a precaution, not the thing under
    test.
    """
    from wintegrate.diagnostics import WindowCensus

    kill_processes(names)
    lowered_procs = {n.lower() for n in names}
    lowered_classes = {c.lower() for c in window_classes}

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        survivors = []
        for snap in WindowCensus.capture():
            if not snap.is_visible:
                continue
            if lowered_classes and snap.class_name.lower() in lowered_classes:
                survivors.append(snap)
                continue
            try:
                image = get_process_image_name(snap.pid)
            except Exception:
                continue
            if image and image.lower() in lowered_procs:
                survivors.append(snap)
        if not survivors:
            if package_family_name and session_state_dirs:
                # Only once the windows are gone: the files are still open until
                # then, and clearing them early achieves nothing.
                cleared = clear_package_session_state(package_family_name, session_state_dirs)
                logger.debug(
                    f"Cleared {cleared} persisted session file(s) for {package_family_name}"
                )
            return True
        time.sleep(0.2)

    logger.warning(
        f"Sweep left {len(survivors)} window(s) alive after {timeout}s: "
        f"{[(s.hwnd, s.title) for s in survivors[:3]]}"
    )
    return False
