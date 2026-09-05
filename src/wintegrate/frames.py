"""Frames out of a recording, at the moments the timeline names.

A recording answers "what did the screen look like when the step failed?" only
if someone scrubs to that moment. `recording_anchor.json` maps an event's
`monotonic` onto video time; this module pulls the frames at those times into
`frames/` next to the video, named by video time and by the event that put them
there, so the reader -- or an agent -- opens `t041830ms_step-failed_submit.png`
instead of a player.

Priority is explicit because the frame budget is small: the window around the
last `step_failed` first, the tail of the recording second, everything else
after. Marks that do not fit are *dropped whole and recorded as dropped*: a
silently truncated frame set reads as the whole story.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from wintegrate.exceptions import DiagnosticPipelineError

_PYAV_HINT = (
    "Frame extraction needs PyAV, which ships in the optional 'video' extra: "
    "pip install 'wintegrate[video]'"
)

PRIORITY_FAILURE = 0
PRIORITY_TAIL = 1
PRIORITY_REST = 2


def _load_av():
    """Imports PyAV on demand, the way `_load_pil_image` guards Pillow."""
    try:
        import av
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise DiagnosticPipelineError(_PYAV_HINT) from exc
    return av


@dataclass(frozen=True)
class FrameMark:
    """One frame worth pulling: where in the video, why, and how much it matters."""

    video_ms: int
    kind: str
    label: str
    priority: int
    event: dict[str, Any] | None = None

    @property
    def filename(self) -> str:
        return f"t{self.video_ms:06d}ms_{slug(self.kind)}_{slug(self.label)}.png"


def slug(text: str, limit: int = 40) -> str:
    """A filename-safe version of an event type or step name."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-._")
    return (cleaned[:limit].rstrip("-._")) or "event"


def video_ms_for(event: dict[str, Any], anchor: dict[str, Any] | None) -> int | None:
    """Where an event falls in the video, by the anchor's formula; None if it cannot be placed.

    `recording_anchor.json` says `video_ms = (event.monotonic - monotonic_start) * 1000`.
    An event without `monotonic`, or an anchor without `monotonic_start`, has no
    place in the video and is not guessed at.
    """
    if not anchor or not isinstance(event, dict):
        return None
    mono = event.get("monotonic")
    start = anchor.get("monotonic_start")
    if not isinstance(mono, (int, float)) or not isinstance(start, (int, float)):
        return None
    return max(0, int(round((mono - start) * 1000)))


def plan_marks(
    events: list[dict[str, Any]],
    anchor: dict[str, Any] | None,
    *,
    tail_ms: int | None,
    max_frames: int = 8,
    window_ms: tuple[int, ...] = (-1000, -500, 0, 500),
    bucket_ms: int = 100,
) -> tuple[list[FrameMark], list[FrameMark]]:
    """Which frames to pull, in priority order, and which marks did not fit.

    Pure. `events` are timeline entries (each with `type`, `message`, `monotonic`);
    `tail_ms` is the recording's last frame time, or None when unknown.

    - The last `step_failed` event gets a window of frames around it
      (`window_ms` offsets), priority 0.
    - The tail frame, priority 1.
    - Every other event that can be placed, priority 2, most recent first.

    Two marks within `bucket_ms` of each other are the same frame; the earlier
    (higher-priority) one wins. Under `max_frames`, whole marks are dropped and
    returned in the second list.
    """
    wanted: list[FrameMark] = []
    failures = [e for e in events if e.get("type") == "step_failed"]
    if failures:
        last = failures[-1]
        at = video_ms_for(last, anchor)
        if at is not None:
            label = f"{last.get('message') or last.get('step') or 'step'}"
            for offset in window_ms:
                ms = at + offset
                if ms < 0 or (tail_ms is not None and ms > tail_ms):
                    continue
                wanted.append(FrameMark(ms, "step-failed", label, PRIORITY_FAILURE, last))
    if tail_ms is not None and tail_ms >= 0:
        wanted.append(FrameMark(int(tail_ms), "tail", "last-frame", PRIORITY_TAIL, None))
    for e in reversed(events):
        if e.get("type") == "step_failed":
            continue
        ms = video_ms_for(e, anchor)
        if ms is None or (tail_ms is not None and ms > tail_ms):
            continue
        wanted.append(
            FrameMark(
                ms, str(e.get("type") or "event"), str(e.get("message") or ""), PRIORITY_REST, e
            )
        )

    # Within the failure window, in time order; among the rest, newest first --
    # the events nearest the failure are the ones worth a frame when the budget
    # runs out.
    wanted.sort(
        key=lambda m: (m.priority, -m.video_ms if m.priority == PRIORITY_REST else m.video_ms)
    )
    kept: list[FrameMark] = []
    dropped: list[FrameMark] = []
    seen: set[int] = set()
    for mark in wanted:
        bucket = mark.video_ms // max(1, bucket_ms)
        if bucket in seen:
            continue  # the same frame as one already chosen: not a drop, a duplicate
        if len(kept) >= max_frames:
            dropped.append(mark)
            continue
        seen.add(bucket)
        kept.append(mark)
    return kept, dropped


def extract_frames(
    video: str | Path,
    anchor: dict[str, Any] | None,
    events: list[dict[str, Any]],
    out_dir: str | Path,
    *,
    max_frames: int = 8,
) -> dict[str, Any]:
    """Writes the frames `plan_marks` chooses into `out_dir`, plus `index.json`.

    One sequential decode, nearest frame to each mark. Returns the index that was
    written: per frame the file, the requested and actual video time, the kind
    and label, and the originating event; plus the marks that were dropped and
    why. Raises `DiagnosticPipelineError` when PyAV is missing or the video
    cannot be decoded -- callers on a teardown path wrap it.
    """
    av = _load_av()
    video = Path(video)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[dict[str, Any]] = []
    try:
        with av.open(str(video)) as container:
            stream = container.streams.video[0]
            # Frame times relative to the stream's own start. A fragmented
            # recording (the recorder's `frag_keyframe+empty_moov`) carries an
            # edit-list offset -- measured: the frame stamped pts 0 decodes at
            # t=0.2 s -- and without this every frame would land 200 ms late.
            start_s = 0.0
            if stream.start_time is not None and stream.time_base is not None:
                start_s = float(stream.start_time * stream.time_base)
            tail_ms = None
            if stream.duration is not None and stream.time_base is not None:
                tail_ms = int(float(stream.duration * stream.time_base) * 1000)
            elif container.duration is not None:
                tail_ms = int(container.duration / 1000)  # microseconds -> ms
            kept, dropped = plan_marks(events, anchor, tail_ms=tail_ms, max_frames=max_frames)
            targets = sorted(kept, key=lambda m: m.video_ms)
            if not targets:
                index = {
                    "video": video.name,
                    "frames": [],
                    "dropped": [asdict(m) for m in dropped],
                    "note": "no mark could be placed in the video",
                }
                (out_dir / "index.json").write_text(
                    json.dumps(index, indent=2, default=str), encoding="utf-8"
                )
                return index

            previous = None  # (ms, frame)
            i = 0

            def emit(mark: FrameMark, ms: int, frame) -> None:
                path = out_dir / mark.filename
                frame.to_image().save(path)
                written.append(
                    {
                        "file": path.name,
                        "video_ms": mark.video_ms,
                        "frame_ms": ms,
                        "kind": mark.kind,
                        "label": mark.label,
                        "priority": mark.priority,
                        "event": mark.event,
                    }
                )

            for frame in container.decode(stream):
                if frame.time is None:
                    continue
                ms = int(round((frame.time - start_s) * 1000))
                while i < len(targets) and ms >= targets[i].video_ms:
                    mark = targets[i]
                    if previous is not None and abs(previous[0] - mark.video_ms) < abs(
                        ms - mark.video_ms
                    ):
                        emit(mark, previous[0], previous[1])
                    else:
                        emit(mark, ms, frame)
                    i += 1
                previous = (ms, frame)
                if i >= len(targets):
                    break
            # Marks past the last decoded frame get the last frame there was.
            while i < len(targets) and previous is not None:
                emit(targets[i], previous[0], previous[1])
                i += 1
    except DiagnosticPipelineError:
        raise
    except Exception as exc:
        raise DiagnosticPipelineError(
            f"frame extraction failed on {video.name}: {type(exc).__name__}: {exc}"
        ) from exc

    index = {
        "video": video.name,
        "anchor": anchor,
        "max_frames": max_frames,
        "frames": written,
        "dropped": [asdict(m) for m in dropped],
    }
    (out_dir / "index.json").write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")
    return index
