"""Shaping a session's artifacts into something a report can show.

Pure functions over the files a `Session` writes: the journal becomes a step
tree in run order, a census diff becomes the windows that actually leaked, an
event becomes one readable line. Shared by the pytest-html plugin and the
GitHub Step Summary so the two never disagree about what happened.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wintegrate.frames import video_ms_for

# Windows that exist for the process, not for the user: message-only windows,
# IME and tooltip hosts, the shell's own. A census diff that lists them reads as
# a leak on every passing test, and a warning on every test is no warning.
_NOISE_CLASSES = {
    "MessageWindowClass",
    "MSCTFIME UI",
    "IME",
    "tooltips_class32",
    "Windows.UI.Core.CoreWindow",
    "ApplicationFrameWindow",
    "Xaml_WindowedPopupClass",
    "OleMainThreadWndClass",
    "CicMarshalWndClass",
    "Shell_TrayWnd",
    "Progman",
    "WorkerW",
}


def read_journal(artifact_dir: Path) -> list[dict[str, Any]]:
    """Events in order, from the jsonl (authoritative) or the json written at exit."""
    jsonl = artifact_dir / "session_events.jsonl"
    if jsonl.exists():
        events = []
        for line in jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a line cut short by a kill is not evidence of anything
        return events
    pretty = artifact_dir / "session_events.json"
    if pretty.exists():
        try:
            data = json.loads(pretty.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else data.get("events", [])
        except (json.JSONDecodeError, AttributeError):
            return []
    return []


def build_step_tree(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nests `step_start` / `step_ok` / `step_failed` events the way they ran.

    Each node: name, status ('ok' | 'failed' | 'open'), start/end monotonic,
    seconds, error, signature, detail (the exception's message), the events
    logged directly inside it, and its children. A step left open by a death
    keeps status 'open' rather than being guessed at.
    """
    root: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    for e in events:
        kind = e.get("type")
        if kind == "step_start":
            node = {
                "name": e.get("message", ""),
                "status": "open",
                "start": e.get("monotonic"),
                "end": None,
                "seconds": None,
                "error": None,
                "signature": None,
                "detail": None,
                "events": [],
                "children": [],
                "start_event": e,
            }
            (stack[-1]["children"] if stack else root).append(node)
            stack.append(node)
        elif kind in ("step_ok", "step_failed") and stack:
            node = stack.pop()
            node["status"] = "ok" if kind == "step_ok" else "failed"
            node["end"] = e.get("monotonic")
            node["seconds"] = e.get("seconds")
            node["error"] = e.get("error")
            node["signature"] = e.get("signature")
            node["detail"] = e.get("detail")
            node["end_event"] = e
        elif stack:
            stack[-1]["events"].append(e)
    return root


def flatten_steps(tree: list[dict[str, Any]], depth: int = 0) -> list[tuple[int, dict[str, Any]]]:
    """Run order with nesting depth, for a flat table that still shows the tree."""
    out: list[tuple[int, dict[str, Any]]] = []
    for node in tree:
        out.append((depth, node))
        out.extend(flatten_steps(node["children"], depth + 1))
    return out


def frame_mark_for(node: dict[str, Any], anchor: dict | None) -> int | None:
    """Where in the video a step's frame is: its end (the result, or the moment it
    failed), or its start when it never ended."""
    return video_ms_for(node.get("end_event") or node.get("start_event") or {}, anchor)


def format_video_time(ms: int | None) -> str:
    if ms is None:
        return ""
    return f"{ms // 60000}:{(ms % 60000) / 1000:04.1f}"


def summarize_event(e: dict[str, Any]) -> str:
    """One readable line for the events a step commonly contains; raw for the rest."""
    kind, msg = str(e.get("type", "")), str(e.get("message", ""))
    if kind == "launch_app":
        return f"launched {msg.removeprefix('Launching ').strip()}"
    if kind == "window_discovered":
        return f"window found: {msg.removeprefix('Window ').strip()}"
    if kind == "screenshot":
        return (msg[0].lower() + msg[1:]) if msg else "screenshot"
    if kind == "heartbeat":
        return "heartbeat"
    return f"{kind.replace('_', ' ')}: {msg}" if msg else kind.replace("_", " ")


def leaked_windows(added: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The census additions worth a warning: visible, titled, and not the process's
    own plumbing."""
    kept = []
    for w in added:
        if w.get("is_visible") is False:
            continue
        if w.get("class_name") in _NOISE_CLASSES:
            continue
        if not (w.get("title") or w.get("name")):
            continue
        kept.append(w)
    return kept


def one_line(text: Any, limit: int = 300) -> str:
    """Markdown-safe single line: newlines collapsed, cut at a word, marked when cut."""
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0]
    return cut + " …"
