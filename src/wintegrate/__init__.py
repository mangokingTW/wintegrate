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
    find_packaged_app,
    launch_packaged_app,
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
from wintegrate.element import UiaElement, ValueReading
from wintegrate.env import (
    PlatformCapabilities,
    desktop_only,
    env,
    is_windows_desktop,
    is_windows_server,
    requires_ime,
    requires_windows_build,
    server_only,
)
from wintegrate.exceptions import (
    ActionVerificationError,
    DiagnosticPipelineError,
    ElementNotFoundError,
    FocusStealDetectedError,
    TextMismatchError,
    ValueUnavailableError,
    WindowDiscoveryTimeoutError,
    WintegrateError,
)
from wintegrate.interop import (
    KEY_NAMES,
    CloakReason,
    ImeConversion,
    get_composition_string,
    get_cursor_state,
    get_foreground_window,
    get_ime_status,
    get_keyboard_layout,
    get_keyboard_layout_list,
    get_process_image_name,
    get_window_class,
    get_window_cloak_reason,
    get_window_pid,
    get_window_title,
    parse_hotkey,
    parse_key_spec,
    send_char_input,
    send_hotkey,
    send_keys,
    send_mouse_click,
    send_physical_keys,
    send_vk_input,
    set_ime_conversion,
    set_ime_open,
)
from wintegrate.keyboard_overlay import (
    KeyStrokeEvent,
    KeyTracker,
    draw_keyboard_hud,
)
from wintegrate.pointer_overlay import (
    ClickEvent,
    ClickTracker,
    cursor_overlay_image,
    draw_click_markers,
    draw_pointer_overlay,
)
from wintegrate.recorder import (
    ActionPlayer,
    RecordedAction,
    TextActionTimelineRecorder,
    inspect_desktop_tree,
)
from wintegrate.scintilla import EolMode, ScintillaView, is_scintilla
from wintegrate.session import Session, SessionConfig
from wintegrate.text import count_lines, normalize_line_endings
from wintegrate.window import Window

__all__ = [
    "__version__",
    "ActionPlayer",
    "ActionVerificationError",
    "AppHandle",
    "AppSpec",
    "CALCULATOR",
    "capture_screen_image",
    "ClickEvent",
    "ClickTracker",
    "capture_window_image",
    "CensusDiff",
    "CloakReason",
    "ContinuousRecorder",
    "count_lines",
    "cursor_overlay_image",
    "draw_click_markers",
    "draw_keyboard_hud",
    "draw_pointer_overlay",
    "DataGrid",
    "DataGridCell",
    "DataGridRow",
    "desktop_only",
    "DiagnosticPipelineError",
    "ElementNotFoundError",
    "env",
    "EolMode",
    "find_packaged_app",
    "FocusStealDetectedError",
    "get_composition_string",
    "get_cursor_state",
    "get_foreground_window",
    "get_ime_status",
    "get_keyboard_layout",
    "get_keyboard_layout_list",
    "get_process_image_name",
    "get_window_class",
    "get_window_cloak_reason",
    "get_window_pid",
    "get_window_title",
    "ImeConversion",
    "inspect_desktop_tree",
    "is_scintilla",
    "is_windows_desktop",
    "is_windows_server",
    "KEY_NAMES",
    "KeyStrokeEvent",
    "KeyTracker",
    "launch_packaged_app",
    "normalize_line_endings",
    "NOTEPAD",
    "parse_hotkey",
    "parse_key_spec",
    "PlatformCapabilities",
    "RecordedAction",
    "requires_ime",
    "requires_windows_build",
    "ScintillaView",
    "send_char_input",
    "send_hotkey",
    "send_keys",
    "send_mouse_click",
    "send_physical_keys",
    "send_vk_input",
    "server_only",
    "Session",
    "SessionConfig",
    "set_ime_conversion",
    "set_ime_open",
    "sweep_processes_verified",
    "TextActionTimelineRecorder",
    "TextMismatchError",
    "TreeView",
    "TreeViewItem",
    "UiaElement",
    "ValueReading",
    "ValueUnavailableError",
    "Window",
    "WindowCensus",
    "WindowDiscoveryTimeoutError",
    "WindowSnapshot",
    "WintegrateError",
]
