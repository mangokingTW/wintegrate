"""wintegrate: A CI-first Windows UI automation library designed for unattended runners."""

from __future__ import annotations

from wintegrate.diagnostics import CensusDiff, ContinuousRecorder, WindowCensus, WindowSnapshot
from wintegrate.element import UiaElement
from wintegrate.exceptions import (
    ActionVerificationError,
    DiagnosticPipelineError,
    ElementNotFoundError,
    FocusStealDetectedError,
    TextMismatchError,
    WindowDiscoveryTimeoutError,
    WintegrateError,
)
from wintegrate.recorder import (
    ActionPlayer,
    RecordedAction,
    TextActionTimelineRecorder,
    inspect_desktop_tree,
)
from wintegrate.session import Session, SessionConfig
from wintegrate.text import count_lines, normalize_line_endings
from wintegrate.window import Window

__all__ = [
    "Session",
    "SessionConfig",
    "Window",
    "UiaElement",
    "normalize_line_endings",
    "count_lines",
    "WintegrateError",
    "WindowDiscoveryTimeoutError",
    "ElementNotFoundError",
    "ActionVerificationError",
    "TextMismatchError",
    "FocusStealDetectedError",
    "DiagnosticPipelineError",
    "ContinuousRecorder",
    "WindowCensus",
    "WindowSnapshot",
    "CensusDiff",
    "RecordedAction",
    "TextActionTimelineRecorder",
    "ActionPlayer",
    "inspect_desktop_tree",
]
