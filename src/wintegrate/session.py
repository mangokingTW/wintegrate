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
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
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
    set_launch_output_dir,
)
from wintegrate.element import UiaElement
from wintegrate.env import env, is_ci
from wintegrate.exceptions import ConsoleHostEndedError
from wintegrate.interop import (
    SW_HIDE,
    SW_SHOWNA,
    WNDENUMPROC,
    attach_to_input_desktop,
    console_client_pids,
    get_ancestor_pids,
    get_foreground_window,
    get_process_table,
    get_window_class,
    get_window_pid,
    get_window_title,
    has_console,
    kernel32,
    protected_pids,
    terminate_pid,
    user32,
)
from wintegrate.sweep import (
    InterventionResult,
    KillPlan,
    build_kill_plan,
    dry_run_requested,
    execute_kill_plan,
    hide_reason,
    restore_targets,
    write_plan,
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


def console_host_verdict(
    preflight: dict[str, Any],
    console_attached_now: bool | None,
    console_clients_now: tuple[int, ...] | None,
    exc: BaseException,
    plan: KillPlan | None,
) -> dict[str, Any] | None:
    """Whether `exc` is the console host being destroyed, on evidence. None when it is not.

    Pure, so the rule is testable without a desktop. The rule is differential: the
    console probes answered at preflight (`console_attached` True, a non-empty
    client list) and answer nothing now (False, empty, or the probe itself failed
    -- passed in as None). A process that never had a console cannot produce a
    verdict, so the common path cannot manufacture one; and an exception that
    already is the verdict is left alone.
    """
    if isinstance(exc, (ConsoleHostEndedError, GeneratorExit)):
        return None
    clients_before = preflight.get("console_clients") or []
    had_console = bool(preflight.get("console_attached")) and bool(clients_before)
    gone = not console_attached_now and not console_clients_now
    if not (had_console and gone):
        return None
    ended: list[str] = []
    ended_pids: list[int] = []
    if plan is not None:
        names = {e.pid: e.name for e in plan.entries}
        for pid, result in plan.results.items():
            if result == "terminated":
                ended.append(names.get(int(pid), "?"))
                ended_pids.append(int(pid))
    peers = [c.get("name", "?") for c in clients_before if c.get("pid") != os.getpid()]
    return {
        "ended": sorted(set(ended)),
        "ended_pids": ended_pids,
        "peers": peers,
        "original": type(exc).__name__,
    }


def collect_preflight() -> dict[str, Any]:
    """What this process is, before it touches anything: written first, so a run that dies has it.

    Every probe is wrapped on its own; a probe that fails lands in `probe_errors`
    and never blocks the session. `warnings` holds the sentences worth reading
    first -- above all which processes share this console, because ending that
    console ends them: measured on a hosted runner, `python.exe`, `pwsh.exe` and
    `Runner.Worker.exe`, the last of which is the agent reporting the job.
    """
    out: dict[str, Any] = {"probe_errors": {}, "warnings": []}

    def probe(name: str, fn: Callable[[], Any]) -> Any:
        try:
            value = fn()
            out[name] = value
            return value
        except Exception as exc:
            out["probe_errors"][name] = f"{type(exc).__name__}: {exc}"
            return None

    import platform

    out["pid"] = os.getpid()
    out["python"] = sys.version.split()[0]
    out["machine"] = platform.machine()
    out["env"] = {
        k: os.environ.get(k) for k in ("GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", "RUNNER_ARCH", "CI")
    }
    table = probe("process_table_size", lambda: len(get_process_table()))
    names: dict[int, str] = {}
    if table is not None:
        try:
            names = {pid: image for pid, (_p, image) in get_process_table().items()}
        except Exception:
            names = {}

    def named(pids) -> list[dict[str, Any]]:
        return [{"pid": pid, "name": names.get(pid, "?")} for pid in pids]

    probe("ancestors", lambda: named(sorted(get_ancestor_pids() - {os.getpid()})))
    attached = probe("console_attached", has_console)
    probe("console_window", lambda: int(kernel32.GetConsoleWindow() or 0))
    peers = probe("console_clients", lambda: named(console_client_pids()))
    probe("protected", lambda: {str(pid): rel for pid, rel in protected_pids().items()})

    def fg() -> dict[str, Any]:
        hwnd = get_foreground_window()
        pid = get_window_pid(hwnd) if hwnd else 0
        return {
            "hwnd": hwnd,
            "class": get_window_class(hwnd) if hwnd else "",
            "title": get_window_title(hwnd) if hwnd else "",
            "pid": pid,
            "name": names.get(pid, "?"),
        }

    probe("foreground", fg)
    if peers:
        others = [p["name"] for p in peers if p["pid"] != os.getpid()]
        if others:
            out["warnings"].append(
                "ending this console ends: "
                + ", ".join(others)
                + " -- a sweep must not touch its host"
            )
    if attached and out.get("console_window") == 0:
        out["warnings"].append(
            "GetConsoleWindow=0 while attached to a console: a ConPTY. Any check that asks for the console"
            " window answers 'none' here and is wrong."
        )
    return out


def sanitize_ci_runner_environment(
    dry_run: bool | None = None,
    report_path: str | Path | None = None,
    journal: Callable[..., None] | None = None,
) -> KillPlan:
    """Cleans up known GitHub Actions runner hazards, against a plan written first.

    1. Ends the background processes on the sweep list -- WSL, Edge popups, Store
       apps left over from earlier tests, and terminal hosts when this process
       has no console of its own -- sparing every process this one depends on,
       by measured relation (`protected_pids`), never by name.
    2. Hides background windows that steal the foreground.

    The plan (`KillPlan`) is built from one process snapshot, written to
    `report_path` and handed to `journal` **before** the first kill, and executed
    per pid with `TerminateProcess` so each outcome is recorded. `dry_run`
    (default: `WINTEGRATE_SANITIZE_DRY_RUN`) builds and writes the plan and ends
    nothing -- the way to find out what a sweep would do to a machine before
    letting it. Returns the plan with results.
    """
    attach_to_input_desktop()
    dry = dry_run_requested(dry_run)

    degraded: str | None = None
    try:
        protected = protected_pids()
    except Exception as exc:
        # Fail closed on what can still be measured: self and ancestors. Said
        # out loud, because the console peers are the set that was lethal once.
        degraded = f"protected_pids failed ({type(exc).__name__}: {exc}); console peers unknown"
        logger.warning(f"Runner sweep: {degraded}")
        protected = {pid: "self or ancestor (degraded)" for pid in get_ancestor_pids()}

    attached = has_console()
    logger.info(
        f"Runner sweep: attached to a console = {attached}; "
        f"terminal hosts {'spared' if attached else 'included'}"
    )
    try:
        table = get_process_table()
    except Exception as exc:
        logger.warning(
            f"Runner sweep skipped: process snapshot failed ({type(exc).__name__}: {exc})"
        )
        table = {}
    plan = build_kill_plan(table, sweep_process_names(attached), protected, attached, dry, degraded)

    # On disk and in the journal before anything is ended: if the next call ends
    # this process, this is the record that says so.
    if report_path is not None:
        try:
            write_plan(plan, report_path)
        except Exception as exc:
            logger.warning(f"kill plan not written ({type(exc).__name__}): {exc}")
    if journal is not None:
        journal("kill_plan", plan.summary(), plan=plan.to_dict())
    logger.info(f"Runner sweep plan: {plan.summary()}")

    execute_kill_plan(plan, terminate_pid)
    if journal is not None and not dry:
        journal(
            "kill_result",
            ", ".join(f"{pid}: {r}" for pid, r in plan.results.items()) or "nothing to kill",
            results=plan.results,
        )
    if report_path is not None and not dry:
        try:
            write_plan(plan, report_path)  # now with results
        except Exception:
            pass

    # 2. Hide noisy background popups -- recorded, verified, and undone by the
    # session that did it. Three ShowWindow calls used to run here recording
    # nothing: no list of what was hidden, no check that the hide took (the
    # Start menu's CoreWindow ignores SW_HIDE, measured), and nothing to give
    # the windows back at the end.
    try:
        plan.foreground_before_hide = _describe_foreground(table)
        plan.interventions = hide_noisy_windows(table)
        plan.foreground_after_hide = _describe_foreground(table)
        if journal is not None:
            journal(
                "hide_result",
                plan.interventions_summary(),
                interventions=[asdict(i) for i in plan.interventions],
                foreground_before=plan.foreground_before_hide,
                foreground_after=plan.foreground_after_hide,
            )
        if report_path is not None and not dry:
            try:
                write_plan(plan, report_path)  # now with the interventions
            except Exception:
                pass
    except Exception as exc:
        logger.debug(f"Window hiding skipped ({type(exc).__name__}): {exc}")
    return plan


def _describe_foreground(table: dict[int, tuple[int, str]] | None = None) -> dict[str, Any]:
    hwnd = get_foreground_window()
    pid = get_window_pid(hwnd) if hwnd else 0
    name = (table or {}).get(pid, (0, "?"))[1] if pid else ""
    return {
        "hwnd": hwnd,
        "class": get_window_class(hwnd) if hwnd else "",
        "title": (get_window_title(hwnd) if hwnd else "")[:80],
        "pid": pid,
        "process": name,
    }


def hide_noisy_windows(
    table: dict[int, tuple[int, str]] | None = None, settle_seconds: float = 0.3
) -> list[InterventionResult]:
    """Hides the windows `hide_reason` names, and says for each whether the hide took.

    Intended state and observed state are both recorded: `ShowWindow(SW_HIDE)`
    returns the *previous* visibility, not success, so the only way to know a
    window is hidden is to ask again after a moment.
    """
    candidates: list[tuple[int, str, str, str]] = []

    def enum_proc(hwnd, _):
        try:
            if user32.IsWindowVisible(hwnd):
                cls = get_window_class(hwnd)
                title = get_window_title(hwnd)
                reason = hide_reason(cls, title)
                if reason:
                    candidates.append((hwnd, cls, title, reason))
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
    for hwnd, _cls, _title, _reason in candidates:
        user32.ShowWindow(hwnd, SW_HIDE)
    if candidates:
        time.sleep(settle_seconds)
    results = []
    for hwnd, cls, title, reason in candidates:
        pid = get_window_pid(hwnd)
        exists = bool(user32.IsWindow(hwnd))
        visible = bool(user32.IsWindowVisible(hwnd)) if exists else False
        results.append(
            InterventionResult(
                action="hide",
                hwnd=hwnd,
                class_name=cls,
                title=title[:80],
                process=(table or {}).get(pid, (0, "?"))[1],
                reason=reason,
                intended={"visible": False},
                observed={"exists": exists, "visible": visible},
                verified=exists and not visible,
            )
        )
    return results


def restore_hidden_windows(
    interventions: list[InterventionResult], settle_seconds: float = 0.3
) -> list[InterventionResult]:
    """Shows again what `hide_noisy_windows` hid and still holds hidden; records each."""
    targets = restore_targets(
        interventions,
        window_exists=lambda h: bool(user32.IsWindow(h)),
        window_visible=lambda h: bool(user32.IsWindowVisible(h)),
    )
    for i in targets:
        user32.ShowWindow(i.hwnd, SW_SHOWNA)
    if targets:
        time.sleep(settle_seconds)
    results = []
    for i in targets:
        exists = bool(user32.IsWindow(i.hwnd))
        visible = bool(user32.IsWindowVisible(i.hwnd)) if exists else False
        results.append(
            InterventionResult(
                action="restore",
                hwnd=i.hwnd,
                class_name=i.class_name,
                title=i.title,
                process=i.process,
                reason=f"hidden by this session ({i.reason})",
                intended={"visible": True},
                observed={"exists": exists, "visible": visible},
                verified=exists and visible,
            )
        )
    return results


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
        self.preflight: dict[str, Any] = {}
        self._phase: str | None = None
        self._phase_since: float | None = None
        self.kill_plan: KillPlan | None = None
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
                    f"- error `{e.get('signature') or '?'}`: {str(e.get('message'))[:300]}"
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
                elif p.is_dir() and p.name == "frames":
                    files["frames/"] = sum(f.stat().st_size for f in p.iterdir() if f.is_file())
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
            failures = [
                e for e in self.logs if e.get("type") in ("session_error", "console_host_ended")
            ]
            failure_lines: list[str] = []
            if failures:
                failure_lines = ["## Failure", ""]
                for e in failures:
                    sig = e.get("signature") or (
                        f"ConsoleHostEndedError[ended={'+'.join(e.get('ended') or [])}]"
                        if e.get("type") == "console_host_ended"
                        else "?"
                    )
                    failure_lines.append(f"- `{sig}` -- {str(e.get('message'))[:400]}")
                failure_lines += [
                    "",
                    "The signature is the class plus the facts that identify *this* failure; compare",
                    "it across runs before comparing counts.",
                    "",
                ]
            lines = [
                "# Read this first",
                "",
                f"State: **{state}** -- written {index['written']} by pid {os.getpid()}"
                + (f", run {self._run_id}" if self._run_id else "")
                + (f", inside step `{self._current_step}`" if self._current_step else "")
                + ".",
                "",
                *failure_lines,
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
            if "preflight.json" in files:
                lines.append(
                    "- `preflight.json` is what this process was before it touched anything: console"
                    " clients, protected pids, foreground. Its `warnings` are the sentences to read first."
                )
            frames_dir = self.artifact_dir / "frames"
            if frames_dir.is_dir():
                count = len(list(frames_dir.glob("*.png")))
                lines.append(
                    f"- `frames/` holds {count} frame(s) of the recording at the moments the timeline"
                    " names -- the window around the last `step_failed` first, then the tail. Each file"
                    " is named by video time and event; `frames/index.json` maps files to events and"
                    " lists the marks that did not fit the budget."
                )
            if "kill_plan.json" in files:
                lines.append(
                    "- `kill_plan.json` was written **before** the first kill. If `session_events.jsonl`"
                    " ends at `kill_plan` with no `kill_result`, the sweep ended this process: read the"
                    " plan's `kills` against `preflight.json`'s console clients."
                )
                if self.kill_plan is not None and self.kill_plan.interventions:
                    lines.append(
                        "  Its `interventions` list every window the sweep hid (and, at exit, showed"
                        " again), each with the state intended and the state re-measured a moment"
                        " later; `verified: false` means the window ignored the request."
                    )
            launched = sorted(
                f for f in files if f.startswith("launched_") and f.endswith((".out", ".err"))
            )
            if launched:
                lines.append(
                    "- `launched_NN.out` / `.err` hold what each process started through"
                    " `launch_and_discover` printed; a child that wrote an error and exited is the"
                    " usual reason no window appeared."
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
            # The windows that came and went during the *failing* step were being
            # computed for step_ok and thrown away here -- the only path that
            # matters. And the signature, so two failures with the same count stop
            # reading as the same problem.
            self.log_event(
                "step_failed",
                name,
                seconds=round(elapsed, 3),
                error=type(exc).__name__,
                signature=getattr(exc, "signature", type(exc).__name__),
                **self._census_delta(before),
            )
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
        # Children launched through Window.launch_and_discover write their
        # stdout/stderr here rather than inheriting this process's -- see
        # diagnostics.set_launch_output_dir.
        set_launch_output_dir(self.artifact_dir)
        # Preflight before anything is touched: what this process is, what shares
        # its console, what is in the foreground. On disk first, so a run that
        # dies in the next hundred lines still says what it was.
        self.preflight = collect_preflight()
        try:
            (self.artifact_dir / "preflight.json").write_text(
                json.dumps(self.preflight, indent=2, default=str), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug(f"preflight.json skipped ({type(exc).__name__}): {exc}")
        self.log_event(
            "preflight",
            "; ".join(self.preflight.get("warnings") or ["no warnings"]),
            console_clients=self.preflight.get("console_clients"),
            foreground=self.preflight.get("foreground"),
            probe_errors=self.preflight.get("probe_errors"),
        )
        for line in self.preflight.get("warnings") or []:
            logger.warning(f"preflight: {line}")
        # Everything below can end this process -- the sweep did once, by ending
        # the console host it shared with this process. Wrapped so that if it
        # happens again the exception says so, on evidence, instead of surfacing
        # as a KeyboardInterrupt at whatever line was executing.
        try:
            self._prepare_desktop()
        except BaseException as exc:
            named = self._name_death(exc)
            if named is exc:
                raise
            raise named from exc
        return self

    def _mark_phase(self, name: str) -> None:
        """Labels what `__enter__` is doing, so a death can say how far it got."""
        self._phase = name
        self._phase_since = time.monotonic()

    def _prepare_desktop(self) -> None:
        """The hazardous half of `__enter__`: recorder, OOBE, desktop, sweep, census."""
        self._mark_phase("attach")
        attach_to_input_desktop()

        # The recorder starts before the runner is touched. Everything below it
        # kills processes, hides windows and switches desktops -- exactly the
        # actions a recording exists to show -- and used to run off-camera, with
        # the mp4 not yet created at the moment anything went wrong.
        self._mark_phase("recorder")
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

        self._mark_phase("oobe")
        if self.config.should_dismiss_oobe:
            try_dismiss_oobe_privacy_screen(timeout=3.0)

        self._mark_phase("virtual_desktop")
        if self.config.should_isolate_virtual_desktop:
            self._setup_isolated_virtual_desktop()

        self._mark_phase("sanitize")
        if self.config.should_sanitize_runner:
            self.log_event("sanitize_start", "sanitising the runner")
            self.kill_plan = sanitize_ci_runner_environment(
                report_path=self.artifact_dir / "kill_plan.json",
                journal=lambda kind, message, **fields: self.log_event(kind, message, **fields),
            )
            self.log_event("sanitize_done", "runner sanitised")

        if self.config.should_dismiss_oobe:
            try_dismiss_oobe_privacy_screen(timeout=3.0)

        self._mark_phase("census")
        # Capture baseline window state
        self.initial_census = WindowCensus.capture()
        self.log_event(
            "session_start", "Baseline window census captured", count=len(self.initial_census)
        )
        self._write_index("running")

    def _name_death(self, exc: BaseException) -> BaseException:
        """Replaces `exc` with a ConsoleHostEndedError when the evidence supports it.

        Runs in a process that may be dying: one console re-probe, no census, no
        screenshot, no subprocess, and its own try/except -- a failure in here
        must never replace the original exception. The verdict goes to the
        journal before it is raised.
        """
        try:
            try:
                attached_now: bool | None = has_console()
            except Exception:
                attached_now = None
            try:
                clients_now: tuple[int, ...] | None = console_client_pids()
            except Exception:
                clients_now = None
            verdict = console_host_verdict(
                self.preflight, attached_now, clients_now, exc, self.kill_plan
            )
            if verdict is None:
                return exc
            since = round(time.monotonic() - self._phase_since, 2) if self._phase_since else None
            ended = ", ".join(f"{n}" for n in verdict["ended"]) or "nothing this session planned"
            peers = ", ".join(verdict["peers"]) or "none"
            message = (
                f"this {verdict['original']} is your console host being destroyed, not a Ctrl-C and not a"
                f" timeout. Console at session start: {len(self.preflight.get('console_clients') or [])}"
                f" processes ({peers} besides this one); console now: attached={attached_now},"
                f" clients={clients_now}. Last phase: {self._phase}"
                + (f", entered {since}s ago" if since is not None else "")
                + f". The sweep ended: {ended}. Where the traceback points is where this process happened"
                " to be when the console died, not what killed it. The other console processes are dying"
                " with it; if one is your CI worker, the step will report cancelled and ordinary"
                " cancellation will not work."
            )
            self.log_event(
                "console_host_ended",
                message,
                ended=verdict["ended"],
                ended_pids=verdict["ended_pids"],
                peers=verdict["peers"],
                phase=self._phase,
                original=verdict["original"],
            )
            return ConsoleHostEndedError(
                message, ended=verdict["ended"], peers=verdict["peers"], phase=self._phase
            )
        except Exception:
            return exc

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
            self.log_event(
                "session_error",
                f"{exc_type.__name__}: {exc_val}",
                signature=getattr(exc_val, "signature", exc_type.__name__),
            )
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

        # Give back what the sweep hid, before the census records the desktop
        # as this session leaves it. Only what this session hid, only if still
        # hidden, only if still there -- and re-measured, like the hide was.
        if self.kill_plan is not None and self.kill_plan.interventions:
            try:
                restored = restore_hidden_windows(self.kill_plan.interventions)
                self.kill_plan.interventions.extend(restored)
                self.log_event(
                    "restore_result",
                    "; ".join(
                        f"{r.class_name}({r.hwnd:#x}) [{'ok' if r.verified else 'NOT verified'}]"
                        for r in restored
                    )
                    or "nothing to restore",
                    interventions=[asdict(r) for r in restored],
                )
                try:
                    write_plan(self.kill_plan, self.artifact_dir / "kill_plan.json")
                except Exception:
                    pass
            except Exception as exc:
                logger.debug(f"restore skipped ({type(exc).__name__}): {exc}")
        set_launch_output_dir(None)

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
            self._extract_failure_frames()

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

    def _extract_failure_frames(self, max_frames: int = 8) -> None:
        """Pulls the recording's frames around the failure into `frames/`.

        After the recorder has stopped, so the file is complete. A diagnostic:
        it never raises, and it says in the timeline whether it ran and how many
        frames it kept and dropped.
        """
        if not self.recorder or not self.recorder.backend:
            return
        video = self.artifact_dir / "session_recording.mp4"
        if not video.is_file():
            return
        try:
            from wintegrate.frames import extract_frames

            index = extract_frames(
                video,
                self.recorder.anchor(),
                list(self.logs),
                self.artifact_dir / "frames",
                max_frames=max_frames,
            )
            self.log_event(
                "frames_extracted",
                f"{len(index['frames'])} frame(s) into frames/, {len(index['dropped'])} mark(s) dropped",
                frames=[f["file"] for f in index["frames"]],
                dropped=len(index["dropped"]),
            )
        except Exception as exc:
            self.log_event("frames_skipped", f"{type(exc).__name__}: {exc}")

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
