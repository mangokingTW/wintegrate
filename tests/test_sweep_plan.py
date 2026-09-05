"""The sweep as a plan: built from measurements, written before the first kill, executed per pid.

Pure parts, offline. The measurements themselves are Win32 and are exercised on
the Windows VM and in CI.
"""

import json

from wintegrate.sweep import (
    KillPlan,
    build_kill_plan,
    dry_run_requested,
    execute_kill_plan,
    write_plan,
)

TABLE = {
    4: (0, "system"),
    100: (4, "services.exe"),
    200: (100, "windowsterminal.exe"),
    300: (200, "pwsh.exe"),
    400: (300, "python.exe"),  # self
    500: (300, "runner.worker.exe"),  # shares the console
    600: (100, "notepad.exe"),
    700: (100, "msedge.exe"),
    800: (100, "explorer.exe"),
}
PROTECTED = {
    400: "self",
    300: "ancestor of this process",
    500: "shares this process's console",
    200: "ancestor of a process sharing this console",
}


def test_only_the_sweep_list_is_planned_and_protected_entries_are_spared_with_their_reason():
    plan = build_kill_plan(TABLE, ["WindowsTerminal", "notepad", "msedge"], PROTECTED, True, False)
    by_pid = {e.pid: e for e in plan.entries}
    assert set(by_pid) == {200, 600, 700}  # explorer and python are not on the list, so not listed
    assert by_pid[200].verdict == "spare" and "sharing this console" in by_pid[200].reason
    assert by_pid[600].verdict == "kill" and by_pid[700].verdict == "kill"
    assert [e.pid for e in plan.kills] == [600, 700]
    assert "spared windowsterminal.exe(200)" in plan.summary()


def test_names_match_with_or_without_exe():
    plan = build_kill_plan(TABLE, ["notepad.exe", "MSEDGE"], {}, False, False)
    assert sorted(e.pid for e in plan.kills) == [600, 700]


def test_execution_records_each_outcome_and_a_dry_run_ends_nothing():
    calls = []

    def fake_terminate(pid):
        calls.append(pid)
        return "terminated" if pid != 700 else "access denied"

    plan = build_kill_plan(TABLE, ["notepad", "msedge"], PROTECTED, True, dry_run=False)
    execute_kill_plan(plan, fake_terminate)
    assert calls == [600, 700]
    assert plan.results == {600: "terminated", 700: "access denied"}

    calls.clear()
    dry = build_kill_plan(TABLE, ["notepad", "msedge"], PROTECTED, True, dry_run=True)
    execute_kill_plan(dry, fake_terminate)
    assert calls == [] and dry.results == {}
    assert dry.summary().startswith("DRY RUN: would kill notepad.exe(600), msedge.exe(700)")


def test_plan_is_written_as_json_with_its_summary(tmp_path):
    plan = build_kill_plan(TABLE, ["notepad"], PROTECTED, True, False, protection_degraded=None)
    write_plan(plan, tmp_path / "a" / "kill_plan.json")
    data = json.loads((tmp_path / "a" / "kill_plan.json").read_text(encoding="utf-8"))
    assert data["entries"][0]["name"] == "notepad.exe" and data["summary"].startswith(
        "kill notepad.exe(600)"
    )
    assert data["dry_run"] is False and data["protection_degraded"] is None


def test_dry_run_comes_from_the_argument_first_then_the_environment(monkeypatch):
    monkeypatch.delenv("WINTEGRATE_SANITIZE_DRY_RUN", raising=False)
    assert dry_run_requested(None) is False
    assert dry_run_requested(True) is True
    monkeypatch.setenv("WINTEGRATE_SANITIZE_DRY_RUN", "1")
    assert dry_run_requested(None) is True
    assert dry_run_requested(False) is False


def test_degraded_protection_is_carried_in_the_plan():
    plan = KillPlan(
        True, ("notepad",), [], False, protection_degraded="protected_pids failed (OSError)"
    )
    assert plan.to_dict()["protection_degraded"].startswith("protected_pids failed")


def test_collect_preflight_never_raises_off_windows():
    from wintegrate.session import collect_preflight

    out = collect_preflight()
    assert out["pid"] and "python" in out and isinstance(out["probe_errors"], dict)
    assert isinstance(out["warnings"], list)
