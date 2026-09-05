"""A failing session leaves frames/ behind: the recording at the failure, on disk, named.

Windows only: it needs a real recording. Offline, the planning and the decode are
covered by test_frames.py against a synthetic video.
"""

from __future__ import annotations

import json
import sys
import time

import pytest

from wintegrate.session import Session, SessionConfig

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="records the live desktop")


def test_a_failed_session_extracts_frames_around_the_failure(tmp_path):
    art = tmp_path / "artifacts"
    session = Session(
        SessionConfig(
            artifact_dir=art,
            record_video=True,
            fps=10,
            sanitize_runner=False,
            isolated_virtual_desktop=False,
            dismiss_oobe=False,
        )
    )
    with pytest.raises(RuntimeError, match="deliberate"):
        with session:
            with session.step("wait a moment"):
                time.sleep(1.2)
            with session.step("the step that fails"):
                time.sleep(0.6)
                raise RuntimeError("deliberate")
    if not (art / "session_recording.mp4").is_file():
        pytest.skip("no recording on this runner (PyAV encoder unavailable)")
    events = [
        json.loads(line)
        for line in (art / "session_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    frames = art / "frames"
    pngs = sorted(p.name for p in frames.glob("*.png"))
    # The journal says why when there are none: frames_skipped carries the reason.
    assert pngs, "a failed session with a recording must leave frames/; journal says: " + str(
        [e for e in events if e["type"].startswith("frames_")]
    )
    assert any("step-failed" in n for n in pngs) and any("tail" in n for n in pngs)
    index = json.loads((frames / "index.json").read_text(encoding="utf-8"))
    assert [f["file"] for f in index["frames"]] == sorted(
        pngs, key=lambda n: [f["file"] for f in index["frames"]].index(n)
    )
    read_me = (art / "READ_THIS_FIRST.md").read_text(encoding="utf-8")
    assert "`frames/`" in read_me
    assert (
        "frames/" in json.loads((art / "artifact_index.json").read_text(encoding="utf-8"))["files"]
    )
    assert any(e["type"] == "frames_extracted" for e in events)
