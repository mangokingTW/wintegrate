"""wintegrate's pinned argtypes must not escape into the rest of the process.

Reported against 0.1.0: `ctypes.windll` is a process-wide cache, and the WinDLL
it returns caches its function pointers, so `ctypes.windll.user32.SendInput` is
one shared object. argtypes pinned on it are visible to every other ctypes user
in the process, and ctypes checks pointer arguments by *type identity* rather
than layout — so an unrelated library passing its own byte-identical INPUT
struct fails with "expected LP_INPUT instance instead of LP_Input", raised from
inside that library.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

import pytest

from wintegrate import interop

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 DLL handles")

# Functions wintegrate pins that another library plausibly also calls.
# SendInput and MapVirtualKeyW are the exact pair pydirectinput caches.
SHARED_RISK_FUNCTIONS = ["SendInput", "MapVirtualKeyW", "VkKeyScanW", "GetKeyboardLayout"]


def test_handles_are_private_instances():
    assert interop.user32 is not ctypes.windll.user32
    assert interop.kernel32 is not ctypes.windll.kernel32
    assert interop.imm32 is not ctypes.windll.imm32
    assert interop.gdi32 is not ctypes.windll.gdi32
    assert interop.ole32 is not ctypes.windll.ole32


@pytest.mark.parametrize("name", SHARED_RISK_FUNCTIONS)
def test_pinned_argtypes_do_not_leak_to_the_shared_handle(name):
    """The process-wide handle stays unpinned even though wintegrate pinned its own."""
    ours = getattr(interop.user32, name)
    theirs = getattr(ctypes.windll.user32, name)

    assert ours is not theirs
    assert ours.argtypes, f"{name} should be pinned on wintegrate's handle"
    assert theirs.argtypes is None, (
        f"{name} argtypes leaked into ctypes.windll.user32; another ctypes user "
        "passing its own struct would fail on pointer type identity"
    )


def test_another_library_can_still_call_sendinput_with_its_own_struct():
    """The reported failure, reproduced end to end against the shared handle.

    A separate INPUT definition — byte-identical, different Python type — must
    still be accepted by ctypes.windll.user32.SendInput. Sending zero events
    exercises argument conversion without injecting anything.
    """

    class TheirKeybdInput(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class TheirUnion(ctypes.Union):
        _fields_ = [("ki", TheirKeybdInput)]

    class TheirInput(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", TheirUnion)]

    theirs = TheirInput()
    # nInputs=0: nothing is injected, but ctypes still converts every argument,
    # which is where the leaked argtypes used to raise.
    ctypes.windll.user32.SendInput(0, ctypes.pointer(theirs), ctypes.sizeof(theirs))
