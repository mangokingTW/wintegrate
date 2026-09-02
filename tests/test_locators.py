"""Unit tests for Playwright-style Locators and rich control abstractions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from wintegrate.controls import (
    CheckBox,
    Menu,
    ProgressBar,
    Slider,
    TabControl,
)
from wintegrate.element import ValueReading
from wintegrate.exceptions import ElementNotFoundError
from wintegrate.locators import ROLE_TO_CONTROL_TYPE_ID, Locator


def test_role_to_control_type_mapping():
    """Verifies that all standard user-friendly role aliases map to valid UIA Control Type IDs."""
    assert ROLE_TO_CONTROL_TYPE_ID["button"] == 50000
    assert ROLE_TO_CONTROL_TYPE_ID["checkbox"] == 50002
    assert ROLE_TO_CONTROL_TYPE_ID["combobox"] == 50003
    assert ROLE_TO_CONTROL_TYPE_ID["edit"] == 50004
    assert ROLE_TO_CONTROL_TYPE_ID["textbox"] == 50004
    assert ROLE_TO_CONTROL_TYPE_ID["tab"] == 50018
    assert ROLE_TO_CONTROL_TYPE_ID["tabitem"] == 50019
    assert ROLE_TO_CONTROL_TYPE_ID["menuitem"] == 50011
    assert ROLE_TO_CONTROL_TYPE_ID["slider"] == 50015
    assert ROLE_TO_CONTROL_TYPE_ID["treeitem"] == 50024


def test_locator_slicing_and_count():
    """Verifies count, first, last, nth slicing on Locator."""
    mock_elem_1 = MagicMock()
    mock_elem_1.name = "Item 1"
    mock_elem_2 = MagicMock()
    mock_elem_2.name = "Item 2"
    mock_elem_3 = MagicMock()
    mock_elem_3.name = "Item 3"

    items = [mock_elem_1, mock_elem_2, mock_elem_3]
    loc = Locator(lambda: MagicMock(), lambda root: items, description="MockList")

    assert loc.count() == 3
    assert loc.first.element == mock_elem_1
    assert loc.last.element == mock_elem_3
    assert loc.nth(1).element == mock_elem_2

    # Out of range
    assert loc.nth(99).count() == 0


def test_locator_filter_by_text():
    """Verifies filtering locators by text substring."""
    mock_1 = MagicMock()
    mock_1.read_value.return_value = ValueReading("Submit Form", "Name")
    mock_1.name = "Submit Form"

    mock_2 = MagicMock()
    mock_2.read_value.return_value = ValueReading("Cancel", "Name")
    mock_2.name = "Cancel"

    loc = Locator(lambda: MagicMock(), lambda root: [mock_1, mock_2], description="Buttons")

    filtered = loc.filter(has_text="Submit")
    assert filtered.count() == 1
    assert filtered.first.element == mock_1

    empty = loc.filter(has_text="NonExistent")
    assert empty.count() == 0


def test_locator_filter_by_automation_id_and_class():
    """Verifies filtering locators by automation_id and class_name."""
    mock_1 = MagicMock()
    mock_1.automation_id = "btn_ok"
    mock_1.class_name = "Button"

    mock_2 = MagicMock()
    mock_2.automation_id = "btn_cancel"
    mock_2.class_name = "Button"

    loc = Locator(lambda: MagicMock(), lambda root: [mock_1, mock_2], description="Buttons")

    assert loc.filter(automation_id="btn_ok").count() == 1
    assert loc.filter(automation_id="btn_ok").first.element == mock_1
    assert loc.filter(class_name="Button").count() == 2
    assert loc.filter(class_name="Edit").count() == 0


def test_locator_all():
    """Verifies loc.all() returns individual Locators."""
    mock_1 = MagicMock()
    mock_2 = MagicMock()

    loc = Locator(lambda: MagicMock(), lambda root: [mock_1, mock_2], description="Items")
    all_locs = loc.all()
    assert len(all_locs) == 2
    assert all_locs[0].element == mock_1
    assert all_locs[1].element == mock_2


def test_locator_wait_for_timeout():
    """Verifies Locator.wait_for raises ElementNotFoundError on timeout."""
    loc = Locator(lambda: MagicMock(), lambda root: [], description="Ghost")
    with pytest.raises(ElementNotFoundError, match="Timed out after 0.1s"):
        loc._wait_for_elements(timeout=0.1, min_count=1)


def test_locator_wait_for_hidden():
    """Verifies Locator.wait_for(state='hidden')."""
    mock_elem = MagicMock()
    # Initially present, then hidden
    call_count = [0]

    def query(root):
        call_count[0] += 1
        if call_count[0] < 3:
            return [mock_elem]
        return []

    loc = Locator(lambda: MagicMock(), query, description="DisappearingModal")
    loc.wait_for(state="hidden", timeout=1.0)
    assert call_count[0] >= 3


def test_checkbox_wrapper():
    """Verifies CheckBox wrapper reading toggle state."""
    mock_elem = MagicMock()
    mock_elem.name = "Enable Dark Mode"
    mock_toggle = MagicMock()
    mock_toggle.CurrentToggleState = 1  # 1 = On

    mock_elem._pattern.return_value = mock_toggle

    cb = CheckBox(mock_elem)
    assert cb.name == "Enable Dark Mode"
    assert cb.is_checked() is True

    mock_toggle.CurrentToggleState = 0  # 0 = Off
    assert cb.is_checked() is False


def test_tab_control_wrapper():
    """Verifies TabControl and TabItem wrapper methods."""
    mock_tab_1 = MagicMock()
    mock_tab_1.name = "General"
    mock_sel_1 = MagicMock()
    mock_sel_1.CurrentIsSelected = True
    mock_tab_1._pattern.return_value = mock_sel_1

    mock_tab_2 = MagicMock()
    mock_tab_2.name = "Advanced"
    mock_sel_2 = MagicMock()
    mock_sel_2.CurrentIsSelected = False
    mock_tab_2._pattern.return_value = mock_sel_2

    mock_container = MagicMock()
    mock_container.name = "SettingsTabs"
    mock_container.find_all.return_value = [mock_tab_1, mock_tab_2]

    tc = TabControl(mock_container)
    assert tc.tab_names == ["General", "Advanced"]
    assert tc.active_tab.name == "General"


def test_slider_wrapper():
    """Verifies Slider and ProgressBar wrappers."""
    mock_elem = MagicMock()
    mock_elem.name = "Volume"
    mock_range = MagicMock()
    mock_range.CurrentValue = 75.0
    mock_range.CurrentMinimum = 0.0
    mock_range.CurrentMaximum = 100.0
    mock_elem._pattern.return_value = mock_range

    slider = Slider(mock_elem)
    assert slider.value == 75.0
    assert slider.minimum == 0.0
    assert slider.maximum == 100.0

    pb = ProgressBar(mock_elem)
    assert pb.value == 75.0
    assert pb.maximum == 100.0


def test_menu_wrapper():
    """Verifies Menu and MenuItem wrapper methods."""
    mock_menu_root = MagicMock()
    mock_item_1 = MagicMock()
    mock_item_1.name = "File"
    mock_item_2 = MagicMock()
    mock_item_2.name = "Edit"

    mock_menu_root.find_all.return_value = [mock_item_1, mock_item_2]

    menu = Menu(mock_menu_root)
    assert [i.name for i in menu.items] == ["File", "Edit"]
