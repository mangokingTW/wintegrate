"""Text manipulation and line-break normalization utilities for Windows CI testing.

In Windows controls (like Notepad), line endings may return as \\r, \\r\\n, or \\n.
Counting raw '\\n' fails when controls return only '\\r'.
Standard Python str.splitlines() drops trailing delimiters ('abc\\r' -> ['abc']),
which obscures trailing newlines created by pressing Enter at the end of a document.
"""

from __future__ import annotations


def normalize_line_endings(text: str) -> str:
    """Normalizes all Windows/DOS/Unix line endings (\\r\\n, \\r, \\n) to standard \\n."""
    if not text:
        return ""
    # Replace \r\n first, then remaining standalone \r with \n
    return text.replace("\r\n", "\n").replace("\r", "\n")


def count_lines(text: str) -> int:
    """
    Counts lines accurately including empty trailing lines created by Enter.

    Examples:
        count_lines("") -> 0
        count_lines("hello") -> 1
        count_lines("hello\\n") -> 2
        count_lines("hello\\r") -> 2
        count_lines("hello\\r\\nworld") -> 2
        count_lines("hello\\r\\nworld\\r\\n") -> 3
    """
    if not text:
        return 0
    norm = normalize_line_endings(text)
    # Split on literal \n without dropping trailing empty parts
    return len(norm.split("\n"))
