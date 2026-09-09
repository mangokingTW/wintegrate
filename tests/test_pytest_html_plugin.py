"""The pytest-html plugin's pure parts: reading the journal into a step tree and
rendering it. The rendering is checked for what a reader needs to see -- a
cross on the failed step, its signature, the events inside -- not for markup."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("pytest_html", reason="pip install wintegrate[html]")

from wintegrate.pytest_plugin import (  # noqa: E402
    build_step_tree,
    flatten_steps,
    last_failure_screenshot,
    read_journal,
    render_steps,
)


def ev(kind, mono, message="", **extra):
    return {"type": kind, "monotonic": mono, "message": message, **extra}


EVENTS = [
    ev("session_open", 100.0, "journal opened"),
    ev("step_start", 101.0, "Given the app is running"),
    ev("launch_app", 101.1, "Launching notepad", step="Given the app is running"),
    ev("step_start", 101.5, "find the editor"),
    ev("step_ok", 102.0, "find the editor", seconds=0.5),
    ev("step_ok", 102.1, "Given the app is running", seconds=1.1),
    ev("step_start", 103.0, "When I type"),
    ev(
        "step_failed",
        104.0,
        "When I type",
        seconds=1.0,
        error="TextMismatchError",
        signature="TextMismatchError[expected=hello]",
    ),
    ev("step_start", 105.0, "left open by a kill"),
]


def test_steps_nest_the_way_they_ran():
    tree = build_step_tree(EVENTS)
    assert [n["name"] for n in tree] == [
        "Given the app is running",
        "When I type",
        "left open by a kill",
    ]
    given = tree[0]
    assert [c["name"] for c in given["children"]] == ["find the editor"]
    assert given["status"] == "ok" and given["seconds"] == 1.1
    assert [e["type"] for e in given["events"]] == ["launch_app"]


def test_a_failed_step_keeps_its_signature_and_an_open_one_is_not_guessed():
    tree = build_step_tree(EVENTS)
    failed = tree[1]
    assert failed["status"] == "failed"
    assert failed["signature"] == "TextMismatchError[expected=hello]"
    assert tree[2]["status"] == "open" and tree[2]["end"] is None
    assert [(d, n["name"]) for d, n in flatten_steps(tree)] == [
        (0, "Given the app is running"),
        (1, "find the editor"),
        (0, "When I type"),
        (0, "left open by a kill"),
    ]


def test_rendering_shows_the_cross_the_signature_and_the_inner_events():
    tree = build_step_tree(EVENTS)
    out = render_steps(tree, thumbs={}, anchor=None)
    assert out.count("&#10003;") == 2  # two ok steps
    assert out.count("&#10007;") == 1  # one failed
    assert "&#8230;" in out  # the open one
    assert "TextMismatchError[expected=hello]" in out
    assert "launch_app: Launching notepad" in out
    assert "1.10s" in out


def test_rendering_places_the_frame_at_the_step_end_and_names_the_video_time():
    """The frame is the step's result (or the moment it failed), so it is taken at
    the end; a step that never ended keeps its start."""
    tree = build_step_tree(EVENTS)
    anchor = {"monotonic_start": 100.0}
    # "Given the app is running" ended at 102.1 -> 2100 ms; the open step started at 105.0
    out = render_steps(
        tree,
        thumbs={
            2100: ("data:image/png;base64,AAAA", 720, 540),
            5000: ("data:image/png;base64,BBBB", 720, 540),
        },
        anchor=anchor,
    )
    assert "background-image:url(data:image/png;base64,AAAA)" in out
    assert "background-image:url(data:image/png;base64,BBBB)" in out
    assert "<img" not in out  # pytest-html's media viewer rewrites <img> inside extras
    assert 'href="data:' not in out  # Chromium refuses top-frame navigation to data: URLs
    assert '<details class="wt-frame">' in out
    assert '<td class="wt-at">0:02.1</td>' in out
    assert '<td class="wt-at">0:04.0</td>' in out  # the failed step ended at 104.0


def test_the_jsonl_is_read_line_by_line_and_a_cut_line_is_dropped(tmp_path):
    lines = [json.dumps(e) for e in EVENTS[:3]] + ['{"type": "step_start", "mess']
    (tmp_path / "session_events.jsonl").write_text("\n".join(lines), encoding="utf-8")
    events = read_journal(tmp_path)
    assert [e["type"] for e in events] == ["session_open", "step_start", "launch_app"]


def test_the_newest_failure_screenshot_wins(tmp_path):
    assert last_failure_screenshot(tmp_path) is None
    (tmp_path / "failure_screenshot.png").write_bytes(b"a")
    (tmp_path / "failure-When-I-type.png").write_bytes(b"b")
    import os
    import time

    now = time.time()
    os.utime(tmp_path / "failure_screenshot.png", (now - 10, now - 10))
    os.utime(tmp_path / "failure-When-I-type.png", (now, now))
    assert last_failure_screenshot(tmp_path).name == "failure-When-I-type.png"


def test_render_session_shows_window_leaks(tmp_path):
    from wintegrate.pytest_plugin import render_session

    census_file = tmp_path / "window_census.json"
    census_file.write_text(
        json.dumps(
            {"added": [{"title": "Dialog Leaked", "class_name": "#32770", "is_visible": True}]}
        ),
        encoding="utf-8",
    )
    block, _ = render_session(tmp_path, failed=False, error=None)
    assert "still open at exit" in block
    assert "Dialog Leaked" in block


def test_a_failed_step_shows_the_exception_message_not_only_its_type():
    events = EVENTS[:7] + [
        {**EVENTS[7], "detail": "[When I type] expected 'hello', the editor holds 'hel'"}
    ]
    tree = build_step_tree(events)
    out = render_steps(tree, thumbs={}, anchor=None)
    assert "expected &#x27;hello&#x27;, the editor holds &#x27;hel&#x27;" in out


def test_events_inside_a_step_read_as_sentences_with_the_raw_lines_on_demand():
    tree = build_step_tree(EVENTS)
    out = render_steps(tree, thumbs={}, anchor=None)
    assert "<li>launched notepad</li>" in out
    assert "raw events (1)" in out
    assert "launch_app: Launching notepad" in out


def test_the_table_has_a_header_naming_its_columns():
    from wintegrate.pytest_plugin import render_steps_table

    out = render_steps_table(build_step_tree(EVENTS), {}, None)
    assert "<th>step</th><th>took</th><th>video</th>" in out
    assert "frame at the end of the step" in out


def test_leak_warning_ignores_the_process_own_plumbing(tmp_path):
    from wintegrate.pytest_plugin import render_session

    (tmp_path / "window_census.json").write_text(
        json.dumps(
            {
                "added": [
                    {"title": "", "class_name": "MessageWindowClass", "is_visible": False},
                    {"title": "Tooltip", "class_name": "tooltips_class32", "is_visible": True},
                    {"title": "Untitled - Notepad", "class_name": "Notepad", "is_visible": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    block, _ = render_session(tmp_path, failed=False, error=None)
    assert "1 window(s) still open at exit" in block
    assert "Untitled - Notepad" in block
    assert "MessageWindowClass" not in block


def test_render_session_attaches_screenshot_when_failed(tmp_path):
    from wintegrate.pytest_plugin import render_session

    (tmp_path / "failure_screenshot.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    )
    block, media = render_session(tmp_path, failed=True, error="AssertionError: deliberate")
    assert len(media) == 1
    assert "AssertionError: deliberate" in block


def test_step_summary_lists_steps_in_run_order_with_nesting_and_one_line_errors(
    tmp_path, monkeypatch
):
    """The GitHub Step Summary must read like the run: parent before child, indented,
    the failure on one line inside its callout, and no runner-local paths."""
    from wintegrate import Session, SessionConfig

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_x.py::test_y (call)")
    session = Session(SessionConfig(artifact_dir=tmp_path / "art", record_video=False))
    session.logs = EVENTS[:7] + [
        {**EVENTS[7], "detail": "expected 'hello'\nthe editor holds 'hel'"}
    ]
    (tmp_path / "art").mkdir()
    session._write_step_summary(AssertionError, AssertionError("boom"))
    text = summary.read_text(encoding="utf-8")
    assert text.index("| Given the app is running") < text.index("find the editor")
    assert "| &nbsp;&nbsp;&nbsp;&nbsp;find the editor" in text
    assert (
        "> `AssertionError` in step **When I type**: expected 'hello' the editor holds 'hel'"
        in text
    )
    assert str(tmp_path) not in text  # folder name only, never the runner's path
    assert "- files:" not in text
