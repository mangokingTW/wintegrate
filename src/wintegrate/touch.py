"""Touch and multi-finger gestures, injected through a synthetic digitizer.

Windows has no API for "pinch" or "swipe". It has one for *contacts*: where each
finger is, frame by frame, and whether it is down, moving or lifted. Every
gesture in this module is a trajectory of contacts, and the recogniser that turns
one into a pinch lives in the system and the application, not here.

That is why nothing in this module is named after an outcome. `pinch()` moves two
contacts apart; whether the application zooms is the caller's assertion to make,
because the thresholds involved -- distance, timing, `GESTURECONFIG` -- are
system metrics that differ between machines. A method called `pinch_to_zoom()`
would be promising something this layer cannot deliver.

`available()` is the other deliberate shape. Injection reports success on hosts
where nothing receives the contact -- measured on a GitHub ARM runner whose
desktop was covered by a full-screen onboarding window, where all three calls
returned true and no window saw anything. So availability is established by
injecting a real tap into a window of our own and checking it arrived, once, and
caching the answer.
"""

from __future__ import annotations

import ctypes
import logging
import math
import sys
import time
from contextlib import contextmanager
from ctypes import wintypes

from wintegrate.interop import (
    POINTER_FLAG_DOWN,
    POINTER_FLAG_INCONTACT,
    POINTER_FLAG_INRANGE,
    POINTER_FLAG_UP,
    POINTER_FLAG_UPDATE,
    TOUCH_MAX_CONTACTS,
    create_touch_device,
    destroy_touch_device,
    initialize_legacy_touch_injection,
    inject_touch_frame,
    make_touch_contact,
    touch_injection_api_available,
    user32,
)

logger = logging.getLogger(__name__)

#: Seconds between injected frames during a moving gesture. 8 ms is about
#: 120 Hz, in the range a real digitizer reports at, and slow enough that the
#: recogniser sees a trajectory rather than a teleport.
DEFAULT_FRAME_INTERVAL = 0.008

#: How long a tap holds contact before lifting. A tap that goes down and up in
#: the same instant is a legitimate frame sequence that some controls ignore.
DEFAULT_TAP_DURATION = 0.05

#: Long-press default. Windows' own press-and-hold threshold is around 500 ms;
#: this leaves margin above it.
DEFAULT_LONG_PRESS_DURATION = 0.9

DOWN_FLAGS = POINTER_FLAG_DOWN | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT
MOVE_FLAGS = POINTER_FLAG_UPDATE | POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT
UP_FLAGS = POINTER_FLAG_UP


def _interpolate(start: tuple[int, int], end: tuple[int, int], steps: int) -> list[tuple[int, int]]:
    """Points along a straight line, excluding the start, including the end."""
    steps = max(1, steps)
    x0, y0 = start
    x1, y1 = end
    return [
        (round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps))
        for i in range(1, steps + 1)
    ]


class Contact:
    """One finger, while a `Touch.contacts()` block is open.

    Holds only its own id and position. Frames are whole-hand, so moving a
    contact means asking the owning `Touch` to send a frame describing all of
    them -- a contact cannot move on its own without lifting the others.
    """

    def __init__(self, owner: Touch, pointer_id: int, x: int, y: int):
        self._owner = owner
        self.pointer_id = pointer_id
        self.x = x
        self.y = y
        self.down = False

    @property
    def position(self) -> tuple[int, int]:
        return (self.x, self.y)

    def move_to(self, x: int, y: int) -> bool:
        """Moves this contact and sends one frame for the whole hand."""
        self.x, self.y = int(x), int(y)
        return self._owner._send_frame()

    def __repr__(self) -> str:
        return f"<Contact id={self.pointer_id} at=({self.x}, {self.y}) down={self.down}>"


class Touch:
    """Injects touch contacts, and gestures built out of them.

    A single instance owns one synthetic digitizer, created on first use and
    destroyed with `close()`. Reuse it: creating a device per tap works, but the
    device shows up in `GetPointerDevices` and churning them is noise.
    """

    def __init__(self, max_contacts: int = TOUCH_MAX_CONTACTS):
        self.max_contacts = max_contacts
        self._device: int | None = None
        self._legacy = False
        self._started = False
        self._open: list[Contact] = []
        self._available: bool | None = None

    # --- device lifetime ----------------------------------------------------

    def _ensure_device(self) -> bool:
        if self._started:
            return self._device is not None or self._legacy
        self._started = True
        self._device = create_touch_device(self.max_contacts)
        if self._device is None:
            # No modern device: fall back to the Windows 8 path, which injects
            # without a handle. Measured working on every host where the modern
            # one worked, so this is a compatibility path rather than a repair.
            self._legacy = initialize_legacy_touch_injection(self.max_contacts)
            if not self._legacy:
                logger.warning("No touch injection path is available on this host")
        return self._device is not None or self._legacy

    def close(self) -> None:
        destroy_touch_device(self._device)
        self._device = None
        self._legacy = False
        self._started = False
        self._open = []

    def __enter__(self) -> Touch:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # --- capability ---------------------------------------------------------

    def available(self, force: bool = False) -> bool:
        """Whether an injected contact actually reaches a window.

        Not "is the API there": that question has a different answer. This taps
        a hidden window of our own and reports whether the tap arrived, because
        a host can accept every injection and deliver none -- which is what a
        desktop covered by another window looks like from here.

        Cached after the first call; pass `force=True` to measure again, which
        is worth doing if the desktop has been cleared since.
        """
        if self._available is not None and not force:
            return self._available
        if sys.platform != "win32" or not touch_injection_api_available():
            self._available = False
            return False
        self._available = _delivery_check(self)
        if not self._available:
            logger.warning(
                "Touch injection is accepted but not delivered on this host. A "
                "full-screen window owning the foreground is the usual cause: on "
                "a fresh GitHub ARM runner the onboarding screen does exactly "
                "this, and closing it makes touch work."
            )
        return self._available

    # --- frames -------------------------------------------------------------

    def _send_frame(self) -> bool:
        """Sends one frame describing every open contact."""
        if not self._open:
            return True
        contacts = [
            make_touch_contact(
                c.x, c.y, DOWN_FLAGS if not c.down else MOVE_FLAGS, pointer_id=c.pointer_id
            )
            for c in self._open
        ]
        ok = inject_touch_frame(self._device, contacts)
        for c in self._open:
            c.down = True
        return ok

    # --- single-finger ------------------------------------------------------

    def tap(self, x: int, y: int, duration: float = DEFAULT_TAP_DURATION) -> bool:
        """Presses and lifts one contact. Returns whether every frame was accepted."""
        if not self._ensure_device():
            return False
        x, y = int(x), int(y)
        ok = inject_touch_frame(self._device, [make_touch_contact(x, y, DOWN_FLAGS)])
        if duration > 0:
            time.sleep(duration)
            # A holding frame, so the contact has a duration rather than
            # existing only between two adjacent injections.
            ok = inject_touch_frame(self._device, [make_touch_contact(x, y, MOVE_FLAGS)]) and ok
        return inject_touch_frame(self._device, [make_touch_contact(x, y, UP_FLAGS)]) and ok

    def double_tap(self, x: int, y: int, interval: float = 0.08) -> bool:
        """Two taps at one point, `interval` apart.

        The interval has to stay under the system's double-click time, which is
        a user setting (`GetDoubleClickTime`, 500 ms by default) -- so this reads
        it rather than assuming, and clamps.
        """
        limit = 0.5
        try:
            limit = max(0.05, user32.GetDoubleClickTime() / 1000.0 * 0.6)
        except Exception as exc:  # pragma: no cover - metric is advisory
            logger.debug(f"GetDoubleClickTime unavailable ({type(exc).__name__}): {exc}")
        gap = min(interval, limit)
        first = self.tap(x, y)
        time.sleep(gap)
        return self.tap(x, y) and first

    def long_press(self, x: int, y: int, duration: float = DEFAULT_LONG_PRESS_DURATION) -> bool:
        """Holds one contact still, re-sending frames for the whole duration.

        The frames are not decoration: a contact that is injected once and then
        left alone is a finger that stopped reporting, and press-and-hold
        recognition wants to see it stay.
        """
        if not self._ensure_device():
            return False
        x, y = int(x), int(y)
        ok = inject_touch_frame(self._device, [make_touch_contact(x, y, DOWN_FLAGS)])
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            time.sleep(DEFAULT_FRAME_INTERVAL)
            ok = inject_touch_frame(self._device, [make_touch_contact(x, y, MOVE_FLAGS)]) and ok
        return inject_touch_frame(self._device, [make_touch_contact(x, y, UP_FLAGS)]) and ok

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        steps: int = 12,
        frame_interval: float = DEFAULT_FRAME_INTERVAL,
    ) -> bool:
        """Drags one contact along a straight line.

        Whether this pans, scrolls, flicks or selects is up to what is underneath
        and how far it travelled -- pan thresholds are system metrics. This
        guarantees the trajectory, not the interpretation.
        """
        if not self._ensure_device():
            return False
        start = (int(start_x), int(start_y))
        ok = inject_touch_frame(self._device, [make_touch_contact(*start, DOWN_FLAGS)])
        for x, y in _interpolate(start, (int(end_x), int(end_y)), steps):
            time.sleep(frame_interval)
            ok = inject_touch_frame(self._device, [make_touch_contact(x, y, MOVE_FLAGS)]) and ok
        return (
            inject_touch_frame(self._device, [make_touch_contact(int(end_x), int(end_y), UP_FLAGS)])
            and ok
        )

    # --- multi-finger -------------------------------------------------------

    @contextmanager
    def contacts(self, points: list[tuple[int, int]]):
        """Puts several contacts down and yields them, for hand-written gestures.

        Use this when the shape is not one of the gestures below:

            with touch.contacts([(100, 100), (300, 300)]) as (a, b):
                a.move_to(120, 120)
                b.move_to(280, 280)

        Frames are whole-hand, so `Contact.move_to` sends the positions of every
        open contact -- one finger cannot move without the others being restated,
        or they would read as lifted.
        """
        if not self._ensure_device():
            yield []
            return
        if len(points) > self.max_contacts:
            raise ValueError(
                f"{len(points)} contacts requested but the device was created for "
                f"{self.max_contacts}"
            )
        self._open = [
            Contact(self, pointer_id=i, x=int(x), y=int(y)) for i, (x, y) in enumerate(points)
        ]
        self._send_frame()
        try:
            yield tuple(self._open)
        finally:
            up = [
                make_touch_contact(c.x, c.y, UP_FLAGS, pointer_id=c.pointer_id) for c in self._open
            ]
            inject_touch_frame(self._device, up)
            self._open = []

    def pinch(
        self,
        centre_x: int,
        centre_y: int,
        start_radius: int,
        end_radius: int,
        steps: int = 12,
        angle_degrees: float = 0.0,
        frame_interval: float = DEFAULT_FRAME_INTERVAL,
    ) -> bool:
        """Moves two contacts along a line through a centre, together or apart.

        `end_radius > start_radius` spreads; smaller pinches. Nothing here
        promises a zoom -- see this module's docstring.
        """
        if not self._ensure_device():
            return False
        rad = math.radians(angle_degrees)
        dx, dy = math.cos(rad), math.sin(rad)

        def pair(radius: float) -> list[tuple[int, int]]:
            return [
                (round(centre_x + dx * radius), round(centre_y + dy * radius)),
                (round(centre_x - dx * radius), round(centre_y - dy * radius)),
            ]

        ok = True
        with self.contacts(pair(start_radius)) as held:
            if not held:
                return False
            a, b = held
            for i in range(1, max(1, steps) + 1):
                radius = start_radius + (end_radius - start_radius) * i / max(1, steps)
                (ax, ay), (bx, by) = pair(radius)
                a.x, a.y = ax, ay
                b.x, b.y = bx, by
                time.sleep(frame_interval)
                ok = self._send_frame() and ok
        return ok

    def rotate(
        self,
        centre_x: int,
        centre_y: int,
        radius: int,
        degrees: float,
        steps: int = 16,
        frame_interval: float = DEFAULT_FRAME_INTERVAL,
    ) -> bool:
        """Turns two opposed contacts about a centre by `degrees`."""
        if not self._ensure_device():
            return False

        def pair(angle: float) -> list[tuple[int, int]]:
            rad = math.radians(angle)
            dx, dy = math.cos(rad) * radius, math.sin(rad) * radius
            return [
                (round(centre_x + dx), round(centre_y + dy)),
                (round(centre_x - dx), round(centre_y - dy)),
            ]

        ok = True
        with self.contacts(pair(0.0)) as held:
            if not held:
                return False
            a, b = held
            for i in range(1, max(1, steps) + 1):
                (ax, ay), (bx, by) = pair(degrees * i / max(1, steps))
                a.x, a.y = ax, ay
                b.x, b.y = bx, by
                time.sleep(frame_interval)
                ok = self._send_frame() and ok
        return ok


# --- delivery check ---------------------------------------------------------
#
# A window of our own, tapped once, to answer "did anything receive it". Kept
# private and deliberately small: it exists because the injection return value
# does not answer that question.

_WM_POINTERDOWN = 0x0246
_WM_TOUCH = 0x0240
_WM_LBUTTONDOWN = 0x0201
_WS_POPUP = 0x80000000
_WS_VISIBLE = 0x10000000
_WS_EX_TOPMOST = 0x00000008
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TRANSPARENT = 0x00000020
_SW_SHOWNOACTIVATE = 4
_PM_REMOVE = 0x0001
_CLASS_NAME = "WintegrateTouchDeliveryCheck"
_class_registered = False


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_void_p),
        ("lParam", ctypes.c_void_p),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class _WNDCLASSW(ctypes.Structure):
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


def _delivery_check(touch: Touch) -> bool:
    """Taps a small window of our own and reports whether the tap arrived."""
    if not touch._ensure_device():
        return False
    global _class_registered

    from wintegrate.interop import kernel32

    _WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_void_p, wintypes.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p
    )
    received = []

    def wndproc(hwnd, msg, wparam, lparam):
        if msg in (_WM_POINTERDOWN, _WM_TOUCH, _WM_LBUTTONDOWN):
            received.append(msg)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = _WNDPROC(wndproc)
    try:
        cls = _WNDCLASSW()
        cls.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
        cls.hInstance = kernel32.GetModuleHandleW(None)
        cls.lpszClassName = _CLASS_NAME
        if not _class_registered:
            if not user32.RegisterClassW(ctypes.byref(cls)):
                logger.debug(f"delivery check: RegisterClassW failed ({ctypes.get_last_error()})")
                return False
            _class_registered = True

        # Small, topmost, and off in a corner: the check must not cover whatever
        # the caller is about to automate. NOACTIVATE so it does not steal focus.
        width = height = 60
        left = max(0, user32.GetSystemMetrics(0) - width - 4)
        top = 4
        hwnd = user32.CreateWindowExW(
            _WS_EX_TOPMOST | _WS_EX_NOACTIVATE,
            _CLASS_NAME,
            "touch check",
            _WS_POPUP | _WS_VISIBLE,
            left,
            top,
            width,
            height,
            None,
            None,
            cls.hInstance,
            None,
        )
        if not hwnd:
            logger.debug(f"delivery check: CreateWindowExW failed ({ctypes.get_last_error()})")
            return False
        try:
            user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
            _pump(0.2)
            touch.tap(left + width // 2, top + height // 2)
            _pump(0.5)
            return bool(received)
        finally:
            user32.DestroyWindow(hwnd)
            _pump(0.05)
    except Exception as exc:  # pragma: no cover - the check must never raise
        logger.debug(f"delivery check failed ({type(exc).__name__}): {exc}")
        return False


def _pump(seconds: float) -> None:
    msg = _MSG()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.005)
