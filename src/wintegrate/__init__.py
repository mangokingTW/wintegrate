"""wintegrate: A CI-first Windows UI automation library designed for unattended runners."""

from __future__ import annotations

from wintegrate.session import Session, SessionConfig
from wintegrate.window import Window
from wintegrate.element import UiaElement
from wintegrate.text import normalize_line_endings, count_lines
from wintegrate.exceptions import (
    WintegrateError,
    WindowDiscoveryTimeoutError,
    ElementNotFoundError,
    ActionVerificationError,
    TextMismatchError,
    FocusStealDetectedError,
    DiagnosticPipelineError,
)
from wintegrate.diagnostics import ContinuousRecorder, WindowCensus, WindowSnapshot, CensusDiff
from wintegrate.recorder import (
    RecordedAction,
    TextActionTimelineRecorder,
    ActionPlayer,
    inspect_desktop_tree,
)

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
