"""The runner sweep as a plan: what would be killed, why each thing is spared, what happened.

Written before the first kill, so a sweep that ends its own caller leaves the
plan behind. That is the whole point: in the 0.5.12 incident the sweep killed
the console host it shared with its caller, the caller died 0.27 s later at an
unrelated-looking line, and nothing on disk said a kill had been attempted.

Pure functions build and describe the plan; one small function executes it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PlannedKill:
    pid: int
    name: str
    verdict: str  # "kill" | "spare"
    reason: str  # why it is spared, or "matches sweep list" for a kill


@dataclass(frozen=True)
class InterventionResult:
    """One thing the sweep did to one window, with what it meant and what it saw after.

    `intended` is the state asked for (e.g. {"visible": False}); `observed` is
    what a re-measurement found a moment later (e.g. {"visible": False,
    "exists": True}); `verified` is whether they agree. A hide that did not
    take is a different fact from one that did -- the Start menu's CoreWindow
    ignores SW_HIDE, measured -- and only a re-measurement tells them apart.
    """

    action: str  # "hide" | "restore"
    hwnd: int
    class_name: str
    title: str
    process: str
    reason: str
    intended: dict
    observed: dict
    verified: bool


@dataclass
class KillPlan:
    attached_to_console: bool
    names: tuple[str, ...]
    entries: list[PlannedKill]
    dry_run: bool
    protection_degraded: str | None = None
    results: dict[int, str] = field(default_factory=dict)
    interventions: list[InterventionResult] = field(default_factory=list)
    foreground_before_hide: dict | None = None
    foreground_after_hide: dict | None = None

    @property
    def kills(self) -> list[PlannedKill]:
        return [e for e in self.entries if e.verdict == "kill"]

    @property
    def spared(self) -> list[PlannedKill]:
        return [e for e in self.entries if e.verdict == "spare"]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["summary"] = self.summary()
        return d

    def interventions_summary(self) -> str:
        if not self.interventions:
            return "hid nothing"
        parts = []
        for i in self.interventions:
            state = "ok" if i.verified else "NOT verified"
            parts.append(f"{i.action} {i.class_name}({i.hwnd:#x}) [{state}]")
        return "; ".join(parts)

    def summary(self) -> str:
        k = ", ".join(f"{e.name}({e.pid})" for e in self.kills) or "nothing"
        sp = ", ".join(f"{e.name}({e.pid}): {e.reason}" for e in self.spared)
        head = f"{'DRY RUN: would kill' if self.dry_run else 'kill'} {k}"
        return head + (f"; spared {sp}" if sp else "")


def _stem(image: str) -> str:
    image = image.lower()
    return image[:-4] if image.endswith(".exe") else image


def build_kill_plan(
    table: dict[int, tuple[int, str]],
    names: Iterable[str],
    protected: dict[int, str],
    attached_to_console: bool,
    dry_run: bool,
    protection_degraded: str | None = None,
) -> KillPlan:
    """Which of the processes named in `names` may be ended, given what protects what.

    `table` is {pid: (ppid, image_name)}, `protected` is {pid: relation} from
    `protected_pids()`. Every process whose name matches gets an entry: a kill,
    or a spare with the relation that spares it. Nothing else is listed -- the
    plan is about the sweep list, not the machine.
    """
    wanted = {_stem(n) for n in names}
    entries = [
        PlannedKill(
            pid,
            image,
            "spare" if pid in protected else "kill",
            protected.get(pid, "matches sweep list"),
        )
        for pid, (_ppid, image) in sorted(table.items())
        if _stem(image) in wanted
    ]
    return KillPlan(attached_to_console, tuple(names), entries, dry_run, protection_degraded)


def execute_kill_plan(plan: KillPlan, terminate: Callable[[int], str]) -> KillPlan:
    """Ends every `kill` entry, recording per pid what `terminate` said. A dry run ends nothing."""
    if plan.dry_run:
        return plan
    for entry in plan.kills:
        plan.results[entry.pid] = terminate(entry.pid)
    return plan


def write_plan(plan: KillPlan, path: str | Path) -> None:
    """Writes the plan as JSON and fsyncs it: this must be on disk before the first kill."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(plan.to_dict(), fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())


def hide_reason(class_name: str, title: str) -> str | None:
    """Why a visible window would be hidden by the sweep, or None to leave it alone.

    Pure, so the rule is testable without a desktop. The rule is the one the
    sweep has always applied: WSL prompts, Edge's first-run pages, and the
    shell's search popup -- windows that take the foreground on a hosted
    runner and belong to nobody's test.
    """
    t = (title or "").lower()
    if "wsl" in t:
        return "WSL prompt"
    if "edge" in t and ("welcome" in t or "first run" in t):
        return "Edge first-run page"
    if "search" in t and class_name == "Windows.UI.Core.CoreWindow":
        return "shell search popup"
    return None


def restore_targets(
    interventions: Iterable[InterventionResult],
    window_exists: Callable[[int], bool],
    window_visible: Callable[[int], bool],
) -> list[InterventionResult]:
    """Which hides to undo: verified hides whose window still exists and is still hidden.

    Pure. Not a window the session never hid; not one that is already back
    (something else showed it); not one that is gone. Restoring more than
    that would put windows on screen that nobody took away.
    """
    out = []
    for i in interventions:
        if i.action != "hide" or not i.verified:
            continue
        if not window_exists(i.hwnd) or window_visible(i.hwnd):
            continue
        out.append(i)
    return out


def dry_run_requested(explicit: bool | None) -> bool:
    """`explicit` wins; otherwise WINTEGRATE_SANITIZE_DRY_RUN=1/true asks for a dry run."""
    if explicit is not None:
        return explicit
    return os.environ.get("WINTEGRATE_SANITIZE_DRY_RUN", "").strip().lower() in ("1", "true", "yes")
