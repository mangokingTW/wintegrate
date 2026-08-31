"""Polling helpers for assertions about state that arrives asynchronously.

`send_keys` and `send_physical_keys` return once SendInput has accepted the
events, not once the control has processed them. Asserting straight afterwards
races the target's message loop; sleeping a fixed interval first only moves the
race, and picks a number that is too short on a loaded runner and wasted on every
other run.

A real failure this replaces: `assert 'hel' == 'hello'` on a GitHub x64 runner —
two of five scan codes had not landed within the 0.3s the test slept for.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

POLL_INTERVAL = 0.05
DEFAULT_TIMEOUT = 3.0


def settled(
    read: Callable[[], Any],
    matches: Callable[[Any], bool],
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Polls `read()` until `matches(value)`, and returns the last value either way.

    Returns rather than raises, deliberately: the caller's own assert then
    produces the diff. `assert settled(...) == "hello"` still reports
    `assert 'hel' == 'hello'`, which names what actually went wrong — a helper
    that raised its own error would replace that with something vaguer.
    """
    deadline = time.monotonic() + timeout
    value = read()
    while not matches(value) and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        value = read()
    return value


def value_of(element, expected: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """The element's value once it equals `expected`, or the last value seen."""
    return settled(element.get_value, lambda v: v == expected, timeout)


def value_in(element, allowed: tuple[str, ...], timeout: float = DEFAULT_TIMEOUT) -> str:
    """The element's value once it is one of `allowed`, or the last value seen.

    For genuinely undefined outcomes — the selection state after focus is the
    example — where several results are legitimate and the point is only that the
    value has stopped changing.
    """
    return settled(element.get_value, lambda v: v in allowed, timeout)
