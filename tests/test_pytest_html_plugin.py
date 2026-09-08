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
    assert len(flatten_steps(tree)) == 4


def test_rendering_shows_the_cross_the_signature_and_the_inner_events():
    tree = build_step_tree(EVENTS)
    out = render_steps(tree, thumbs={}, anchor=None)
    assert out.count("&#10003;") == 2  # two ok steps
    assert out.count("&#10007;") == 1  # one failed
    assert "&#8230;" in out  # the open one
    assert "TextMismatchError[expected=hello]" in out
    assert "launch_app: Launching notepad" in out
    assert "1.10s" in out


def test_rendering_places_a_thumbnail_at_the_step_start_time():
    tree = build_step_tree(EVENTS)
    anchor = {"monotonic_start": 100.0}
    out = render_steps(tree, thumbs={1000: ("data:image/png;base64,AAAA", 240, 180)}, anchor=anchor)
    assert "background-image:url(data:image/png;base64,AAAA)" in out
    assert "<img" not in out  # pytest-html's media viewer rewrites <img> inside extras


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
        json.dumps({"added": [{"name": "Dialog Leaked", "class_name": "#32770"}]}),
        encoding="utf-8",
    )
    block, _ = render_session(tmp_path, failed=False, error=None)
    assert "Window leak detected" in block
    assert "Dialog Leaked" in block


def test_render_steps_thumbnails_have_zoom_link():
    tree = build_step_tree(EVENTS)
    anchor = {"monotonic_start": 100.0}
    out = render_steps(tree, thumbs={1000: ("data:image/png;base64,AAAA", 240, 180)}, anchor=anchor)
    assert '<a class="wt-thumb-link"' in out
    assert 'href="data:image/png;base64,AAAA"' in out
    assert 'target="_blank"' in out
