"""Unit tests for Playwright-style Mouse controller and pointer gestures."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from wintegrate.element import UiaElement
from wintegrate.locators import Locator
from wintegrate.mouse import Mouse


def test_mouse_position_returns_tuple():
    """Verifies that mouse.position always returns a valid (x, y) 2-tuple."""
    mouse = Mouse()
    pos = mouse.position
    assert isinstance(pos, tuple)
    assert len(pos) == 2
    assert isinstance(pos[0], int)
    assert isinstance(pos[1], int)


@patch("wintegrate.mouse.send_mouse_move")
@patch("wintegrate.mouse.send_mouse_click")
@patch("wintegrate.mouse.send_mouse_right_click")
@patch("wintegrate.mouse.send_mouse_middle_click")
@patch("wintegrate.mouse.send_mouse_double_click")
@patch("wintegrate.mouse.send_mouse_down")
@patch("wintegrate.mouse.send_mouse_up")
@patch("wintegrate.mouse.send_mouse_wheel")
@patch("wintegrate.mouse.send_mouse_hwheel")
@patch("wintegrate.mouse.send_mouse_drag")
def test_mouse_controller_dispatches(
    mock_drag,
    mock_hwheel,
    mock_wheel,
    mock_up,
    mock_down,
    mock_dblclick,
    mock_middle_click,
    mock_right_click,
    mock_click,
    mock_move,
):
    """Verifies all Mouse helper methods correctly route to underlying interop primitives."""
    mouse = Mouse()

    # Move
    mouse.move(100, 200, steps=5, delay=0.01)
    mock_move.assert_called_once_with(100, 200, steps=5, delay=0.01)

    # Click variants
    mouse.click(50, 60, button="left")
    mock_click.assert_called_once_with(50, 60, move_event=True)

    mouse.right_click(70, 80)
    mock_right_click.assert_called_once_with(70, 80, move_event=True)

    mouse.middle_click(90, 100)
    mock_middle_click.assert_called_once_with(90, 100, move_event=True)

    mouse.dblclick(110, 120, delay=0.02)
    mock_dblclick.assert_called_once_with(110, 120, move_event=True, interval=0.02)

    # Down / Up
    mouse.down(button="right", x=10, y=20)
    mock_down.assert_called_once_with(button="right", x=10, y=20)

    mouse.up(button="right", x=10, y=20)
    mock_up.assert_called_once_with(button="right", x=10, y=20)

    # Wheel (vertical & horizontal)
    mouse.wheel(delta_y=120, delta_x=-240, x=300, y=400)
    mock_wheel.assert_called_once_with(120, x=300, y=400, move_event=True)
    mock_hwheel.assert_called_once_with(-240, x=300, y=400, move_event=True)

    # Drag
    mouse.drag(10, 20, 300, 400, steps=8, delay=0.02)
    mock_drag.assert_called_once_with(10, 20, 300, 400, steps=8, delay=0.02)


def test_mouse_invalid_button_raises():
    """Verifies invalid button names raise ValueError."""
    mouse = Mouse()
    with pytest.raises(ValueError, match="Unknown mouse button 'invalid'"):
        mouse.click(10, 20, button="invalid")


@patch("wintegrate.element.send_mouse_move")
@patch("wintegrate.element.send_mouse_middle_click")
def test_element_hover_and_middle_click(mock_middle_click, mock_move):
    """Verifies UiaElement.hover() and UiaElement.middle_click() coordinate calculation."""
    mock_com = MagicMock()
    mock_rect = MagicMock()
    mock_rect.left = 100
    mock_rect.top = 200
    mock_rect.right = 300
    mock_rect.bottom = 400
    mock_com.CurrentBoundingRectangle = mock_rect

    elem = UiaElement(mock_com)

    # Hover calculates center: ( (100+300)//2, (200+400)//2 ) = (200, 300)
    elem.hover(steps=4)
    mock_move.assert_called_once_with(200, 300, steps=4, delay=0.0)

    # Middle click calculates center
    elem.middle_click()
    mock_middle_click.assert_called_once_with(200, 300)


def test_locator_hover_and_middle_click():
    """Verifies Locator.hover() and Locator.middle_click()."""
    mock_elem = MagicMock()
    loc = Locator(lambda: MagicMock(), lambda root: [mock_elem], description="Button")

    loc.hover(steps=3)
    mock_elem.hover.assert_called_once_with(steps=3, delay=0.0)

    loc.middle_click()
    mock_elem.middle_click.assert_called_once()


def test_locator_drag_to():
    """Verifies Locator.drag_to(target_locator)."""
    source_elem = MagicMock()
    target_elem = MagicMock()

    source_loc = Locator(lambda: MagicMock(), lambda root: [source_elem], description="Source")
    target_loc = Locator(lambda: MagicMock(), lambda root: [target_elem], description="Target")

    source_loc.drag_to(target_loc, steps=15, duration=0.3)
    source_elem.drag_to.assert_called_once_with(target_elem, steps=15, duration=0.3)
