"""What a process is doing while its window is awaited.

"No window appeared within 90s" says what did not happen. These samples say
what did: whether the launched process is alive, whether it is burning CPU or
idle, how many threads it has, and whether it has created any window at all,
visible or not -- plus, when the wait runs out, who else on the machine was
busy. Measured on a hosted arm64 runner: a WPF fixture that took 18 seconds to
show its window on one launch had shown nothing after 90 on the previous one,
and nothing on the desktop or in its stderr said why. This is the instrument
that was missing.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any

from wintegrate.interop import kernel32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE = ctypes.c_void_p(-1).value

# Processes whose place in the top-CPU list explains a stall on a fresh runner:
# the antivirus scanning a first-run script, .NET's native-image compiler, and
# Windows servicing. Named so the message can say so.
BUSY_BY_NAME = {
    "msmpeng.exe": "Microsoft Defender scanning",
    "mscorsvw.exe": ".NET native image generation",
    "ngen.exe": ".NET native image generation",
    "ngentask.exe": ".NET native image generation",
    "tiworker.exe": "Windows servicing",
    "trustedinstaller.exe": "Windows servicing",
    "msiexec.exe": "an MSI installer",
    "werfault.exe": "Windows Error Reporting (something crashed)",
    "compattelrunner.exe": "compatibility telemetry",
}


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _filetime_seconds(ft: _FILETIME) -> float:
    return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7


def _cpu_seconds(handle) -> float | None:
    created, exited, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        return None
    return round(_filetime_seconds(kernel) + _filetime_seconds(user), 3)


def _working_set_mb(handle) -> float | None:
    counters = _PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    fn = getattr(kernel32, "K32GetProcessMemoryInfo", None)
    if fn is None or not fn(handle, ctypes.byref(counters), counters.cb):
        return None
    return round(counters.WorkingSetSize / (1024 * 1024), 1)


def _thread_counts() -> dict[int, int]:
    """{pid: thread count} for every process, one Toolhelp snapshot."""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE:
        return {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        out: dict[int, int] = {}
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return {}
        while True:
            out[int(entry.th32ProcessID)] = int(entry.cntThreads)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return out
    finally:
        kernel32.CloseHandle(snapshot)


def _process_names() -> dict[int, str]:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE:
        return {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        out: dict[int, str] = {}
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return {}
        while True:
            out[int(entry.th32ProcessID)] = str(entry.szExeFile).lower()
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return out
    finally:
        kernel32.CloseHandle(snapshot)


@dataclass
class ProcessSample:
    """One look at the awaited process."""

    at: float  # seconds since the wait began
    alive: bool
    exit_code: int | None
    cpu_seconds: float | None
    threads: int | None
    working_set_mb: float | None
    windows: list[dict[str, Any]] = field(default_factory=list)  # every top-level, visible or not

    def as_event(self) -> dict[str, Any]:
        return {
            "at": round(self.at, 1),
            "alive": self.alive,
            "exit_code": self.exit_code,
            "cpu_seconds": self.cpu_seconds,
            "threads": self.threads,
            "working_set_mb": self.working_set_mb,
            "windows": len(self.windows),
            "visible_windows": sum(1 for w in self.windows if w.get("visible")),
        }


def sample_process(pid: int, at: float = 0.0, census: list | None = None) -> ProcessSample:
    """Alive, CPU time, threads, memory, and the windows the process owns right now.

    Never raises: a process that has gone is a sample that says so.
    """
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    alive, code, cpu, wset = False, None, None, None
    if handle:
        try:
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                alive = exit_code.value == STILL_ACTIVE
                code = None if alive else int(exit_code.value)
            cpu = _cpu_seconds(handle)
            wset = _working_set_mb(handle) if alive else None
        finally:
            kernel32.CloseHandle(handle)
    threads = _thread_counts().get(pid) if alive else None
    windows: list[dict[str, Any]] = []
    if census is None and alive:
        try:
            from wintegrate.diagnostics import WindowCensus

            census = WindowCensus.capture()
        except Exception:  # noqa: BLE001 - a sample must not raise
            census = []
    for w in census or []:
        if getattr(w, "pid", None) == pid:
            windows.append(
                {
                    "hwnd": w.hwnd,
                    "class": w.class_name,
                    "title": w.title,
                    "visible": bool(w.is_visible),
                }
            )
    return ProcessSample(at, alive, code, cpu, threads, wset, windows)


def machine_snapshot(interval: float = 0.5, top: int = 6) -> dict[str, Any]:
    """Who was busy: the processes that used the most CPU over `interval` seconds,
    and whether any of the known stall-makers (Defender, ngen, servicing) is
    among the running processes. Never raises."""
    try:
        names = _process_names()

        def cpu_map() -> dict[int, float]:
            out: dict[int, float] = {}
            for pid in names:
                if pid == 0:
                    continue
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if not handle:
                    continue
                try:
                    cpu = _cpu_seconds(handle)
                    if cpu is not None:
                        out[pid] = cpu
                finally:
                    kernel32.CloseHandle(handle)
            return out

        first = cpu_map()
        time.sleep(interval)
        second = cpu_map()
        deltas = sorted(
            ((second.get(pid, c) - c, pid) for pid, c in first.items()),
            reverse=True,
        )
        busiest = [
            {
                "pid": pid,
                "name": names.get(pid, "?"),
                "cpu_percent_of_one_core": round(100 * delta / interval, 1),
            }
            for delta, pid in deltas[:top]
            if delta > 0
        ]
        # Named only when actually consuming CPU: Defender's service exists on
        # every Windows, so its presence says nothing; its place in the busy list does.
        present = sorted({BUSY_BY_NAME[b["name"]] for b in busiest if b["name"] in BUSY_BY_NAME})
        return {
            "interval": interval,
            "busiest": busiest,
            "known_busy": present,
            "processes": len(names),
        }
    except Exception as exc:  # noqa: BLE001 - decorating a failure must not replace it
        return {"error": f"{type(exc).__name__}: {exc}"}


def describe_wait(
    pid: int, image: str, samples: list[ProcessSample], machine: dict[str, Any] | None
) -> str:
    """The paragraph for a timeout message: what the process did while it was awaited."""
    if not samples:
        return ""
    last = samples[-1]
    first = samples[0]
    lines = [f" While waiting, {image or 'the process'} (pid {pid}) was sampled {len(samples)}x:"]
    if not last.alive:
        lines.append(f" it exited with code {last.exit_code} at {last.at:.0f}s.")
    else:
        cpu0 = first.cpu_seconds or 0.0
        cpu1 = last.cpu_seconds if last.cpu_seconds is not None else cpu0
        lines.append(
            f" still alive at {last.at:.0f}s, CPU {cpu1:.1f}s total ({cpu1 - cpu0:+.1f}s over the wait), "
            f"threads {first.threads}->{last.threads}, working set {last.working_set_mb} MB."
        )
    windows = {(w["class"], w["title"], w["visible"]) for s in samples for w in s.windows}
    if windows:
        shown = ", ".join(
            f"{cls!r} {title!r}{'' if vis else ' (hidden)'}"
            for cls, title, vis in sorted(windows)[:6]
        )
        lines.append(f" Windows it created: {shown}.")
    else:
        lines.append(" It created no top-level window at all, visible or hidden.")
    if machine and not machine.get("error"):
        busiest = machine.get("busiest") or []
        if busiest:
            lines.append(
                " Busiest on the machine: "
                + ", ".join(f"{b['name']} {b['cpu_percent_of_one_core']}%" for b in busiest[:5])
                + "."
            )
        known = machine.get("known_busy") or []
        if known:
            lines.append(" That is " + "; ".join(known) + ".")
    return "".join(lines)
