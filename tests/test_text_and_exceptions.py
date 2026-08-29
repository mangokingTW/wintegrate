"""Unit tests for text normalization, line counting, and exception formatting."""

from wintegrate.text import normalize_line_endings, count_lines
from wintegrate.exceptions import WintegrateError


def test_normalize_line_endings():
    assert normalize_line_endings("") == ""
    assert normalize_line_endings("abc") == "abc"
    assert normalize_line_endings("abc\r\ndef") == "abc\ndef"
    assert normalize_line_endings("abc\rdef") == "abc\ndef"
    assert normalize_line_endings("abc\ndef") == "abc\ndef"
    assert normalize_line_endings("line1\r\nline2\rline3\n") == "line1\nline2\nline3\n"


def test_count_lines_trailing_breaks():
    # Empty string has 0 lines
    assert count_lines("") == 0

    # Single line without newline has 1 line
    assert count_lines("single line") == 1

    # Line with trailing newline (Enter pressed once at end) has 2 lines
    assert count_lines("first line\n") == 2
    assert count_lines("first line\r") == 2
    assert count_lines("first line\r\n") == 2

    # Two lines with trailing newline has 3 lines
    assert count_lines("line1\nline2\n") == 3
    assert count_lines("line1\r\nline2\r\n") == 3


def test_exception_diagnostics_formatting():
    err = WintegrateError("Failed step", diagnostics={"hwnd": 1234, "pid": 5678})
    assert "Failed step" in str(err)
    assert "1234" in str(err)
    assert "5678" in str(err)
