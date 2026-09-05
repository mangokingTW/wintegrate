"""What the CI sweep is allowed to kill.

The list is a constant, so this is a small test. It exists because getting it
wrong is not small: the sweep killed the terminal hosting the process that
called it, which ended that process a second later, mid-run.
"""

from wintegrate.session import SWEEP_PROCESS_NAMES, TERMINAL_HOST_NAMES, sweep_process_names


def test_a_caller_with_a_console_never_sweeps_terminal_hosts():
    """A console-subsystem process does not own its console -- a terminal does.

    On Windows 11 that terminal is Windows Terminal, and killing it destroys
    the console and every process attached to it. Excluding it by pid does not
    work: it is neither an ancestor of the caller nor the owner of
    GetConsoleWindow(), which belongs to a brokered OpenConsole.exe whose
    parent chain runs to services.exe.
    """
    names = sweep_process_names(caller_has_console=True)
    assert "WindowsTerminal" not in names
    for host in TERMINAL_HOST_NAMES:
        assert host not in names


def test_a_caller_without_a_console_still_sweeps_them():
    """The ordinary case for a runner started by a service or a GUI.

    A leftover terminal window takes the foreground, which is exactly what the
    sweep is for, so it must not be given up in general.
    """
    names = sweep_process_names(caller_has_console=False)
    for host in TERMINAL_HOST_NAMES:
        assert host in names


def test_the_rest_of_the_list_is_swept_either_way():
    for has_console in (True, False):
        names = sweep_process_names(caller_has_console=has_console)
        for expected in SWEEP_PROCESS_NAMES:
            assert expected in names
