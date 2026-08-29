"""Tests for WindowCensus snapshot diffing and census tracking."""

from wintegrate.diagnostics import WindowCensus, WindowSnapshot, CensusDiff


def test_census_diff():
    before = [
        WindowSnapshot(hwnd=100, title="Window A", class_name="ClassA", pid=10, is_visible=True),
        WindowSnapshot(hwnd=101, title="Window B", class_name="ClassB", pid=11, is_visible=True),
    ]
    after = [
        WindowSnapshot(hwnd=100, title="Window A", class_name="ClassA", pid=10, is_visible=True),
        WindowSnapshot(hwnd=102, title="Window C", class_name="ClassC", pid=12, is_visible=True),
    ]

    diff = WindowCensus.diff(before, after)
    assert len(diff.added) == 1
    assert diff.added[0].hwnd == 102
    assert diff.added[0].title == "Window C"

    assert len(diff.removed) == 1
    assert diff.removed[0].hwnd == 101
    assert diff.removed[0].title == "Window B"

    assert len(diff.persisted) == 1
    assert diff.persisted[0].hwnd == 100
