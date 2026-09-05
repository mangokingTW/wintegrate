"""Failure signatures: the class plus the facts that identify *this* failure, from an allow-list."""

import pytest

from wintegrate import exceptions as ex


def test_signature_is_the_class_name_when_no_fact_is_listed():
    assert ex.WintegrateError("x").signature == "WintegrateError"
    assert ex.ElementNotFoundError("x").signature == "ElementNotFoundError"


def test_only_allow_listed_facts_take_part_and_none_is_skipped():
    e = ex.FocusStealDetectedError(
        "focus lost",
        foreground_image="explorer.exe",
        foreground_class=None,
        foreground_is_target_process=False,
        hwnd=0x1234,  # a per-run integer: kept as a fact, never in the signature
    )
    assert (
        e.signature
        == "FocusStealDetectedError[foreground_image=explorer.exe,foreground_is_target_process=False]"
    )
    assert e.facts["hwnd"] == 0x1234
    assert str(e) == "focus lost"  # the message is untouched


def test_sequences_join_with_plus_so_a_signature_stays_one_token():
    e = ex.WindowDiscoveryTimeoutError(
        "no window", process_names=("notepad.exe", "Notepad.exe"), window_classes=("Notepad",)
    )
    assert (
        e.signature
        == "WindowDiscoveryTimeoutError[process_names=notepad.exe+Notepad.exe,window_classes=Notepad]"
    )


def test_diagnostics_still_render_and_facts_are_separate():
    e = ex.ActionTimeoutError("slow", diagnostics={"a": 1}, condition="visible")
    assert "Diagnostics" in str(e)
    assert e.signature == "ActionTimeoutError[condition=visible]"


@pytest.mark.parametrize(
    "cls",
    [c for c in vars(ex).values() if isinstance(c, type) and issubclass(c, ex.WintegrateError)],
)
def test_no_signature_key_is_a_per_run_integer(cls):
    """hwnd, pid and timestamps make every signature unique and destroy the comparison."""
    for key in cls.SIGNATURE_KEYS:
        assert not any(bad in key for bad in ("hwnd", "pid", "time", "stamp")), (cls.__name__, key)


def test_console_host_ended_is_a_keyboard_interrupt_and_a_wintegrate_error():
    e = ex.ConsoleHostEndedError(
        "gone",
        ended=["windowsterminal.exe"],
        peers=["pwsh.exe", "runner.worker.exe"],
        phase="sanitize",
    )
    assert isinstance(e, KeyboardInterrupt) and isinstance(e, ex.WintegrateError)
    assert (
        e.signature
        == "ConsoleHostEndedError[ended=windowsterminal.exe,peers=pwsh.exe+runner.worker.exe,phase=sanitize]"
    )
    with pytest.raises(KeyboardInterrupt):
        raise e
