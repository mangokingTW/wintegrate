"""What a process is doing while its window is awaited.

Three facts, sampled while the wait goes on: is it still alive (and if not,
its exit code), is it doing work (CPU seconds), and has it created any
top-level window at all, visible or not. "No window appeared within 90s" says
what did not happen; these say what did.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any

from wintegrate.interop import kernel32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


def _cpu_seconds(handle) -> float | None:
    created, exited, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
    ok = kernel32.GetProcessTimes(
        handle,
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    )
    if not ok:
        return None
    ticks = sum((ft.dwHighDateTime << 32) | ft.dwLowDateTime for ft in (kernel, user))
    return round(ticks / 1e7, 2)


@dataclass
class ProcessSample:
    """One look at the awaited process."""

    at: float  # seconds since the wait began
    alive: bool
    exit_code: int | None
    cpu_seconds: float | None
    windows: list[dict[str, Any]] = field(default_factory=list)  # every top-level, visible or not

    def as_event(self) -> dict[str, Any]:
        return {
            "at": round(self.at, 1),
            "alive": self.alive,
            "exit_code": self.exit_code,
            "cpu_seconds": self.cpu_seconds,
            "windows": len(self.windows),
            "visible_windows": sum(1 for w in self.windows if w.get("visible")),
        }


def sample_process(pid: int, at: float = 0.0, census: list | None = None) -> ProcessSample:
    """Alive, CPU time, and the windows the process owns in `census` (a
    `WindowCensus.capture()` list). Never raises: a process that has gone is a
    sample that says so."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    alive, code, cpu = False, None, None
    if handle:
        try:
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                alive = exit_code.value == STILL_ACTIVE
                code = None if alive else int(exit_code.value)
            cpu = _cpu_seconds(handle)
        finally:
            kernel32.CloseHandle(handle)
    windows = [
        {"hwnd": w.hwnd, "class": w.class_name, "title": w.title, "visible": bool(w.is_visible)}
        for w in (census or [])
        if getattr(w, "pid", None) == pid
    ]
    return ProcessSample(at, alive, code, cpu, windows)


def describe_wait(pid: int, image: str, samples: list[ProcessSample]) -> str:
    """The sentence for a timeout message: what the process did while awaited."""
    if not samples:
        return ""
    first, last = samples[0], samples[-1]
    who = f"{image or 'the process'} (pid {pid})"
    if not last.alive:
        state = f"exited with code {last.exit_code} at {last.at:.0f}s"
    else:
        cpu0, cpu1 = first.cpu_seconds or 0.0, last.cpu_seconds or 0.0
        state = (
            f"still alive at {last.at:.0f}s, having used {cpu1 - cpu0:.1f}s of CPU over the wait"
        )
    windows = {(w["class"], w["title"], w["visible"]) for s in samples for w in s.windows}
    if windows:
        shown = ", ".join(
            f"{cls!r} {title!r}{'' if vis else ' (hidden)'}"
            for cls, title, vis in sorted(windows)[:6]
        )
        made = f"windows it created: {shown}"
    else:
        made = "it created no top-level window at all, visible or hidden"
    return f" While waiting, {who} was sampled {len(samples)}x: {state}; {made}."


# --- what Windows itself can say at the moment the wait gives up -----------------

# One PowerShell invocation, JSON out. Each probe is a fact Windows already keeps:
# the awaited process's threads and what they wait on; whether a PowerShell
# engine reached "Available" (event 400, in the classic 'Windows PowerShell' log); Defender scan start/finish
# (1000/1001) and detections (1116/1117); CAPI2 revocation-check records, when
# that log is enabled; the FontCache service starting (System 7036), which is what
# a first WPF window waits for; Application Hang reports (1002).
_PROBE_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$pid_ = [int]__PID__; $since = (Get-Date).AddSeconds(-[double]__SINCE__)
$out = [ordered]@{}
$p = Get-Process -Id $pid_
if ($p) {
  $out.process = [ordered]@{ name = $p.ProcessName; start = $p.StartTime.ToString('o'); cpu_s = [math]::Round($p.TotalProcessorTime.TotalSeconds, 2);
    threads = @($p.Threads | ForEach-Object { [ordered]@{ id = $_.Id; state = "$($_.ThreadState)"; wait = "$($_.WaitReason)"; cpu_s = [math]::Round($_.TotalProcessorTime.TotalSeconds, 2) } }) }
}
function Recent($log, $ids) {
  $f = @{ LogName = $log; StartTime = $since }; if ($ids) { $f.Id = $ids }
  @(Get-WinEvent -FilterHashtable $f -MaxEvents 40 -ErrorAction SilentlyContinue | ForEach-Object {
    [ordered]@{ t = $_.TimeCreated.ToString('HH:mm:ss.fff'); id = $_.Id; text = (($_.Message -split "`n")[0]).Trim() } })
}
$out.powershell_engine = Recent 'Windows PowerShell' @(400, 403)
$out.defender = Recent 'Microsoft-Windows-Windows Defender/Operational' @(1000, 1001, 1116, 1117)
$capi = Get-WinEvent -ListLog 'Microsoft-Windows-CAPI2/Operational' -ErrorAction SilentlyContinue
$out.capi2_enabled = [bool]($capi -and $capi.IsEnabled)
$out.capi2 = if ($out.capi2_enabled) { Recent 'Microsoft-Windows-CAPI2/Operational' $null } else { @() }
$out.services = @(Recent 'System' @(7036) | Where-Object { $_.text -match 'Font|Presentation|Defender|Update|Installer' })
$out.hangs = Recent 'Application' @(1002)
$out | ConvertTo-Json -Depth 5 -Compress
"""


def windows_probe(pid: int, since_seconds: float, timeout: float = 25.0) -> dict[str, Any]:
    """Asks Windows what it knows about the awaited process and the last
    `since_seconds`: thread wait reasons, PowerShell engine state events,
    Defender activity, CAPI2 revocation checks, FontCache service starts,
    Application Hang reports. Never raises."""
    import json
    import subprocess

    try:
        # -Command does not take positional arguments the way -File does, so the
        # two numbers are written into the script text.
        script = _PROBE_SCRIPT.replace("__PID__", str(int(pid))).replace(
            "__SINCE__", repr(float(since_seconds))
        )
        run = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        text = (run.stdout or "").strip()
        if not text:
            err = (run.stderr or "").strip().splitlines()
            tail = err[-1][:200] if err else ""
            return {"error": f"probe printed nothing (exit {run.returncode}): {tail}"}
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001 - decorating a failure must not replace it
        return {"error": f"{type(exc).__name__}: {exc}"}


def _normalize_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def describe_probe(probe: dict[str, Any]) -> str:
    """The sentences for a timeout message, only for the facts that carry news."""
    if not probe or probe.get("error"):
        return f" (Windows probe failed: {probe.get('error')})" if probe else ""
    parts: list[str] = []
    proc = probe.get("process") or {}
    threads = _normalize_list(proc.get("threads"))
    if threads:
        waits: dict[str, int] = {}
        for t in threads:
            key = (
                f"{t.get('state')}/{t.get('wait')}"
                if t.get("state") == "Wait"
                else str(t.get("state"))
            )
            waits[key] = waits.get(key, 0) + 1
        summary = ", ".join(f"{n}x {k}" for k, n in sorted(waits.items(), key=lambda kv: -kv[1]))
        parts.append(f" Its {len(threads)} threads: {summary}.")
    engine = _normalize_list(probe.get("powershell_engine"))
    if engine:
        started = [e for e in engine if e.get("id") == 400]
        parts.append(
            f" PowerShell engine events since the wait began: {len(engine)}"
            + (
                f", last 'Available' at {started[-1]['t']}"
                if started
                else ", none reached 'Available'"
            )
            + "."
        )
    defender = _normalize_list(probe.get("defender"))
    if defender:
        parts.append(
            " Defender: " + "; ".join(f"{e['t']} {e['text'][:80]}" for e in defender[-3:]) + "."
        )
    if probe.get("capi2_enabled"):
        capi = _normalize_list(probe.get("capi2"))
        if capi:
            parts.append(
                f" {len(capi)} certificate revocation-check record(s), e.g. "
                + "; ".join(f"{e['t']} {e['text'][:70]}" for e in capi[-2:])
                + "."
            )
    else:
        parts.append(
            " (CAPI2 log disabled: revocation checks were not recorded; enable with"
            " wevtutil sl Microsoft-Windows-CAPI2/Operational /e:true.)"
        )
    services = _normalize_list(probe.get("services"))
    if services:
        parts.append(
            " Services: " + "; ".join(f"{e['t']} {e['text'][:80]}" for e in services[-3:]) + "."
        )
    hangs = _normalize_list(probe.get("hangs"))
    if hangs:
        parts.append(
            " Application Hang reported: "
            + "; ".join(f"{e['t']} {e['text'][:80]}" for e in hangs[-2:])
            + "."
        )
    return "".join(parts)
