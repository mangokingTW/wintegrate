"""The caption that says which test a recording is showing.

A recording of a suite shows applications being driven with nothing to say which
test is driving them. The caption is composited after the screen grab, in the
bottom-left corner, so it cannot be covered -- the same reason the pointer and
keyboard overlays are drawn there rather than in a window.
"""

from __future__ import annotations

import sys

import pytest

from wintegrate.caption_overlay import (
    DEFAULT_MARGIN_X,
    DEFAULT_MARGIN_Y,
    MAX_WIDTH_FRACTION,
    draw_caption,
)

Image = pytest.importorskip("PIL.Image", reason="captions need Pillow")


def _blank(width=800, height=600, colour=(255, 255, 255)):
    return Image.new("RGB", (width, height), colour)


def _changed_pixels(before, after) -> list[tuple[int, int]]:
    b, a = before.load(), after.load()
    return [(x, y) for y in range(before.height) for x in range(before.width) if b[x, y] != a[x, y]]


def test_the_caption_lands_in_the_bottom_left():
    frame = _blank()
    before = frame.copy()
    draw_caption(frame, "test_the_thing_under_test")

    changed = _changed_pixels(before, frame)
    assert changed, "the caption drew nothing"

    xs = [x for x, _ in changed]
    ys = [y for _, y in changed]
    assert min(xs) >= DEFAULT_MARGIN_X - 1, f"the caption starts at x={min(xs)}, left of its margin"
    assert max(ys) <= frame.height - DEFAULT_MARGIN_Y + 1, (
        f"the caption reaches y={max(ys)}, below its margin"
    )
    # Bottom-left means bottom-left: not spilling into the middle of the frame, where
    # the keyboard HUD lives, and not into the top half at all.
    assert max(xs) < frame.width // 2, (
        f"the caption reaches x={max(xs)}, past halfway across a {frame.width}px frame"
    )
    assert min(ys) > frame.height // 2, (
        f"the caption reaches y={min(ys)}, into the top half of the frame"
    )


def test_an_empty_caption_draws_nothing():
    """A recorder can carry a caption that is only set for part of a run."""
    for text in ("", "   ", "\n"):
        frame = _blank()
        before = frame.copy()
        draw_caption(frame, text)
        assert not _changed_pixels(before, frame), f"{text!r} drew something"


def test_a_subtitle_makes_the_panel_taller():
    one = _blank()
    draw_caption(one, "test_name")
    two = _blank()
    draw_caption(two, "test_name", subtitle="src/module/test_file.py")

    one_top = min(y for _, y in _changed_pixels(_blank(), one))
    two_top = min(y for _, y in _changed_pixels(_blank(), two))
    assert two_top < one_top, (
        f"the panel with a subtitle starts at y={two_top}, no higher than the one "
        f"without at y={one_top}, so the subtitle did not add a line"
    )


def test_a_long_id_is_trimmed_from_the_front():
    """The tail of a test id is the part worth keeping.

    `…::test_table_mode_returns_tab_separated_rows` says what is running;
    `src/modules/PowerOCR/Tests/…` says where it lives, which the viewer already
    knows.
    """
    frame = _blank(width=800)
    long_id = (
        "src/modules/PowerOCR/Tests/ui-verification/test_powerocr_capture_modes.py"
        "::test_table_mode_returns_tab_separated_rows"
    )
    draw_caption(frame, long_id)

    changed = _changed_pixels(_blank(width=800), frame)
    assert changed, "the caption drew nothing"
    width = max(x for x, _ in changed) - min(x for x, _ in changed)
    limit = int(800 * MAX_WIDTH_FRACTION)
    assert width <= limit, (
        f"the caption is {width}px wide on an 800px frame, past its {limit}px limit"
    )


def test_the_caption_survives_a_frame_too_small_for_it():
    """A frame that cannot fit the panel is left alone rather than raising."""
    frame = Image.new("RGB", (40, 30), (255, 255, 255))
    before = frame.copy()
    draw_caption(frame, "a caption far wider than this frame")
    # Either it fitted something or it skipped; what it must not do is raise.
    assert frame.size == before.size


@pytest.mark.skipif(sys.platform != "win32", reason="the recorder is Windows-only")
def test_the_recorder_exposes_a_settable_caption():
    """The recorder holds the text; the caller decides what tests are called.

    Set while recording, so a pytest hook can assign it per test and the frames
    from that point on say so.
    """
    import inspect

    from wintegrate import ContinuousRecorder

    parameters = inspect.signature(ContinuousRecorder).parameters
    assert "caption" in parameters, "the recorder takes no caption"
    assert parameters["caption"].default == "", (
        "the caption should default to empty, so a recorder that is never told what "
        "is running draws no panel"
    )

    recorder = ContinuousRecorder("unused.mp4")
    assert recorder.caption == ""
    recorder.caption = "test_something"
    assert recorder.caption == "test_something"
