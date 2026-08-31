"""Scintilla queries, driven against a real Notepad++ when one is installed.

Skipped rather than mocked. The whole point of this module is what a live
Scintilla answers to messages sent from another process, and a mock would assert
my understanding of Scintilla instead of Scintilla's behaviour.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from target_apps import find_executable
from waits import settled

from wintegrate import EolMode, ScintillaView, Window, is_scintilla
from wintegrate.apps import sweep_processes_verified
from wintegrate.interop import send_char_input, send_keys

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

LINES = ("alpha", "beta", "gamma")
# "alpha\r\nbeta\r\ngamma\r\n" — the byte length the assertions below check against.
EXPECTED_BYTES = sum(len(line) + 2 for line in LINES)


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
            edit = win.find_text_input(timeout=30.0)
            edit.set_focus()
            settled(lambda: edit.get_value(), lambda v: isinstance(v, str), timeout=5.0)
            for line in LINES:
                for ch in line:
                    send_char_input(ch)
                send_keys("{ENTER}")
            # Wait for the last line to arrive rather than sleeping: typing returns
            # once SendInput accepted the events, not once Scintilla processed them.
            settled(edit.get_value, lambda v: v.count("\n") >= len(LINES), timeout=5.0)
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
