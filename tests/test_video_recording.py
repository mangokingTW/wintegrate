"""Tests for ContinuousRecorder and desktop video generation."""

import os
import time
from pathlib import Path
from wintegrate import ContinuousRecorder


def test_continuous_recorder_creates_video_artifact(tmp_path):
    video_path = tmp_path / "test_recording.mp4"
    recorder = ContinuousRecorder(output_path=video_path, fps=30)
    
    started = recorder.start()
    if not started:
        # In headless environments without ffmpeg installed, recorder gracefully reports False
        return

    # Record 1.5 seconds of desktop frames
    time.sleep(1.5)
    frames_recorded = recorder.stop()

    assert frames_recorded > 0
    assert video_path.exists()
    assert video_path.stat().st_size > 0

    # Also check if ffmpeg stderr log was written
    log_path = video_path.with_suffix(".ffmpeg.log")
    assert log_path.exists()
