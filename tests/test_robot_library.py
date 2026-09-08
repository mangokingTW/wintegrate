"""The Robot Framework keyword library: surface and wiring, without a desktop.

The keywords themselves are one-line calls into the Python API, which has its
own tests. What can go wrong here is the wiring: a keyword name that Robot cannot
see, an application name that resolves to the wrong thing, a suite file that
names a keyword this library does not have. `robot --dryrun` checks the last one
without launching anything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("robot", reason="pip install wintegrate[robot]")

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="wintegrate imports Win32")

from wintegrate import CALCULATOR, NOTEPAD  # noqa: E402
from wintegrate.robot.library import WintegrateLibrary, resolve_app  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "robot" / "notepad.robot"


def test_known_names_resolve_to_their_specs():
    assert resolve_app("notepad") is NOTEPAD
    assert resolve_app(" Calculator ") is CALCULATOR


def test_anything_else_is_a_command_line():
    assert resolve_app("wt.exe -w new") == ["wt.exe", "-w", "new"]


def test_the_library_is_its_own_listener():
    lib = WintegrateLibrary(record_video=False)
    assert lib.ROBOT_LISTENER_API_VERSION == 3
    assert lib.ROBOT_LIBRARY_LISTENER is lib


def test_every_keyword_the_example_uses_exists():
    """`--dryrun` resolves every keyword and argument count without running any.

    A suite that names a keyword this library does not have fails here, on a
    machine with no Notepad, rather than on the runner after the session opened.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "robot",
            "--dryrun",
            "--output",
            "NONE",
            "--report",
            "NONE",
            "--log",
            "NONE",
            str(EXAMPLE),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
