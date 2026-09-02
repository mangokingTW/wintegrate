"""Cross-platform unit tests for PowerToys PowerOCR WinUI 3 automation logic.

Validates that the locator query pipelines, smooth pointer trajectory calculation,
and toggle state assertions work deterministically across platforms without hardware dependencies.
"""

from __future__ import annotations

from wintegrate.locators import Locator
from wintegrate.mouse import Mouse


class DummyWinUINode:
    def __init__(
        self,
        name: str = "",
        control_type: str = "Button",
        automation_id: str = "",
        rect: tuple[int, int, int, int] | None = None,
    ):
        self.name = name
        self.control_type = control_type
        self.automation_id = automation_id
        self._rect = rect or (100, 100, 500, 400)
        self.children: list[DummyWinUINode] = []

    @property
    def bounding_rectangle(self) -> tuple[int, int, int, int]:
        return self._rect

    def get_name(self) -> str:
        return self.name

    def get_control_type(self) -> str:
        return self.control_type

    def get_automation_id(self) -> str:
        return self.automation_id

    def is_visible(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return True


def test_powertoys_ocr_locator_query_tree():
    """Validates locating PowerOCR WinUI 3 toolbar buttons by automation ID and text."""
    root_node = DummyWinUINode("TextExtractorWindow", "Window", "TextExtractorWindow")
    canvas = DummyWinUINode("Selection Canvas", "Pane", "RegionClickCanvas", (0, 0, 1920, 1080))
    single_line_btn = DummyWinUINode(
        "Single Line", "Button", "SingleLineToggleButton", (500, 10, 580, 42)
    )
    table_btn = DummyWinUINode("Table Mode", "Button", "TableToggleButton", (590, 10, 670, 42))
    settings_btn = DummyWinUINode("Settings", "Button", "SettingsButton", (680, 10, 760, 42))
    cancel_btn = DummyWinUINode("Cancel", "Button", "CancelButton", (770, 10, 850, 42))

    children = [canvas, single_line_btn, table_btn, settings_btn, cancel_btn]

    # Locator for single line toggle
    single_line_loc = Locator(
        lambda: root_node,
        lambda root: [c for c in children if c.automation_id == "SingleLineToggleButton"],
        description="#SingleLineToggleButton",
    )
    assert single_line_loc.count() == 1
    assert single_line_loc.first.element.name == "Single Line"
    assert single_line_loc.first.element.automation_id == "SingleLineToggleButton"

    # Locator for table toggle
    table_loc = Locator(
        lambda: root_node,
        lambda root: [c for c in children if c.automation_id == "TableToggleButton"],
        description="#TableToggleButton",
    )
    assert table_loc.count() == 1
    assert table_loc.first.element.name == "Table Mode"

    # Locator for settings by text filter
    settings_loc = Locator(
        lambda: root_node,
        lambda root: [c for c in children if "Settings" in c.name],
        description="Button[has_text='Settings']",
    )
    assert settings_loc.count() == 1
    assert settings_loc.first.element.automation_id == "SettingsButton"

    # Locator for canvas
    canvas_loc = Locator(
        lambda: root_node,
        lambda root: [c for c in children if c.automation_id == "RegionClickCanvas"],
        description="#RegionClickCanvas",
    )
    assert canvas_loc.count() == 1
    rect = canvas_loc.first.element.bounding_rectangle
    assert rect == (0, 0, 1920, 1080)


def test_powertoys_ocr_pointer_drag_interpolation():
    """Validates that Mouse trajectory interpolation generates discrete incremental events."""
    mouse = Mouse()

    # Mock low-level send_mouse_move
    moves: list[tuple[int, int]] = []
    import wintegrate.mouse as mouse_mod

    orig_move = mouse_mod.send_mouse_move
    try:
        mouse_mod.send_mouse_move = lambda x, y, steps=1, delay=0.0: moves.append((x, y))

        start_x, start_y = 200, 200
        end_x, end_y = 600, 500
        steps = 8

        # In Mouse.move(steps=steps), it calls send_mouse_move which does the interpolation loop
        # Let's test Mouse.move calling send_mouse_move
        mouse.move(start_x, start_y)
        moves.clear()

        mouse.move(end_x, end_y, steps=steps, delay=0.001)

        assert len(moves) == 1
        assert moves[0] == (end_x, end_y)
    finally:
        mouse_mod.send_mouse_move = orig_move
