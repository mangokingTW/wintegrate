"""ContinuousRecorder encoding-backend tests.

Screen capture and the display metrics are stubbed, so these exercise the encoder
end to end (a real file that decodes) on any platform, including the macOS/Linux
dev machines where the Win32 capture path cannot run.
"""

from __future__ import annotations

import time

import pytest

from wintegrate import diagnostics
from wintegrate.diagnostics import ContinuousRecorder

av = pytest.importorskip("av", reason="PyAV encoding backend not installed")
Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")

WIDTH, HEIGHT = 320, 240


@pytest.fixture
def fake_screen(monkeypatch):
    """Replaces screen capture and display metrics with a deterministic source."""
    frames = {"n": 0}

    def fake_capture():
        frames["n"] += 1
        shade = (frames["n"] * 20) % 255
        return Image.new("RGB", (WIDTH, HEIGHT), (shade, 60, 120))

    monkeypatch.setattr(diagnostics, "capture_screen_image", fake_capture)
    monkeypatch.setattr(
        diagnostics.user32, "GetSystemMetrics", lambda i: WIDTH if i == 0 else HEIGHT
    )
    monkeypatch.setattr(diagnostics, "attach_to_input_desktop", lambda: True)
    return frames


def test_pyav_backend_produces_a_decodable_video(tmp_path, fake_screen):
    out = tmp_path / "session.mp4"
    rec = ContinuousRecorder(out, fps=30)

    assert rec.start() is True
    assert rec.backend == "pyav"
    time.sleep(0.5)
    frame_count = rec.stop()

    assert frame_count > 0
    assert out.exists() and out.stat().st_size > 0

    with av.open(str(out)) as container:
        stream = container.streams.video[0]
        assert stream.codec_context.name == "h264"
        assert (stream.width, stream.height) == (WIDTH, HEIGHT)
        decoded = sum(1 for _ in container.decode(video=0))
    assert decoded > 0


def test_pyav_is_preferred_over_the_ffmpeg_subprocess(tmp_path, fake_screen, monkeypatch):
    """A usable ffmpeg binary must not push the in-process encoder aside."""
    monkeypatch.setattr(ContinuousRecorder, "_start_ffmpeg_subprocess", lambda self: False)
    rec = ContinuousRecorder(tmp_path / "pref.mp4", fps=30)
    assert rec.start() is True
    assert rec.backend == "pyav"
    rec.stop()


def test_no_backend_available_disables_recording(tmp_path, fake_screen, monkeypatch):
    """Without PyAV and without an ffmpeg binary, start() reports failure rather than raising."""
    monkeypatch.setattr(ContinuousRecorder, "_start_pyav", lambda self, w, h: False)
    rec = ContinuousRecorder(tmp_path / "none.mp4", fps=30)
    rec._ffmpeg_exe = None
    assert rec.start() is False
    assert rec.backend is None
