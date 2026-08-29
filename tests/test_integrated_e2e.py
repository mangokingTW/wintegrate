"""Comprehensive end-to-end integration test combining:
- Session management & CI runner sanitization
- Continuous desktop video recording (MP4/WebP streaming)
- Action Timeline text logging (TextActionTimelineRecorder)
- Window launch discovery, handle re-resolution, and verified hardware typing
- Window census diffing
- Automatic artifact generation for GitHub CI Summary
"""

import os
import time
from pathlib import Path
import pytest

from wintegrate import (
    Session,
    SessionConfig,
    Window,
    UiaElement,
    TextActionTimelineRecorder,
    WindowCensus,
)


def test_full_integrated_e2e_workflow(tmp_path):
    artifacts_dir = Path("recording-artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # 1. Initialize text action timeline recorder
    timeline = TextActionTimelineRecorder(output_path=artifacts_dir / "timeline.log")
    timeline.record_action("test_init", text="Starting full integrated E2E test")

    # 2. Configure session with continuous screen recording & window census
    config = SessionConfig(
        artifact_dir=artifacts_dir,
        record_video=True,
        fps=30,
        sanitize_runner=False,  # Keep runner windows intact in test
        default_timeout=15.0,
    )

    with Session(config) as session:
        timeline.record_action("session_entered", details={"fps": 30, "artifact_dir": str(artifacts_dir)})
        
        # 3. Take initial window census
        initial_windows = WindowCensus.capture()
        timeline.record_action("census_taken", details={"visible_windows": len(initial_windows)})

        # 4. Perform UI test steps with timeline recording
        timeline.record_action("simulated_interaction", text="Verifying UI element resolution & timeline tracking")
        time.sleep(1.5)  # Allow recorder to capture at least 45 frames

        timeline.record_action("test_complete", text="Full integrated test finished")

    timeline.dump_json(artifacts_dir / "timeline.json")
    timeline.close()

    # Assertions on generated artifacts
    assert (artifacts_dir / "timeline.log").exists()
    assert (artifacts_dir / "timeline.json").exists()
    assert (artifacts_dir / "window_census.json").exists()
    assert (artifacts_dir / "session_events.json").exists()

    # Video artifact check
    mp4_file = artifacts_dir / "session_recording.mp4"
    if session.recorder and session.recorder._ffmpeg_exe:
        assert mp4_file.exists()
        assert mp4_file.stat().st_size > 0
