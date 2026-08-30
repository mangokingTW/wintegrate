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
import subprocess
from dataclasses import dataclass

from wintegrate.element import UiaElement
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


NOTEPAD = AppSpec(
    name="notepad",
    command=("notepad.exe",),
    process_names=("notepad.exe",),
    window_classes=("Notepad",),  # same class for classic and Store Notepad
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
