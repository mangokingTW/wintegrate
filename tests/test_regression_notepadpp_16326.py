"""A real upstream bug, reproduced against the build that had it.

Notepad++ #16326: `Ctrl+Shift+D` inserted an invisible `EOT` (0x04) control
character at the caret. Scintilla maps `Ctrl+Shift+<letter>` to the corresponding
C0 control character, and Notepad++ has a setting that suppresses it —
"Prevent control characters input" — which the portable package shipped *off*
because the `npcNoInputC0` attribute was missing from its `config.xml`, and a
missing boolean defaults to false.

Fixed by commit 38ee65fd (2025-03-28), one attribute:

    - <GUIConfig name="ScintillaPrimaryView" ... eolShow="hide" borderWidth="2" ...
    + <GUIConfig name="ScintillaPrimaryView" ... eolShow="hide" npcNoInputC0="yes" borderWidth="2" ...

`v8.7.9` is behind that commit; `v8.8` contains it.

Why this bug is worth a test rather than a paragraph: **the failure is invisible**.
The inserted character does not render, the caret does not appear to move, and
the title bar's modified marker looks the same as it would after any edit. A
human running the reproduction steps sees nothing at all. `SCI_GETLENGTH` answers
in one call:

    8.8    typed 'abc' -> length 3 -> Ctrl+Shift+D -> length 3
    8.7.9  typed 'abc' -> length 3 -> Ctrl+Shift+D -> length 4, value 'abc\x04'

Both builds are the portable arm64 package, extracted side by side. Portable
matters twice over: the fix *is* in the portable package's config, and
`doLocalConf.xml` keeps each build's settings in its own directory, so the two
versions cannot contaminate each other.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from waits import settled

from wintegrate import ScintillaView, Window
from wintegrate.apps import sweep_processes_verified
from wintegrate.interop import (
    get_foreground_window,
    get_window_class,
    get_window_title,
    send_char_input,
    send_keys,
)

# Not part of the release gate. This is a case study: it downloads two old builds
# and proves the library can catch a real upstream bug, which is a different job
# from checking that the library still works. Run it from the
# `upstream-bug-demo` workflow (workflow_dispatch), or locally once the two
# builds are extracted.
#
# `target_app` is kept as well so the main matrix jobs, which run
# `-m "not target_app"`, keep excluding it.
pytestmark = [
    pytest.mark.upstream_bug,
    pytest.mark.target_app,
    pytest.mark.skipif(sys.platform != "win32", reason="drives a live Win32 editor"),
]

# Absent builds skip, which is right for a laptop. The demo workflow sets this so
# that a fetch step which silently produced nothing cannot leave a green run.
REQUIRE_BUILDS = os.environ.get("WINTEGRATE_REQUIRE_UPSTREAM_BUILDS") == "1"

# Where the two portable builds are extracted. Set up by the CI step, or by hand:
#   Invoke-WebRequest .../v8.7.9/npp.8.7.9.portable.arm64.zip -OutFile a.zip
#   Expand-Archive a.zip -DestinationPath C:\npp\8.7.9
PORTABLE_ROOT = Path(r"C:\npp")
BUGGY_VERSION = "8.7.9"
FIXED_VERSION = "8.8"

EOT = "\x04"
SEED_TEXT = "abc"


def _portable(version: str) -> Path:
    exe = PORTABLE_ROOT / version / "notepad++.exe"
    if not exe.exists():
        message = (
            f"portable Notepad++ {version} is not at {exe}. This test compares a build "
            "that has upstream bug #16326 against one that does not, so both have to be "
            "present; see the module docstring for the two download URLs."
        )
        if REQUIRE_BUILDS:
            pytest.fail(message)
        pytest.skip(message)
    return exe


def _typed_document(exe: Path):
    """A portable Notepad++ with SEED_TEXT typed into a fresh document."""
    sweep_processes_verified(("notepad++.exe",), ("Notepad++",))
    proc = subprocess.Popen([str(exe), "-nosession", "-multiInst", "-noPlugin"])
    win = Window.find(class_name="Notepad++", timeout=60.0)

    deadline = time.monotonic() + 15.0
    while get_foreground_window() != win.hwnd and time.monotonic() < deadline:
        win.set_foreground(verify=False)
        time.sleep(0.25)
    foreground = get_foreground_window()
    assert foreground == win.hwnd, (
        f"Notepad++ never became the foreground window; <class="
        f"{get_window_class(foreground)!r} title={get_window_title(foreground)!r}> has it, "
        "so the keystrokes below would have gone there"
    )

    edit = win.find_text_input(timeout=30.0)
    edit.set_focus()
    for character in SEED_TEXT:
        send_char_input(character)
    typed = settled(edit.get_value, lambda v: v == SEED_TEXT, timeout=10.0)
    assert typed == SEED_TEXT, f"seeding the document produced {typed!r}, expected {SEED_TEXT!r}"
    return proc, win, edit


def _length_after_ctrl_shift_d(exe: Path) -> tuple[int, int, str]:
    """(length before, length after, document after) around one Ctrl+Shift+D."""
    proc, _win, edit = _typed_document(exe)
    try:
        view = ScintillaView.from_element(edit)
        before = view.length
        send_keys("^+d")
        # Polled, not slept: nothing observable is *supposed* to change here, so
        # this waits out the window in which a character could still arrive rather
        # than concluding early. settled returns the last reading either way.
        after = settled(lambda: view.length, lambda n: n != before, timeout=3.0)
        return before, after, edit.get_value()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        sweep_processes_verified(("notepad++.exe",), ("Notepad++",))


def test_fixed_build_does_not_insert_a_control_character():
    """8.8 ships npcNoInputC0="yes", so Ctrl+Shift+D does nothing."""
    before, after, document = _length_after_ctrl_shift_d(_portable(FIXED_VERSION))
    assert after == before, (
        f"Notepad++ {FIXED_VERSION} grew the document from {before} to {after} bytes on "
        f"Ctrl+Shift+D and now holds {document!r} — upstream #16326 has regressed"
    )
    assert EOT not in document, f"an EOT reached the document: {document!r}"


def test_buggy_build_inserts_an_invisible_eot():
    """8.7.9 reproduces #16326, and this records exactly how it looks.

    Asserting that the *old* build is still broken is not redundant. It is what
    makes the test above meaningful: without it, a version pin that quietly
    started resolving to a fixed build would turn this pair green while testing
    nothing.
    """
    before, after, document = _length_after_ctrl_shift_d(_portable(BUGGY_VERSION))
    assert after == before + 1, (
        f"Notepad++ {BUGGY_VERSION} was expected to reproduce #16326 by growing the "
        f"document by one byte, but went from {before} to {after} — is this really the "
        "pre-38ee65fd build?"
    )
    assert document == SEED_TEXT + EOT, f"expected the seed text plus one EOT, got {document!r}"


def test_the_difference_is_invisible_on_screen():
    """The reason this needs a byte count rather than a screenshot.

    Both builds render identically after Ctrl+Shift+D — EOT has no glyph unless
    "Show Control Characters" is on, which it is not by default. So the pixel
    technique that works for WinMerge's diff highlighting cannot see this at all,
    and neither can a human following the reproduction steps.
    """
    pytest.importorskip("PIL", reason="needs Pillow (extras: video)")
    from tests_support_pixels import rows_containing_colour

    from wintegrate.diagnostics import capture_window_image

    shots = {}
    for version in (FIXED_VERSION, BUGGY_VERSION):
        proc, win, edit = _typed_document(_portable(version))
        try:
            send_keys("^+d")
            time.sleep(1.0)
            image = capture_window_image(win.hwnd)
            assert image.getbbox() is not None, f"{version}: captured a blank window"
            # A cheap, stable summary of "what is drawn in the text area": how many
            # rows carry the default text colour. Comparing whole images would fail
            # on the title bar, which names the version.
            shots[version] = len(rows_containing_colour(image, (0, 0, 0), 40))
            documents = edit.get_value()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            sweep_processes_verified(("notepad++.exe",), ("Notepad++",))
        assert documents is not None

    assert shots[FIXED_VERSION] == shots[BUGGY_VERSION], (
        f"the two builds drew a different number of text rows "
        f"({shots}) — if the EOT has become visible, this test's premise is gone "
        "and the byte-count assertions above are no longer the only way to see it"
    )
