"""Scintilla queries, driven against a real Notepad++ when one is installed.

Skipped rather than mocked. The whole point of this module is what a live
Scintilla answers to messages sent from another process, and a mock would assert
my understanding of Scintilla instead of Scintilla's behaviour.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from target_apps import assert_version, find_executable, installed_file_version
from waits import settled

from wintegrate import EolMode, ScintillaView, Window, is_scintilla
from wintegrate.apps import sweep_processes_verified
from wintegrate.diagnostics import WindowCensus
from wintegrate.interop import (
    get_foreground_window,
    get_window_class,
    get_window_title,
    send_char_input,
    send_keys,
)

pytestmark = [
    pytest.mark.target_app,
    pytest.mark.skipif(sys.platform != "win32", reason="drives a live Win32 editor"),
]

# Chocolatey and the official installer disagree about the location, and the 32-bit
# build lands in Program Files (x86) even on a 64-bit machine.
NPP_CANDIDATES = (
    Path(r"C:\Program Files\Notepad++\notepad++.exe"),
    Path(r"C:\Program Files (x86)\Notepad++\notepad++.exe"),
)

# The build every assertion below was measured against. Chocolatey package
# `notepadplusplus 8.9.8` installs this file version.
VERIFIED_VERSION = "8.9.8.0"

SCROLLBAR_PART_IDS = frozenset(
    {"UpButton", "DownButton", "UpPageButton", "DownPageButton", "LeftButton", "RightButton"}
)

LINES = ("alpha", "beta", "gamma")


def _describe_foreground() -> str:
    hwnd = get_foreground_window()
    if not hwnd:
        return "no window"
    return f"<hwnd={hwnd:#x} class={get_window_class(hwnd)!r} title={get_window_title(hwnd)!r}>"


def _foreground_settled(win: Window, timeout: float = 15.0) -> bool:
    """Whether `win` holds the foreground, retrying the request.

    `Window.foreground()` is entered with verify=False across this suite because
    the request can lose a race with whatever else the desktop is doing. Here it
    has to be verified: a window in front of Notepad++ makes every keystroke land
    somewhere else, and nothing else in the failure says so.
    """
    deadline = time.monotonic() + timeout
    while True:
        if get_foreground_window() == win.hwnd:
            return True
        if time.monotonic() >= deadline:
            return False
        win.set_foreground(verify=False)
        time.sleep(0.25)


# "alpha\r\nbeta\r\ngamma\r\n" — the byte length the assertions below check against.
EXPECTED_BYTES = sum(len(line) + 2 for line in LINES)
# What get_value() returns for that document. Scintilla reports CRLF line ends as
# "\r\n"; the fixture waits for this exact string rather than for a line count.
EXPECTED_TEXT = "".join(f"{line}\r\n" for line in LINES)


@pytest.fixture
def editor():
    """A Notepad++ window with three known lines typed into it.

    -nosession stops it restoring the previous document, -multiInst stops the
    singleton behaviour from handing back somebody else's window, and -noPlugin
    keeps third-party plugins out of the measurement.
    """
    npp = find_executable("Notepad++", NPP_CANDIDATES)
    sweep_processes_verified(("notepad++.exe",), ("Notepad++",))
    proc = subprocess.Popen([str(npp), "-nosession", "-multiInst", "-noPlugin"])
    try:
        win = Window.find(class_name="Notepad++", timeout=60.0)
        with win.foreground(verify=False):
            # Verified, and it says who has the foreground when it fails. Without
            # this the symptom is that nothing typed arrives at all — length 0,
            # get_value() empty, Ctrl+F opening no dialog — with every element
            # still resolving and the window still looking correct. That reads as
            # a broken editor rather than as a window in front of it.
            assert _foreground_settled(win), (
                "Notepad++ never became the foreground window; "
                f"{_describe_foreground()} has it instead, so every keystroke "
                "below would have gone there"
            )
            edit = win.find_text_input(timeout=30.0)
            edit.set_focus()
            settled(lambda: edit.get_value(), lambda v: isinstance(v, str), timeout=5.0)
            for line in LINES:
                for ch in line:
                    send_char_input(ch)
                send_keys("{ENTER}")
            # Wait for the whole text, not just for the newline count. Counting
            # newlines passes while a character inside a line is missing, and the
            # failure then surfaces as a byte length three short of expected in
            # whichever test reads it first.
            typed = settled(edit.get_value, lambda v: v == EXPECTED_TEXT, timeout=10.0)
            assert typed == EXPECTED_TEXT, (
                f"typing produced {typed!r}, expected {EXPECTED_TEXT!r} — a "
                "keystroke was dropped, so every assertion below is measuring the "
                "wrong document"
            )
            yield win, edit
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_find_text_input_reaches_scintilla(editor):
    """The gap that made this work necessary.

    Scintilla appears in the UIA tree as a Pane supporting no patterns at all, so
    the ladder's control-type entries never matched it and find_text_input failed
    on Notepad++ with the editor plainly on screen. Identification was missing,
    not capability.
    """
    _win, edit = editor
    assert is_scintilla(edit.class_name), edit.describe()


def test_text_comes_through_wm_gettext(editor):
    """Reading needs no Scintilla message at all.

    get_value() falls through to WM_GETTEXT, which USER32 marshals across the
    process boundary — unlike the SCI_* messages, which take a pointer into the
    caller's address space.
    """
    _win, edit = editor
    text = edit.get_value()
    for line in LINES:
        assert line in text
    assert "\r\n" in text, "Scintilla ends lines with CRLF here; see eol_mode"


def test_line_count_is_asked_rather_than_counted(editor):
    """The reason this module exists.

    Counting newlines in the text is the obvious alternative and is ambiguous:
    CRLF endings, a trailing newline, and controls that report bare CR all change
    the answer. Scintilla knows, so ask it.
    """
    _win, edit = editor
    sci = ScintillaView.from_element(edit)
    assert sci.line_count == len(LINES) + 1, "three lines plus the trailing empty one"


def test_length_is_bytes_not_characters(editor):
    """A distinction that silently breaks comparisons against len(text)."""
    _win, edit = editor
    sci = ScintillaView.from_element(edit)
    assert sci.codepage == 65001, "expected UTF-8"
    assert sci.length == EXPECTED_BYTES

    for ch in "中文":
        send_char_input(ch)
    settled(lambda: sci.length, lambda n: n > EXPECTED_BYTES, timeout=5.0)
    # Two CJK characters: six UTF-8 bytes, two UTF-16 characters.
    assert sci.length == EXPECTED_BYTES + 6
    assert len(edit.get_value()) == EXPECTED_BYTES + 2


def test_selection_is_reportable(editor):
    """What WM_GETTEXT cannot answer, and a find/replace test needs."""
    _win, edit = editor
    sci = ScintillaView.from_element(edit)
    assert not sci.has_selection

    send_keys("^{HOME}{DOWN}+{END}")  # select the second line
    start, end = settled(lambda: sci.selection, lambda s: s[0] != s[1], timeout=5.0)
    assert (start, end) == (len(LINES[0]) + 2, len(LINES[0]) + 2 + len(LINES[1]))
    assert sci.has_selection
    assert sci.line_of_position(start) == 1


def test_line_geometry_agrees_with_itself(editor):
    """Cross-checks, because a wrong message constant returns a plausible number.

    Each of these could pass alone with a mismatched constant; together they
    cannot. That is the only defence available when the values come from another
    process and there is nothing to compare them to but each other.
    """
    _win, edit = editor
    sci = ScintillaView.from_element(edit)
    for i, line in enumerate(LINES):
        assert sci.line_length(i) == len(line) + 2, f"line {i} plus CRLF"
        assert sci.position_of_line(i) == sum(len(x) + 2 for x in LINES[:i])
        assert sci.line_of_position(sci.position_of_line(i)) == i


def test_document_state_is_visible(editor):
    _win, edit = editor
    sci = ScintillaView.from_element(edit)
    assert sci.is_modified is True, "text was typed and not saved"
    assert sci.eol_mode is EolMode.CRLF
    assert sci.tab_width > 0
    assert sci.char_at(0) == ord(LINES[0][0])
    assert repr(sci).startswith("<ScintillaView")


def test_from_element_refuses_a_non_scintilla_control(editor):
    """Handing back an inert view would surface as a wrong answer elsewhere."""
    win, _edit = editor
    root = win.re_resolve_element()
    with pytest.raises(ValueError, match="not a Scintilla control"):
        ScintillaView.from_element(root)


def test_toolbar_buttons_expose_no_automation_id(editor):
    """Characterisation: an MFC toolbar offers nothing but its translated names.

    Every button in Notepad++'s main window has an empty `automation_id`, so the
    only handle on them is `name` — and the names are localized (`新增(N)` on a
    zh-TW machine, `New` on an English one). That rules out addressing them
    portably, which is why the button test below uses a dialog instead.

    Position is not a fallback either: sorting by bounding rectangle puts two 0x0
    phantom buttons first, and clicking the topmost-leftmost one did nothing.
    """
    win, _ = editor
    buttons = win.re_resolve_element().find_all(control_type_id=50000)
    assert len(buttons) > 10, f"only {len(buttons)} buttons — is the toolbar hidden?"
    # A scroll bar's arrow and paging buttons are Buttons too, and those *do* carry
    # ids — but they are UIA's own standard names for scroll bar parts, not
    # anything Notepad++ named. Excluded so the assertion is about the toolbar.
    with_id = [b for b in buttons if b.automation_id and b.automation_id not in SCROLLBAR_PART_IDS]
    assert not with_id, (
        "some Notepad++ toolbar buttons now carry an automation_id "
        f"({[b.automation_id for b in with_id][:5]}) — they could be addressed directly"
    )


def test_dialog_buttons_carry_their_win32_control_id(editor):
    """A button click with an observable result, addressed language-independently.

    Notepad++'s Find dialog is a plain Win32 `#32770`, and UIA reports each
    control's *control ID* as its automation id — `1` for IDOK, `2` for IDCANCEL,
    and Notepad++'s own ids for the rest. Numbers do not get translated, so this
    is the one route to a button in this application that survives a locale
    change.

    The dialog is a separate top-level window, not a descendant of the main one,
    so it has to be found on the desktop rather than under the editor.
    """
    win, edit = editor
    edit.set_focus()
    send_keys("^f")

    def find_dialog():
        for snap in WindowCensus.capture():
            if snap.is_visible and snap.class_name == "#32770" and snap.pid == win.pid:
                return Window(snap.hwnd, snap.pid)
        return None

    dialog = settled(find_dialog, lambda d: d is not None, timeout=15.0)
    assert dialog is not None, "Ctrl+F did not open a #32770 dialog"

    buttons = {
        b.automation_id: b for b in dialog.re_resolve_element().find_all(control_type_id=50000)
    }
    assert "1" in buttons, f"no IDOK button; ids present: {sorted(buttons)}"
    cancel = buttons.get("2")
    assert cancel is not None, f"no IDCANCEL button; ids present: {sorted(buttons)}"

    cancel.click()
    gone = settled(find_dialog, lambda d: d is None, timeout=15.0)
    assert gone is None, "clicking IDCANCEL left the Find dialog open"


def test_notepadpp_is_the_verified_version():
    """Pins the build. See `assert_version` for why a release gate needs this."""
    npp = find_executable("Notepad++", NPP_CANDIDATES)
    assert_version("Notepad++", installed_file_version(npp, "Notepad++"), VERIFIED_VERSION)
