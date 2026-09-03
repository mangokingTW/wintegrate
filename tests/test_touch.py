"""Touch injection: the frame arithmetic anywhere, a real button on Windows.

The unit tests below run on any platform, because building a contact frame is
arithmetic and struct packing. The one that matters runs only on Windows and
presses a real `BUTTON`, asserting `WM_COMMAND` / `BN_CLICKED` -- Windows sends
that only if it decided the control was pressed, which is the claim this module
makes and one layer further out than "the contact was accepted".

It presses the same button with the mouse first. That control case is not
decoration: without it, "touch did not press the button" cannot be told from "the
button was not where the test aimed", and that is exactly the mistake that made
an earlier round of measurements on the ARM runner meaningless -- a full-screen
onboarding window was covering the desktop, so nothing reached anything, and the
result read as a touch limitation.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

try:
    import pytest
except ImportError:  # pragma: no cover - the suite also runs without pytest
    pytest = None

from wintegrate.interop import (
    POINTER_FLAG_DOWN,
    POINTER_FLAG_UP,
    TOUCH_DEFAULT_CONTACT_RADIUS,
    TOUCH_DEFAULT_PRESSURE,
    make_touch_contact,
)
from wintegrate.touch import (
    DOWN_FLAGS,
    MOVE_FLAGS,
    UP_FLAGS,
    Touch,
    _interpolate,
)

WINDOWS = sys.platform == "win32"


# --- frame arithmetic, any platform -----------------------------------------


def test_a_contact_describes_a_patch_not_a_point():
    """The contact rectangle is centred on the coordinate.

    A contact with no area is accepted by the API but describes an infinitely
    small fingertip, and gesture recognisers do read the area.
    """
    contact = make_touch_contact(100, 200, POINTER_FLAG_DOWN)
    r = TOUCH_DEFAULT_CONTACT_RADIUS
    assert (contact.pointerInfo.ptPixelLocation.x, contact.pointerInfo.ptPixelLocation.y) == (
        100,
        200,
    )
    assert (contact.rcContact.left, contact.rcContact.right) == (100 - r, 100 + r)
    assert (contact.rcContact.top, contact.rcContact.bottom) == (200 - r, 200 + r)
    assert contact.pressure == TOUCH_DEFAULT_PRESSURE
    assert contact.pointerInfo.pointerFlags == POINTER_FLAG_DOWN


def test_contact_ids_are_carried_through():
    """Multi-finger frames are told apart by pointer id, so it must survive."""
    for pointer_id in (0, 1, 9):
        contact = make_touch_contact(1, 1, POINTER_FLAG_UP, pointer_id=pointer_id)
        assert contact.pointerInfo.pointerId == pointer_id


def test_the_flag_sets_say_down_moving_and_lifted():
    """DOWN and UPDATE claim contact; UP must not, or the finger never lifts."""
    from wintegrate.interop import POINTER_FLAG_INCONTACT, POINTER_FLAG_INRANGE

    assert DOWN_FLAGS & POINTER_FLAG_DOWN
    assert DOWN_FLAGS & POINTER_FLAG_INCONTACT
    assert MOVE_FLAGS & POINTER_FLAG_INRANGE
    assert MOVE_FLAGS & POINTER_FLAG_INCONTACT
    assert UP_FLAGS & POINTER_FLAG_UP
    assert not UP_FLAGS & POINTER_FLAG_INCONTACT, "an UP frame still holding contact never lifts"


def test_interpolation_ends_on_the_target():
    """A swipe that stops short of its destination is a different gesture."""
    points = _interpolate((0, 0), (10, 20), 5)
    assert len(points) == 5
    assert points[-1] == (10, 20)
    assert (0, 0) not in points, "the start is already sent as the DOWN frame"
    # Monotonic, so the trajectory reads as a drag rather than a jitter.
    assert points == sorted(points)


def test_interpolation_survives_a_zero_step_request():
    """steps=0 would divide by zero; it means 'go straight there'."""
    assert _interpolate((0, 0), (5, 5), 0) == [(5, 5)]


def test_more_contacts_than_the_device_was_made_for_is_an_error():
    """Silently dropping a finger would make a two-finger gesture a one-finger one."""
    touch = Touch(max_contacts=2)
    try:
        with touch.contacts([(0, 0), (1, 1), (2, 2)]):
            pass
    except ValueError as exc:
        assert "2" in str(exc)
    else:
        if WINDOWS:
            raise AssertionError("three contacts on a two-contact device should raise")


def test_every_gesture_declines_rather_than_raises_where_touch_is_absent():
    """Off Windows there is no digitizer, and that is not an exception.

    Callers branch on the return value; a raise here would make importing this
    module on a developer's Mac a hard failure instead of a False.
    """
    if WINDOWS:
        return
    touch = Touch()
    assert touch.available() is False
    assert touch.tap(5, 5) is False
    assert touch.double_tap(5, 5) is False
    assert touch.long_press(5, 5, duration=0.01) is False
    assert touch.swipe(0, 0, 5, 5) is False
    assert touch.pinch(50, 50, 10, 40) is False
    assert touch.rotate(50, 50, 20, 90) is False
    with touch.contacts([(1, 1)]) as held:
        assert held == []


# --- a real button, Windows only --------------------------------------------

_WM_COMMAND = 0x0111
_BN_CLICKED = 0
_BUTTON_ID = 1000
_WS_CHILD = 0x40000000
_WS_VISIBLE = 0x10000000
_WS_POPUP = 0x80000000
_WS_EX_TOPMOST = 0x00000008
_SW_SHOW = 5
_PM_REMOVE = 0x0001
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000
_INPUT_MOUSE = 0


class _ButtonHost:
    """A popup window with one real BUTTON, counting its BN_CLICKED messages."""

    def __init__(self):
        from wintegrate.interop import INPUT, MOUSEINPUT, kernel32, user32

        self.user32 = user32
        self.kernel32 = kernel32
        self.INPUT = INPUT
        self.MOUSEINPUT = MOUSEINPUT
        self.clicks = 0
        self.hwnd = None
        self.button = None

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", ctypes.c_uint),
                ("wParam", ctypes.c_void_p),
                ("lParam", ctypes.c_void_p),
                ("time", wintypes.DWORD),
                ("pt", wintypes.POINT),
            ]

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        self._MSG = MSG
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_void_p, wintypes.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p
        )
        self._proc = WNDPROC(self._wndproc)

        # Declared here rather than trusted: an undeclared CreateWindowExW
        # converts hInstance as c_int and dies with OverflowError on a 64-bit
        # handle, which has cost this project several debugging sessions.
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.WORD
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        user32.DefWindowProcW.restype = ctypes.c_void_p
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(MSG),
            wintypes.HWND,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        user32.DispatchMessageW.restype = ctypes.c_void_p
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
        user32.GetDlgCtrlID.restype = ctypes.c_int

        cls = WNDCLASSW()
        cls.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        cls.hInstance = kernel32.GetModuleHandleW(None)
        cls.lpszClassName = "WintegrateTouchTestHost"
        cls.hbrBackground = ctypes.c_void_p(6)
        user32.RegisterClassW(ctypes.byref(cls))  # idempotent enough for one process

        width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        self.hwnd = user32.CreateWindowExW(
            _WS_EX_TOPMOST,
            "WintegrateTouchTestHost",
            "wintegrate touch test",
            _WS_POPUP | _WS_VISIBLE,
            width // 4,
            height // 4,
            400,
            240,
            None,
            None,
            cls.hInstance,
            None,
        )
        if self.hwnd:
            self.button = user32.CreateWindowExW(
                0,
                "BUTTON",
                "Tap me",
                _WS_CHILD | _WS_VISIBLE,
                100,
                80,
                200,
                80,
                self.hwnd,
                ctypes.c_void_p(_BUTTON_ID),
                cls.hInstance,
                None,
            )
            user32.ShowWindow(self.hwnd, _SW_SHOW)
            user32.SetForegroundWindow(self.hwnd)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == _WM_COMMAND:
            wp = int(wparam or 0)
            if (wp & 0xFFFF) == _BUTTON_ID and ((wp >> 16) & 0xFFFF) == _BN_CLICKED:
                self.clicks += 1
        return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def centre(self) -> tuple[int, int]:
        r = wintypes.RECT()
        self.user32.GetWindowRect(self.button, ctypes.byref(r))
        return ((r.left + r.right) // 2, (r.top + r.bottom) // 2)

    def control_id_at(self, x: int, y: int) -> int:
        return self.user32.GetDlgCtrlID(self.user32.WindowFromPoint(wintypes.POINT(x, y)))

    def pump(self, seconds: float) -> None:
        msg = self._MSG()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            while self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
                self.user32.TranslateMessage(ctypes.byref(msg))
                self.user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.005)

    def click_with_mouse(self, x: int, y: int) -> None:
        sw = self.user32.GetSystemMetrics(0)
        sh = self.user32.GetSystemMetrics(1)
        ax = int(x * 65535 / max(sw - 1, 1))
        ay = int(y * 65535 / max(sh - 1, 1))
        events = []
        for flags in (
            _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE,
            _MOUSEEVENTF_LEFTDOWN | _MOUSEEVENTF_ABSOLUTE,
            _MOUSEEVENTF_LEFTUP | _MOUSEEVENTF_ABSOLUTE,
        ):
            item = self.INPUT()
            item.type = _INPUT_MOUSE
            item.mi = self.MOUSEINPUT(
                dx=ax, dy=ay, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=None
            )
            events.append(item)
        array = (self.INPUT * len(events))(*events)
        self.user32.SendInput(len(array), array, ctypes.sizeof(self.INPUT))

    def close(self) -> None:
        if self.hwnd:
            self.user32.DestroyWindow(self.hwnd)


def test_an_injected_contact_presses_a_real_button():
    """The claim: a tap makes Windows report the button as clicked."""
    if not WINDOWS:
        return

    host = _ButtonHost()
    if not host.button:
        host.close()
        if pytest is not None:
            pytest.skip("could not create the test window")
        return
    try:
        host.pump(0.4)
        x, y = host.centre()

        # The control case, and the reason this test can be believed. If the
        # mouse cannot press the button either, the desktop is covered or the
        # coordinate is wrong, and nothing about touch has been measured.
        at_point = host.control_id_at(x, y)
        host.click_with_mouse(x, y)
        host.pump(0.8)
        if host.clicks == 0:
            message = (
                f"the mouse could not press the button at ({x}, {y}); control id at "
                f"that point is {at_point}, expected {_BUTTON_ID}. Something is "
                "covering the desktop -- on a fresh GitHub ARM runner that is the "
                "onboarding window, which CI closes before the GUI tests."
            )
            host.close()
            if pytest is not None:
                pytest.skip(message)
            return
        after_mouse = host.clicks

        with Touch() as touch:
            if not touch.available():
                host.close()
                if pytest is not None:
                    pytest.skip("touch injection is not delivered on this host")
                return
            assert touch.tap(x, y), "the tap was refused by the injection API"
        host.pump(0.8)

        assert host.clicks > after_mouse, (
            f"touch did not press the button: {after_mouse} click(s) after the mouse, "
            f"{host.clicks} after the tap, control id at ({x}, {y}) = {at_point}"
        )
    finally:
        host.close()


if __name__ == "__main__":
    test_a_contact_describes_a_patch_not_a_point()
    test_contact_ids_are_carried_through()
    test_the_flag_sets_say_down_moving_and_lifted()
    test_interpolation_ends_on_the_target()
    test_interpolation_survives_a_zero_step_request()
    test_more_contacts_than_the_device_was_made_for_is_an_error()
    test_every_gesture_declines_rather_than_raises_where_touch_is_absent()
    test_an_injected_contact_presses_a_real_button()
    print("All touch tests passed!")
