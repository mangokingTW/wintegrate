"""Tests for TextActionTimelineRecorder, ActionPlayer, and inspect_desktop_tree."""

import json
from pathlib import Path
from wintegrate import (
    TextActionTimelineRecorder,
    ActionPlayer,
    RecordedAction,
    inspect_desktop_tree,
)


def test_text_action_timeline_recorder(tmp_path):
    log_file = tmp_path / "action_timeline.txt"
    json_file = tmp_path / "action_timeline.json"

    recorder = TextActionTimelineRecorder(output_path=log_file)
    recorder.record_action(
        "type",
        text="Hello World",
        details={"expected_delta": 1},
    )
    recorder.record_action(
        "invoke",
        details={"verify_closed": True},
    )
    recorder.dump_json(json_file)
    recorder.close()

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "TYPE" in content
    assert "Hello World" in content
    assert "INVOKE" in content

    assert json_file.exists()
    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert len(data) == 2
    assert data[0]["action_type"] == "type"
    assert data[0]["text_content"] == "Hello World"


def test_action_player_deserialization(tmp_path):
    json_file = tmp_path / "actions.json"
    actions = [
        {"timestamp_offset": 0.1, "action_type": "set_focus", "target_automation_id": "EditBox", "target_name": None, "target_class": None, "window_title": None, "text_content": None, "details": {}},
        {"timestamp_offset": 0.5, "action_type": "type", "target_automation_id": "EditBox", "target_name": None, "target_class": None, "window_title": None, "text_content": "Text", "details": {}},
    ]
    json_file.write_text(json.dumps(actions), encoding="utf-8")

    player = ActionPlayer.from_json(json_file)
    assert len(player.actions) == 2
    assert player.actions[1].text_content == "Text"


def test_inspect_desktop_tree():
    res = inspect_desktop_tree()
    assert "desktop_windows" in res
    assert isinstance(res["desktop_windows"], list)
