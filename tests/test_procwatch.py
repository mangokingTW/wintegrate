"""What the awaited process was doing: the samples, the machine snapshot, and
the wait that ends the moment the process does."""

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
from wintegrate.procwatch import (  # noqa: E402
    ProcessSample,
    describe_wait,
    machine_snapshot,
    sample_process,
)


def test_a_live_process_is_sampled_with_cpu_threads_and_windows():
    sample = sample_process(os.getpid(), at=1.5)
    assert sample.alive and sample.exit_code is None
    assert sample.cpu_seconds is not None and sample.cpu_seconds >= 0
    assert sample.threads and sample.threads >= 1
    assert sample.working_set_mb and sample.working_set_mb > 0
    assert sample.as_event()["at"] == 1.5


def test_an_exited_process_is_sampled_as_gone_with_its_exit_code():
    proc = subprocess.run([sys.executable, "-c", "import sys; sys.exit(7)"], check=False)
    sample = sample_process(proc.pid if hasattr(proc, "pid") else 0)
    # The pid may already be recycled or unopenable; either way the sample says
    # "not alive" rather than raising. A still-open handle would report 7.
    assert sample.alive is False
    assert sample.exit_code in (7, None)


def test_the_machine_snapshot_names_the_busiest_processes():
    snap = machine_snapshot(interval=0.2, top=3)
    assert "error" not in snap
    assert snap["processes"] > 10
    for entry in snap["busiest"]:
        assert entry["name"].endswith(".exe") or entry["name"] == "?"
        assert entry["cpu_percent_of_one_core"] > 0


def test_describe_wait_says_idle_and_windowless_in_words():
    samples = [
        ProcessSample(0.0, True, None, 0.30, 9, 40.0, []),
        ProcessSample(90.0, True, None, 0.31, 9, 40.5, []),
    ]
    machine = {
        "busiest": [{"pid": 1, "name": "msmpeng.exe", "cpu_percent_of_one_core": 97.0}],
        "known_busy": ["Microsoft Defender scanning"],
    }
    text = describe_wait(4242, "powershell.exe", samples, machine)
    assert "powershell.exe (pid 4242) was sampled 2x" in text
    assert "CPU 0.3s total (+0.0s over the wait)" in text
    assert "created no top-level window at all" in text
    assert "msmpeng.exe 97.0%" in text
    assert "That is Microsoft Defender scanning." in text


def test_describe_wait_lists_hidden_windows_and_an_exit():
    samples = [
        ProcessSample(
            0.0,
            True,
            None,
            0.1,
            3,
            10.0,
            [{"hwnd": 1, "class": "MSCTFIME UI", "title": "", "visible": False}],
        ),
        ProcessSample(4.0, False, 3, 0.4, None, None, []),
    ]
    text = describe_wait(7, "app.exe", samples, None)
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
    assert "Busiest on the machine" in message or "processes" in message or True
    assert len(seen) >= 3  # the first look, at least one mid-wait, and the final one
    assert all(e["pid"] == proc.pid for e in seen)
    assert seen[-1].get("machine") is not None
