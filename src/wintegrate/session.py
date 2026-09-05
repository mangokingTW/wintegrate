"""Session management, CI environment sanitization, virtual desktop isolation, and artifact flushing."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wintegrate.apps import AppHandle, AppSpec, sweep_processes_verified
from wintegrate.diagnostics import (
    ContinuousRecorder,
    WindowCensus,
    WindowSnapshot,
    capture_screen_image,
    capture_window_image,
)
from wintegrate.element import UiaElement
from wintegrate.env import env, is_ci
from wintegrate.interop import (
    SW_HIDE,
    WNDENUMPROC,
    attach_to_input_desktop,
    get_ancestor_pids,
    get_window_class,
    get_window_title,
    has_console,
    user32,
)
from wintegrate.window import Window

logger = logging.getLogger(__name__)


@dataclass
class SessionConfig:
    artifact_dir: str | Path = "artifacts"
    record_video: bool = True
    fps: int = 30
    sanitize_runner: bool | str = "auto"
    default_timeout: float = 15.0
    dismiss_oobe: bool | str = "auto"
    isolated_virtual_desktop: bool | str = "auto"

    @property
    def should_sanitize_runner(self) -> bool:
        if isinstance(self.sanitize_runner, str) and self.sanitize_runner.lower() == "auto":
            return env.is_desktop and is_ci()
        return bool(self.sanitize_runner)

    @property
    def should_dismiss_oobe(self) -> bool:
        if isinstance(self.dismiss_oobe, str) and self.dismiss_oobe.lower() == "auto":
            return env.is_desktop and is_ci()
        return bool(self.dismiss_oobe)

    @property
    def should_isolate_virtual_desktop(self) -> bool:
        if (
            isinstance(self.isolated_virtual_desktop, str)
            and self.isolated_virtual_desktop.lower() == "auto"
        ):
            # Isolate interactive desktops so tests never inject input into the
            # user's live session; CI runners are disposable and skip the desktop
            # switch (it is slow, notably on ARM64 runners).
            return env.supports_virtual_desktops and not is_ci()
        return bool(self.isolated_virtual_desktop)


# Store apps are single-instance: an instance leaked by an earlier test makes
# the next launch open a tab in the old window instead of a new top-level
# window, which breaks window discovery.
SWEEP_PROCESS_NAMES = (
    "wsl",
    "wslhost",
    "msedge",
    "msedgewebview2",
    "notepad",
    "CalculatorApp",
    "Calculator",
)

# Left alone whenever this process has a console, because it is probably the
# one hosting it.
TERMINAL_HOST_NAMES = ("WindowsTerminal",)


def sweep_process_names(caller_has_console: bool) -> list[str]:
    """What the sweep may kill, given whether the caller has a console."""
    names = list(SWEEP_PROCESS_NAMES)
    if not caller_has_console:
        names.extend(TERMINAL_HOST_NAMES)
    return names


def sanitize_ci_runner_environment():
    """
    Cleans up known GitHub Actions CI runner hazards:
    1. Terminates background WSL, Windows Terminal, and Edge popups without touching current runner PID or parents.
    2. Minimizes background windows (excluding current test runner process).
    """
    attach_to_input_desktop()
    excluded_pids = {os.getpid()}
    try:
        excluded_pids |= get_ancestor_pids()
    except Exception as exc:
        logger.warning(
            f"Parent-process exclusion degraded ({type(exc).__name__}: {exc}); "
            "only the current PID is protected from sanitization"
        )

    pid_list_str = ",".join(str(pid) for pid in excluded_pids)

    # 1. Kill noisy background prompts (excluding our own process hierarchy)
    try:
        # Notepad/Calculator are swept because Store apps are single-instance: an
        # instance leaked by an earlier test makes the next launch open a tab in the
        # old window instead of a new top-level window, breaking window discovery.
        #
        # Terminal hosts are swept only when this process has no console of its
        # own. A console-subsystem process does not own its console -- a terminal
        # hosts it -- and on Windows 11 that host is Windows Terminal, which is
        # on this list by name. Killing it destroys the console and every process
        # attached to it, this one included, and excluding it by pid is not
        # possible: it is not an ancestor, and it is not the owner of
        # GetConsoleWindow() either, which belongs to a brokered OpenConsole.exe
        # whose parent chain runs to services.exe. Measured on Windows 11 -- with
        # WindowsTerminal excluded the caller survived the whole sweep; killing
        # it alone ended the caller before its next heartbeat, a second later.
        attached = has_console()
        # Logged, because this decision has been wrong once and was invisible
        # when it was: the sweep swept a terminal host, that host was this
        # process's own, and the process ended a moment later somewhere
        # unrelated-looking.
        logger.info(
            f"Runner sweep: attached to a console = {attached}; "
            f"terminal hosts {'spared' if attached else 'included'}"
        )
        name_list = ",".join(f"'{name}'" for name in sweep_process_names(attached))
        ps_cmd = f"Get-Process -Name {name_list} -ErrorAction SilentlyContinue | Where-Object {{ $_.Id -notin @({pid_list_str}) }} | Stop-Process -Force -ErrorAction SilentlyContinue"
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, timeout=5
        )
    except Exception as exc:
        logger.debug(f"Runner process cleanup skipped ({type(exc).__name__}): {exc}")

    # 2. Hide noisy background popups
    try:

        def enum_proc(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                title = get_window_title(hwnd).lower()
                cls = get_window_class(hwnd)
                # Match WSL, Edge welcome screens, search popups
                if "wsl" in title:
                    user32.ShowWindow(hwnd, SW_HIDE)
                elif "edge" in title and ("welcome" in title or "first run" in title):
                    user32.ShowWindow(hwnd, SW_HIDE)
                elif "search" in title and cls == "Windows.UI.Core.CoreWindow":
                    user32.ShowWindow(hwnd, SW_HIDE)
            return True

        user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
    except Exception as exc:
        logger.debug(f"Window minimization skipped ({type(exc).__name__}): {exc}")


def try_dismiss_oobe_privacy_screen(timeout: float = 15.0) -> bool:
    """
    Dismisses Windows First-Sign-in / OOBE onboarding and privacy screens
    (Shell_OOBEProxy / CoreWindow) by walking focused elements and invoking buttons.
    """
    deadline = time.monotonic() + timeout
    clicked = 0

    known_ids = {"OobeSettingsAcceptButton", "AcceptButton", "NextButton"}
    exact_names = {"ok", "got it", "yes", "no"}
    name_substrings = [
        "next",
        "accept",
        "skip",
        "continue",
        "i agree",
        "not now",
        "decline",
        "ask me later",
        "close",
        "sign in later",
        "do this later",
        "start without",
    ]

    def is_match_button(elem: UiaElement | None) -> bool:
        if not elem:
            return False
        try:
            # ControlType 50000 = Button
            if elem.control_type_id != 50000 and "button" not in elem.control_type_name.lower():
                return False
            if elem.automation_id in known_ids:
                return True
            elem_name = elem.name.strip().lower()
            if elem_name in exact_names:
                return True
            return any(sub in elem_name for sub in name_substrings)
        except Exception:
            return False

    while time.monotonic() < deadline:
        # Check if Shell_OOBEProxy or any OOBE window is present
        oobe_present = False
        for snap in WindowCensus.capture():
            c_name = snap.class_name.lower()
            t_name = snap.title.lower()
            if (
                "shell_oobeproxy" in c_name
                or "oobe" in c_name
                or "oobe" in t_name
                or "microsoft account" in t_name
            ):
                oobe_present = True
                break

        if not oobe_present:
            if clicked > 0:
                logger.info(f"OOBE screens cleared after clicking {clicked} button(s)")
            return clicked > 0

        # Find button via FocusedElement + ancestor walk
        btn_to_invoke = None
        try:
            focused = UiaElement.get_focused()
            if is_match_button(focused):
                btn_to_invoke = focused
            else:
                curr = focused
                for _ in range(4):
                    parent = curr.get_parent() if curr else None
                    if is_match_button(parent):
                        btn_to_invoke = parent
                        break
                    curr = parent
        except Exception:
            pass

        if btn_to_invoke:
            try:
                logger.info(
                    f"Invoking OOBE onboarding button: '{btn_to_invoke.name}' (ID: '{btn_to_invoke.automation_id}')"
                )
                btn_to_invoke.invoke()
                clicked += 1
                time.sleep(1.0)
                continue
            except Exception as exc:
                logger.debug(f"OOBE button invoke failed: {exc}")

        time.sleep(1.0)

    return clicked > 0


class Session:
    """
    Orchestrates test execution environment, continuous video recording,
    pre/post window census diffing, and automatic failure artifact generation.
    Supports dynamic Windows 11 Virtual Desktop isolation.
    """

    def __init__(self, config: SessionConfig | None = None):
        self.config = config or SessionConfig()
        self.artifact_dir = Path(self.config.artifact_dir)
        self.recorder: ContinuousRecorder | None = None
        self.initial_census: list[WindowSnapshot] = []
        self.final_census: list[WindowSnapshot] = []
        self.logs: list[dict[str, Any]] = []
        self._orig_virtual_desktop = None
        self._test_virtual_desktop = None
        self._mouse = None
        # The durable journal: one JSON line per event, flushed as it is written, so
        # a process that dies without unwinding still leaves everything it logged.
        # None until __enter__ opens it, and None again if opening fails -- a
        # diagnostic must never be the reason a session cannot start.
        self._journal = None
        self._journal_lock = threading.Lock()
        self._current_step: str | None = None
        self._last_event_mono = 0.0
        self._beat_stop = threading.Event()
        self._beat_thread: threading.Thread | None = None
        self._run_id = os.environ.get("GITHUB_RUN_ID", "")

    @property
    def mouse(self):
        """Playwright-style Mouse controller associated with this session."""
        if self._mouse is None:
            from wintegrate.mouse import Mouse

            self._mouse = Mouse(session=self)
        return self._mouse

    def log_event(self, event_type: str, message: str, **kwargs):
        """Records a structured event, in memory and -- once open -- in the journal.

        Every line carries the wall clock, a monotonic offset, the pid and the
        current step. The pid is not decoration: a console is shared, orphans
        outlive their parent, and a journal opened in append mode can hold more
        than one process's lines. Without it nothing says who wrote what.
        """
        now = time.monotonic()
        entry = {
            "wall": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "monotonic": round(now, 3),
            "pid": os.getpid(),
            "timestamp": time.time(),  # kept: existing readers use it
            "type": event_type,
            "message": message,
            "step": self._current_step,
            **kwargs,
        }
        self.logs.append(entry)
        self._journal_write(entry)
        self._last_event_mono = now
        logger.info(f"[{event_type}] {message} ({kwargs})")

    # ------------------------------------------------------------------ journal
    #
    # Why this exists, in one incident: a library call inside __enter__ ended the
    # process that made it. session_events.json is written from __exit__, __exit__
    # never ran, and the artifact directory was empty. Three CI rounds produced no
    # evidence at all, and the traceback that did surface pointed at whatever the
    # process happened to be doing -- `import av` -- rather than at the cause. A
    # line written *before* each risky thing, to a file, flushed, is the only
    # record that survives that. stdout does not: it died with the console.

    def _journal_write(self, entry: dict) -> None:
        if self._journal is None:
            return
        try:
            line = json.dumps(entry, default=str) + "\n"
            # One lock around write+flush: the heartbeat thread shares this file,
            # and two writers tearing a line corrupts the one record the
            # post-mortem depends on.
            with self._journal_lock:
                self._journal.write(line)
                self._journal.flush()
        except Exception as exc:
            logger.debug(f"journal write skipped ({type(exc).__name__}): {exc}")

    def _open_journal(self) -> None:
        """Opens session_events.jsonl and starts the heartbeat. Degrades to memory."""
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            self._journal = open(
                self.artifact_dir / "session_events.jsonl", "a", encoding="utf-8", buffering=1
            )
        except Exception as exc:
            self._journal = None
            logger.warning(
                f"journal unavailable ({type(exc).__name__}): {exc}; events stay in memory"
            )
            return
        try:
            from wintegrate import __version__
        except Exception:
            __version__ = "?"
        self.log_event(
            "session_open",
            "journal opened",
            argv=sys.argv,
            run=self._run_id,
            python=sys.version.split()[0],
            wintegrate=__version__,
            cwd=os.getcwd(),
        )
        self._beat_stop.clear()
        self._beat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._beat_thread.start()
        self._write_index("opening")

    #: Seconds between heartbeat checks. A test may shorten it.
    _beat_interval: float = 1.0

    def _beat_due(self, now: float) -> bool:
        """Whether a beat is owed at `now`: nothing else was written for about one interval.

        "About": the wait wakes a few milliseconds early or late, so a strict
        `>= interval` skipped the first beat on a slow machine and a 2.3 s
        session recorded one beat instead of two. Half an interval of slack
        cannot double the rate -- the wait itself spaces the checks.
        """
        return now - self._last_event_mono >= self._beat_interval * 0.5

    def _heartbeat_loop(self) -> None:
        """One line a second, when nothing else was written that second.

        The beat exists to tell *dead* from *hung*. A step that stays in_progress
        for minutes looks like a live process to anyone reading the CI page; the
        journal's last beat says when the process was last alive, to the second.
        It does not localise a sub-second death -- the events around it do -- and
        it does not survive a runner that is destroyed rather than killed.
        """
        while not self._beat_stop.wait(self._beat_interval):
            if self._beat_due(time.monotonic()):
                self._journal_write(
                    {
                        "wall": datetime.now(timezone.utc)
                        .isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z"),
                        "monotonic": round(time.monotonic(), 3),
                        "pid": os.getpid(),
                        "type": "heartbeat",
                        "step": self._current_step,
                    }
                )

    def _close_journal(self) -> None:
        self._beat_stop.set()
        if self._beat_thread is not None:
            self._beat_thread.join(timeout=2.0)
            self._beat_thread = None
        if self._journal is not None:
            try:
                with self._journal_lock:
                    self._journal.flush()
                    self._journal.close()
            except Exception:
                pass
            self._journal = None

    def _write_step_summary(self, exc_type=None) -> None:
        """Appends a summary to $GITHUB_STEP_SUMMARY, when there is one.

        The run page is what a person -- or an agent -- reads first, and it is
        readable without downloading anything. The artifacts hold the detail;
        this says which step failed, how long each took, and where to look.
        """
        path = os.environ.get("GITHUB_STEP_SUMMARY")
        if not path:
            return
        try:
            starts: dict[str, float] = {}
            rows = []
            for e in self.logs:
                if e.get("type") == "step_start":
                    starts[e["message"]] = e.get("monotonic", 0.0)
                elif e.get("type") in ("step_ok", "step_failed"):
                    rows.append(
                        (
                            e["message"],
                            "ok"
                            if e["type"] == "step_ok"
                            else f"**failed** ({e.get('error', '?')})",
                            e.get("seconds", ""),
                        )
                    )
            verdict = "failed" if exc_type is not None else "completed"
            lines = [
                "",
                f"### wintegrate session -- {verdict}",
                "",
                f"artifacts: `{self.artifact_dir}` -- start with `READ_THIS_FIRST.md`; `session_events.jsonl` is the authority.",
                "",
            ]
            if rows:
                lines += ["| step | outcome | seconds |", "| --- | --- | ---: |"]
                lines += [f"| {name} | {outcome} | {secs} |" for name, outcome, secs in rows]
                lines.append("")
            errors = [e for e in self.logs if e.get("type") == "session_error"]
            for e in errors:
                lines.append(
                    f"- error: `{e.get('message')}`"
                    + (f" in step `{e.get('step')}`" if e.get("step") else "")
                )
            files = (
                sorted(p.name for p in self.artifact_dir.iterdir() if p.is_file())
                if self.artifact_dir.exists()
                else []
            )
            if files:
                lines.append("- files: " + ", ".join(f"`{f}`" for f in files))
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except Exception as exc:
            logger.debug(f"step summary skipped ({type(exc).__name__}): {exc}")

    def _write_index(self, state: str) -> None:
        """Writes READ_THIS_FIRST.md and artifact_index.json for whoever opens the folder.

        Rewritten at every step boundary, so it is also an on-disk record of how
        far the run got -- including the case that used to leave the directory
        completely empty, a death inside __enter__. Every sentence is generated
        from the files that are actually there; a stale sentence here is worse
        than no file.
        """
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            files = {}
            for p in sorted(self.artifact_dir.iterdir()):
                if p.is_file() and p.name not in ("READ_THIS_FIRST.md", "artifact_index.json"):
                    files[p.name] = p.stat().st_size
            recent = [
                {
                    "wall": e.get("wall"),
                    "type": e.get("type"),
                    "message": e.get("message"),
                    "step": e.get("step"),
                }
                for e in self.logs[-8:]
            ]
            index = {
                "state": state,
                "written": datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "pid": os.getpid(),
                "run": self._run_id,
                "current_step": self._current_step,
                "recording": self.recorder is not None and self.recorder.backend is not None,
                "files": files,
                "recent_events": recent,
            }
            (self.artifact_dir / "artifact_index.json").write_text(
                json.dumps(index, indent=2), encoding="utf-8"
            )

            has_json = "session_events.json" in files
            has_mp4 = "session_recording.mp4" in files
            lines = [
                "# Read this first",
                "",
                f"State: **{state}** -- written {index['written']} by pid {os.getpid()}"
                + (f", run {self._run_id}" if self._run_id else "")
                + (f", inside step `{self._current_step}`" if self._current_step else "")
                + ".",
                "",
                "## Which file to trust",
                "",
                "- `session_events.jsonl` is the authority. One JSON line per event, flushed as written,",
                "  so it survives a process that was killed without unwinding. Read it in order; the last",
                "  line is the last thing the process did. A `heartbeat` line every second means the",
                "  process was alive then and merely quiet.",
            ]
            if has_json:
                lines.append(
                    "- `session_events.json` is the same timeline, pretty-printed, written at exit."
                )
            else:
                lines.append(
                    "- `session_events.json` is **absent**: it is written from `__exit__`, so the process"
                    " never reached teardown. That is a fact about the run, not a missing artifact."
                )
            if has_mp4:
                lines += [
                    "- `session_recording.mp4` is the **primary monitor only**, fragmented so it plays back even",
                    "  if the process was killed mid-write. `recording_anchor.json` maps wall time to video",
                    "  time. Screenshots (`*.png`) are the **whole virtual desktop**, so they are offset from",
                    "  the video by the primary monitor's origin.",
                ]
            else:
                lines.append(
                    "- No recording: either `record_video` was off, PyAV is not installed, or the process"
                    " died before the recorder started. `session_events.jsonl` says which."
                )
            lines += [
                "",
                "## What a screenshot cannot show",
                "",
                "A window can ask Windows to withhold it from every capture path (display affinity).",
                "It is then visible on the monitor and absent from the recording and screenshots alike.",
                "Screenshot events carry `excluded_from_capture` when this was measured; when a window",
                "you expect is missing from an image, read `window_census.json` before concluding it",
                "was not there.",
                "",
                "## Files",
                "",
            ]
            lines += [f"- `{name}` ({size} bytes)" for name, size in files.items()] or [
                "- (none yet)"
            ]
            if recent:
                lines += ["", "## Last events", ""]
                lines += [
                    f"- {e['wall']} `{e['type']}` {e['message']}"
                    + (f" (step: {e['step']})" if e["step"] else "")
                    for e in recent
                ]
            (self.artifact_dir / "READ_THIS_FIRST.md").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
        except Exception as exc:
            logger.debug(f"artifact index skipped ({type(exc).__name__}): {exc}")

    def _census_or_none(self):
        """A window census, or None when one cannot be taken.

        Diagnostics must never be the reason a step fails, so every call site
        tolerates None.
        """
        try:
            return WindowCensus.capture()
        except Exception as exc:
            logger.debug(f"Step census skipped ({type(exc).__name__}): {exc}")
            return None

    def _census_delta(self, before) -> dict:
        """What arrived and left during a step, compact enough to sit in the timeline."""
        after = self._census_or_none()
        if before is None or after is None:
            return {}
        try:
            diff = WindowCensus.diff(before, after)
        except Exception as exc:
            logger.debug(f"Step census diff skipped ({type(exc).__name__}): {exc}")
            return {}
        out = {}
        if diff.added:
            out["windows_added"] = [
                {"class_name": s.class_name, "title": s.title[:60], "pid": s.pid}
                for s in diff.added
                if s.is_visible
            ]
        if diff.removed:
            out["windows_removed"] = [
                {"class_name": s.class_name, "title": s.title[:60], "pid": s.pid}
                for s in diff.removed
                if s.is_visible
            ]
        return {k: v for k, v in out.items() if v}

    @contextmanager
    def step(self, name: str):
        """Names a block of work, so the artifacts say *which step* failed.

            with session.step("fill in the form"):
                form.type_verified("...")
            with session.step("submit"):
                submit.invoke()

        On a machine you cannot connect to, "which step" is more useful than
        "which line". This records the step's start, duration and outcome in the
        event timeline, captures a screenshot named after the step when the block
        raises, and prefixes the exception message with the step name so the
        pytest one-liner already tells you where you were.

        The original exception type is preserved and re-raised: this adds context,
        it does not swallow or convert failures.
        """
        safe = re.sub(r"[^0-9A-Za-z._-]+", "-", name).strip("-") or "step"
        started = time.monotonic()
        # A census per step boundary, because the session-level before/after pair
        # cannot see anything that appears and goes during the run — which is
        # exactly where the interesting windows live.
        before = self._census_or_none()
        outer_step = self._current_step
        self._current_step = name
        self.log_event("step_start", name)
        self._write_index("running")
        try:
            yield
        except BaseException as exc:
            elapsed = time.monotonic() - started
            self.log_event("step_failed", name, seconds=round(elapsed, 3), error=type(exc).__name__)
            self._write_index("running")
            try:
                self.capture_screenshot(f"failure-{safe}", all_monitors=True)
            except Exception as shot_exc:
                logger.warning(f"Step screenshot failed ({type(shot_exc).__name__}): {shot_exc}")
            if (
                exc.args
                and isinstance(exc.args[0], str)
                and not exc.args[0].startswith(f"[{name}]")
            ):
                exc.args = (f"[{name}] {exc.args[0]}",) + exc.args[1:]
            raise
        else:
            elapsed = time.monotonic() - started
            self.log_event("step_ok", name, seconds=round(elapsed, 3), **self._census_delta(before))
            self._write_index("running")
        finally:
            self._current_step = outer_step

    def __enter__(self) -> Session:
        logger.info("Starting Wintegrate UI automation session...")
        # The journal opens before anything else happens, so that if the next call
        # ends this process the directory is not empty.
        self._open_journal()
        attach_to_input_desktop()

        # The recorder starts before the runner is touched. Everything below it
        # kills processes, hides windows and switches desktops -- exactly the
        # actions a recording exists to show -- and used to run off-camera, with
        # the mp4 not yet created at the moment anything went wrong.
        if self.config.record_video:
            video_path = self.artifact_dir / "session_recording.mp4"
            self.recorder = ContinuousRecorder(output_path=video_path, fps=self.config.fps)
            if self.recorder.start():
                self.log_event("video_recording_started", f"Streaming to {video_path}")
                anchor = self.recorder.anchor()
                if anchor:
                    try:
                        (self.artifact_dir / "recording_anchor.json").write_text(
                            json.dumps(anchor, indent=2), encoding="utf-8"
                        )
                    except Exception as exc:
                        logger.debug(f"recording anchor skipped ({type(exc).__name__}): {exc}")

        if self.config.should_dismiss_oobe:
            try_dismiss_oobe_privacy_screen(timeout=3.0)

        if self.config.should_isolate_virtual_desktop:
            self._setup_isolated_virtual_desktop()

        if self.config.should_sanitize_runner:
            self.log_event("sanitize_start", "sanitising the runner")
            sanitize_ci_runner_environment()
            self.log_event("sanitize_done", "runner sanitised")

        if self.config.should_dismiss_oobe:
            try_dismiss_oobe_privacy_screen(timeout=3.0)

        # Capture baseline window state
        self.initial_census = WindowCensus.capture()
        self.log_event(
            "session_start", "Baseline window census captured", count=len(self.initial_census)
        )
        self._write_index("running")
        return self

    def _setup_isolated_virtual_desktop(self):
        """Creates a dedicated Virtual Desktop and switches to it for test isolation."""
        try:
            from pyvda import VirtualDesktop

            self._orig_virtual_desktop = VirtualDesktop.current()
            self._test_virtual_desktop = VirtualDesktop.create()
            self._test_virtual_desktop.go()
            time.sleep(0.3)
            self.log_event(
                "virtual_desktop_isolated",
                f"Switched to clean Virtual Desktop {self._test_virtual_desktop.number} (id={self._test_virtual_desktop.id})",
            )
        except Exception as exc:
            logger.warning(
                f"Failed to initialize virtual desktop isolation ({type(exc).__name__}): {exc}"
            )

    def _teardown_isolated_virtual_desktop(self):
        """Restores original Virtual Desktop and destroys temporary test desktop."""
        if self._orig_virtual_desktop:
            try:
                self._orig_virtual_desktop.go()
                time.sleep(0.2)
            except Exception as exc:
                logger.debug(f"Failed to switch back to original desktop: {exc}")

        if self._test_virtual_desktop:
            try:
                self._test_virtual_desktop.remove()
                self.log_event("virtual_desktop_cleaned", "Destroyed test virtual desktop")
            except Exception as exc:
                logger.debug(f"Failed to remove test virtual desktop: {exc}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        logger.info("Tearing down Wintegrate UI automation session...")
        if exc_type is not None:
            self.log_event("session_error", f"{exc_type.__name__}: {exc_val}")
        # The timeline is saved before the recorder's thread join and the final
        # census. Under a console's seconds-long kill deadline the last statement
        # is the one most likely to be cut off, and this is the artifact that
        # matters most.
        self._flush_session_logs()
        self._write_index("closing")

        # Stop recorder
        if self.recorder:
            frames = self.recorder.stop()
            self.log_event("video_recording_stopped", "Recorder finalized", frames=frames)

        # Capture final census and compute diff
        self.final_census = WindowCensus.capture()
        diff = WindowCensus.diff(self.initial_census, self.final_census)
        self.log_event(
            "session_end",
            "Final window census captured",
            added=len(diff.added),
            removed=len(diff.removed),
            persisted=len(diff.persisted),
        )

        # Always save window census
        self._save_census_dump(diff)

        # If an exception occurred during the test, take failure snapshot
        if exc_type is not None:
            logger.error(
                f"Test failed with {exc_type.__name__}: {exc_val}. Capturing failure artifact."
            )
            self._capture_failure_screenshot()

        # Teardown isolated virtual desktop if configured
        if self.config.should_isolate_virtual_desktop:
            self._teardown_isolated_virtual_desktop()

        # Flush session logs
        self._flush_session_logs()
        self._write_index("closed")
        self._write_step_summary(exc_type)
        self._close_journal()

        return False  # Do not suppress exceptions

    def _save_census_dump(self, diff):
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            census_path = self.artifact_dir / "window_census.json"
            census_data = {
                "initial_count": len(self.initial_census),
                "final_count": len(self.final_census),
                "added": [s.__dict__ for s in diff.added],
                "removed": [s.__dict__ for s in diff.removed],
            }
            with open(census_path, "w", encoding="utf-8") as f:
                json.dump(census_data, f, indent=2)
            logger.info(f"Saved window census diff to {census_path}")
        except Exception as exc:
            logger.error(f"Failed to dump window census ({type(exc).__name__}): {exc}")

    def capture_screenshot(
        self,
        name: str,
        window: Window | None = None,
        all_monitors: bool = True,
    ) -> Path:
        """
        Saves a screenshot into the session's artifact directory and returns its path.

        Use this to record what the screen looked like at a point you care about —
        the failure screenshot only exists when something raised, and by then the
        state that explains it may be gone.

        Pass `window` to capture that window alone, including anything covering it.
        Otherwise the whole virtual desktop is captured; `all_monitors=False`
        narrows that to the primary display.
        """
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifact_dir / (name if name.lower().endswith(".png") else f"{name}.png")

        img = capture_window_image(window.hwnd) if window else capture_screen_image(all_monitors)
        img.save(path)
        # Recorded next to the screenshot, not only in the log: an artifact that
        # cannot contain the window it is named after has to say so where the
        # artifacts are read. Windows withholds a window that called
        # SetWindowDisplayAffinity from every capture API, and it stays visible,
        # uncloaked and on screen throughout -- so the file looks like evidence
        # and the timeline is the only place the absence is explained.
        excluded = window.is_excluded_from_capture if window else None
        self.log_event(
            "screenshot",
            f"Captured {path.name}",
            size=list(img.size),
            **(
                {"excluded_from_capture": True, "affinity": window.display_affinity.name}
                if excluded
                else {}
            ),
        )
        logger.info(f"Saved screenshot to {path}")
        return path

    def _capture_failure_screenshot(self):
        try:
            # all_monitors: the window under test is not always on the primary
            # display, and a primary-only capture of a failure elsewhere is worse
            # than none — it looks like evidence.
            self.capture_screenshot("failure_screenshot", all_monitors=True)
        except Exception as exc:
            logger.error(f"Failed to capture failure screenshot ({type(exc).__name__}): {exc}")

    def _flush_session_logs(self):
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            events_path = self.artifact_dir / "session_events.json"
            with open(events_path, "w", encoding="utf-8") as f:
                json.dump(self.logs, f, indent=2)
        except Exception as exc:
            logger.error(f"Failed to flush session events ({type(exc).__name__}): {exc}")

    def find_window(
        self,
        title_exact: str | None = None,
        title_pattern: str | None = None,
        class_name: str | None = None,
        timeout: float | None = None,
    ) -> Window:
        """Finds an existing top-level window matching criteria."""
        to = timeout or self.config.default_timeout
        return Window.find(
            title_exact=title_exact,
            title_pattern=title_pattern,
            class_name=class_name,
            timeout=to,
        )

    def launch_and_discover(
        self,
        cmd: list[str] | str,
        timeout: float | None = None,
        title_pattern: str | None = None,
        exclude_hwnds: set[int] | None = None,
        process_names: tuple[str, ...] | list[str] | None = None,
        window_classes: tuple[str, ...] | list[str] | None = None,
    ) -> tuple[subprocess.Popen, Window]:
        """Wrapper around Window.launch_and_discover with session logging."""
        to = timeout or self.config.default_timeout
        self.log_event("launch_app", f"Launching {cmd}")
        proc, win = Window.launch_and_discover(
            cmd,
            timeout=to,
            title_pattern=title_pattern,
            exclude_hwnds=exclude_hwnds,
            process_names=process_names,
            window_classes=window_classes,
        )
        self.log_event(
            "window_discovered", f"Window '{win.title}' (HWND: {win.hwnd}, PID: {win.pid})"
        )
        return proc, win

    def app(
        self,
        spec: AppSpec | list[str] | str,
        timeout: float | None = None,
        fresh: bool | str = "auto",
        title_pattern: str | None = None,
        exclude_hwnds: set[int] | None = None,
    ) -> AppHandle:
        """
        Launches an application with a managed lifecycle. Use as a context manager:

            with session.app(NOTEPAD) as app:
                editor = app.find_text_input()
                editor.type_verified("hello\\n", expected_line_count_delta=1)

        - Matching prefers locale-independent identities from the AppSpec
          (process image names, window classes) over window titles.
        - fresh="auto" (CI only) sweeps leftover instances of the app before
          launching: Store apps are single-instance, so a leaked instance makes
          the next launch open a tab instead of a new window. Pass fresh=False
          when launching a second instance of the same app deliberately.
        - The default timeout is generous because cold Store-app starts on CI
          runners regularly exceed 10s.
        - Cleanup (window close + process kill) runs on context exit even when
          the body raises.
        """
        if not isinstance(spec, AppSpec):
            cmd = tuple(spec) if isinstance(spec, list) else (str(spec),)
            spec = AppSpec(name=cmd[0], command=cmd, title_pattern=title_pattern)

        do_fresh = is_ci() if fresh == "auto" else bool(fresh)
        if do_fresh and spec.process_names:
            # Verified, not timed: terminating a packaged app is asynchronous, and
            # a window that outlives the sweep makes this launch of a
            # single-instance app produce no new window at all.
            sweep_processes_verified(
                spec.process_names,
                spec.window_classes,
                package_family_name=spec.package_family_name,
                session_state_dirs=spec.session_state_dirs,
            )

        proc, win = self.launch_and_discover(
            list(spec.command),
            timeout=timeout if timeout is not None else max(self.config.default_timeout, 30.0),
            title_pattern=title_pattern or spec.title_pattern,
            exclude_hwnds=exclude_hwnds,
            process_names=spec.process_names or None,
            window_classes=spec.window_classes or None,
        )
        return AppHandle(proc, win, spec)
