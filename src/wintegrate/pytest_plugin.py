"""pytest-html integration: the session's evidence, inside the test's row.

Loaded through the `pytest11` entry point and inert unless pytest-html is
active (`pytest --html=report.html`). For every `Session` a test opened, the
report's row for that test then carries:

- the last failure screenshot first, then the error, then the steps -- a
  failure should be readable without expanding anything else (Playwright's
  report does this and it is the reason people like it);
- one row per `Session.step`, tick or cross, duration, and the frame the
  recording holds at that moment (Maestro);
- steps nested the way they ran, each expandable to the events it contained
  with their arguments (Allure's step tree, Robot's keyword log);
- the recording itself, as a video the report plays.

Everything is read from the artifacts the session already writes
(`session_events.jsonl`, `recording_anchor.json`, `*.png`,
`session_recording.mp4`); nothing here changes what a session records.
"""

from __future__ import annotations

import base64
import html
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from wintegrate import session as _session_module
from wintegrate.frames import video_ms_for

THUMB_WIDTH = 240
MAX_STEP_FRAMES = 24


# --- pure: reading and shaping the journal -------------------------------------


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
    seconds, error, signature, the events logged directly inside it, and its
    children. A step left open by a death keeps status 'open' rather than being
    guessed at.
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
            node["end_event"] = e
        elif stack:
            stack[-1]["events"].append(e)
    return root


def flatten_steps(tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in tree:
        out.append(node)
        out.extend(flatten_steps(node["children"]))
    return out


def last_failure_screenshot(artifact_dir: Path) -> Path | None:
    """The newest `failure*.png`: the step's own, or the session's at exit."""
    shots = sorted(
        (p for p in artifact_dir.glob("failure*.png") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    return shots[-1] if shots else None


def _fmt_args(e: dict[str, Any]) -> str:
    skip = {"wall", "monotonic", "pid", "timestamp", "type", "message", "step"}
    parts = [
        f"{k}={json.dumps(v, ensure_ascii=False, default=str)}"
        for k, v in e.items()
        if k not in skip
    ]
    return " ".join(parts)


# --- frames: one per step, from the recording ------------------------------------


def frames_at(video: Path, marks_ms: list[int], width: int = THUMB_WIDTH) -> dict[int, bytes]:
    """PNG bytes of the frame nearest each mark, one sequential decode.

    Returns what it could decode; a missing PyAV or an unreadable video gives an
    empty dict, and the report then simply has no thumbnails.
    """
    if not marks_ms:
        return {}
    try:
        from wintegrate.frames import _load_av

        av = _load_av()
    except Exception:  # noqa: BLE001 - optional extra
        return {}
    wanted = sorted(set(marks_ms))
    out: dict[int, bytes] = {}
    try:
        with av.open(str(video)) as container:
            stream = container.streams.video[0]
            first_pts: int | None = None
            pending = list(wanted)
            last_frame = None
            last_ms = None
            for frame in container.decode(stream):
                if frame.pts is None:
                    continue
                if first_pts is None:
                    first_pts = frame.pts
                ms = int((frame.pts - first_pts) * stream.time_base * 1000)
                while pending and ms >= pending[0]:
                    target = pending.pop(0)
                    pick = (
                        frame
                        if last_frame is None or abs(ms - target) <= abs(last_ms - target)
                        else last_frame
                    )
                    out[target] = _frame_png(pick, width)
                last_frame, last_ms = frame, ms
                if not pending:
                    break
            for target in pending:  # past the end: the last frame stands in
                if last_frame is not None:
                    out[target] = _frame_png(last_frame, width)
    except Exception:  # noqa: BLE001
        return out
    return out


def _frame_png(frame, width: int) -> bytes:
    import io

    img = frame.to_image()
    if img.width > width:
        img = img.resize((width, max(1, int(img.height * width / img.width))))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --- rendering -----------------------------------------------------------------

_CSS = """
<style>
.wt-report{font:13px/1.4 system-ui,Segoe UI,sans-serif;margin:6px 0 10px}
.wt-report .wt-head{margin:6px 0 4px;font-weight:600}
.wt-report .wt-err{white-space:pre-wrap;background:#fff3f3;border-left:3px solid #d33;padding:6px 8px;margin:4px 0}
.wt-report table.wt-steps{border-collapse:collapse;width:100%}
.wt-report table.wt-steps td{vertical-align:top;padding:4px 6px;border-top:1px solid #e5e5e5}
.wt-report .wt-ok{color:#1a7f37;font-weight:700}
.wt-report .wt-fail{color:#c00;font-weight:700}
.wt-report .wt-open{color:#b58900;font-weight:700}
.wt-report .wt-dur{color:#666;white-space:nowrap}
.wt-report details summary{cursor:pointer;color:#444}
.wt-report .wt-ev{font:12px/1.35 ui-monospace,Consolas,monospace;color:#333;margin:2px 0 0 12px;white-space:pre-wrap}
.wt-report .wt-thumb{display:block;border:1px solid #ccc;background:#000 center/contain no-repeat}
.wt-report .wt-meta{color:#666;margin-top:6px}
</style>
"""


def _status_mark(status: str) -> str:
    return {
        "ok": '<span class="wt-ok">&#10003;</span>',
        "failed": '<span class="wt-fail">&#10007;</span>',
    }.get(status, '<span class="wt-open">&#8230;</span>')


def render_steps(
    tree: list[dict[str, Any]],
    thumbs: dict[int, tuple[str, int, int]],
    anchor: dict | None,
    depth: int = 0,
) -> str:
    rows = []
    for node in tree:
        indent = "&nbsp;" * (4 * depth)
        dur = f"{node['seconds']:.2f}s" if isinstance(node.get("seconds"), (int, float)) else ""
        ms = video_ms_for(node.get("start_event") or {}, anchor)
        # A div with a background, not an <img>: pytest-html's script collects
        # the <img> elements inside a result's extras for its media viewer and
        # rewrites the first one it finds, which turned the first thumbnail into
        # the failure screenshot and left the viewer empty.
        thumb = ""
        if ms in thumbs:
            uri, w, h = thumbs[ms]
            thumb = (
                f'<div class="wt-thumb" role="img" aria-label="frame at {ms} ms" '
                f'style="width:{w}px;height:{h}px;background-image:url({uri})"></div>'
            )
        err = ""
        if node["status"] == "failed":
            sig = node.get("signature") or node.get("error") or ""
            err = f'<div class="wt-fail">{html.escape(str(sig))}</div>'
        inner = ""
        if node["events"]:
            lines = "\n".join(
                html.escape(f"{e.get('type', '')}: {e.get('message', '')} {_fmt_args(e)}".rstrip())
                for e in node["events"]
            )
            inner = f'<details><summary>{len(node["events"])} event(s)</summary><div class="wt-ev">{lines}</div></details>'
        rows.append(
            f"<tr><td>{_status_mark(node['status'])}</td>"
            f"<td>{indent}{html.escape(node['name'])}{err}{inner}</td>"
            f'<td class="wt-dur">{dur}</td><td>{thumb}</td></tr>'
        )
        if node["children"]:
            rows.append(render_steps(node["children"], thumbs, anchor, depth + 1))
    return "".join(rows)


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _png_size(png: bytes) -> tuple[int, int]:
    """Width and height from the IHDR chunk; no decoder needed."""
    if len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    return THUMB_WIDTH, THUMB_WIDTH * 3 // 4


def render_session(artifact_dir: Path, failed: bool, error: str | None) -> tuple[str, list]:
    """The HTML block for one session and the pytest-html extras to attach with it."""
    from pytest_html import extras

    events = read_journal(artifact_dir)
    tree = build_step_tree(events)
    anchor_path = artifact_dir / "recording_anchor.json"
    anchor = None
    if anchor_path.exists():
        try:
            anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            anchor = None
    video = artifact_dir / "session_recording.mp4"

    thumbs: dict[int, tuple[str, int, int]] = {}
    if anchor and video.exists():
        marks = []
        for node in flatten_steps(tree)[:MAX_STEP_FRAMES]:
            ms = video_ms_for(node.get("start_event") or {}, anchor)
            if ms is not None:
                marks.append(ms)
        thumbs = {
            ms: (_data_uri(png), *_png_size(png)) for ms, png in frames_at(video, marks).items()
        }

    attached: list = []
    parts = [_CSS, '<div class="wt-report">']
    if failed:
        shot = last_failure_screenshot(artifact_dir)
        if shot is not None:
            attached.append(
                extras.png(base64.b64encode(shot.read_bytes()).decode("ascii"), name=shot.name)
            )
        if error:
            parts.append(f'<div class="wt-err">{html.escape(error)}</div>')
    if tree:
        parts.append('<div class="wt-head">Steps</div>')
        parts.append(f'<table class="wt-steps">{render_steps(tree, thumbs, anchor)}</table>')
    else:
        parts.append(
            '<div class="wt-meta">No steps recorded; wrap work in <code>session.step(...)</code> to see it here.</div>'
        )
    if video.exists():
        attached.append(
            extras.mp4(
                base64.b64encode(video.read_bytes()).decode("ascii"), name="session_recording.mp4"
            )
        )
    parts.append(
        f'<div class="wt-meta">artifacts: <code>{html.escape(str(artifact_dir))}</code> &middot; {len(events)} events</div>'
    )
    parts.append("</div>")
    return "".join(parts), attached


# --- pytest hooks --------------------------------------------------------------


def _html_active(config) -> bool:
    return (
        sys.platform == "win32"
        and config.pluginmanager.hasplugin("html")
        and bool(config.getoption("htmlpath", None))
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    item._wintegrate_sessions_before = len(_session_module.RECENT_SESSIONS)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not _html_active(item.config) or report.when != "call":
        return
    start = getattr(item, "_wintegrate_sessions_before", len(_session_module.RECENT_SESSIONS))
    sessions = _session_module.RECENT_SESSIONS[start:]
    if not sessions:
        return
    from pytest_html import extras

    attached = []
    error = (
        report.longreprtext.strip().splitlines()[-1]
        if report.failed and report.longreprtext
        else None
    )
    for record in sessions:
        block, media = render_session(Path(record["artifact_dir"]), report.failed, error)
        attached.extend(media)  # screenshot first: it is what a reader wants to see
        attached.append(extras.html(block))
    report.extras = getattr(report, "extras", []) + attached


@pytest.hookimpl(optionalhook=True)
def pytest_html_report_title(report):
    report.title = "wintegrate test report"


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_summary(prefix, summary, postfix, session):
    n = len(_session_module.RECENT_SESSIONS)
    failed = sum(1 for r in _session_module.RECENT_SESSIONS if r.get("failed"))
    prefix.append(
        f"<p>wintegrate sessions: {n}, of which {failed} closed on an exception. "
        "A failed test's row opens on its last screenshot, then the error, then the steps "
        "with the frame the recording holds at each one.</p>"
    )
