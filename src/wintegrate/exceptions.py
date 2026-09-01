"""Exceptions hierarchy for wintegrate."""

from __future__ import annotations

from typing import Any


class WintegrateError(Exception):
    """Base exception for all wintegrate errors."""

    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}

    def __str__(self) -> str:
        base = super().__str__()
        if not self.diagnostics:
            return base
        return f"{base} | Diagnostics: {self.diagnostics}"


class WindowDiscoveryTimeoutError(WintegrateError):
    """Raised when a target window cannot be discovered within the configured timeout."""


class ElementNotFoundError(WintegrateError):
    """Raised when a UIA element or descendant cannot be located."""


class ActionVerificationError(WintegrateError):
    """Raised when an action is executed but its post-condition cannot be verified."""


class TextMismatchError(ActionVerificationError):
    """Raised when text typing or value setting results in a mismatch."""


class FocusStealDetectedError(ActionVerificationError):
    """Raised when focus was stolen away during an active verification or typing sequence."""


class DiagnosticPipelineError(WintegrateError):
    """Raised when a diagnostic subsystem (e.g. streaming recorder) fails."""


class ValueUnavailableError(WintegrateError):
    """Raised when an element holds no readable text source.

    Not "the text is empty" — that is an answer, and it is returned as `''`. This
    is "nothing in this element can be asked what text it holds", where the only
    thing left to report would be the element's Name, which is its *label*.
    Substituting a label for content is how an empty field comes back looking
    like it has something in it.
    """
