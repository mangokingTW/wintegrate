"""Regression tests for Window.find criteria semantics.

Criteria must be combined with AND. The earlier implementation returned on the
first criterion that matched, so `find(title_pattern=..., class_name="#32770")`
handed back any unrelated dialog on the desktop — a wrong window returned
silently, with no exception for the caller to catch.
"""

from __future__ import annotations

import pytest

from wintegrate import Window
from wintegrate.diagnostics import WindowCensus, WindowSnapshot
from wintegrate.exceptions import WindowDiscoveryTimeoutError

DESKTOP = [
    WindowSnapshot(
        hwnd=1, title="Some Other Dialog", class_name="#32770", pid=100, is_visible=True
    ),
    WindowSnapshot(hwnd=2, title="Settings", class_name="CabinetWClass", pid=200, is_visible=True),
    WindowSnapshot(hwnd=3, title="Settings", class_name="#32770", pid=300, is_visible=True),
    WindowSnapshot(hwnd=4, title="Hidden Settings", class_name="#32770", pid=400, is_visible=False),
]


@pytest.fixture
def fake_desktop(monkeypatch):
    monkeypatch.setattr(WindowCensus, "capture", staticmethod(lambda: list(DESKTOP)))


def test_criteria_are_anded(fake_desktop):
    """Title and class must both match — not either one."""
    win = Window.find(title_pattern="Settings", class_name="#32770", timeout=0.3)
    assert win.hwnd == 3


def test_single_criterion_still_matches_first(fake_desktop):
    assert Window.find(class_name="#32770", timeout=0.3).hwnd == 1
    assert Window.find(title_exact="Settings", timeout=0.3).hwnd == 2


def test_pid_narrows_search(fake_desktop):
    """pid targets one process even when title and class are ambiguous."""
    assert Window.find(class_name="#32770", pid=300, timeout=0.3).hwnd == 3


def test_invisible_windows_are_skipped(fake_desktop):
    with pytest.raises(WindowDiscoveryTimeoutError):
        Window.find(title_exact="Hidden Settings", timeout=0.3)


def test_unsatisfiable_combination_raises(fake_desktop):
    """No window matches every criterion, so the caller gets an error, not a wrong window."""
    with pytest.raises(WindowDiscoveryTimeoutError):
        Window.find(title_exact="Settings", class_name="Notepad", timeout=0.3)


def test_no_criteria_rejected():
    with pytest.raises(ValueError):
        Window.find(timeout=0.3)


def test_element_find_descendant_no_criteria_rejected():
    from wintegrate.element import UiaElement

    elem = UiaElement(None)
    with pytest.raises(ValueError, match="At least one search criterion"):
        elem.find_descendant()

