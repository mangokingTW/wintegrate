"""Exceptions hierarchy for wintegrate."""

from __future__ import annotations

from typing import Any


class WintegrateError(Exception):
    """Base exception for all wintegrate errors.

    Keyword arguments beyond `diagnostics` are *facts* about the failure, kept on
    the instance and used to build `signature`. Only the keys a class lists in
    `SIGNATURE_KEYS` take part, so two runs that failed the same way produce the
    same signature: no hwnd, pid or timestamp may appear in that list -- one
    per-run integer makes every signature unique and destroys the comparison it
    exists for. The list is reviewed with the class.
    """

    SIGNATURE_KEYS: tuple[str, ...] = ()

    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None, **facts: Any):
        super().__init__(message)
        self.diagnostics = diagnostics or {}
        self.facts: dict[str, Any] = {k: v for k, v in facts.items() if v is not None}

    @property
    def signature(self) -> str:
        """`ClassName[key=value,...]`, from the class's allow-listed facts only.

        `FocusStealDetectedError[foreground_image=explorer.exe]` says what
        `FocusStealDetectedError` alone does not: two of them with different
        foregrounds are two problems, not one that happened twice.
        """
        parts = []
        for key in self.SIGNATURE_KEYS:
            value = self.facts.get(key)
            if value is None or value == "" or value == () or value == []:
                continue
            if isinstance(value, (list, tuple, set, frozenset)):
                value = "+".join(str(v) for v in value)
            parts.append(f"{key}={value}")
        name = type(self).__name__
        return f"{name}[{','.join(parts)}]" if parts else name

    def __str__(self) -> str:
        base = super().__str__()
        if not self.diagnostics:
            return base
        return f"{base} | Diagnostics: {self.diagnostics}"


class WindowDiscoveryTimeoutError(WintegrateError):
    """Raised when a target window cannot be discovered within the configured timeout."""

    SIGNATURE_KEYS = (
        "process_names",
        "window_classes",
        "title_pattern",
        "class_name",
        "title_exact",
    )


class ElementNotFoundError(WintegrateError):
    """Raised when a UIA element or descendant cannot be located."""

    SIGNATURE_KEYS = ("automation_id", "class_name", "control_type_id", "name_contains")


class ActionVerificationError(WintegrateError):
    """Raised when an action is executed but its post-condition cannot be verified."""


class ActionTimeoutError(ActionVerificationError):
    """Raised when waiting for an element state or condition times out."""

    SIGNATURE_KEYS = ("condition",)


TimeoutError = ActionTimeoutError


class TextMismatchError(ActionVerificationError):
    """Raised when text typing or value setting results in a mismatch."""

    SIGNATURE_KEYS = ("foreground_image", "foreground_class", "foreground_is_target_process")


class FocusStealDetectedError(ActionVerificationError):
    """Raised when focus was stolen away during an active verification or typing sequence."""

    SIGNATURE_KEYS = ("foreground_image", "foreground_class", "foreground_is_target_process")


class ArtifactMissingError(WintegrateError):
    """Raised when a file another process should have written did not arrive in time.

    Carries the directory listing at the deadline, so the negative says what it
    examined.
    """


class DiagnosticPipelineError(WintegrateError):
    """Raised when a diagnostic subsystem (e.g. streaming recorder) fails."""

    SIGNATURE_KEYS = ("stage",)


class ValueUnavailableError(WintegrateError):
    """Raised when an element holds no readable text source.

    Not "the text is empty" — that is an answer, and it is returned as `''`. This
    is "nothing in this element can be asked what text it holds", where the only
    thing left to report would be the element's Name, which is its *label*.
    Substituting a label for content is how an empty field comes back looking
    like it has something in it.
    """


class ConsoleHostEndedError(WintegrateError, KeyboardInterrupt):
    """The console this process was attached to was destroyed while the session was starting.

    What reaches the traceback is a `KeyboardInterrupt` at whatever line the
    process happened to be on: destroying a console delivers CTRL_C_EVENT to every
    process attached to it. This class says what that interrupt was. It is still a
    `KeyboardInterrupt`, so a pytest run aborts once, as it should on a machine
    whose console is gone, instead of producing one error per remaining test.

    Raised only on evidence: the console probes that answered at preflight no
    longer do. A real Ctrl-C with the console intact is re-raised untouched.
    """

    SIGNATURE_KEYS = ("ended", "peers", "phase")
