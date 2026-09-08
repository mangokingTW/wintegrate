"""Robot Framework keyword library for wintegrate.

    *** Settings ***
    Library    wintegrate.robot.WintegrateLibrary    record_video=True

The library is also its own listener: it names the running test in the
recording's caption, embeds a screenshot into log.html when a test fails, and
closes the session when the suite ends. Install with `pip install
"wintegrate[robot]"`.
"""

from wintegrate.robot.library import WintegrateLibrary

__all__ = ["WintegrateLibrary"]
