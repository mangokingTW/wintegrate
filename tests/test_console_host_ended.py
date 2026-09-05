"""Naming the death: a KeyboardInterrupt is re-labelled only when the console really went away."""

import json

from wintegrate.exceptions import ConsoleHostEndedError
from wintegrate.session import Session, SessionConfig, console_host_verdict
from wintegrate.sweep import KillPlan, PlannedKill

PREFLIGHT_WITH_CONSOLE = {
    "console_attached": True,
    "console_clients": [
        {"pid": 4812, "name": "python.exe"},
        {"pid": 3120, "name": "pwsh.exe"},
        {"pid": 1544, "name": "runner.worker.exe"},
    ],
}
PLAN = KillPlan(
    True,
    ("wsl", "WindowsTerminal"),
    [
        PlannedKill(9001, "windowsterminal.exe", "kill", "matches sweep list"),
        PlannedKill(9002, "wsl.exe", "kill", "matches sweep list"),
    ],
    False,
    results={9001: "terminated", 9002: "gone"},
)


def test_console_gone_after_being_there_is_the_verdict_with_what_the_sweep_ended():
    v = console_host_verdict(PREFLIGHT_WITH_CONSOLE, False, (), KeyboardInterrupt(), PLAN)
    assert v == {
        "ended": ["windowsterminal.exe"],
        "ended_pids": [9001],
        "peers": ["python.exe", "pwsh.exe", "runner.worker.exe"],
        "original": "KeyboardInterrupt",
    }


def test_a_probe_that_now_raises_counts_as_gone():
    assert (
        console_host_verdict(PREFLIGHT_WITH_CONSOLE, None, None, KeyboardInterrupt(), None)
        is not None
    )


def test_console_still_there_means_a_real_ctrl_c_and_no_relabel():
    assert (
        console_host_verdict(
            PREFLIGHT_WITH_CONSOLE, True, (4812, 3120, 1544), KeyboardInterrupt(), PLAN
        )
        is None
    )


def test_a_process_that_never_had_a_console_cannot_produce_a_verdict():
    assert (
        console_host_verdict(
            {"console_attached": False, "console_clients": []}, False, (), KeyboardInterrupt(), PLAN
        )
        is None
    )
    assert console_host_verdict({}, None, None, KeyboardInterrupt(), None) is None


def test_the_rule_is_about_evidence_not_exception_type():
    # A console that vanished re-labels whatever surfaced -- an OSError from a probe as much as an interrupt.
    assert (
        console_host_verdict(PREFLIGHT_WITH_CONSOLE, False, (), OSError(6, "invalid handle"), None)
        is not None
    )


def test_an_exception_that_already_is_the_verdict_is_left_alone():
    already = ConsoleHostEndedError("gone")
    assert console_host_verdict(PREFLIGHT_WITH_CONSOLE, False, (), already, PLAN) is None


def test_name_death_relabels_and_journals_when_the_console_is_gone(tmp_path, monkeypatch):
    import wintegrate.session as sessmod

    monkeypatch.setattr(sessmod, "has_console", lambda: False)
    monkeypatch.setattr(sessmod, "console_client_pids", lambda: ())
    session = Session(SessionConfig(artifact_dir=tmp_path / "a", record_video=False))
    session._open_journal()
    try:
        session.preflight = dict(PREFLIGHT_WITH_CONSOLE)
        session.kill_plan = PLAN
        session._mark_phase("sanitize")
        named = session._name_death(KeyboardInterrupt())
    finally:
        session._close_journal()
    assert isinstance(named, ConsoleHostEndedError)
    assert "console host being destroyed" in str(named) and "windowsterminal.exe" in str(named)
    assert "Last phase: sanitize" in str(named)
    events = [
        json.loads(line)
        for line in (tmp_path / "a" / "session_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    ev = next(e for e in events if e["type"] == "console_host_ended")
    assert (
        ev["ended"] == ["windowsterminal.exe"]
        and ev["phase"] == "sanitize"
        and ev["original"] == "KeyboardInterrupt"
    )


def test_name_death_returns_the_same_exception_when_the_console_is_intact(tmp_path, monkeypatch):
    import wintegrate.session as sessmod

    monkeypatch.setattr(sessmod, "has_console", lambda: True)
    monkeypatch.setattr(sessmod, "console_client_pids", lambda: (4812, 3120, 1544))
    session = Session(SessionConfig(artifact_dir=tmp_path / "a", record_video=False))
    session._open_journal()
    try:
        session.preflight = dict(PREFLIGHT_WITH_CONSOLE)
        exc = KeyboardInterrupt()
        assert session._name_death(exc) is exc
    finally:
        session._close_journal()


def test_a_failure_inside_the_namer_never_replaces_the_original(tmp_path, monkeypatch):
    import wintegrate.session as sessmod

    monkeypatch.setattr(sessmod, "has_console", lambda: False)
    monkeypatch.setattr(sessmod, "console_client_pids", lambda: ())
    session = Session(SessionConfig(artifact_dir=tmp_path / "a", record_video=False))
    session.preflight = dict(PREFLIGHT_WITH_CONSOLE)
    monkeypatch.setattr(
        session, "log_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("journal broke"))
    )
    exc = KeyboardInterrupt()
    assert session._name_death(exc) is exc
