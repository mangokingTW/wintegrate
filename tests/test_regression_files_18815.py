"""Files #18815, reproduced against the build that had it.

Alt+Enter opens Properties. It also puts `DefWindowProc` into its menu-tracking
path, which sends the window a `WM_MENUCHAR` looking for a mnemonic; there is no
menu, nothing matches, and the default handling answers `MNC_IGNORE` — which
plays the system Asterisk sound. PR #18815 handles the message in Files' own
window-message subclass and answers `MNC_CLOSE` instead:

    else if (e.Message.MessageId == WM_MENUCHAR && (e.Message.WParam & 0xFFFF) == '\\r')
    {
        e.Result = Win32PInvoke.MNC_CLOSE << 16;
        e.Handled = true;
    }

Measured, same package on both sides:

    build       Enter (0x0D)   'a'          Esc
    4.2.9.0     0x00010000     0x00000000   0x00000000
    4.2.7.0     0x00000000     0x00000000   0x00000000

**This is a bug whose entire symptom is a sound.** Nothing renders differently,
no file changes, and a screenshot of the two builds is identical — a recording
would have to capture audio to see it at all. The return value of one message
answers it exactly, with no UI interaction to time.

`WM_MENUCHAR` is `0x0120`, below `WM_USER`, so it is a *system* message and
USER32 marshals it across the process boundary. A custom message would have gone
across as two integers with no dispatch into the receiver's subclass at all —
the same boundary that makes `WM_GETTEXT` usable on Scintilla and `SCI_GETTEXT`
not.

The fix is deliberately narrow — only `'\\r'` — so the other characters are
asserted too. Without them a build that answered `MNC_CLOSE` to *everything*,
swallowing mnemonics the shell relies on, would pass as fixed.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

import pytest

from wintegrate import Window
from wintegrate.apps import sweep_processes_verified

# Not part of the release gate; see tests/test_regression_notepadpp_16326.py.
pytestmark = [
    pytest.mark.upstream_bug,
    pytest.mark.target_app,
    pytest.mark.skipif(sys.platform != "win32", reason="drives a live WinUI 3 application"),
]

REQUIRE_BUILDS = os.environ.get("WINTEGRATE_REQUIRE_UPSTREAM_BUILDS") == "1"

PACKAGE = "Files"
PROCESS = "Files.exe"
WINDOW_CLASS = "WinUIDesktopWin32WindowClass"

BUGGY_VERSION = "4.2.7.0"
FIXED_VERSION = "4.2.9.0"

WM_MENUCHAR = 0x0120
MF_POPUP = 0x0010
MNC_IGNORE = 0
MNC_CLOSE = 1

# Files' window takes a while to come up on a cold start, and answering
# WM_MENUCHAR needs the subclass to be installed, not just the window to exist.
SETTLE_AFTER_LAUNCH = float(os.environ.get("WT_FILES_SETTLE", "8"))

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = wintypes.LPARAM


def _installed_version() -> str:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-AppxPackage -Name '{PACKAGE}').Version",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return result.stdout.strip()


def _require_one_of(*versions: str) -> str:
    version = _installed_version()
    if version in versions:
        return version
    message = (
        f"Files {version or '(not installed)'} is installed; this test needs one of "
        f"{', '.join(versions)}. Files is an MSIX package, so the two builds cannot be "
        "present at once and installing one removes the other."
    )
    if REQUIRE_BUILDS:
        pytest.fail(message)
    pytest.skip(message)


def _only_on(expected: str) -> str:
    """Gates an assertion to the build it is about.

    Skipping locally is right — only one of the two builds can be installed at a
    time. Skipping *in CI* is not: the workflow installs one build per run and
    selects the matching test with `-k`, so a skip there means the wrong build is
    present and the run would go green having asserted nothing.
    """
    version = _require_one_of(BUGGY_VERSION, FIXED_VERSION)
    if version == expected:
        return version
    message = f"Files {version} is installed; this assertion is about {expected}"
    if REQUIRE_BUILDS:
        pytest.fail(
            f"{message}. The workflow installs one build per run, so this test "
            "should not have been selected against this one."
        )
    pytest.skip(message)


@pytest.fixture(scope="module")
def files_window():
    """One launch for the whole module: nothing here changes the app's state."""
    from target_apps import find_packaged_app, launch_packaged_app

    _require_one_of(BUGGY_VERSION, FIXED_VERSION)
    sweep_processes_verified((PROCESS,), ("Files",))
    proc, win = Window.launch_and_discover(
        launch_packaged_app(find_packaged_app(PACKAGE)),
        timeout=180.0,
        process_names=(PROCESS,),
        window_classes=(WINDOW_CLASS,),
        require_all=True,
    )
    try:
        time.sleep(SETTLE_AFTER_LAUNCH)
        yield win
    finally:
        proc.terminate()
        sweep_processes_verified((PROCESS,), ("Files",))


def _menu_char(win: Window, char: int) -> int:
    """The command half of the window's answer to WM_MENUCHAR.

    The result packs the chosen item index in the low word and the command in
    the high word, so the command is what this returns. Comparing the whole
    LRESULT would tie the assertion to an item index that neither build sets.
    """
    result = _user32.SendMessageW(win.hwnd, WM_MENUCHAR, (MF_POPUP << 16) | char, 0)
    return (result >> 16) & 0xFFFF


def test_enter_is_answered_on_the_fixed_build(files_window):
    version = _only_on(FIXED_VERSION)

    answer = _menu_char(files_window, ord("\r"))
    assert answer == MNC_CLOSE, (
        f"Files {version} answered WM_MENUCHAR for Enter with {answer} rather than "
        f"MNC_CLOSE ({MNC_CLOSE}), so DefWindowProc still handles it and Alt+Enter still "
        "plays the Asterisk sound — upstream #18815 has regressed"
    )


def test_enter_is_unanswered_on_the_build_that_had_the_bug(files_window):
    version = _only_on(BUGGY_VERSION)

    answer = _menu_char(files_window, ord("\r"))
    assert answer == MNC_IGNORE, (
        f"Files {version} answered WM_MENUCHAR for Enter with {answer}, but this build "
        f"predates PR #18815 and should leave it to DefWindowProc ({MNC_IGNORE}). Either "
        "this is not the build the issue was reported against, or the message never "
        "reached the window"
    )


@pytest.mark.parametrize("label,char", [("a", ord("a")), ("Esc", 0x1B)])
def test_other_characters_are_left_to_defwindowproc(files_window, label, char):
    """The control that makes the assertions above mean something.

    The fix only answers `'\\r'`. A build that answered MNC_CLOSE to every
    character would satisfy the fixed-build assertion while breaking every real
    mnemonic, and nothing else here would notice.
    """
    version = _require_one_of(BUGGY_VERSION, FIXED_VERSION)
    answer = _menu_char(files_window, char)
    assert answer == MNC_IGNORE, (
        f"Files {version} answered WM_MENUCHAR for {label!r} with {answer}; only Enter is "
        f"meant to be intercepted, everything else belongs to DefWindowProc ({MNC_IGNORE})"
    )
