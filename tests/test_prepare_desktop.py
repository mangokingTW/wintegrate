"""The pure rule behind prepare_desktop.py, without a desktop."""

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / ".github/actions/setup-windows-gui-test/prepare_desktop.py"
_spec = importlib.util.spec_from_file_location("prepare_desktop", _PATH)
prepare_desktop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prepare_desktop)


@pytest.mark.parametrize(
    ("cls", "title", "expected"),
    [
        ("#32770", "System Properties", "close"),
        ("#32770", "Performance Options", "close"),
        ("#32770", "Save As", None),
        ("ConsoleWindowClass", r"C:\ProgramData\GitHub\hosted-compute-agent\hosted-compute-agent", "hide"),
        ("ConsoleWindowClass", "", "hide"),
        ("Windows.UI.Core.CoreWindow", "Search", "hide"),
        ("Windows.UI.Core.CoreWindow", "Start", "hide"),
        ("Windows.UI.Core.CoreWindow", "Microsoft account", None),
        ("Notepad", "Untitled - Notepad", None),
        ("Qt681QWindowIcon", "System Properties", None),
    ],
)
def test_classify(cls, title, expected):
    assert prepare_desktop.classify(cls, title) == expected
