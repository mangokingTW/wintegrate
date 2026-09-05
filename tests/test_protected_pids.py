"""Which processes a sweep or a forced close must not touch.

The rules, not the measurements. What makes a process protected is a relation to
this one -- never its name: `WindowsTerminal` ended a run because it hosted that
run's console, and is an ordinary target in a run that is not attached to it.
"""

from wintegrate.interop import _relations

# 900 <- 800 <- 700 is this process's ancestry; 500 shares its console under 400.
PARENTS = {900: 800, 800: 700, 700: 0, 500: 400, 400: 0, 123: 0}


def test_self_and_ancestors_are_protected():
    protected = _relations(PARENTS, console_peers=(), self_pid=900)
    assert protected[900] == "self"
    assert protected[800] == "ancestor of this process"
    assert protected[700] == "ancestor of this process"


def test_console_peers_and_their_ancestors_are_protected():
    """The peers are the point: a console is shared, and ending it ends them all.

    On a hosted runner the peers were this process, the shell above it and the
    agent reporting the job.
    """
    protected = _relations(PARENTS, console_peers=(500,), self_pid=900)
    assert protected[500] == "shares this process's console"
    assert protected[400] == "ancestor of a process sharing this console"


def test_an_unrelated_process_is_not_protected():
    protected = _relations(PARENTS, console_peers=(500,), self_pid=900)
    assert 123 not in protected


def test_the_most_direct_relation_wins():
    """A process can qualify twice; the label has to be the one a reader acts on."""
    protected = _relations(PARENTS, console_peers=(900, 800), self_pid=900)
    assert protected[900] == "self"
    assert protected[800] == "shares this process's console"


def test_a_recycled_pid_cycle_terminates():
    """PIDs are recycled, so a parent chain can close on itself."""
    protected = _relations({1: 2, 2: 1}, console_peers=(), self_pid=1)
    assert protected[1] == "self"
    assert protected[2] == "ancestor of this process"
