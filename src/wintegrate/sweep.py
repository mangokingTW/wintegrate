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


@dataclass
class KillPlan:
    attached_to_console: bool
    names: tuple[str, ...]
    entries: list[PlannedKill]
    dry_run: bool
    protection_degraded: str | None = None
    results: dict[int, str] = field(default_factory=dict)

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


def dry_run_requested(explicit: bool | None) -> bool:
    """`explicit` wins; otherwise WINTEGRATE_SANITIZE_DRY_RUN=1/true asks for a dry run."""
    if explicit is not None:
        return explicit
    return os.environ.get("WINTEGRATE_SANITIZE_DRY_RUN", "").strip().lower() in ("1", "true", "yes")
