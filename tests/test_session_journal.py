"""The journal survives what the session cannot.

Everything here runs without a desktop: it constructs a Session and drives the
journal directly, because the failure it exists for -- a process ended inside
__enter__ -- is exactly the one no live test can stage.
"""

import json
import time

from wintegrate.session import Session, SessionConfig


def _lines(path):
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_journal_opens_before_anything_and_records_who_wrote(tmp_path):
    session = Session(SessionConfig(artifact_dir=tmp_path / "a", record_video=False))
    session._open_journal()
    try:
        session.log_event("probe", "hello", extra=1)
    finally:
        session._close_journal()

    lines = _lines(tmp_path / "a" / "session_events.jsonl")
    assert lines[0]["type"] == "session_open"
    assert lines[0]["argv"] and "python" in lines[0]
    probe = [e for e in lines if e["type"] == "probe"][0]
    for key in ("wall", "monotonic", "pid", "timestamp", "step"):
        assert key in probe, key
    assert probe["extra"] == 1
    assert probe["pid"] == lines[0]["pid"]
    # The wall clock is UTC and unambiguous about it.
    assert probe["wall"].endswith("Z")


def test_heartbeat_is_due_only_after_a_quiet_interval(tmp_path):
    session = Session(SessionConfig(artifact_dir=tmp_path / "a", record_video=False))
    session._beat_interval = 1.0
    session._last_event_mono = 100.0
    assert session._beat_due(100.2) is False  # something was just written
    assert session._beat_due(100.6) is True  # a wait that woke slightly early still beats
    assert session._beat_due(101.0) is True


def test_heartbeat_beats_while_quiet_and_not_while_busy(tmp_path):
    session = Session(SessionConfig(artifact_dir=tmp_path / "a", record_video=False))
    session._beat_interval = 0.1
    session._open_journal()
    try:
        t0 = time.monotonic()
        time.sleep(1.0)
        quiet_elapsed = time.monotonic() - t0
        session.log_event("probe", "busy-start")
        for _ in range(20):  # busy: writes every 25 ms
            session.log_event("probe", "busy")
            time.sleep(0.025)
    finally:
        session._close_journal()
    lines = _lines(tmp_path / "a" / "session_events.jsonl")
    first_probe = next(i for i, e in enumerate(lines) if e["type"] == "probe")
    quiet_beats = [e for e in lines[:first_probe] if e["type"] == "heartbeat"]
    busy_beats = [e for e in lines[first_probe:] if e["type"] == "heartbeat"]
    # Bounded by what actually elapsed, not by the nominal second: a starved CI
    # runner (measured: 3 beats in a nominal 1.0 s on windows-11-arm) wakes the
    # thread late, and late is fine -- the beat exists to prove liveness, not rate.
    assert 1 <= len(quiet_beats) <= quiet_elapsed / session._beat_interval + 2, (
        len(quiet_beats),
        quiet_elapsed,
    )
    assert all("pid" in b and "wall" in b for b in quiet_beats)
    # While the journal is being written to, at most one stall-induced beat.
    assert len(busy_beats) <= 1, busy_beats


def test_index_is_written_at_open_and_describes_absences(tmp_path):
    session = Session(SessionConfig(artifact_dir=tmp_path / "a", record_video=False))
    session._open_journal()
    try:
        text = (tmp_path / "a" / "READ_THIS_FIRST.md").read_text(encoding="utf-8")
        index = json.loads((tmp_path / "a" / "artifact_index.json").read_text(encoding="utf-8"))
    finally:
        session._close_journal()
    assert index["state"] == "opening"
    assert "session_events.jsonl" in index["files"]
    assert "session_events.jsonl" in text
    # No exit yet, so the pretty JSON is absent -- and the index says what that means.
    assert "never reached teardown" in text
    # The trap that cost a day: a window can be absent from every capture by design.
    assert "display affinity" in text


def test_step_tracks_the_current_step_and_restores_it(tmp_path):
    session = Session(SessionConfig(artifact_dir=tmp_path / "a", record_video=False))
    session._open_journal()
    try:
        assert session._current_step is None
        with session.step("outer"):
            assert session._current_step == "outer"
            with session.step("inner"):
                assert session._current_step == "inner"
                session.log_event("probe", "inside")
            assert session._current_step == "outer"
        assert session._current_step is None
    finally:
        session._close_journal()
    probe = [e for e in _lines(tmp_path / "a" / "session_events.jsonl") if e["type"] == "probe"][0]
    assert probe["step"] == "inner"
    index = json.loads((tmp_path / "a" / "artifact_index.json").read_text(encoding="utf-8"))
    assert index["state"] == "running"


def test_journal_failure_degrades_to_memory(tmp_path):
    blocker = tmp_path / "a"
    blocker.write_text("not a directory")
    session = Session(SessionConfig(artifact_dir=blocker, record_video=False))
    session._open_journal()  # must not raise
    session.log_event("probe", "still recorded in memory")
    session._close_journal()
    assert session._journal is None
    assert any(e["type"] == "probe" for e in session.logs)


def test_job_summary_is_written_when_the_runner_asks(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    session = Session(SessionConfig(artifact_dir=tmp_path / "a", record_video=False))
    session._open_journal()
    try:
        with session.step("open the thing"):
            pass
        try:
            with session.step("break the thing"):
                raise ValueError("boom")
        except ValueError:
            pass
        session.log_event("session_error", "ValueError: boom", step="break the thing")
        session._write_step_summary(ValueError)
    finally:
        session._close_journal()
    text = summary.read_text(encoding="utf-8")
    assert "wintegrate session -- failed" in text
    assert "| open the thing | ok |" in text
    assert "break the thing" in text and "**failed** (ValueError)" in text
    assert "READ_THIS_FIRST.md" in text and "session_events.jsonl" in text


def test_job_summary_is_silent_off_ci(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    session = Session(SessionConfig(artifact_dir=tmp_path / "a", record_video=False))
    session._write_step_summary(None)  # must not raise, must write nothing
    assert not (tmp_path / "summary.md").exists()
