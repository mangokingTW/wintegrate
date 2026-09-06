"""Pytest fixtures and configuration for platform-aware Windows UI automation testing."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from wintegrate import (
    ContinuousRecorder,
    desktop_only,
    env,
    is_windows_desktop,
    is_windows_server,
    server_only,
)

__all__ = [
    "env",
    "is_windows_server",
    "is_windows_desktop",
    "desktop_only",
    "server_only",
]

logger = logging.getLogger(__name__)

# One video for the whole run, at a deliberately low frame rate: this is evidence
# to scrub through, not something anyone watches at full speed, and 10 fps keeps a
# three-minute suite inside a couple of megabytes.
SUITE_RECORDING_FPS = 10

# The run's recorder, held at module level so the per-test hooks below can reach
# it. A fixture would not do: `pytest_runtest_logstart` fires outside any test's
# fixtures, which is precisely when the caption has to change.
_suite_recorder: ContinuousRecorder | None = None

# What the caption should say right now. The hooks own this unconditionally,
# because the first test's logstart fires before the session fixture has built
# the recorder — writing only to the recorder would leave that one test unnamed.
_caption: tuple[str, str] = ("", "")


def _apply_caption():
    if _suite_recorder is not None:
        _suite_recorder.caption, _suite_recorder.caption_subtitle = _caption


def pytest_runtest_logstart(nodeid, location):
    """Names the running test in the recording's bottom-left corner.

    A single video of the whole suite is only searchable if each frame says what
    produced it; otherwise finding the stretch that belongs to one test means
    counting windows and guessing. The recorder draws whatever is in `caption`,
    so setting it here is the entire integration.

    The test's own name goes on the first line and its file on the second: the
    name is what a viewer is looking for, and a long path would push it out.
    """
    global _caption
    filename, _lineno, _domain = location
    _caption = (nodeid.split("::")[-1], str(filename))
    _apply_caption()


def pytest_runtest_logfinish(nodeid, location):
    """Clears the caption between tests, so a frame never names the wrong one."""
    global _caption
    _caption = ("", "")
    _apply_caption()


@pytest.fixture(scope="session", autouse=True)
def full_suite_recording():
    """Records the entire pytest run to a single video when opted in.

    A per-session recording shows you one scenario. This shows you the run, which
    is a different question: on a CI runner the thing that broke test 40 is often
    a dialog that appeared during test 12 and never went away. Only the continuous
    video puts those two facts in the same frame.

    Off by default — it costs a capture thread for the whole run — and enabled in
    CI by setting WINTEGRATE_RECORD_SUITE=1.
    """
    if not os.environ.get("WINTEGRATE_RECORD_SUITE") or not env.is_windows:
        yield
        return

    arch = "arm64" if env.is_arm64 else "x64"
    output = Path("recording-artifacts") / f"full-suite-{arch}.mp4"
    recorder = ContinuousRecorder(output, fps=SUITE_RECORDING_FPS)

    started = False
    try:
        started = recorder.start()
    except Exception as exc:
        logger.warning(f"Full-suite recording failed to start ({type(exc).__name__}): {exc}")

    if not started:
        # A missing video must never be the reason a test run fails; the artifact
        # is diagnostic, not part of what is under test.
        logger.warning("Full-suite recording unavailable; continuing without it.")
        yield
        return

    logger.info(f"Full-suite recording started via {recorder.backend} -> {output}")
    # The job log timestamps events in wall time; the video counts from zero. Without
    # this file the offset has to be reconstructed from the step's end time and the
    # video's duration, which is guesswork to the second.
    try:
        import json as _json

        anchor_path = output.with_suffix(".anchor.json")
        anchor_path.write_text(_json.dumps(recorder.anchor(), indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Full-suite recording anchor not written ({type(exc).__name__}): {exc}")
    global _suite_recorder
    _suite_recorder = recorder
    # The first test is already running by the time this fixture builds the
    # recorder, so hand it the caption that logstart has already set.
    _apply_caption()
    try:
        yield
    finally:
        _suite_recorder = None
        try:
            frames = recorder.stop()
            logger.info(f"Full-suite recording finished: {frames} frames -> {output}")
        except Exception as exc:
            logger.warning(f"Full-suite recording failed to stop ({type(exc).__name__}): {exc}")


def _prepare_desktop_module():
    """The action's prepare_desktop.py, loaded from the checkout: one copy of the rule
    for the action and for this suite."""
    import importlib.util

    path = (
        Path(__file__).resolve().parents[1]
        / ".github/actions/setup-windows-gui-test/prepare_desktop.py"
    )
    spec = importlib.util.spec_from_file_location("prepare_desktop", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session", autouse=True)
def desktop_prepared(full_suite_recording):
    """Clears the OOBE privacy screen before the first test, not inside the first Session.

    `try_dismiss_oobe_privacy_screen` has lived in `Session.__enter__` since the
    first version, so on a hosted arm64 runner the privacy page stayed up until
    the first test that opened a Session -- about 70 s and forty tests in,
    measured from the suite recording of run 33956153832. Tests that never open
    a Session ran under it the whole time. Depends on `full_suite_recording` so
    it happens after the camera starts, and writes what it saw and did to
    `recording-artifacts/desktop_prep.json`, because a session fixture's stdout
    is only shown when the first test fails.
    """
    if not env.is_windows:
        yield
        return
    import json

    # The three moves (OOBE page, runner dialogs, agent console, then the Start
    # menu the dismissal leaves open) live in the composite action's
    # prepare_desktop.py so a harness that never runs pytest gets them too; this
    # suite is one more caller, on camera.
    record = _prepare_desktop_module().prepare_desktop()
    try:
        out = Path("recording-artifacts")
        out.mkdir(exist_ok=True)
        (out / "desktop_prep.json").write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning(f"desktop_prep.json not written ({type(exc).__name__}): {exc}")
    logger.info(f"desktop prepared: {record}")
    yield


@pytest.fixture(scope="session", autouse=True)
def foreground_log(desktop_prepared):
    """Records every change of the foreground window for the whole run.

    A probe, so the next question has an answer on disk: in run 33960623532 the
    Start menu was back on screen ~0.4 s after a Notepad was killed, four jobs
    out of four, and neither the job log nor the recording says what activated
    it or what the foreground was in between. One line per change, with the
    owning process, into `recording-artifacts/foreground_log.jsonl`. Polls at
    10 Hz; a probe must not cost the suite anything noticeable.
    """
    if not env.is_windows:
        yield
        return
    import json
    import threading
    import time

    from wintegrate import interop
    from wintegrate.interop import (
        get_foreground_window,
        get_window_class,
        get_window_pid,
        get_window_title,
        user32,
    )

    # get_process_table arrives with #102; until it lands, name a pid the older way.
    _table = getattr(interop, "get_process_table", None)
    _name_of = getattr(interop, "get_process_image_name", None)

    def process_name(pid: int, cache: dict[int, str]) -> str:
        if pid in cache:
            return cache[pid]
        try:
            if _table is not None:
                cache.update({p: img for p, (_pp, img) in _table().items()})
            elif _name_of is not None:
                cache[pid] = _name_of(pid) or "?"
        except Exception:
            pass
        return cache.get(pid, "?")

    stop = threading.Event()
    out = Path("recording-artifacts")
    out.mkdir(exist_ok=True)
    path = out / "foreground_log.jsonl"

    def run() -> None:
        last = None
        names: dict[int, str] = {}
        with open(path, "a", encoding="utf-8", buffering=1) as fh:
            while not stop.is_set():
                try:
                    hwnd = get_foreground_window()
                    if hwnd != last:
                        pid = get_window_pid(hwnd) if hwnd else 0
                        if pid:
                            process_name(pid, names)
                        fh.write(
                            json.dumps(
                                {
                                    "wall": time.time(),
                                    "monotonic": round(time.monotonic(), 3),
                                    "hwnd": hwnd,
                                    "class": get_window_class(hwnd) if hwnd else "",
                                    "title": (get_window_title(hwnd) if hwnd else "")[:80],
                                    "pid": pid,
                                    "process": names.get(pid, "?"),
                                    "visible": bool(user32.IsWindowVisible(hwnd)) if hwnd else None,
                                },
                                default=str,
                            )
                            + "\n"
                        )
                        last = hwnd
                except Exception as exc:  # the probe never takes the suite down
                    fh.write(
                        json.dumps({"wall": time.time(), "error": f"{type(exc).__name__}: {exc}"})
                        + "\n"
                    )
                stop.wait(0.1)

    thread = threading.Thread(target=run, name="foreground-log", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2.0)
