"""Playwright-style Mouse controller for Windows desktop UI automation."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import TYPE_CHECKING

from wintegrate.interop import (
    send_mouse_click,
    send_mouse_double_click,
    send_mouse_down,
    send_mouse_drag,
    send_mouse_hwheel,
    send_mouse_middle_click,
    send_mouse_move,
    send_mouse_right_click,
    send_mouse_up,
    send_mouse_wheel,
    user32,
)

if TYPE_CHECKING:
    from wintegrate.session import Session


class Mouse:
    """
    Playwright-style Mouse controller for Windows desktop automation.

    Provides high-level physical and interpolated pointer interactions,
    multi-button clicks, dragging, and wheel scrolling.
    """

    def __init__(self, session: Session | None = None):
        self._session = session

    @property
    def position(self) -> tuple[int, int]:
        """Returns the current screen coordinates (x, y) of the mouse pointer."""
        try:
            if user32 is not None:
                pt = wintypes.POINT()
                if user32.GetCursorPos(ctypes.byref(pt)):
                    return (pt.x, pt.y)
        except Exception:
            pass
        return (0, 0)

    def move(
        self,
        x: int,
        y: int,
        *,
        steps: int = 1,
        delay: float = 0.0,
    ) -> None:
        """
        Moves the mouse pointer to the specified (x, y) coordinates.

        If `steps` > 1, interpolates the movement across intermediate points,
        emitting `WM_MOUSEMOVE` events at each step (ideal for video recording
        and hovering over menus/tooltips).
        """
        send_mouse_move(x, y, steps=steps, delay=delay)

    def down(
        self,
        button: str = "left",
        *,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        """Presses and holds the specified mouse button ('left', 'right', 'middle')."""
        send_mouse_down(button=button, x=x, y=y)

    def up(
        self,
        button: str = "left",
        *,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        """Releases the specified mouse button ('left', 'right', 'middle')."""
        send_mouse_up(button=button, x=x, y=y)

    def click(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        count: int = 1,
        delay: float = 0.0,
    ) -> None:
        """
        Moves to (x, y) and performs one or more clicks with the specified button.
        """
        btn = button.lower().strip()
        for i in range(count):
            if btn == "left":
                send_mouse_click(x, y, move_event=True)
            elif btn == "right":
                send_mouse_right_click(x, y, move_event=True)
            elif btn == "middle":
                send_mouse_middle_click(x, y, move_event=True)
            else:
                raise ValueError(
                    f"Unknown mouse button {button!r}. Choose 'left', 'right', or 'middle'."
                )
            if delay > 0 and i < count - 1:
                time.sleep(delay)

    def dblclick(
        self,
        x: int,
        y: int,
        *,
        delay: float = 0.05,
    ) -> None:
        """Moves to (x, y) and performs a double left mouse click."""
        send_mouse_double_click(x, y, move_event=True, interval=delay)

    def right_click(self, x: int, y: int) -> None:
        """Moves to (x, y) and performs a single right mouse click."""
        self.click(x, y, button="right")

    def middle_click(self, x: int, y: int) -> None:
        """Moves to (x, y) and performs a single middle mouse click."""
        self.click(x, y, button="middle")

    def wheel(
        self,
        delta_y: int = 0,
        delta_x: int = 0,
        *,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        """
        Dispatches vertical and/or horizontal mouse wheel events.

        - `delta_y`: Vertical wheel movement (positive = scroll up, negative = scroll down).
        - `delta_x`: Horizontal wheel movement (positive = scroll right, negative = scroll left).
        """
        if delta_y != 0:
            send_mouse_wheel(delta_y, x=x, y=y, move_event=True)
        if delta_x != 0:
            send_mouse_hwheel(delta_x, x=x, y=y, move_event=True)

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        *,
        steps: int = 10,
        delay: float = 0.01,
    ) -> None:
        """Smoothly drags from (start_x, start_y) to (end_x, end_y) with the left button held."""
        send_mouse_drag(start_x, start_y, end_x, end_y, steps=steps, delay=delay)
