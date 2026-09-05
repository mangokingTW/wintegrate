"""Waiting for a file another process is writing.

`time.sleep(2)` after launching something that writes a file is the assumption
this replaces: that the file is there, whole, by then. `expect_artifact` waits
for it to exist, be non-empty and stop growing, and when it does not arrive
says what *was* in the directory -- a negative result that names what it
examined is the difference between "the file is missing" and "the file is
missing, and here is the .tmp it left behind".
"""

from __future__ import annotations

import time
from pathlib import Path

from wintegrate.exceptions import ArtifactMissingError


def expect_artifact(path: str | Path, timeout: float = 10.0, poll: float = 0.1) -> Path:
    """Returns `path` once it exists, is non-empty and its size held for one poll.

    Raises `ArtifactMissingError` at the deadline, listing the directory.
    """
    target = Path(path)
    deadline = time.monotonic() + timeout
    last_size = -1
    while True:
        size = target.stat().st_size if target.is_file() else -1
        if size > 0 and size == last_size:
            return target
        last_size = size
        if time.monotonic() >= deadline:
            break
        time.sleep(poll)
    parent = target.parent
    try:
        listing = sorted(
            f"{p.name} ({p.stat().st_size} bytes)" if p.is_file() else f"{p.name}/"
            for p in parent.iterdir()
        )
    except OSError as exc:
        listing = [f"(could not list {parent}: {type(exc).__name__}: {exc})"]
    state = (
        "never appeared"
        if size < 0
        else (
            "exists but is empty" if size == 0 else f"still growing ({size} bytes at the deadline)"
        )
    )
    raise ArtifactMissingError(
        f"Expected {target.name} in {parent} within {timeout:g}s: it {state}. "
        f"The directory holds {len(listing)} entr{'y' if len(listing) == 1 else 'ies'}: "
        + (", ".join(listing) if listing else "(nothing)"),
        diagnostics={"expected": str(target), "state": state, "directory": listing},
    )
