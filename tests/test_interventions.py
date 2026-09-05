"""The sweep's hides are decided by a pure rule, recorded with what was observed, and undone selectively."""

from wintegrate.sweep import InterventionResult, KillPlan, hide_reason, restore_targets


def test_hide_reason_names_the_three_classes_of_noise_and_nothing_else():
    assert hide_reason("ConsoleWindowClass", "WSL is installing") == "WSL prompt"
    assert hide_reason("Chrome_WidgetWin_1", "Welcome to Microsoft Edge") == "Edge first-run page"
    assert hide_reason("Windows.UI.Core.CoreWindow", "Search") == "shell search popup"
    assert hide_reason("#32770", "Search") is None  # the class matters for the search rule
    assert hide_reason("Notepad", "Untitled - Notepad") is None
    assert hide_reason("Chrome_WidgetWin_1", "Edge") is None  # "edge" alone is not a first-run page


def _hide(hwnd, verified=True, action="hide"):
    return InterventionResult(
        action=action,
        hwnd=hwnd,
        class_name="X",
        title="t",
        process="p.exe",
        reason="r",
        intended={"visible": False},
        observed={"exists": True, "visible": not verified},
        verified=verified,
    )


def test_restore_only_what_this_session_hid_and_is_still_hidden():
    interventions = [
        _hide(1),  # hidden and still hidden -> restore
        _hide(2),  # window is gone -> leave
        _hide(3),  # somebody already showed it -> leave
        _hide(4, verified=False),  # the hide never took -> nothing to undo
        _hide(5, action="restore"),  # a previous restore record, not a hide
    ]
    exists = {1: True, 2: False, 3: True, 4: True, 5: True}
    visible = {1: False, 2: False, 3: True, 4: True, 5: False}
    targets = restore_targets(interventions, exists.__getitem__, visible.__getitem__)
    assert [t.hwnd for t in targets] == [1]


def test_plan_carries_interventions_into_its_json_and_summary():
    plan = KillPlan(True, ("wsl",), [], False)
    plan.interventions = [_hide(0x10), _hide(0x20, verified=False)]
    d = plan.to_dict()
    assert d["interventions"][0]["intended"] == {"visible": False}
    assert d["interventions"][1]["verified"] is False
    assert plan.interventions_summary() == "hide X(0x10) [ok]; hide X(0x20) [NOT verified]"
    assert KillPlan(True, (), [], False).interventions_summary() == "hid nothing"
