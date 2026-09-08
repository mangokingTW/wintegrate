"""Keywords over the same verified operations the Python API exposes.

Every keyword calls the library function it is named after; nothing is
re-implemented here, and nothing is caught. A `type_verified` that times out
raises in Robot exactly as it raises in pytest, because a keyword that swallowed
it would turn a red step into a green one.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from wintegrate import CALCULATOR, NOTEPAD, AppSpec, Session, SessionConfig, UiaElement
from wintegrate.apps import AppHandle
from wintegrate.interop import send_keys as _send_keys
from wintegrate.locators import Locator

try:
    from robot.api import logger
    from robot.api.deco import keyword, library
    from robot.libraries.BuiltIn import BuiltIn
except ImportError:  # pragma: no cover - documented on a machine without robot

    def keyword(name=None, tags=(), types=()):  # type: ignore[misc]
        def decorate(fn):
            return fn

        return decorate if not callable(name) else name

    def library(*_args, **_kwargs):  # type: ignore[misc]
        def decorate(cls):
            return cls

        return decorate

    logger = None
    BuiltIn = None

KNOWN_APPS: dict[str, AppSpec] = {
    "notepad": NOTEPAD,
    "calculator": CALCULATOR,
}


def resolve_app(app: str | AppSpec) -> AppSpec | list[str]:
    """A known name (`notepad`, `calculator`), an AppSpec, or a command line.

    A bare word that is not a known name is a command: `wt.exe -w new` launches
    exactly that and lets discovery find whatever window it produces.
    """
    if isinstance(app, AppSpec):
        return app
    spec = KNOWN_APPS.get(app.strip().casefold())
    if spec is not None:
        return spec
    return app.split()


@library(scope="SUITE", version="0.6.2", listener="SELF")
class WintegrateLibrary:
    """Drives a Windows desktop application through wintegrate from Robot Framework.

    One session per suite. `Start Session` and `Stop Session` are keywords so a
    suite can place them; the listener closes the session at suite end if the
    suite did not, so a failing setup never leaks a recorder or an app.
    """

    ROBOT_LISTENER_API_VERSION = 3

    def __init__(
        self,
        artifact_dir: str = "robot-artifacts",
        record_video: bool = True,
        sanitize_runner: str = "auto",
        default_timeout: float = 15.0,
    ):
        self._config = SessionConfig(
            artifact_dir=Path(artifact_dir),
            record_video=record_video,
            sanitize_runner=sanitize_runner,
            default_timeout=default_timeout,
        )
        self._session: Session | None = None
        self._apps: list[AppHandle] = []

    # --- listener -----------------------------------------------------------

    def _start_test(self, data, result) -> None:
        if self._session is None:
            return
        self._session.log_event("robot_test_start", data.name)
        recorder = self._session.recorder
        if recorder is not None:
            recorder.caption, recorder.caption_subtitle = data.name, data.source.name

    def _end_test(self, data, result) -> None:
        if self._session is None:
            return
        if result.failed:
            try:
                safe = re.sub(r"[^0-9A-Za-z._-]+", "-", data.name).strip("-") or "test"
                path = self._session.capture_screenshot(f"failed-{safe}")
                self._embed(path)
            except Exception as exc:  # noqa: BLE001 - evidence must not mask the failure
                self._log(f"failure screenshot not captured: {type(exc).__name__}: {exc}")
        self._session.log_event("robot_test_end", data.name, status=result.status)
        recorder = self._session.recorder
        if recorder is not None:
            recorder.caption, recorder.caption_subtitle = "", ""

    def _end_suite(self, data, result) -> None:
        if self._session is not None:
            self.stop_session()

    # --- session ------------------------------------------------------------

    @keyword("Start Session")
    def start_session(self) -> None:
        """Opens the wintegrate session: preflight, runner sweep, recording."""
        if self._session is not None:
            return
        session = Session(self._config)
        session.__enter__()
        self._session = session
        self._log(f"session artifacts: {self._config.artifact_dir}")

    @keyword("Stop Session")
    def stop_session(self) -> None:
        """Closes every launched app and the session, writing the artifact index."""
        for app in reversed(self._apps):
            try:
                app.close()
            except Exception as exc:  # noqa: BLE001
                self._log(f"closing {app.window!r}: {type(exc).__name__}: {exc}")
        self._apps.clear()
        if self._session is not None:
            session, self._session = self._session, None
            session.__exit__(None, None, None)
            recording = self._config.artifact_dir / "session_recording.mp4"
            if recording.exists():
                self._embed_video(recording)

    @keyword("Log Event")
    def log_event(self, event_type: str, message: str) -> None:
        """Writes a line into the session's event journal."""
        self._require_session().log_event(event_type, message)

    @keyword("Capture Screenshot")
    def capture_screenshot(self, name: str) -> str:
        """Saves a screenshot into the artifact directory and shows it in the log."""
        path = self._require_session().capture_screenshot(name)
        self._embed(path)
        return str(path)

    # --- applications ---------------------------------------------------------

    @keyword("Launch App")
    def launch_app(self, app: str, fresh: str = "auto") -> AppHandle:
        """Launches `notepad`, `calculator`, or a command line, and returns its handle.

        Discovery keys off process image names and window classes, so the same
        keyword works on a localized Windows. `fresh=auto` sweeps leftover
        instances on CI before launching.
        """
        session = self._require_session()
        handle = session.app(resolve_app(app), fresh=self._fresh(fresh))
        handle.__enter__()
        self._apps.append(handle)
        self._log(f"launched {handle.window!r}")
        return handle

    @keyword("Close App")
    def close_app(self, app: AppHandle) -> None:
        """Closes the window and ends the process, verified."""
        app.close()
        if app in self._apps:
            self._apps.remove(app)

    @keyword("Find Text Input")
    def find_text_input(self, app: AppHandle, timeout: float = 20.0) -> UiaElement:
        """The app's main editable text control, without naming a control."""
        return app.find_text_input(timeout=timeout)

    @keyword("Locate")
    def locate(self, app: AppHandle, selector: str) -> Locator:
        """A Playwright-style locator, e.g. `role=button[name=OK]`."""
        return app.locator(selector)

    @keyword("Get By Role")
    def get_by_role(self, app: AppHandle, role: str, name: str | None = None) -> Locator:
        return app.get_by_role(role, name=name)

    # --- actions ------------------------------------------------------------

    @keyword("Type Verified")
    def type_verified(
        self,
        element: UiaElement,
        text: str,
        expected_line_count_delta: int | None = None,
        verify_contains: str | None = None,
    ) -> None:
        """Sends real keystrokes and returns only once the text is in the control."""
        element.type_verified(
            text,
            expected_line_count_delta=expected_line_count_delta,
            verify_contains=verify_contains,
        )

    @keyword("Get Value")
    def get_value(self, element: UiaElement) -> str:
        """The control's current text."""
        return element.get_value()

    @keyword("Click")
    def click(self, target: Locator | UiaElement) -> None:
        """Clicks a locator or element; raises if there is no rectangle to aim at."""
        target.click()

    @keyword("Send Keys")
    def send_keys(self, spec: str) -> None:
        """A SendKeys-style spec to whatever has focus: `{ESC}`, `^a`, `hello{ENTER}`."""
        if not _send_keys(spec):
            raise AssertionError(f"the system refused to inject {spec!r}")

    # --- focus ----------------------------------------------------------------

    @keyword("Get Focused Element")
    def get_focused_element(self) -> UiaElement:
        """The element that has keyboard focus, from UI Automation."""
        return UiaElement.get_focused()

    @keyword("Describe Element")
    def describe_element(self, element: UiaElement) -> str:
        """One line: control type, class, name, automation id, patterns."""
        return element.describe()

    @keyword("Focused Element Class Should Be")
    def focused_element_class_should_be(self, class_name: str) -> None:
        """Fails unless keyboard focus is on an element of this class."""
        focused = UiaElement.get_focused()
        if focused.class_name != class_name:
            raise AssertionError(
                f"focus is on {focused.describe()} rect={focused.bounding_rectangle}, "
                f"expected class {class_name!r}"
            )

    # --- helpers --------------------------------------------------------------

    def _require_session(self) -> Session:
        if self._session is None:
            self.start_session()
        assert self._session is not None
        return self._session

    @staticmethod
    def _fresh(value: str) -> bool | str:
        lowered = value.strip().casefold()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
        return "auto"

    def _log(self, message: str) -> None:
        if logger is not None:
            logger.info(message)

    def _relative_to_log(self, path: Path) -> str:
        out = None
        if BuiltIn is not None:
            try:
                out = BuiltIn().get_variable_value("${OUTPUT DIR}")
            except Exception:  # noqa: BLE001 - outside a run there is no variable
                out = None
        base = Path(out) if out else Path.cwd()
        try:
            return os.path.relpath(path.resolve(), base).replace(os.sep, "/")
        except ValueError:  # different drives
            return path.resolve().as_uri()

    def _embed(self, path: Path) -> None:
        if logger is None:
            return
        src = self._relative_to_log(Path(path))
        logger.info(f'<a href="{src}"><img src="{src}" width="800"></a>', html=True)

    def _embed_video(self, path: Path) -> None:
        if logger is None:
            return
        src = self._relative_to_log(path)
        logger.info(
            f'<video src="{src}" controls width="800"></video><br><a href="{src}">{path.name}</a>',
            html=True,
        )
