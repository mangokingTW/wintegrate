"""pytest-html integration: the session's evidence, inside the test's row.

Loaded through the `pytest11` entry point and inert unless pytest-html is
active (`pytest --html=report.html`). For every `Session` a test opened, the
report's row for that test then carries:

- the last failure screenshot first, then the error, then the steps -- a
  failure should be readable without expanding anything else (Playwright's
  report does this and it is the reason people like it);
- one row per `Session.step`, tick or cross, duration, where it sits in the
  video, and the frame the recording holds at the end of the step (Maestro);
- steps nested the way they ran, each with the events inside it as readable
  lines and the raw lines on demand (Allure's step tree, Robot's keyword log);
- the recording itself, as a video the report plays.

Everything is read from the artifacts the session already writes
(`session_events.jsonl`, `recording_anchor.json`, `*.png`,
`session_recording.mp4`); nothing here changes what a session records.
"""

from __future__ import annotations

import base64
import html
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from wintegrate import session as _session_module
from wintegrate.evidence import (
    build_step_tree,
    flatten_steps,
    format_video_time,
    frame_mark_for,
    leaked_windows,
    one_line,
    read_journal,
    summarize_event,
)

__all__ = [
    "build_step_tree",
    "flatten_steps",
    "frames_at",
    "last_failure_screenshot",
    "read_journal",
    "render_session",
    "render_steps",
]

FRAME_WIDTH = 720  # decoded once; shown at half width until the frame is clicked open
MAX_STEP_FRAMES = 24
EVENTS_SHOWN = 4


def last_failure_screenshot(artifact_dir: Path) -> Path | None:
    """The step's own `failure-<step>.png` when there is one, else the session's
    `failure_screenshot.png`.

    The step's is taken the instant the step raised; the session's is taken at
    exit, after the test's own cleanup has usually closed the window, so it tends
    to show an empty desktop.
    """
    by_step = sorted(
        (p for p in artifact_dir.glob("failure-*.png") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    if by_step:
        return by_step[-1]
    at_exit = artifact_dir / "failure_screenshot.png"
    return at_exit if at_exit.exists() else None


def _fmt_args(e: dict[str, Any]) -> str:
    skip = {"wall", "monotonic", "pid", "timestamp", "type", "message", "step"}
    parts = [
        f"{k}={json.dumps(v, ensure_ascii=False, default=str)}"
        for k, v in e.items()
        if k not in skip
    ]
    return " ".join(parts)


# --- frames: one per step, from the recording ------------------------------------


def frames_at(video: Path, marks_ms: list[int], width: int = FRAME_WIDTH) -> dict[int, bytes]:
    """PNG bytes of the frame nearest each mark, one sequential decode.

    Returns what it could decode; a missing PyAV or an unreadable video gives an
    empty dict, and the report then simply has no frames.
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


def _data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _png_size(png: bytes) -> tuple[int, int]:
    """Width and height from the IHDR chunk; no decoder needed."""
    if len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    return FRAME_WIDTH, FRAME_WIDTH * 3 // 4


# --- rendering -----------------------------------------------------------------

_CSS = """
<style>
.wt-report{font:13px/1.4 system-ui,Segoe UI,sans-serif;margin:6px 0 10px}
.wt-report .wt-head{margin:6px 0 4px;font-weight:600}
.wt-report .wt-err{white-space:pre-wrap;background:#fff3f3;border-left:3px solid #d33;padding:6px 8px;margin:4px 0;font-family:ui-monospace,Consolas,monospace}
.wt-report .wt-warn{background:#fff8e6;border-left:3px solid #e3a008;padding:6px 8px;margin:4px 0;color:#723b00}
.wt-report table.wt-steps{border-collapse:collapse;width:100%;table-layout:fixed}
.wt-report table.wt-steps th{text-align:left;color:#666;font-weight:600;padding:2px 6px;border-bottom:1px solid #ccc}
.wt-report table.wt-steps td{vertical-align:top;padding:4px 6px;border-top:1px solid #e5e5e5;overflow-wrap:anywhere}
.wt-report .wt-c-status{width:26px}.wt-report .wt-c-dur{width:60px}.wt-report .wt-c-at{width:62px}.wt-report .wt-c-frame{width:372px}
.wt-report .wt-ok{color:#1a7f37;font-weight:700}
.wt-report .wt-fail{color:#c00;font-weight:700}
.wt-report .wt-open{color:#b58900;font-weight:700}
.wt-report .wt-tree-guide{color:#aaa;font-family:monospace;user-select:none;margin-right:4px}
.wt-report .wt-dur,.wt-report .wt-at{color:#666;white-space:nowrap;font-variant-numeric:tabular-nums}
.wt-report .wt-detail{color:#c00;white-space:pre-wrap;margin-top:2px;font-family:ui-monospace,Consolas,monospace;font-size:12px}
.wt-report ul.wt-evs{margin:3px 0 0;padding-left:16px;color:#444}
.wt-report details summary{cursor:pointer;color:#666}
.wt-report .wt-ev{font:12px/1.35 ui-monospace,Consolas,monospace;color:#333;margin:2px 0 0 12px;white-space:pre-wrap}
.wt-report details.wt-frame summary{list-style:none;display:inline-block}
.wt-report details.wt-frame summary::-webkit-details-marker{display:none}
.wt-report .wt-thumb{display:block;width:360px;aspect-ratio:var(--ar,4/3);border:1px solid #ccc;background:#000 center/contain no-repeat;cursor:zoom-in}
.wt-report details.wt-frame[open] .wt-thumb{width:min(92vw,1440px);cursor:zoom-out}
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
    total = len(tree)
    for idx, node in enumerate(tree):
        is_last = idx == total - 1
        tree_prefix = ""
        if depth > 0:
            indent = "&nbsp;&nbsp;&nbsp;&nbsp;" * (depth - 1)
            branch = "&#9492;&#9472;&nbsp;" if is_last else "&#9500;&#9472;&nbsp;"
            tree_prefix = f'<span class="wt-tree-guide">{indent}{branch}</span>'
        dur = f"{node['seconds']:.2f}s" if isinstance(node.get("seconds"), (int, float)) else ""
        ms = frame_mark_for(node, anchor)
        at = format_video_time(ms) if anchor else ""
        # A div with a background inside <details>, not an <img> in an <a>:
        # pytest-html's script collects the <img> elements inside a result's
        # extras for its media viewer and rewrites the first one it finds, and
        # Chromium refuses to navigate a top frame to a data: URL, so a link to
        # the image opens nothing. Clicking the <details> resizes the same
        # element through CSS; one copy of the frame, no script.
        thumb = ""
        if ms in thumbs:
            uri, w, h = thumbs[ms]
            thumb = (
                f'<details class="wt-frame"><summary><div class="wt-thumb" role="img" '
                f'aria-label="frame at {html.escape(at) or f"{ms} ms"}" style="--ar:{w}/{h};'
                f'background-image:url({uri})"></div></summary></details>'
            )
        err = ""
        if node["status"] == "failed":
            text = node.get("detail") or node.get("signature") or node.get("error") or "failed"
            err = f'<div class="wt-detail">{html.escape(str(text))}</div>'
        inner = ""
        if node["events"]:
            shown = node["events"][:EVENTS_SHOWN]
            items = "".join(f"<li>{html.escape(summarize_event(e))}</li>" for e in shown)
            more = len(node["events"]) - len(shown)
            tail = f"<li>&hellip; {more} more</li>" if more > 0 else ""
            raw = "\n".join(
                html.escape(f"{e.get('type', '')}: {e.get('message', '')} {_fmt_args(e)}".rstrip())
                for e in node["events"]
            )
            inner = (
                f'<ul class="wt-evs">{items}{tail}</ul>'
                f"<details><summary>raw events ({len(node['events'])})</summary>"
                f'<div class="wt-ev">{raw}</div></details>'
            )
        rows.append(
            f"<tr><td>{_status_mark(node['status'])}</td>"
            f"<td>{tree_prefix}{html.escape(node['name'])}{err}{inner}</td>"
            f'<td class="wt-dur">{dur}</td><td class="wt-at">{html.escape(at)}</td>'
            f"<td>{thumb}</td></tr>"
        )
        if node["children"]:
            rows.append(render_steps(node["children"], thumbs, anchor, depth + 1))
    return "".join(rows)


def render_steps_table(tree, thumbs, anchor) -> str:
    frame_head = "frame at the end of the step" + (" (click to enlarge)" if thumbs else "")
    head = (
        '<colgroup><col class="wt-c-status"><col><col class="wt-c-dur"><col class="wt-c-at">'
        '<col class="wt-c-frame"></colgroup>'
        f"<thead><tr><th></th><th>step</th><th>took</th><th>video</th><th>{frame_head}</th></tr></thead>"
    )
    return (
        f'<table class="wt-steps">{head}<tbody>{render_steps(tree, thumbs, anchor)}</tbody></table>'
    )


def _artifact_hint(artifact_dir: Path) -> str:
    """Where the reader can actually find the files: the folder's name, and on a
    runner the upload it travels in. A runner-local path is a dead end."""
    name = html.escape(artifact_dir.name)
    if os.environ.get("GITHUB_ACTIONS"):
        return f"artifacts: folder <code>{name}</code> in this job's uploaded artifacts"
    return f"artifacts: <code>{html.escape(str(artifact_dir))}</code>"


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
        for _depth, node in flatten_steps(tree)[:MAX_STEP_FRAMES]:
            ms = frame_mark_for(node, anchor)
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

    census_file = artifact_dir / "window_census.json"
    if census_file.exists():
        try:
            cdata = json.loads(census_file.read_text(encoding="utf-8"))
            leaked = leaked_windows(cdata.get("added", []))
        except Exception:  # noqa: BLE001 - a census we cannot read is not a leak
            leaked = []
        if leaked:
            sample = ", ".join(
                f"<code>{html.escape(w.get('title') or w.get('name') or w.get('class_name') or 'window')}</code>"
                for w in leaked[:3]
            )
            if len(leaked) > 3:
                sample += f" and {len(leaked) - 3} more"
            parts.append(
                f'<div class="wt-warn">&#9888; <strong>{len(leaked)} window(s) still open at exit:</strong> {sample}</div>'
            )

    if tree:
        parts.append('<div class="wt-head">Steps</div>')
        parts.append(render_steps_table(tree, thumbs, anchor))
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
    parts.append(f'<div class="wt-meta">{_artifact_hint(artifact_dir)}</div>')
    parts.append("</div>")
    return "".join(parts), attached


# --- pytest hooks --------------------------------------------------------------


def _html_active(config) -> bool:
    return (
        sys.platform == "win32"
        and config.pluginmanager.hasplugin("html")
        and bool(config.getoption("htmlpath", None))
    )


def _failure_message(report) -> str | None:
    """The exception's own message: what a reader wants before the file:line."""
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    if crash is not None and getattr(crash, "message", None):
        return str(crash.message).strip()
    text = (report.longreprtext or "").strip()
    return text.splitlines()[-1] if text else None


_TESTS_WITH_SESSIONS: list[tuple[str, str, int]] = []  # (nodeid, outcome, sessions)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    item._wintegrate_sessions_before = len(_session_module.RECENT_SESSIONS)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return
    start = getattr(item, "_wintegrate_sessions_before", len(_session_module.RECENT_SESSIONS))
    sessions = _session_module.RECENT_SESSIONS[start:]
    if not sessions:
        return

    for record in sessions:
        record.setdefault("test_id", item.nodeid)
    _TESTS_WITH_SESSIONS.append((item.nodeid, report.outcome, len(sessions)))
    report._wintegrate_session_count = len(sessions)

    if not _html_active(item.config):
        return

    from pytest_html import extras

    attached = []
    error = _failure_message(report) if report.failed else None
    for record in sessions:
        session_failed = report.failed or record.get("failed", False)
        session_error = error or (
            f"session closed on {record.get('error')}" if record.get("failed") else None
        )
        block, media = render_session(Path(record["artifact_dir"]), session_failed, session_error)
        attached.extend(media)  # screenshot first: it is what a reader wants to see
        attached.append(extras.html(block))
    report.extras = getattr(report, "extras", []) + attached


@pytest.hookimpl(optionalhook=True)
def pytest_html_report_title(report):
    report.title = "wintegrate test report"


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_table_row(report, cells):
    """Marks the rows that carry session evidence, so they can be found in a long
    table where every passing row is collapsed."""
    if report.when != "call":
        return
    count = getattr(report, "_wintegrate_session_count", 0)
    if count and len(cells) > 1:
        badge = (
            f' <span title="{count} wintegrate session(s) inside: screenshot, steps, recording">'
            "&#127916;</span></td>"
        )
        cells[1] = cells[1].replace("</td>", badge, 1)


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_summary(prefix, summary, postfix, session):
    n = len(_session_module.RECENT_SESSIONS)
    failed = sum(1 for r in _session_module.RECENT_SESSIONS if r.get("failed"))
    prefix.append(
        f"<p>{n} wintegrate session(s), {failed} closed on an exception. "
        "Rows marked &#127916; carry the evidence: screenshot, error, steps, recording.</p>"
    )
    if _TESTS_WITH_SESSIONS:
        items = "".join(
            f'<li><span class="wt-{"fail" if o == "failed" else "ok"}">{html.escape(o)}</span> '
            f"{html.escape(nodeid)} ({c})</li>"
            for nodeid, o, c in _TESTS_WITH_SESSIONS
        )
        prefix.append(
            f"<details><summary>{len(_TESTS_WITH_SESSIONS)} test(s) with sessions</summary>"
            f"<ul>{items}</ul></details>"
        )


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """A run-level overview at the end of $GITHUB_STEP_SUMMARY."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    sessions = _session_module.RECENT_SESSIONS
    if not sessions:
        return
    failed_sessions = [s for s in sessions if s.get("failed")]
    lines = [
        "",
        "## wintegrate: run overview",
        "",
        f"{len(sessions)} session(s), {len(sessions) - len(failed_sessions)} passed, "
        f"{len(failed_sessions)} failed. Each session's folder is in this job's uploaded artifacts.",
        "",
    ]
    if failed_sessions:
        lines += ["| test | what failed | folder |", "| :--- | :--- | :--- |"]
        for s in failed_sessions:
            test_name = s.get("test_id") or "unknown"
            what = one_line(s.get("detail") or s.get("error") or "failed", 160)
            folder = Path(s.get("artifact_dir") or "").name
            lines.append(f"| `{test_name}` | {what} | `{folder}` |")
        lines.append("")
    try:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass
