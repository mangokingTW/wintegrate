"""Launched children write to the session's artifact dir, never to this process's stdio."""

from wintegrate import diagnostics
from wintegrate.window import _child_output_note


def test_no_session_means_no_files(monkeypatch):
    monkeypatch.setattr(diagnostics, "_launch_output_dir", None)
    assert diagnostics.launch_output_paths() is None


def test_a_session_hands_out_fresh_numbered_pairs(tmp_path, monkeypatch):
    diagnostics.set_launch_output_dir(tmp_path)
    try:
        monkeypatch.setattr(diagnostics, "_launch_seq", 0)
        a = diagnostics.launch_output_paths()
        b = diagnostics.launch_output_paths()
        assert a is not None and b is not None
        assert a[0].name == "launched_01.out" and a[1].name == "launched_01.err"
        assert b[0].name == "launched_02.out" and a[0].parent == tmp_path
    finally:
        diagnostics.set_launch_output_dir(None)
    assert diagnostics.launch_output_paths() is None


def test_timeout_note_quotes_only_non_empty_output(tmp_path):
    out, err = tmp_path / "launched_01.out", tmp_path / "launched_01.err"
    out.write_text("", encoding="utf-8")
    err.write_text("Traceback\nModuleNotFoundError: No module named 'av'\n", encoding="utf-8")
    note = _child_output_note((out, err))
    assert "stdout" not in note
    assert "stderr (launched_01.err)" in note and "ModuleNotFoundError" in note
    assert _child_output_note(None) == ""
    assert _child_output_note((tmp_path / "missing.out", tmp_path / "missing.err")) == ""


def test_timeout_note_is_bounded(tmp_path):
    out, err = tmp_path / "a.out", tmp_path / "a.err"
    out.write_text("\n".join(f"line {i}" for i in range(20)), encoding="utf-8")
    err.write_text("", encoding="utf-8")
    note = _child_output_note((out, err), limit=3)
    assert "line 2" in note and "line 3" not in note and "17 more line(s)" in note
