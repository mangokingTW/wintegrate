"""What the awaited process was doing: alive, CPU, windows -- and the wait that
ends the moment the process does."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 process APIs")

from wintegrate import Window  # noqa: E402
from wintegrate.diagnostics import WindowCensus, set_wait_observer  # noqa: E402
from wintegrate.exceptions import WindowDiscoveryTimeoutError  # noqa: E402
from wintegrate.procwatch import ProcessSample, describe_wait, sample_process  # noqa: E402


def test_a_live_process_is_sampled_alive_with_cpu_time():
    sample = sample_process(os.getpid(), at=1.5, census=WindowCensus.capture())
    assert sample.alive and sample.exit_code is None
    assert sample.cpu_seconds is not None and sample.cpu_seconds >= 0
    assert sample.as_event()["at"] == 1.5


def test_a_gone_process_is_a_sample_that_says_so():
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(7)"])
    proc.wait(timeout=30)
    sample = sample_process(proc.pid)
    assert sample.alive is False
    assert sample.exit_code in (7, None)  # None if the pid could no longer be opened


def test_describe_wait_says_idle_and_windowless_in_words():
    samples = [ProcessSample(0.0, True, None, 0.30), ProcessSample(90.0, True, None, 0.31)]
    text = describe_wait(4242, "powershell.exe", samples)
    assert "powershell.exe (pid 4242) was sampled 2x" in text
    assert "still alive at 90s, having used 0.0s of CPU over the wait" in text
    assert "created no top-level window at all" in text


def test_describe_wait_lists_hidden_windows_and_an_exit():
    samples = [
        ProcessSample(
            0.0,
            True,
            None,
            0.1,
            [{"hwnd": 1, "class": "MSCTFIME UI", "title": "", "visible": False}],
        ),
        ProcessSample(4.0, False, 3, 0.4),
    ]
    text = describe_wait(7, "app.exe", samples)
    assert "exited with code 3 at 4s" in text
    assert "'MSCTFIME UI' '' (hidden)" in text


def test_a_process_that_dies_ends_the_wait_at_once_with_its_exit_code():
    before = WindowCensus.capture()
    started = time.monotonic()
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"])
    with pytest.raises(WindowDiscoveryTimeoutError) as excinfo:
        Window.wait_for_new(
            before, timeout=20.0, title_pattern="never-there", watch_pid=proc.pid, sample_every=0.5
        )
    elapsed = time.monotonic() - started
    assert elapsed < 10, f"the wait ran {elapsed:.1f}s past a process that had exited"
    assert "exited with code 3" in str(excinfo.value)
    assert excinfo.value.facts.get("exit_code") == 3


def test_a_long_wait_samples_the_process_and_reports_it_in_the_timeout():
    seen: list[dict] = []
    set_wait_observer(seen.append)
    before = WindowCensus.capture()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with pytest.raises(WindowDiscoveryTimeoutError) as excinfo:
            Window.wait_for_new(
                before,
                timeout=3.0,
                title_pattern="never-there",
                watch_pid=proc.pid,
                sample_every=1.0,
            )
    finally:
        set_wait_observer(None)
        proc.kill()
    message = str(excinfo.value)
    assert "was sampled" in message and "still alive at 3s" in message
    assert "created no top-level window" in message
    assert len(seen) >= 3  # the first look, at least one mid-wait, and the final one
    assert all(e["pid"] == proc.pid for e in seen)


def test_the_windows_probe_answers_for_a_live_process():
    from wintegrate.procwatch import describe_probe, windows_probe

    probe = windows_probe(os.getpid(), since_seconds=60)
    assert "error" not in probe, probe
    threads = probe["process"]["threads"]
    threads = threads if isinstance(threads, list) else [threads]
    assert threads and all("state" in t and "wait" in t for t in threads)
    assert "capi2_enabled" in probe
    text = describe_probe(probe)
    assert "threads:" in text
    assert ("CAPI2 log disabled" in text) or ("revocation-check" in text) or probe["capi2_enabled"]


def test_describe_probe_reports_a_failed_probe_without_raising():
    from wintegrate.procwatch import describe_probe

    assert "probe failed" in describe_probe({"error": "boom"})
    assert describe_probe({}) == ""
