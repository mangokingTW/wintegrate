"""wintegrate: A CI-first Windows UI automation library designed for unattended runners."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("wintegrate")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0.dev0"

from wintegrate.apps import (
    CALCULATOR,
    NOTEPAD,
    AppHandle,
    AppSpec,
    sweep_processes_verified,
)
from wintegrate.controls import (
    DataGrid,
    DataGridCell,
    DataGridRow,
    TreeView,
    TreeViewItem,
)
from wintegrate.diagnostics import (
    CensusDiff,
    ContinuousRecorder,
    WindowCensus,
    WindowSnapshot,
    capture_screen_image,
    capture_window_image,
)
from wintegrate.element import UiaElement
from wintegrate.env import (
    PlatformCapabilities,
    desktop_only,
    env,
    is_windows_desktop,
    is_windows_server,
    server_only,
)
from wintegrate.exceptions import (
    ActionVerificationError,
    DiagnosticPipelineError,
    ElementNotFoundError,
    FocusStealDetectedError,
    TextMismatchError,
    WindowDiscoveryTimeoutError,
    WintegrateError,
)
from wintegrate.interop import (
    KEY_NAMES,
    get_composition_string,
    get_foreground_window,
    get_ime_status,
    get_keyboard_layout,
    get_keyboard_layout_list,
    get_process_image_name,
    get_window_class,
    get_window_pid,
    get_window_title,
    parse_key_spec,
    send_char_input,
    send_keys,
    send_mouse_click,
    send_physical_keys,
    send_vk_input,
    set_ime_conversion,
    set_ime_open,
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
    "__version__",
    "Session",
    "SessionConfig",
    "Window",
    "UiaElement",
    "AppSpec",
    "DataGrid",
    "DataGridRow",
    "DataGridCell",
    "TreeView",
    "TreeViewItem",
    "AppHandle",
    "NOTEPAD",
    "sweep_processes_verified",
    "CALCULATOR",
    "env",
    "PlatformCapabilities",
    "is_windows_server",
    "is_windows_desktop",
    "desktop_only",
    "server_only",
    "normalize_line_endings",
    "count_lines",
    "send_char_input",
    "send_keys",
    "send_vk_input",
    "send_physical_keys",
    "get_ime_status",
    "set_ime_open",
    "set_ime_conversion",
    "get_composition_string",
    "get_keyboard_layout",
    "get_keyboard_layout_list",
    "parse_key_spec",
    "KEY_NAMES",
    "send_mouse_click",
    "get_foreground_window",
    "get_process_image_name",
    "get_window_class",
    "get_window_pid",
    "get_window_title",
    "WintegrateError",
    "WindowDiscoveryTimeoutError",
    "ElementNotFoundError",
    "ActionVerificationError",
    "TextMismatchError",
    "FocusStealDetectedError",
    "DiagnosticPipelineError",
    "ContinuousRecorder",
    "capture_screen_image",
    "capture_window_image",
    "WindowCensus",
    "WindowSnapshot",
    "CensusDiff",
    "RecordedAction",
    "TextActionTimelineRecorder",
    "ActionPlayer",
    "inspect_desktop_tree",
]
