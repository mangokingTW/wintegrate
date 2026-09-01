"""The recorder draws the pointer and its clicks into the frame itself.

A BitBlt of the desktop contains no cursor, so before this a recording of a run
showed windows changing with nothing to say where the pointer was. Adding an
on-screen visualiser instead does not work in every case: an activated topmost
window draws above an overlay that stays unactivated, and the measured difference
was total -- the same window and the same click point, with only WS_EX_TOPMOST
changing, went from 2032 ring pixels to 0.

`test_a_topmost_window_cannot_cover_the_markers` is the reason this module exists.
The rest is the machinery it needs.
"""

from __future__ import annotations

import ctypes
import sys
import time

import pytest

from wintegrate.interop import (
    RECT,
    WM_LBUTTONDOWN,
    WM_MBUTTONDOWN,
    WM_RBUTTONDOWN,
    get_cursor_state,
    send_mouse_click,
    user32,
)
from wintegrate.pointer_overlay import (
    BUTTON_COLOURS,
    CLICK_LINGER_SECONDS,
    ClickEvent,
    ClickTracker,
    cursor_overlay_image,
    draw_click_markers,
    draw_cursor,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="uses Win32 GDI and input")

Image = pytest.importorskip("PIL.Image", reason="pointer overlays need Pillow")


def _blank(width=400, height=300, colour=(255, 255, 255)):
    return Image.new("RGB", (width, height), colour)


def _pixels_matching(img, colour, background=(255, 255, 255), tolerance=24, min_strength=0.15):
    """Counts pixels that are a plausible blend of `background` and `colour`.

    A per-channel tolerance around the pure colour does not work: the markers are
    drawn with alpha, so by the time a ring is a third of the way through its
    linger it sits at (255, 110, 138) rather than (255, 45, 85) -- 65 away on one
    channel, which is a real marker that a tolerance tight enough to exclude the
    other two button colours would reject.

    So the blend fraction is recovered from whichever channel has the most room to
    move, and the remaining channels have to agree with it.
    """
    span, index = max((abs(background[i] - colour[i]), i) for i in range(3))
    if span == 0:
        raise ValueError("colour is indistinguishable from the background")

    hits = 0
    for pixel in img.convert("RGB").getdata():
        strength = (background[index] - pixel[index]) / span
        if strength < min_strength or strength > 1.05:
            continue
        if all(
            abs(pixel[c] - (background[c] - strength * (background[c] - colour[c]))) <= tolerance
            for c in range(3)
        ):
            hits += 1
    return hits


# --- click markers: a pure function, so these run anywhere -------------------


def test_a_recent_click_draws_a_ring_and_a_crosshair():
    img = _blank()
    now = time.monotonic()
    draw_click_markers(img, [ClickEvent(now, 200, 150, WM_LBUTTONDOWN)], now=now)

    assert _pixels_matching(img, BUTTON_COLOURS[WM_LBUTTONDOWN]) > 100, (
        "nothing was drawn for a click that just happened"
    )
    # The crosshair marks the exact point, which is the coordinate the caller asked
    # for -- not wherever the cursor happened to come to rest.
    centre = img.convert("RGB").getpixel((200, 150))
    assert centre != (255, 255, 255), f"the click point itself is unmarked ({centre})"


def test_a_click_past_its_linger_draws_nothing():
    img = _blank()
    now = time.monotonic()
    stale = ClickEvent(now - CLICK_LINGER_SECONDS - 0.1, 200, 150, WM_LBUTTONDOWN)
    draw_click_markers(img, [stale], now=now)
    assert img.convert("RGB").getcolors() == [(400 * 300, (255, 255, 255))], (
        "an expired click left marks on the frame"
    )


def test_the_ring_expands_and_fades_with_age():
    now = time.monotonic()
    fresh, old = _blank(), _blank()
    draw_click_markers(fresh, [ClickEvent(now, 200, 150, WM_LBUTTONDOWN)], now=now)
    draw_click_markers(
        old, [ClickEvent(now - CLICK_LINGER_SECONDS * 0.8, 200, 150, WM_LBUTTONDOWN)], now=now
    )

    def ring_radius(img):
        row = [x for x in range(400) if img.convert("RGB").getpixel((x, 150)) != (255, 255, 255)]
        return (max(row) - min(row)) // 2

    assert ring_radius(old) > ring_radius(fresh), (
        "an older click should show a larger ring, so two clicks can be ordered"
    )

    def strongest_mark(img):
        """How far the most saturated ring pixel gets from the white background.
        Counting near-white pixels cannot measure this: an older ring is fainter
        *and* larger, and the two effects cancel."""
        return max(255 - min(px) for px in img.convert("RGB").getdata())

    assert strongest_mark(old) < strongest_mark(fresh), (
        "an older click should be fainter, so a reader can tell which came first"
    )


@pytest.mark.parametrize("button", [WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN])
def test_each_button_has_its_own_colour(button):
    img = _blank()
    now = time.monotonic()
    draw_click_markers(img, [ClickEvent(now, 200, 150, button)], now=now)
    assert _pixels_matching(img, BUTTON_COLOURS[button]) > 100

    others = [c for b, c in BUTTON_COLOURS.items() if b != button]
    for other in others:
        assert _pixels_matching(img, other) < 20, (
            f"a {hex(button)} click drew something that reads as {other}"
        )


def test_markers_near_an_edge_are_clipped_not_dropped():
    """A click in a corner used to raise; the ring's box runs off the frame."""
    img = _blank()
    now = time.monotonic()
    draw_click_markers(img, [ClickEvent(now, 2, 2, WM_LBUTTONDOWN)], now=now)
    assert _pixels_matching(img, BUTTON_COLOURS[WM_LBUTTONDOWN]) > 10


def test_the_origin_offset_moves_the_marker():
    """`all_monitors=True` captures from the virtual-screen origin, which is
    negative when a monitor sits left of the primary one."""
    a, b = _blank(), _blank()
    now = time.monotonic()
    draw_click_markers(a, [ClickEvent(now, 200, 150, WM_LBUTTONDOWN)], now=now)
    draw_click_markers(b, [ClickEvent(now, 300, 150, WM_LBUTTONDOWN)], now=now, origin=(100, 0))
    assert list(a.getdata()) == list(b.getdata()), (
        "a click at 300 with origin 100 should land where a click at 200 does"
    )


# --- the cursor bitmap ------------------------------------------------------


def test_the_cursor_bitmap_has_opaque_and_transparent_pixels():
    state = get_cursor_state()
    if state is None:
        pytest.skip("no cursor is being shown on this desktop")
    img = cursor_overlay_image(state.handle, scale=2)
    alpha = img.split()[-1]
    lo, hi = alpha.getextrema()
    assert lo == 0, "the cursor bitmap is fully opaque, so it would paste as a block"
    assert hi > 0, "the cursor bitmap is fully transparent, so nothing would show"


def test_the_cursor_bitmap_is_cached_per_handle_and_scale():
    state = get_cursor_state()
    if state is None:
        pytest.skip("no cursor is being shown on this desktop")
    first = cursor_overlay_image(state.handle, scale=2)
    assert cursor_overlay_image(state.handle, scale=2) is first
    assert cursor_overlay_image(state.handle, scale=3) is not first


def test_drawing_the_cursor_changes_the_frame_where_the_cursor_is():
    state = get_cursor_state()
    if state is None:
        pytest.skip("no cursor is being shown on this desktop")
    user32.SetCursorPos(200, 150)
    time.sleep(0.2)

    plain = _blank()
    drawn = _blank()
    draw_cursor(drawn)

    assert list(plain.getdata()) != list(drawn.getdata()), "no cursor was drawn"
    # The halo makes it legible on a white background, which a white arrow is not.
    dark = sum(1 for r, g, b in drawn.getdata() if r < 90 and g < 90 and b < 90)
    assert dark > 20, "the cursor was drawn with no dark outline, so it vanishes on white"


def test_capture_screen_image_leaves_the_cursor_out_by_default():
    """Callers diff these images; a pointer wandering in would be a flake."""
    import inspect

    from wintegrate import capture_screen_image

    assert inspect.signature(capture_screen_image).parameters["draw_cursor"].default is False


# --- the click hook ---------------------------------------------------------


class _Tracker:
    """Starts a ClickTracker, failing on a broken fixture and skipping only when the
    host refuses the hook. A setup bug that skips looks exactly like a limitation."""

    def __enter__(self):
        self.tracker = ClickTracker()
        started = self.tracker.start()
        if self.tracker.setup_error is not None:
            raise AssertionError(
                f"the click tracker is broken: "
                f"{type(self.tracker.setup_error).__name__}: {self.tracker.setup_error}"
            )
        if not started:
            pytest.skip(
                f"SetWindowsHookExW(WH_MOUSE_LL) returned NULL "
                f"(GetLastError={ctypes.get_last_error()}); this host does not allow it"
            )
        return self.tracker

    def __exit__(self, *exc):
        self.tracker.stop()


def test_the_tracker_records_a_click_at_the_point_it_was_sent_to():
    x, y = user32.GetSystemMetrics(0) // 3, user32.GetSystemMetrics(1) // 3
    user32.SetCursorPos(5, 5)
    with _Tracker() as tracker:
        time.sleep(0.3)
        send_mouse_click(x, y)
        time.sleep(0.5)
        events = list(tracker.events)

    downs = [e for e in events if e.button == WM_LBUTTONDOWN]
    assert downs, f"the hook saw no button-down; events: {events[:5]}"
    assert abs(downs[-1].x - x) <= 2 and abs(downs[-1].y - y) <= 2, (
        f"the click was recorded at ({downs[-1].x}, {downs[-1].y}), not near ({x}, {y})"
    )


def test_recent_drops_events_older_than_the_linger():
    tracker = ClickTracker()
    now = time.monotonic()
    tracker.events.append(ClickEvent(now, 10, 10, WM_LBUTTONDOWN))
    tracker.events.append(ClickEvent(now - CLICK_LINGER_SECONDS - 1, 20, 20, WM_LBUTTONDOWN))
    recent = tracker.recent()
    assert [e.x for e in recent] == [10]


# --- the point of the whole module -----------------------------------------

GA_ROOT = 2
HWND_TOPMOST = -1
SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010


def test_a_topmost_window_cannot_cover_the_markers():
    """The case that defeats an on-screen visualiser.

    An activated topmost window covers everything an unactivated overlay draws
    inside its rectangle -- measured at 2032 ring pixels down to 0. Drawing after
    the screen grab is immune, and this asserts that rather than assuming it.
    """
    tk = pytest.importorskip("tkinter", reason="needs a GUI toolkit to make a window")
    from wintegrate import capture_screen_image

    root = tk.Tk()
    try:
        root.title("pointer-overlay-test")
        root.geometry("400x300+120+120")
        tk.Frame(root, bg="white").place(relwidth=1, relheight=1)
        for _ in range(20):
            root.update()
            time.sleep(0.02)

        hwnd = user32.GetAncestor(int(root.winfo_id()), GA_ROOT)
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2

        user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        )
        user32.SetForegroundWindow(hwnd)
        for _ in range(10):
            root.update()
            time.sleep(0.05)

        with _Tracker() as tracker:
            send_mouse_click(cx, cy)
            for _ in range(10):
                root.update()
                time.sleep(0.03)
            clicks = tracker.recent()
            assert clicks, "the hook saw no click, so this proves nothing about drawing"
            frame = capture_screen_image()
            draw_click_markers(frame, clicks)

        inside = frame.crop(
            (
                max(0, rect.left),
                max(0, rect.top),
                min(frame.width, rect.right),
                min(frame.height, rect.bottom),
            )
        )
        assert _pixels_matching(inside, BUTTON_COLOURS[WM_LBUTTONDOWN]) > 100, (
            "the click marker is missing inside an activated topmost window, which "
            "is exactly the failure drawing after the grab is meant to avoid"
        )
    finally:
        root.destroy()
