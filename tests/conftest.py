"""Pytest fixtures and configuration for platform-aware Windows UI automation testing."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from wintegrate import (
    ContinuousRecorder,
    desktop_only,
    env,
    is_windows_desktop,
    is_windows_server,
    server_only,
)

__all__ = [
    "env",
    "is_windows_server",
    "is_windows_desktop",
    "desktop_only",
    "server_only",
]

logger = logging.getLogger(__name__)

# One video for the whole run, at a deliberately low frame rate: this is evidence
# to scrub through, not something anyone watches at full speed, and 10 fps keeps a
# three-minute suite inside a couple of megabytes.
SUITE_RECORDING_FPS = 10


@pytest.fixture(scope="session", autouse=True)
def full_suite_recording():
    """Records the entire pytest run to a single video when opted in.

    A per-session recording shows you one scenario. This shows you the run, which
    is a different question: on a CI runner the thing that broke test 40 is often
    a dialog that appeared during test 12 and never went away. Only the continuous
    video puts those two facts in the same frame.

    Off by default — it costs a capture thread for the whole run — and enabled in
    CI by setting WINTEGRATE_RECORD_SUITE=1.
    """
    if not os.environ.get("WINTEGRATE_RECORD_SUITE") or not env.is_windows:
        yield
        return

    arch = "arm64" if env.is_arm64 else "x64"
    output = Path("recording-artifacts") / f"full-suite-{arch}.mp4"
    recorder = ContinuousRecorder(output, fps=SUITE_RECORDING_FPS)

    started = False
    try:
        started = recorder.start()
    except Exception as exc:
        logger.warning(f"Full-suite recording failed to start ({type(exc).__name__}): {exc}")

    if not started:
        # A missing video must never be the reason a test run fails; the artifact
        # is diagnostic, not part of what is under test.
        logger.warning("Full-suite recording unavailable; continuing without it.")
        yield
        return

    logger.info(f"Full-suite recording started via {recorder.backend} -> {output}")
    try:
        yield
    finally:
        try:
            frames = recorder.stop()
            logger.info(f"Full-suite recording finished: {frames} frames -> {output}")
        except Exception as exc:
            logger.warning(f"Full-suite recording failed to stop ({type(exc).__name__}): {exc}")
