"""expect_artifact: waits for a file to be there, whole; says what it saw when it is not."""

from __future__ import annotations

import threading
import time

import pytest

from wintegrate import ArtifactMissingError, expect_artifact


def test_a_file_already_there_returns_at_once(tmp_path):
    f = tmp_path / "out.json"
    f.write_text("{}")
    t0 = time.monotonic()
    assert expect_artifact(f, timeout=2.0, poll=0.05) == f
    assert time.monotonic() - t0 < 1.0


def test_a_file_that_appears_late_is_waited_for(tmp_path):
    f = tmp_path / "late.txt"

    def writer():
        time.sleep(0.3)
        f.write_text("hello")

    threading.Thread(target=writer, daemon=True).start()
    assert expect_artifact(f, timeout=3.0, poll=0.05).read_text() == "hello"


def test_a_missing_file_names_what_the_directory_actually_held(tmp_path):
    (tmp_path / "other.tmp").write_text("partial")
    (tmp_path / "sub").mkdir()
    with pytest.raises(ArtifactMissingError) as info:
        expect_artifact(tmp_path / "result.json", timeout=0.3, poll=0.05)
    text = str(info.value)
    assert "result.json" in text and "never appeared" in text
    assert "other.tmp (7 bytes)" in text and "sub/" in text
    assert info.value.diagnostics["state"] == "never appeared"


def test_an_empty_file_is_not_an_artifact(tmp_path):
    f = tmp_path / "empty.bin"
    f.write_bytes(b"")
    with pytest.raises(ArtifactMissingError, match="exists but is empty"):
        expect_artifact(f, timeout=0.3, poll=0.05)
