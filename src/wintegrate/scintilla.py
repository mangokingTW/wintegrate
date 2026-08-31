"""Scintilla-specific queries, for editors that embed it — Notepad++, Notepad4.

Scintilla is not a UIA control in any useful sense. It appears in the tree as a
`Pane` supporting **no patterns at all**, so nothing in the pattern-based API
reaches it. Reading its text works anyway, because `get_value()` falls through to
`WM_GETTEXT` and USER32 marshals that buffer across the process boundary.

What `WM_GETTEXT` cannot answer is everything else: how many lines there are,
what is selected, whether the document is dirty. Those need Scintilla's own
messages, and this module is the escape hatch for them.

**Only scalar messages.** A Scintilla message that takes a pointer — `SCI_GETTEXT`
and friends — expects an address in the *caller's* address space, and nothing
marshals it. Measured against Notepad++ on Windows 11 ARM64: `SCI_GETTEXT` with a
local buffer returns 0 and leaves the buffer empty, and so does the same call
with a buffer allocated in the target process via `VirtualAllocEx`. Messages that
return a number work fine. Every constant here was verified against a live
Notepad++ rather than copied from a header.

Deliberately absent: a char-by-char text reader built on `SCI_GETCHARAT`. It
would work, at one cross-process round trip per character, while `WM_GETTEXT`
already returns the whole document in one — an API that invites an O(n) IPC loop
for something already available is a trap, not a feature.
"""

from __future__ import annotations

import enum
import logging
from ctypes import wintypes

from wintegrate.interop import user32

logger = logging.getLogger(__name__)

SCINTILLA_CLASS_NAMES = ("Scintilla", "Scintilla_DirectWrite")

# Verified against Notepad++ 8.x; values cross-checked against each other
# (SCI_POSITIONFROMLINE agreeing with SCI_GETSELECTIONSTART, SCI_LINELENGTH with
# the line's contents, SCI_GETLENGTH with the document read via WM_GETTEXT).
SCI_GETLENGTH = 2006
SCI_GETCHARAT = 2007
SCI_GETCURRENTPOS = 2008
SCI_GETEOLMODE = 2030
SCI_GETTABWIDTH = 2121
SCI_GETFIRSTVISIBLELINE = 2152
SCI_GETCODEPAGE = 2137
SCI_GETSELECTIONSTART = 2143
SCI_GETSELECTIONEND = 2145
SCI_GETLINECOUNT = 2154
SCI_GETMODIFY = 2159
SCI_LINEFROMPOSITION = 2166
SCI_POSITIONFROMLINE = 2167
SCI_LINELENGTH = 2350


class EolMode(enum.IntEnum):
    """How Scintilla ends its lines, as an enum so a value reads as what it means."""

    CRLF = 0
    CR = 1
    LF = 2


def is_scintilla(class_name: str) -> bool:
    """Whether a window class is a Scintilla editor."""
    return class_name in SCINTILLA_CLASS_NAMES


class ScintillaView:
    """Scalar queries against one Scintilla control, addressed by its HWND.

    Named a *view* rather than an editor because it only reads. Typing goes
    through the normal input path — `send_keys`, `send_physical_keys` — so that a
    test exercises the same route a user does.
    """

    def __init__(self, hwnd: int):
        if not hwnd:
            raise ValueError("ScintillaView needs a window handle")
        self.hwnd = hwnd

    @classmethod
    def from_element(cls, element) -> ScintillaView:
        """Builds a view from the `UiaElement` that `find_text_input()` returned.

        Raises rather than returning None: a caller that reached for Scintilla
        queries on a non-Scintilla control has a bug, and silently handing back
        something inert would surface as a wrong answer somewhere else.
        """
        hwnd = element._element.CurrentNativeWindowHandle
        if not hwnd:
            raise ValueError(f"Element has no native window handle: {element!r}")
        name = element.class_name
        if not is_scintilla(name):
            raise ValueError(f"Element is not a Scintilla control, it is {name!r}: {element!r}")
        return cls(int(hwnd))

    def __repr__(self) -> str:
        try:
            return (
                f"<ScintillaView hwnd={self.hwnd:#x} lines={self.line_count} "
                f"length={self.length} modified={self.is_modified}>"
            )
        except Exception:
            return f"<ScintillaView hwnd={self.hwnd:#x} (unreadable)>"

    def _send(self, message: int, wparam: int = 0, lparam: int = 0) -> int:
        return int(
            user32.SendMessageW(
                wintypes.HWND(self.hwnd),
                wintypes.UINT(message),
                wintypes.WPARAM(wparam),
                wintypes.LPARAM(lparam),
            )
        )

    @property
    def length(self) -> int:
        """Document length in **bytes**, not characters.

        Scintilla stores UTF-8 (see `codepage`), so this exceeds the character
        count whenever the document holds anything outside ASCII — measured at 329
        against 319 UTF-16 characters for a document with five CJK characters.
        Compare against `len(text)` only after deciding which you mean.
        """
        return self._send(SCI_GETLENGTH)

    @property
    def line_count(self) -> int:
        """Number of lines, as Scintilla counts them.

        The reason this module exists. Counting `\\n` in the text is the obvious
        alternative and is wrong often enough to matter: Windows editors end lines
        with `\\r\\n`, some controls report `\\r`, and a trailing newline makes the
        answer ambiguous. Scintilla knows.
        """
        return self._send(SCI_GETLINECOUNT)

    @property
    def codepage(self) -> int:
        """Scintilla's codepage; 65001 means UTF-8, which is the modern default."""
        return self._send(SCI_GETCODEPAGE)

    @property
    def eol_mode(self) -> EolMode:
        return EolMode(self._send(SCI_GETEOLMODE))

    @property
    def tab_width(self) -> int:
        return self._send(SCI_GETTABWIDTH)

    @property
    def is_modified(self) -> bool:
        """Whether the document has unsaved changes."""
        return bool(self._send(SCI_GETMODIFY))

    @property
    def current_position(self) -> int:
        return self._send(SCI_GETCURRENTPOS)

    @property
    def current_line(self) -> int:
        """Zero-based line index of the caret."""
        return self._send(SCI_LINEFROMPOSITION, self.current_position)

    @property
    def first_visible_line(self) -> int:
        return self._send(SCI_GETFIRSTVISIBLELINE)

    @property
    def selection(self) -> tuple[int, int]:
        """The selection as (start, end) byte offsets; equal when nothing is selected."""
        return self._send(SCI_GETSELECTIONSTART), self._send(SCI_GETSELECTIONEND)

    @property
    def has_selection(self) -> bool:
        start, end = self.selection
        return start != end

    def line_length(self, line: int) -> int:
        """Length of `line` in bytes, **including** its line ending."""
        return self._send(SCI_LINELENGTH, line)

    def position_of_line(self, line: int) -> int:
        """Byte offset where `line` starts."""
        return self._send(SCI_POSITIONFROMLINE, line)

    def line_of_position(self, position: int) -> int:
        """Zero-based line containing `position`."""
        return self._send(SCI_LINEFROMPOSITION, position)

    def char_at(self, position: int) -> int:
        """The byte at `position`.

        One round trip per byte, so this is for spot checks — reading a document
        this way is what `WM_GETTEXT` (via `UiaElement.get_value()`) is for.
        """
        return self._send(SCI_GETCHARAT, position) & 0xFF
