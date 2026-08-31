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


LAUNCH_DESKTOP = [
    # The app's own error dialog: right process, wrong window. This is what
    # Files 4.2.9 puts up when the .NET runtime it needs is missing.
    WindowSnapshot(hwnd=11, title="Files.exe", class_name="#32770", pid=900, is_visible=True),
    WindowSnapshot(
        hwnd=12, title="Files", class_name="WinUIDesktopWin32WindowClass", pid=900, is_visible=True
    ),
]


@pytest.fixture
def fake_launch(monkeypatch):
    """A launch where both the error dialog and the real window belong to the app."""
    from unittest.mock import MagicMock

    import wintegrate.window as window_mod

    captures = iter([[], LAUNCH_DESKTOP, LAUNCH_DESKTOP, LAUNCH_DESKTOP])
    monkeypatch.setattr(
        WindowCensus, "capture", staticmethod(lambda: next(captures, LAUNCH_DESKTOP))
    )
    monkeypatch.setattr(window_mod.subprocess, "Popen", lambda *a, **k: MagicMock())
    monkeypatch.setattr(window_mod, "attach_to_input_desktop", lambda: None)
    monkeypatch.setattr(window_mod, "get_process_image_name", lambda pid: "files.exe")
    # is_ready() rejects an untitled match; both fakes have titles, so let it pass.
    monkeypatch.setattr(window_mod, "_describe_desktop_now", lambda before, limit=12: "")


def test_launch_process_name_alone_can_match_the_apps_error_dialog(fake_launch):
    """The permissive default: process name matches, so a #32770 is accepted."""
    # hwnd 11 is the #32770 in LAUNCH_DESKTOP. Asserting on win.class_name instead
    # would query the live OS for a handle that only exists in the fixture.
    _, win = Window.launch_and_discover(["files.exe"], timeout=0.5, process_names=("files.exe",))
    assert win.hwnd == 11


def test_launch_require_all_rejects_the_error_dialog(fake_launch):
    """With require_all, class and process must describe the same window."""
    _, win = Window.launch_and_discover(
        ["files.exe"],
        timeout=0.5,
        process_names=("files.exe",),
        window_classes=("WinUIDesktopWin32WindowClass",),
        require_all=True,
    )
    assert win.hwnd == 12


def test_launch_require_all_unsatisfiable_raises(fake_launch):
    """An unmatchable combination times out rather than returning a wrong window."""
    with pytest.raises(WindowDiscoveryTimeoutError):
        Window.launch_and_discover(
            ["files.exe"],
            timeout=0.5,
            process_names=("files.exe",),
            window_classes=("NoSuchWindowClass",),
            require_all=True,
        )


def test_launch_default_stays_or(fake_launch):
    """Without require_all a wrong class is ignored, which is the documented default."""
    _, win = Window.launch_and_discover(
        ["files.exe"],
        timeout=0.5,
        process_names=("files.exe",),
        window_classes=("NoSuchWindowClass",),
    )
    assert win.hwnd == 11


def test_embedded_browser_document_is_not_a_text_input():
    """A WebView2's Chromium root is a UIA Document, so it matches the ladder's
    Document rung and outranks the app's real Edit controls below it."""
    from types import SimpleNamespace

    from wintegrate.window import _is_embedded_browser_document

    assert _is_embedded_browser_document(SimpleNamespace(automation_id="RootWebArea"))
    assert not _is_embedded_browser_document(SimpleNamespace(automation_id="CurrentPathSet"))
    assert not _is_embedded_browser_document(SimpleNamespace(automation_id=""))


def test_embedded_browser_check_survives_a_dead_element():
    """A stale element raises on property access; that must not abort the ladder."""

    class Dead:
        @property
        def automation_id(self):
            raise OSError("element is gone")

    from wintegrate.window import _is_embedded_browser_document

    assert not _is_embedded_browser_document(Dead())


def test_element_find_descendant_no_criteria_rejected():
    from unittest.mock import MagicMock

    from wintegrate.element import UiaElement

    elem = UiaElement(MagicMock())
    with pytest.raises(ValueError, match="At least one search criterion"):
        elem.find_descendant()
