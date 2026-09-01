"""`send_mouse_click` injects a move event, not just `SetCursorPos`.

`SetCursorPos` relocates the pointer without producing any input event. Anything
watching the mouse therefore never learns it moved: a low-level hook, an overlay
drawing where clicks land, an application updating hover state. It sees a click at
whatever position it last knew about.

This was found by pointing a third-party keystroke visualiser at a wintegrate run:
every click drew its marker at the top-left corner, where the cursor had been when
the tool started, no matter where the click actually went.

The move event is verified through a real `WH_MOUSE_LL` hook rather than by
reading the cursor afterwards — `SetCursorPos` alone would satisfy that, which is
precisely the bug.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes

import pytest

from wintegrate.interop import (
    MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_MOVE,
    MOUSEEVENTF_VIRTUALDESK,
    POINT,
    send_mouse_click,
    user32,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="injects Win32 mouse input")

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
LLMHF_INJECTED = 0x00000001


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(MSLLHOOKSTRUCT)
)


class MouseHookRecorder:
    """Records low-level mouse events on its own thread.

    A private `user32` handle: pinning argtypes on the shared one would change
    them for every other ctypes user in the process, which is a hazard this
    library documents and should not itself create.
    """

    def __init__(self):
        self.events: list[tuple[int, int, int, int]] = []
        self._u = ctypes.WinDLL("user32", use_last_error=True)
        # GetCurrentThreadId lives in kernel32, not user32. Looking it up on the
        # wrong DLL raised inside the hook thread, self._hook stayed None, and the
        # two tests that matter *skipped* with the message "could not install a
        # WH_MOUSE_LL hook" -- my bug wearing an environment failure's clothes.
        self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._u.SetWindowsHookExW.restype = wintypes.HHOOK
        self._u.CallNextHookEx.restype = ctypes.c_long
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self._hook = None
        self._proc = None
        self._setup_error: BaseException | None = None

    def _pump(self):
        def callback(code, wparam, lparam):
            try:
                if code >= 0:
                    info = lparam[0]
                    self.events.append((int(wparam), info.pt.x, info.pt.y, int(info.flags)))
            except Exception:
                pass
            return self._u.CallNextHookEx(None, code, wparam, lparam)

        try:
            self._proc = _HOOKPROC(callback)
            self._tid = self._k.GetCurrentThreadId()
            self._hook = self._u.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            self._setup_error = exc
            self._ready.set()
            return
        self._ready.set()
        if not self._hook:
            return
        msg = wintypes.MSG()
        while self._u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self._u.TranslateMessage(ctypes.byref(msg))
            self._u.DispatchMessageW(ctypes.byref(msg))

    def __enter__(self):
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            pytest.fail("the hook thread never reported back")
        if self._setup_error is not None:
            # A bug in this fixture must fail, not skip. Only SetWindowsHookExW
            # returning NULL is an environment limitation.
            raise AssertionError(
                f"the mouse hook fixture is broken: "
                f"{type(self._setup_error).__name__}: {self._setup_error}"
            )
        if not self._hook:
            pytest.skip(
                f"SetWindowsHookExW(WH_MOUSE_LL) returned NULL "
                f"(GetLastError={ctypes.get_last_error()}); this host does not allow it"
            )
        return self

    def __exit__(self, *exc):
        if self._hook:
            self._u.UnhookWindowsHookEx(self._hook)
        if self._tid:
            self._u.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # WM_QUIT
        if self._thread:
            self._thread.join(timeout=3)

    def moves(self):
        return [e for e in self.events if e[0] == WM_MOUSEMOVE]

    def downs(self):
        return [e for e in self.events if e[0] == WM_LBUTTONDOWN]


def _target_point() -> tuple[int, int]:
    """A point comfortably inside the primary monitor, away from every edge."""
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    return (w // 3, h // 3)


def _park_cursor_far_from(x: int, y: int) -> tuple[int, int]:
    """Puts the cursor somewhere else first, so a stale position is detectable."""
    user32.SetCursorPos(5, 5)
    time.sleep(0.2)
    return (5, 5)


def test_a_move_event_is_injected_before_the_click():
    x, y = _target_point()
    _park_cursor_far_from(x, y)
    with MouseHookRecorder() as rec:
        time.sleep(0.3)
        send_mouse_click(x, y)
        time.sleep(0.5)

    moves = rec.moves()
    assert moves, (
        "no WM_MOUSEMOVE reached a low-level hook, so nothing watching the mouse "
        "can know where the click went"
    )
    # The move must be near the requested point. Absolute coordinates are
    # quantized to 1/65535 of the virtual desktop, so allow a couple of pixels.
    close = [m for m in moves if abs(m[1] - x) <= 2 and abs(m[2] - y) <= 2]
    assert close, f"no move landed near ({x}, {y}); moves seen: {moves[:5]}"
    assert all(m[3] & LLMHF_INJECTED for m in close), "the move should be flagged as injected"

    downs = rec.downs()
    assert downs, "the click itself was not seen by the hook"
    assert abs(downs[0][1] - x) <= 2 and abs(downs[0][2] - y) <= 2, (
        f"the button-down was reported at {downs[0][1:3]}, not near ({x}, {y})"
    )


def test_move_event_can_be_turned_off():
    """`move_event=False` is the older behaviour, for a caller that wants it."""
    x, y = _target_point()
    _park_cursor_far_from(x, y)
    with MouseHookRecorder() as rec:
        time.sleep(0.3)
        send_mouse_click(x, y, move_event=False)
        time.sleep(0.5)

    near = [m for m in rec.moves() if abs(m[1] - x) <= 2 and abs(m[2] - y) <= 2]
    assert not near, f"expected no injected move near the target, got {near[:3]}"
    assert rec.downs(), "the click should still happen without the move event"


def test_the_cursor_ends_up_exactly_where_it_was_asked_to_be():
    """The move event is quantized; `SetCursorPos` runs last so the final position is exact."""
    x, y = _target_point()
    _park_cursor_far_from(x, y)
    send_mouse_click(x, y)
    time.sleep(0.2)
    pos = POINT()
    assert user32.GetCursorPos(ctypes.byref(pos))
    assert (pos.x, pos.y) == (x, y), f"cursor ended at ({pos.x}, {pos.y}), asked for ({x}, {y})"


def test_the_flags_map_to_the_virtual_desktop():
    """ABSOLUTE alone maps onto the primary monitor, which is wrong on a second screen."""
    assert MOUSEEVENTF_VIRTUALDESK == 0x4000
    combined = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    assert combined & MOUSEEVENTF_VIRTUALDESK
    assert combined & MOUSEEVENTF_ABSOLUTE
