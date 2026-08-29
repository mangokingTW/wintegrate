"""E2E verification tests for wintegrate."""

import os
import time
import pytest
from wintegrate import Session, SessionConfig, Window
from wintegrate.diagnostics import WindowCensus


def test_session_lifecycle_and_diagnostics(tmp_path):
    """Tests session pre/post census, artifact recording, and event logging."""
    config = SessionConfig(
        artifact_dir=tmp_path / "artifacts",
        record_video=True,
        fps=30,
        sanitize_runner=False,
        default_timeout=10.0,
    )

    with Session(config) as session:
        session.log_event("TEST_EVENT", "Executing diagnostic lifecycle test")
        snapshots = WindowCensus.capture()
        assert len(snapshots) > 0

    assert (tmp_path / "artifacts" / "window_census.json").exists()
    assert (tmp_path / "artifacts" / "session_events.json").exists()
