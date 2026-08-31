"""WinMerge (Win32 / MFC), the case where UIA is not enough.

WinMerge's editor panes are `Afx:<module base address>:8` with no UIA patterns at
all and no response to `WM_GETTEXT`, so `get_value()` returns `''`. Reading the
merged output file would pass while the diff highlighting was completely broken —
and highlighting is what a visual diff tool is *for*. So one test counts coloured
pixel bands, which is the only evidence available that the diff was rendered.

See `docs/pitfalls.md`, "Some controls expose nothing at all, and pixels are the
only evidence", for why this stays in a test rather than becoming a library API.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from target_apps import find_executable
from tests_support_pixels import count_colour_bands
from waits import settled

from wintegrate import Window
from wintegrate.apps import sweep_processes_verified

pytestmark = [
    pytest.mark.target_app,
    pytest.mark.skipif(sys.platform != "win32", reason="drives a live Win32 application"),
]

WINMERGE_CANDIDATES = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "WinMerge" / "WinMergeU.exe",
    Path(r"C:\Program Files\WinMerge\WinMergeU.exe"),
    Path(r"C:\Program Files (x86)\WinMerge\WinMergeU.exe"),
)

# Sampled from a rendered diff on Windows 11: WinMerge's "changed line" fill.
DIFF_HIGHLIGHT_RGB = (239, 203, 5)
# Measured: 0, 5 and 10 all give the right band count; 20 and 30 start counting
# the selection highlight as a second band. The fill is flat enough that a small
# tolerance is only there to absorb capture rounding.
COLOUR_TOLERANCE = 10
# Changed lines have to be non-adjacent. Bands are *runs* of consecutive rows, so
# two changed lines that touch render as one continuous band — measured: three
# consecutive changes counted 2 bands across 47 highlighted rows, all three lines
# highlighted, merely not separable. Every fourth line keeps them apart.
CHANGE_STRIDE = 4
BASE_LINES = [f"line {n:02d}" for n in range(16)]


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def winmerge():
    return find_executable("WinMerge", WINMERGE_CANDIDATES)


def _launch(exe: Path, left: Path, right: Path) -> tuple[subprocess.Popen, Window]:
    """WinMerge comparing two files, with the user's own settings kept out of it.

    `/noprefs` is what makes this reproducible: without it WinMerge restores
    whatever the last human left behind, including which panes are shown.
    """
    sweep_processes_verified(("WinMergeU.exe",), ("WinMerge",))
    return Window.launch_and_discover(
        [str(exe), "/noprefs", "/maximize", str(left), str(right)],
        timeout=90.0,
        process_names=("WinMergeU.exe",),
        window_classes=("WinMergeWindowClassW",),
        require_all=True,
    )


@pytest.fixture
def compare(winmerge, tmp_path):
    """Opens WinMerge on two files differing in `changed_lines` places."""
    procs: list[subprocess.Popen] = []

    def _open(changed_lines: int):
        left, right = tmp_path / "left.txt", tmp_path / "right.txt"
        _write(left, BASE_LINES)
        modified = list(BASE_LINES)
        for i in range(changed_lines):
            modified[i * CHANGE_STRIDE] = f"CHANGED {i:02d}"
        _write(right, modified)
        proc, win = _launch(winmerge, left, right)
        procs.append(proc)
        return win

    try:
        yield _open
    finally:
        for proc in procs:
            proc.terminate()
        sweep_processes_verified(("WinMergeU.exe",), ("WinMerge",))


def test_window_is_discovered_by_class_and_process(compare):
    """Both identities must describe the same window, so a dialog cannot pass."""
    win = compare(1)
    assert win.class_name == "WinMergeWindowClassW"
    assert win.is_visible


def test_named_toolbar_buttons_are_reachable(compare):
    """WinMerge's toolbar names are its tooltips, and they are stable identifiers.

    Substring matching on purpose: the names carry their keyboard shortcut
    (`Refresh (F5)`), and the shortcut has changed between releases.
    """
    win = compare(1)
    buttons = win.re_resolve_element().find_all(control_type_id=50000)
    names = [b.name for b in buttons if b.name]
    assert names, "no named buttons at all — the toolbar did not render"
    for wanted in ("Refresh", "Next"):
        assert any(wanted in n for n in names), f"no button whose name contains {wanted!r}"


def test_editor_panes_expose_nothing(compare):
    """A characterisation test: this is *why* the pixel test below exists.

    If a future WinMerge starts exposing its editor content, this test fails and
    the pixel approach can be replaced by something better. A green test here is
    not good news; it is a record of a limitation that still holds.
    """
    win = compare(1)
    panes = [
        e
        for e in win.re_resolve_element().find_all(control_type_id=50033)
        if e.class_name.startswith("Afx:")
    ]
    assert panes, "no Afx: panes found — WinMerge's layout changed, revisit this module"
    assert all(p.supported_patterns() == [] for p in panes), (
        "an Afx: pane now supports patterns; UIA may be able to read the diff directly"
    )


def _band_count(win: Window) -> int:
    from wintegrate.diagnostics import capture_window_image

    image = capture_window_image(win.hwnd)
    assert image.getbbox() is not None, "captured a blank image — nothing was rendered"
    return count_colour_bands(image, DIFF_HIGHLIGHT_RGB, COLOUR_TOLERANCE)


@pytest.mark.parametrize("changed_lines", [1, 2, 3])
def test_highlight_band_count_equals_difference_count(compare, changed_lines):
    """The diff is *rendered*, not merely computed.

    Counting bands rather than comparing against a reference image: a baseline
    screenshot has to be regenerated for a font update, a DPI change, a theme
    change or different anti-aliasing. "How many runs of consecutive rows contain
    this colour" is immune to all four.

    Polled rather than captured once. WinMerge's window is discoverable about
    100ms after launch and the highlighting lands later; capturing immediately
    counted 0 bands for three changed lines, while the same comparison measured
    from a settled window counted 3. `settled` returns the last reading it saw, so
    a genuinely wrong count still reaches the assertion message.
    """
    pytest.importorskip("PIL", reason="pixel verification needs Pillow (extras: video)")
    win = compare(changed_lines)
    bands = settled(lambda: _band_count(win), lambda n: n == changed_lines, timeout=20.0)
    assert bands == changed_lines, (
        f"{changed_lines} changed line(s) should render as {changed_lines} highlight "
        f"band(s), counted {bands}"
    )


def test_identical_files_show_no_highlight(compare):
    """The control case, and the one that cannot be waited for positively.

    Zero bands is also what "has not rendered yet" looks like, so unlike the test
    above there is no state to poll *towards*. The gate here is stability — the
    highlighted-row set unchanged across several polls spanning at least a second
    — plus the fact that the parametrized test above proves highlighting renders
    at all in this environment. Stated rather than hidden: if WinMerge ever
    delayed its rendering past this window, this test would pass for the wrong
    reason.
    """
    pytest.importorskip("PIL", reason="pixel verification needs Pillow (extras: video)")
    win = compare(0)
    readings = []
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        readings.append(_band_count(win))
        if len(readings) >= 4 and len(set(readings[-4:])) == 1:
            break
        time.sleep(0.35)
    assert len(readings) >= 4, f"never got a stable reading, saw {readings}"
    assert readings[-1] == 0, f"identical files should highlight nothing, counted {readings[-1]}"
