"""Tests for WindowCensus snapshot diffing and census tracking."""

import re

from wintegrate.diagnostics import WindowCensus, WindowSnapshot
from wintegrate.window import _select_new_window


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


def _snap(hwnd, title="Ready", class_name="ClassA", pid=1, is_visible=True):
    return WindowSnapshot(
        hwnd=hwnd, title=title, class_name=class_name, pid=pid, is_visible=is_visible
    )


def test_select_new_window_ignores_windows_that_were_already_there():
    before = [_snap(100, class_name="Qt681QWindowPopup")]
    after = before + [_snap(200, class_name="Qt681QWindowPopup")]

    found, unready = _select_new_window(before, after, classes={"Qt681QWindowPopup"})
    assert found is not None and found.hwnd == 200
    assert unready is False


def test_select_new_window_skips_invisible_excluded_and_helper_windows():
    before = []
    after = [
        _snap(1, class_name="Qt681QWindowPopup", is_visible=False),
        _snap(2, class_name="Qt681QWindowPopup"),
        _snap(3, class_name="Qt681QWindowPopup"),
    ]
    assert _select_new_window(before, after, classes={"Qt681QWindowPopup"})[0].hwnd == 2
    assert (
        _select_new_window(before, after, excluded={2}, classes={"Qt681QWindowPopup"})[0].hwnd == 3
    )

    # A GDI+ or IME helper window belongs to the process without being its
    # window, and handing one back looks exactly like success.
    helpers = [_snap(4, title="GDI+ Window", class_name="Qt681QWindowPopup")]
    assert _select_new_window(before, helpers, classes={"Qt681QWindowPopup"})[0] is None


def test_select_new_window_reports_a_match_that_is_not_ready_yet():
    before = []
    after = [_snap(1, title="   ", class_name="Qt681QWindowPopup")]

    found, unready = _select_new_window(before, after, classes={"Qt681QWindowPopup"})
    assert found is None
    # The difference between "nothing like that appeared" and "it appeared and
    # was still empty", which is the whole content of a good timeout message.
    assert unready is True


def test_select_new_window_require_all_rejects_a_partial_match():
    before = []
    after = [_snap(1, title="Notepad", class_name="#32770", pid=9)]

    any_of = _select_new_window(before, after, classes={"Notepad"}, title_re=re.compile("Notepad"))
    assert any_of[0] is not None

    # An app's own error dialog runs in the app's process and carries its title,
    # so any-of hands a #32770 back as the app.
    all_of = _select_new_window(
        before, after, classes={"Notepad"}, title_re=re.compile("Notepad"), require_all=True
    )
    assert all_of[0] is None
