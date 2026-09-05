"""Frames at the moments the timeline names: the pure planning, and the decode against a known video."""

from __future__ import annotations

import json
from fractions import Fraction

import pytest

from wintegrate.exceptions import DiagnosticPipelineError
from wintegrate.frames import FrameMark, extract_frames, plan_marks, slug, video_ms_for

ANCHOR = {"monotonic_start": 1000.0, "wall_start": 1.7e9, "fps": 10}


def ev(kind, mono, message="", **extra):
    return {"type": kind, "monotonic": mono, "message": message, **extra}


def test_video_ms_follows_the_anchor_formula_and_refuses_to_guess():
    assert video_ms_for(ev("x", 1041.83), ANCHOR) == 41830
    assert (
        video_ms_for(ev("x", 999.0), ANCHOR) == 0
    )  # before the recording started: clamped, not negative
    assert video_ms_for({"type": "x"}, ANCHOR) is None  # no monotonic
    assert video_ms_for(ev("x", 1001.0), None) is None  # no anchor
    assert video_ms_for(ev("x", 1001.0), {"fps": 10}) is None  # anchor without a start


def test_slug_and_filename_are_filesystem_safe():
    assert slug("submit the form / retry: 2") == "submit-the-form-retry-2"
    assert (
        FrameMark(41830, "step_failed", "submit", 0).filename == "t041830ms_step_failed_submit.png"
    )
    assert slug("") == "event"


def test_plan_puts_the_failure_window_first_then_the_tail_then_the_rest():
    events = [
        ev("session_start", 1001.0, "baseline"),
        ev("step_start", 1010.0, "type"),
        ev("step_failed", 1040.0, "submit", error="TimeoutError"),
        ev("session_error", 1040.2, "TimeoutError: ..."),
    ]
    kept, dropped = plan_marks(events, ANCHOR, tail_ms=60000, max_frames=20)
    kinds = [(m.priority, m.kind, m.video_ms) for m in kept]
    # window around the failure (39000, 39500, 40000, 40500), then tail, then the rest newest-first
    assert kinds[:4] == [
        (0, "step-failed", 39000),
        (0, "step-failed", 39500),
        (0, "step-failed", 40000),
        (0, "step-failed", 40500),
    ]
    assert kinds[4] == (1, "tail", 60000)
    assert [k for k in kinds[5:]] == [
        (2, "session_error", 40200),
        (2, "step_start", 10000),
        (2, "session_start", 1000),
    ]
    assert dropped == []
    assert kept[0].filename == "t039000ms_step-failed_submit.png"


def test_plan_drops_whole_marks_under_the_budget_and_says_which():
    events = [ev("a", 1001.0), ev("b", 1002.0), ev("step_failed", 1030.0, "s")]
    kept, dropped = plan_marks(events, ANCHOR, tail_ms=40000, max_frames=3)
    assert [m.video_ms for m in kept] == [
        29000,
        29500,
        30000,
    ]  # the failure window fills the budget
    assert {m.kind for m in dropped} == {"step-failed", "tail", "a", "b"}  # 30500 + tail + the rest


def test_plan_merges_marks_that_land_on_the_same_frame():
    events = [ev("step_failed", 1030.0, "s"), ev("session_error", 1030.02, "boom")]  # 20 ms apart
    kept, dropped = plan_marks(events, ANCHOR, tail_ms=40000, max_frames=20)
    assert [m.video_ms for m in kept if m.video_ms in (30000, 30020)] == [30000]
    assert dropped == []  # a duplicate is not a drop


def test_plan_without_a_failure_still_gives_the_tail_and_the_rest():
    kept, _ = plan_marks([ev("a", 1005.0)], ANCHOR, tail_ms=9000, max_frames=8)
    assert [(m.kind, m.video_ms) for m in kept] == [("tail", 9000), ("a", 5000)]


def _write_synthetic_video(path, frames=30, fps=10):
    """A video whose frame n is a solid gray of level n*8, stamped like the recorder does (pts in ms)."""
    av = pytest.importorskip("av")
    from PIL import Image

    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("libx264", rate=fps)
        stream.width, stream.height = 64, 48
        stream.pix_fmt = "yuv420p"
        stream.codec_context.time_base = Fraction(1, 1000)
        stream.codec_context.gop_size = fps
        for n in range(frames):
            img = Image.new("RGB", (64, 48), (n * 8, n * 8, n * 8))
            frame = av.VideoFrame.from_image(img).reformat(format="yuv420p")
            frame.pts = n * (1000 // fps)
            frame.time_base = Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


def _gray_of(png):
    from PIL import Image

    with Image.open(png) as im:
        return im.convert("L").getpixel((32, 24))


def test_extract_picks_the_nearest_frame_and_writes_an_index(tmp_path):
    video = tmp_path / "session_recording.mp4"
    _write_synthetic_video(video)  # frames at 0,100,...,2900 ms; gray = 8 * index
    # The recording started at monotonic 1000.0; the step failed 1.23 s in.
    events = [
        ev("step_start", 1000.4, "type"),
        ev("step_failed", 1001.23, "submit"),
        ev("session_error", 1001.25, "boom"),
    ]
    index = extract_frames(video, ANCHOR, events, tmp_path / "frames", max_frames=8)
    files = {f["file"]: f for f in index["frames"]}
    assert "t001230ms_step-failed_submit.png" in files
    picked = files["t001230ms_step-failed_submit.png"]
    assert picked["frame_ms"] in (1200, 1300)  # nearest decoded frame to 1230 ms is 1200
    assert (
        abs(_gray_of(tmp_path / "frames" / picked["file"]) - 8 * (picked["frame_ms"] // 100)) <= 6
    )
    tail = [f for f in index["frames"] if f["kind"] == "tail"][0]
    assert (
        tail["frame_ms"] == 2900 and abs(_gray_of(tmp_path / "frames" / tail["file"]) - 8 * 29) <= 6
    )
    on_disk = json.loads((tmp_path / "frames" / "index.json").read_text(encoding="utf-8"))
    assert [f["file"] for f in on_disk["frames"]] == [f["file"] for f in index["frames"]]
    assert on_disk["dropped"] == []
    assert sorted(p.name for p in (tmp_path / "frames").glob("*.png")) == sorted(files)


def test_extract_respects_the_budget_and_records_what_it_dropped(tmp_path):
    video = tmp_path / "v.mp4"
    _write_synthetic_video(video, frames=20)
    events = [ev("step_failed", 1001.0, "s")] + [ev(f"e{i}", 1000.0 + i * 0.1) for i in range(12)]
    index = extract_frames(video, ANCHOR, events, tmp_path / "frames", max_frames=5)
    assert len(index["frames"]) == 5 and len(list((tmp_path / "frames").glob("*.png"))) == 5
    assert index["dropped"] and all("video_ms" in d for d in index["dropped"])


def test_extract_names_the_video_when_it_cannot_decode(tmp_path):
    bad = tmp_path / "not-a-video.mp4"
    bad.write_bytes(b"this is not an mp4")
    with pytest.raises(DiagnosticPipelineError, match="not-a-video.mp4"):
        extract_frames(bad, ANCHOR, [ev("step_failed", 1001.0, "s")], tmp_path / "frames")
