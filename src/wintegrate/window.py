"""Window management, discovery, and process lifecycle."""

from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from wintegrate.diagnostics import WindowCensus, WindowSnapshot, capture_window_image
from wintegrate.element import UiaElement
from wintegrate.exceptions import (
    ActionVerificationError,
    ElementNotFoundError,
    WindowDiscoveryTimeoutError,
)
from wintegrate.interop import (
    RECT,
    SM_CXVIRTUALSCREEN,
    SM_CYVIRTUALSCREEN,
    SM_XVIRTUALSCREEN,
    SM_YVIRTUALSCREEN,
    SW_RESTORE,
    SWP_SHOWWINDOW,
    VK_CAPITAL,
    CloakReason,
    DisplayAffinity,
    attach_to_input_desktop,
    describe_dialog_contents,
    find_child_windows,
    get_composition_string,
    get_foreground_window,
    get_ime_conversion,
    get_ime_status,
    get_keyboard_layout,
    get_process_image_name,
    get_toggle_key_state,
    get_window_class,
    get_window_cloak_reason,
    get_window_display_affinity,
    get_window_pid,
    get_window_title,
    kernel32,
    load_keyboard_layout,
    set_caps_lock,
    set_ime_conversion,
    set_ime_open,
    user32,
)

logger = logging.getLogger(__name__)

# Locale-independent search ladder for an application's primary text-input element:
# window classes and UIA control types don't change with the UI language, so no
# localized control names are needed.
DEFAULT_TEXT_INPUT_LADDER: tuple[dict, ...] = (
    {"class_name": "RichEditD2DPT"},  # Win11 tabbed Notepad
    {"class_name": "Edit"},  # classic Win32 edit control
    # Scintilla — Notepad++, Notepad4, and anything else embedding the same
    # editor. It appears in the UIA tree as a Pane supporting *no* patterns, so
    # the control-type entries below never match it, and find_text_input used to
    # fail on Notepad++ despite the editor being right there. Reading works
    # anyway: get_value() falls through to WM_GETTEXT, which USER32 marshals
    # across the process boundary — unlike the SCI_* messages, which take a
    # pointer into the caller's address space and return nothing when sent from
    # outside.
    {"class_name": "Scintilla"},
    {"control_type_id": 50030},  # UIA Document
    {"control_type_id": 50004},  # UIA Edit
    # Note: "NotepadTextBox" is deliberately absent — it is the *container* hwnd
    # around the RichEdit child, so it wins the race while the child is still
    # materializing, and its get_value() is always empty (verification can never
    # pass against it).
)

# An embedded WebView2 publishes its Chromium accessibility tree into the host's
# UIA tree, and its root node is a Document — so it matches the Document rung of
# the ladder above and outranks the app's own Edit controls, which sit on the rung
# below. Measured against Files 4.2.9, whose release-notes pane made
# find_text_input return the blog post's document instead of the path box.
#
# Reordering the two rungs would only move the problem: an app whose editor is a
# Document (rich-text controls with no known window class) would then lose to any
# unrelated Edit, such as a search box. The browser root is the part that is never
# the answer, so reject it by name and carry on down the ladder.
WEBVIEW2_DOCUMENT_AUTOMATION_IDS = frozenset({"RootWebArea"})


def _is_embedded_browser_document(element: UiaElement) -> bool:
    try:
        return element.automation_id in WEBVIEW2_DOCUMENT_AUTOMATION_IDS
    except Exception:
        return False


# WinUI 3 / Windows App SDK host their XAML content in a child window rather than
# in the top-level HWND, and keyboard focus has to be inside it before the XAML
# accelerators fire. `DesktopChildSiteBridge` is the current name; the older
# `DesktopWindowContentBridge` is included for earlier Windows App SDK releases
# but was not measured here.
WM_SYSCOMMAND = 0x0112
SC_MAXIMIZE = 0xF030
SC_RESTORE = 0xF120

CONTENT_ISLAND_CLASS_SUBSTRINGS = (
    "DesktopChildSiteBridge",
    "DesktopWindowContentBridge",
)


# Window classes whose contents are worth reading when one turns up unexpectedly.
# '#32770' is the standard Win32 dialog — message boxes, property sheets, and most
# of what an installer or a system component puts in the way. The others are the
# common message-box-alike shells.
DIALOG_WINDOW_CLASSES = frozenset({"#32770", "MessageBoxWindow", "Notepad++Dialog"})


def _alias_note(exe: str, resolved: str | None, packages: list[tuple[str, str, str]] | None) -> str:
    """The sentence to add when the command that showed no window is a packaged app.

    Pure. `resolved` is what the shell ran for `exe`; `packages` the
    (name, version, status) of installed packages whose name contains the
    command's stem, or None if that could not be read. Two shapes of the same
    fact: the command was an app execution alias under `WindowsApps`, or --
    the Windows 11 case for notepad.exe -- an ordinary System32 executable that
    hands off to the package. Empty when there is nothing to say.
    """
    is_alias = bool(resolved) and "\\microsoft\\windowsapps\\" in (resolved or "").lower()
    if not is_alias and not packages:
        return ""
    if is_alias:
        head = f"\n{exe} is an app execution alias ({resolved}): it starts a packaged (Store) app."
    else:
        head = (
            f"\n{exe} resolved to {resolved}, and a packaged (Store) app of that name is "
            "installed; on Windows 11 the executable hands off to the package."
        )
    why = (
        " The Store applies a pending update the moment the app closes, and a launch "
        "during that swap produces no window."
    )
    if packages is None:
        return head + why + " Package state could not be read."
    if not packages:
        return head + why + " No installed package matched the alias name."
    listed = ", ".join(f"{n} {v} [{st}]" for n, v, st in packages)
    tail = f" Installed: {listed}."
    if len(packages) > 1:
        tail += " More than one version is present: an update is staged or was being applied."
    return head + why + tail


def _launch_target_note(cmd: list[str] | str) -> str:
    """Runtime side of `_alias_note`: resolve the command and read its package."""
    try:
        import shutil

        exe = cmd[0] if isinstance(cmd, list) else str(cmd).split()[0]
        resolved = shutil.which(exe)
        stem = Path(exe).stem.lower()
        if not stem:
            return ""
        script = (
            f"Get-AppxPackage -Name '*{stem}*' | "
            'ForEach-Object { "$($_.Name)|$($_.Version)|$($_.Status)" }'
        )
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=15.0,
                creationflags=0x08000000,
            ).stdout
            packages = [
                tuple(line.split("|", 2)) for line in out.splitlines() if line.count("|") == 2
            ]
        except Exception:
            packages = None
        return _alias_note(exe, resolved, packages)  # type: ignore[arg-type]
    except Exception:
        return ""


def _describe_desktop_now(before: list, limit: int = 12, launched: bool = True) -> str:
    """What was on the desktop when discovery gave up, for the exception message.

    "Window failed to appear" says what did not happen; the next question is
    always what happened instead. The window census already answers that, but it
    only ran at session start and end, so the state at the moment of failure —
    the one that matters — was never recorded anywhere.

    Newly-arrived windows come first: those are the candidates that were rejected
    for some reason, and they are what a reader wants to see.

    This decorates an exception that is already being raised, so nothing in it
    may raise: a diagnostic that fails replaces the real error with its own.
    `launched` says whether a process was started and its window awaited; the
    closing line only makes sense then.
    """
    try:
        before_hwnds = {b.hwnd for b in before}
        now = [s for s in WindowCensus.capture() if s.is_visible]
    except Exception as exc:
        return f"\n  (could not census the desktop: {type(exc).__name__}: {exc})"

    fresh_hwnds = {s.hwnd for s in now if s.hwnd not in before_hwnds}
    fresh = [s for s in now if s.hwnd in fresh_hwnds]
    # Titled windows first among the pre-existing ones. A desktop carries a dozen
    # untitled shells — DummyDWMListenerWindow and friends — and letting them fill
    # the budget pushes out the lines a reader came for.
    rest = [s for s in now if s.hwnd not in fresh_hwnds]
    rest.sort(key=lambda s: (not s.title.strip(), s.class_name))

    lines = [f"\n  Visible windows at the moment discovery gave up ({len(now)} total):"]
    shown = (fresh + rest)[:limit]
    for snap in shown:
        mark = "NEW " if snap.hwnd in fresh_hwnds else "    "
        lines.append(
            f"\n    {mark}class={snap.class_name!r} pid={snap.pid} title={snap.title[:48]!r}"
        )
        # What a blocking dialog actually says. The title of the one that broke a
        # CI run here was 'System Properties', which narrows nothing down; the
        # static text inside it is the part somebody can act on, and nobody can
        # look at the screen of a runner that no longer exists.
        if snap.class_name in DIALOG_WINDOW_CLASSES:
            try:
                for line in describe_dialog_contents(snap.hwnd):
                    lines.append(f"\n          {line}")
            except Exception as exc:  # noqa: BLE001 - decoration, never the error
                lines.append(f"\n          (contents unreadable: {type(exc).__name__})")
    hidden = len(now) - len(shown)
    if hidden > 0:
        lines.append(f"\n    ... and {hidden} more (untitled shells last)")
    if launched and not fresh:
        lines.append("\n    (nothing new appeared at all - the launch produced no window)")
    return "".join(lines)


def _is_ignorable_helper(snap) -> bool:
    """Windows that belong to a process without being its window."""
    title = snap.title.lower()
    cls_name = snap.class_name.lower()
    if "gdi+ window" in title or "gdi+ hook window class" in cls_name:
        return True
    if "msctfime ui" in title or "msctfime ui" in cls_name:
        return True
    if "default ime" in title or "ime" == cls_name:
        return True
    return False


def _is_ready(snap) -> bool:
    """Whether a matching window has finished coming up.

    A top-level window becomes visible before it is populated: the class already
    matches while the title is still empty and the content child does not exist
    yet. Handing that shell back looks like success and then fails 20 seconds
    later inside find_text_input, on a window whose title prints as ''. Treat an
    untitled match as "not yet" and keep polling; the real window is usually
    milliseconds away.
    """
    return bool(snap.title.strip())


def _select_new_window(
    before: list,
    after: list,
    excluded: set[int] = frozenset(),
    classes: set[str] | None = None,
    proc_names: set[str] | None = None,
    title_re=None,
    require_all: bool = False,
):
    """The first window in `after` that is new, wanted and ready.

    Returns `(snapshot, saw_unready)`. `saw_unready` says a window matched but
    had not finished coming up, which is the difference between "nothing like
    that appeared" and "it appeared and was still empty" in a timeout message.

    Separate from the waiting so that the matching -- every rule in it learned
    from a run that went wrong -- is testable without a desktop.
    """
    has_criteria = bool(title_re or proc_names or classes)
    saw_unready = False

    def matches(snap) -> bool:
        checks = []
        if classes:
            checks.append(snap.class_name in classes)
        if proc_names:
            checks.append(get_process_image_name(snap.pid) in proc_names)
        if title_re:
            checks.append(bool(title_re.search(snap.title)))
        if not checks:
            return False
        return all(checks) if require_all else any(checks)

    for snap in WindowCensus.diff(before, after).added:
        if not snap.is_visible or snap.hwnd in excluded or _is_ignorable_helper(snap):
            continue
        if has_criteria and not matches(snap):
            continue
        if not _is_ready(snap):
            saw_unready = True
            continue
        return snap, saw_unready
    return None, saw_unready


class Window:
    """Represents a top-level native OS window."""

    def __init__(self, hwnd: int, pid: int | None = None):
        self.hwnd = hwnd
        self.pid = pid or get_window_pid(hwnd)

    def __repr__(self) -> str:
        """A one-line identity for assertion output and logs.

        Tolerates a dead window: by the time a failure is being formatted the
        window may be gone, and a repr that raises replaces a useful message with
        a traceback about formatting.
        """
        try:
            if not user32.IsWindow(self.hwnd):
                # class='' title='' reads like a window with no name. Say what it is:
                # a handle whose window has since been destroyed.
                return f"<Window hwnd={self.hwnd:#x} pid={self.pid} (destroyed: handle no longer valid)>"
            return (
                f"<Window hwnd={self.hwnd:#x} pid={self.pid} "
                f"class={self.class_name!r} title={self.title!r}>"
            )
        except Exception:
            return f"<Window hwnd={self.hwnd:#x} (gone)>"

    def _set_ime_conversion_settled(self, conversion: int, timeout: float = 2.0) -> bool:
        """Sets the conversion mode and waits until the IME reports it.

        WM_IME_CONTROL is sent synchronously, but the IME still has to act on it,
        and callers were sleeping a fixed interval afterwards to cover that —
        which is the pattern this library exists to remove. Polls instead, and
        gives up quietly: an unverifiable mode is not worth failing a caller over
        when the block is about to run either way.
        """
        self.set_ime_conversion(conversion)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = get_ime_conversion(self.hwnd)
            if current is None or int(current) == conversion:
                return True
            time.sleep(0.05)
        logger.debug(f"IME conversion mode did not settle on {conversion} within {timeout}s")
        return False

    @contextmanager
    def ime_mode(self, conversion: int):
        """Puts this window's IME into a known conversion mode for the block.

            with dialog.ime_mode(ImeConversion.ALPHANUMERIC):
                edit.send_physical_keys("hello")

        Why this exists: a scan code means whatever the active input state says it
        means. Under Bopomofo in native mode, unshifted letters are phonetic keys
        the IME swallows into composition, so correct scan-code injection produces
        an empty field. Establishing the mode is the only way to make that
        deterministic — detecting it is not possible (see get_ime_status).

        On exit the previous mode is restored **only if it could be read**. A None
        reading means no IME window answered, which is not the same as
        alphanumeric; restoring a guess would leave the desktop in a state the
        caller never asked for.
        """
        original = get_ime_conversion(self.hwnd)
        caps_was_on = get_toggle_key_state(VK_CAPITAL)
        self._set_ime_conversion_settled(int(conversion))
        if caps_was_on:
            # Caps Lock is desktop-global and survives everything. Left latched it
            # turns "hello" into "HELLO", and the resulting assertion failure
            # points at the injection rather than at a toggle nobody set on
            # purpose. Establish it here, since this block already exists to make
            # typing deterministic.
            set_caps_lock(False)
        try:
            yield self
        finally:
            if original is not None:
                self._set_ime_conversion_settled(int(original))
            if caps_was_on:
                set_caps_lock(True)

    @contextmanager
    def foreground(self, verify: bool = True, timeout: float = 2.0):
        """Brings this window to the foreground for the block, then gives it back.

            with dialog.foreground():
                edit.send_physical_keys("hello")

        The foreground window is a global, shared resource: a test that takes it
        and never returns it leaves the next one typing into whatever it grabbed.
        Restoration is best-effort — the previous window may be gone by now, and
        failing teardown over that would mask the real result.
        """
        previous = get_foreground_window()
        self.set_foreground(verify=verify, timeout=timeout)
        try:
            yield self
        finally:
            if previous and previous != self.hwnd:
                try:
                    Window(previous).set_foreground(verify=False)
                except Exception as exc:
                    logger.debug(f"Could not restore foreground ({type(exc).__name__}): {exc}")

    @property
    def title(self) -> str:
        return get_window_title(self.hwnd)

    @property
    def class_name(self) -> str:
        return get_window_class(self.hwnd)

    @property
    def is_visible(self) -> bool:
        """`IsWindowVisible`, which is **not** the same as "on screen".

        A window DWM has cloaked — a WinUI or UWP app that has hidden itself, or
        anything on another virtual desktop — still answers True here. Use
        `is_on_screen` when the question is whether a user could see it.
        """
        return bool(user32.IsWindowVisible(self.hwnd))

    @property
    def cloak_reason(self) -> CloakReason | None:
        """Why DWM is hiding this window; `CloakReason(0)` if it is not.

        None means the attribute could not be read at all, which is not the same
        answer as "not cloaked" — see `get_window_cloak_reason`.
        """
        return get_window_cloak_reason(self.hwnd)

    @property
    def is_cloaked(self) -> bool | None:
        """Whether DWM is hiding this window. None when it cannot be determined."""
        reason = self.cloak_reason
        return None if reason is None else bool(reason)

    @property
    def display_affinity(self) -> DisplayAffinity | None:
        """Whether this window has asked to be kept out of screen captures.

        None means it could not be read, which is not the same answer as
        `DisplayAffinity.NONE` — see `get_window_display_affinity`.
        """
        return get_window_display_affinity(self.hwnd)

    @property
    def is_excluded_from_capture(self) -> bool | None:
        """Whether captures of this window will come back without it in them.

        None when it cannot be determined. Worth checking before trusting a
        screenshot or a recording of a specific application: this is the one
        reason for a missing window that `is_visible`, `cloak_reason` and
        `is_on_screen` all answer wrongly, because the window really is visible
        and really is on screen — only capture cannot see it.
        """
        affinity = self.display_affinity
        return None if affinity is None else affinity != DisplayAffinity.NONE

    @property
    def is_on_screen(self) -> bool:
        """Whether this window is somewhere a user could actually see it.

        `IsWindowVisible` and not cloaked. The second half is the part that gets
        left out: a Command Palette that has dismissed itself, a Store app that
        has been put away, and a window on another virtual desktop all report
        `is_visible == True`, so a test waiting for one of them to disappear waits
        forever and a test asserting one is gone passes while it is still there.

        A window whose cloak state cannot be read falls back to `is_visible` —
        that is the best available answer, not a silent False.
        """
        if not self.is_visible:
            return False
        cloaked = self.is_cloaked
        return True if cloaked is None else not cloaked

    def get_ime_status(self) -> dict[str, object]:
        """
        Reads this window's IME state: `has_context`, `is_open`, `conversion`,
        `sentence`, `native_mode`, `full_shape`.

        `has_context` is False for a modern XAML/WinUI control, which routes text
        services through TSF rather than IMM32 — that means "ask TSF", not "IME off".
        """
        return get_ime_status(self.hwnd)

    def set_ime_open(self, is_open: bool) -> bool:
        """Opens or closes this window's IME. False when it has no IMM32 context."""
        return set_ime_open(self.hwnd, is_open)

    def set_ime_conversion(self, conversion: int, sentence: int = 0) -> bool:
        """Sets this window's IME conversion mode (see the IME_CMODE_* flags)."""
        return set_ime_conversion(self.hwnd, conversion, sentence)

    def get_composition_string(self) -> str:
        """Returns the IME's in-progress composition text for this window ("" when idle)."""
        return get_composition_string(self.hwnd)

    def set_keyboard_layout_verified(self, layout_id: str, timeout: float = 3.0) -> int:
        """
        Switches this window's thread to a keyboard layout and confirms it took.

        `layout_id` is the eight-hex-digit identifier — "00000409" for en-US,
        "00000404" for zh-TW. Returns the resulting HKL.

        Necessary because the active layout changes what a keystroke *means*: with
        a Bopomofo layout, unshifted letters are phonetic keys that the IME
        swallows into composition, so scan-code input that is completely correct
        produces an empty field. A test that assumes the machine's layout passes or
        fails on where it happens to run.

        The switch is requested, not commanded: only the owning thread can change
        its own layout, so this posts WM_INPUTLANGCHANGEREQUEST and then verifies.

        It can legitimately fail. A layout is loaded per process, and a window in
        another process will reject a request naming an HKL it has never loaded —
        measured on a zh-TW Windows 11 ARM64 desktop, where every variant of the
        request (post and send, with SYSCHARSET, FORWARD and no flag) left the
        target thread on 0x04040404. Callers that need a known layout should treat
        ActionVerificationError as "this machine cannot give me one" and skip,
        rather than continuing with input that will mean something else.
        """
        hkl = load_keyboard_layout(layout_id)
        if not hkl:
            raise ActionVerificationError(f"Could not load keyboard layout {layout_id!r}")

        WM_INPUTLANGCHANGEREQUEST = 0x0050
        INPUTLANGCHANGE_SYSCHARSET = 0x0001
        user32.PostMessageW(self.hwnd, WM_INPUTLANGCHANGEREQUEST, INPUTLANGCHANGE_SYSCHARSET, hkl)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.keyboard_layout == hkl:
                return hkl
            time.sleep(0.1)

        same_process = self.pid == os.getpid()
        raise ActionVerificationError(
            f"Window did not switch to layout {layout_id!r} within {timeout}s "
            f"(still 0x{self.keyboard_layout:X}). "
            + (
                "The window belongs to this process, so the layout is loaded here; "
                "the thread may not be pumping messages."
                if same_process
                else f"The window belongs to pid {self.pid}, and a keyboard layout is "
                "loaded per process — that process has most likely never loaded "
                f"{layout_id!r}, so it rejects the request. Cross-process layout "
                "switching is not reliable; skip the test instead of assuming it worked."
            )
        )

    @property
    def keyboard_layout(self) -> int:
        """The active keyboard layout (HKL) of the thread that owns this window."""
        return get_keyboard_layout(self.hwnd)

    @property
    def keyboard_language_id(self) -> int:
        """The LANGID (low word of the HKL) of this window's keyboard layout."""
        return self.keyboard_layout & 0xFFFF

    def capture(self, path: str | Path | None = None):
        """
        Captures just this window, including anything covering it, and returns a
        PIL Image. Saves to `path` when given.

        Prefer this over a full screenshot when diagnosing a specific window: the
        thing that broke the run is often the popup on top of it, and a desktop
        capture shows that instead of the window you were driving.
        """
        img = capture_window_image(self.hwnd)
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(path)
            logger.info(f"Saved window capture to {path}")
        return img

    def exists(self, require_visible: bool = True, require_on_screen: bool = False) -> bool:
        """Returns whether this window handle is still a live window.

        `require_visible` is `IsWindowVisible`, which a *cloaked* window passes —
        so for a WinUI or UWP window that hides itself, this returns True after it
        has gone away. Pass `require_on_screen=True` when the question is whether
        it is somewhere a user could see it; see `is_on_screen`.

        The default is unchanged deliberately: quietly tightening it would change
        what every existing caller measures.
        """
        if not user32.IsWindow(self.hwnd):
            return False
        if require_on_screen:
            return self.is_on_screen
        return bool(user32.IsWindowVisible(self.hwnd)) if require_visible else True

    def re_resolve_element(self) -> UiaElement:
        """Always resolves a fresh UIA element directly from the HWND."""
        return UiaElement.from_handle(self.hwnd)

    def find_text_input(
        self, timeout: float = 20.0, ladder: tuple[dict, ...] | list[dict] | None = None
    ) -> UiaElement:
        """
        Locates this window's primary text-input element using a locale-independent
        ladder of window classes and UIA control types (see DEFAULT_TEXT_INPUT_LADDER).
        """
        conds = tuple(ladder) if ladder else DEFAULT_TEXT_INPUT_LADDER
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                root = self.re_resolve_element()
                for cond in conds:
                    try:
                        el = root.find_descendant(timeout=0.2, **cond)
                        if el and _is_embedded_browser_document(el):
                            logger.debug(
                                "find_text_input: skipping embedded browser document "
                                f"(automation_id={el.automation_id!r}) matched by {cond}"
                            )
                            continue
                        if el:
                            return el
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(0.2)
        raise ElementNotFoundError(
            f"No text-input element found in window '{self.title}' within {timeout}s"
        )

    def locator(self, selector: str | dict):
        """Returns a Playwright-style Locator rooted at this window."""
        from wintegrate.locators import Locator

        if isinstance(selector, str):
            query_dict = {"name": selector}
        else:
            query_dict = selector

        def query(root) -> list[UiaElement]:
            elem = self.re_resolve_element()
            if not query_dict:
                return [elem]
            return elem.find_all(**query_dict)

        return Locator(
            self.re_resolve_element,
            query,
            description=f"Window({self.title!r}) >> {selector}",
        )

    def get_by_role(self, role: str, name: str | None = None, exact: bool = False):
        """Finds descendant elements in this window by role (e.g. 'button', 'checkbox', 'tab', 'menuitem')."""
        return self.locator({}).get_by_role(role, name=name, exact=exact)

    def get_by_text(self, text: str, exact: bool = False):
        """Finds descendant elements in this window matching text."""
        return self.locator({}).get_by_text(text, exact=exact)

    def get_by_automation_id(self, auto_id: str):
        """Finds descendant elements in this window by UIA automation_id."""
        return self.locator({}).get_by_automation_id(auto_id)

    def get_by_class(self, class_name: str):
        """Finds descendant elements in this window by window class name."""
        return self.locator({}).get_by_class(class_name)

    def find_button(
        self,
        automation_id: str | None = None,
        name: str | None = None,
        timeout: float = 5.0,
    ) -> UiaElement:
        """Locates a button in this window by automation_id or name."""
        query = {"control_type_id": 50000}
        if automation_id is not None:
            query["automation_id"] = automation_id
        if name is not None:
            query["name"] = name
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                root = self.re_resolve_element()
                btn = root.find_descendant(timeout=0.2, **query)
                if btn:
                    return btn
            except Exception:
                pass
            time.sleep(0.1)
        raise ElementNotFoundError(
            f"Button with {query} not found in window '{self.title}' within {timeout}s"
        )

    def find_checkbox(
        self,
        automation_id: str | None = None,
        name: str | None = None,
        timeout: float = 5.0,
    ):
        """Locates a CheckBox in this window."""
        from wintegrate.controls import CheckBox

        query = {"control_type_id": 50002}
        if automation_id is not None:
            query["automation_id"] = automation_id
        if name is not None:
            query["name"] = name
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                root = self.re_resolve_element()
                cb = root.find_descendant(timeout=0.2, **query)
                if cb:
                    return CheckBox(cb)
            except Exception:
                pass
            time.sleep(0.1)
        raise ElementNotFoundError(
            f"CheckBox with {query} not found in window '{self.title}' within {timeout}s"
        )

    def find_tab_control(
        self,
        automation_id: str | None = None,
        name: str | None = None,
        timeout: float = 5.0,
    ):
        """Locates a TabControl in this window."""
        from wintegrate.controls import TabControl

        query = {"control_type_id": 50018}
        if automation_id is not None:
            query["automation_id"] = automation_id
        if name is not None:
            query["name"] = name
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                root = self.re_resolve_element()
                tc = root.find_descendant(timeout=0.2, **query)
                if tc:
                    return TabControl(tc)
            except Exception:
                pass
            time.sleep(0.1)
        raise ElementNotFoundError(
            f"TabControl with {query} not found in window '{self.title}' within {timeout}s"
        )

    def find_menu(
        self,
        automation_id: str | None = None,
        name: str | None = None,
        timeout: float = 5.0,
    ):
        """Locates a Menu / MenuBar in this window."""
        from wintegrate.controls import Menu

        query = {"control_type_id": 50010}  # MenuBar
        if automation_id is not None:
            query["automation_id"] = automation_id
        if name is not None:
            query["name"] = name
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                root = self.re_resolve_element()
                m = root.find_descendant(timeout=0.2, **query)
                if m:
                    return Menu(m)
            except Exception:
                pass
            time.sleep(0.1)
        # Fallback to Menu control type 50009
        query["control_type_id"] = 50009
        root = self.re_resolve_element()
        m = root.find_descendant(timeout=timeout, **query)
        if m:
            return Menu(m)
        raise ElementNotFoundError(
            f"Menu with {query} not found in window '{self.title}' within {timeout}s"
        )

    def find_combobox(
        self,
        automation_id: str | None = None,
        name: str | None = None,
        timeout: float = 5.0,
    ):
        """Locates a ComboBox in this window."""
        from wintegrate.controls import ComboBox

        query = {"control_type_id": 50003}
        if automation_id is not None:
            query["automation_id"] = automation_id
        if name is not None:
            query["name"] = name
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                root = self.re_resolve_element()
                cb = root.find_descendant(timeout=0.2, **query)
                if cb:
                    return ComboBox(cb)
            except Exception:
                pass
            time.sleep(0.1)
        raise ElementNotFoundError(
            f"ComboBox with {query} not found in window '{self.title}' within {timeout}s"
        )

    def focus_content_island(self, timeout: float = 3.0) -> bool:
        """Puts keyboard focus inside a WinUI 3 / Windows App SDK content island.

        A freshly launched WinUI 3 window can be the foreground window with UIA
        focus still on the *top-level HWND* rather than on anything in the XAML
        tree. Every check passes — `GetForegroundWindow()` returns the window and
        `set_foreground()` reports success — and XAML accelerators are still
        dropped, because the content island never sees them. Measured against
        Files 4.2.9: `Ctrl+T` did nothing until focus moved into the island.

        Returns True when focus is inside the island (including when it already
        was), False when the island cannot be found or refuses focus. Nothing is
        clicked, so no selection or activation happens as a side effect.

        Two routes that look like they should work and do not:

        - `SetFocus()` on the top-level window's own UIA element leaves focus
          exactly where it was.
        - focusing the first keyboard-focusable descendant lands on the caption's
          `InputNonClientPointerSource` input sink, which takes focus off the
          top-level window without giving it to the island — so the accelerator
          is still dropped, and the obvious "did focus move?" check reports
          success.
        """
        bridges = find_child_windows(self.hwnd, CONTENT_ISLAND_CLASS_SUBSTRINGS)
        if not bridges:
            logger.debug(
                f"focus_content_island: no content island child window under {self.hwnd:#x} "
                f"(class={self.class_name!r}) — not a WinUI 3 window?"
            )
            return False

        deadline = time.monotonic() + timeout
        while True:
            for bridge in bridges:
                if self._focus_is_inside(bridge):
                    return True
                try:
                    UiaElement.from_handle(bridge).set_focus(verify=False, click=False)
                except Exception as exc:
                    logger.debug(f"focus_content_island: SetFocus raised: {exc}")
            if time.monotonic() >= deadline:
                logger.debug(
                    f"focus_content_island: focus stayed outside the island after {timeout}s"
                )
                return False
            time.sleep(0.05)

    def _focus_is_inside(self, hwnd: int) -> bool:
        """Whether the UIA-focused element is `hwnd` or a descendant of it.

        The whole chain has to be walked, not just up to the first ancestor that
        owns a native window. Measured chain for a focused button inside a
        WinUI 3 island:

            Button (no handle) -> TabView (no handle) -> InputSiteWindowClass
            -> DesktopChildSiteBridge -> WinUIDesktopWin32WindowClass

        `InputSiteWindowClass` owns a handle of its own and sits *below* the
        bridge, so stopping at the first handle answers "not inside" for a focus
        that is plainly inside.
        """
        try:
            node = UiaElement.get_focused()
        except Exception:
            return False
        for _ in range(12):
            if node is None:
                return False
            try:
                if node.handle == hwnd:
                    return True
            except Exception:
                return False
            node = node.get_parent()
        return False

    def ensure_onscreen(self, margin: int = 0) -> bool:
        """Moves the window into the virtual screen if it is positioned outside it.

        A window can be fully off-screen and behave normally in every way that
        does not involve a pointer: it is visible, it is foreground, its UIA tree
        resolves, and patterns like SelectionItem work. What stops working is
        anything that clicks — a synthesised click at a negative coordinate lands
        nowhere, `click()` returns without complaint, and the test fails on the
        post-condition with no hint that the cursor never reached the control.

        Measured against DB Browser for SQLite, which restores its last window
        position: an element reported `(-701, -525, -494, -469)`, and every
        button click was silently discarded while tab selection kept working.

        Applications that remember their geometry will restore a position saved on
        a machine with a different screen layout, which is why this is worth
        checking rather than assuming.

        Returns True if the window is on-screen afterwards (including when it
        already was), False if it could not be moved.
        """
        rect = RECT()
        if not user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            logger.debug(f"ensure_onscreen: GetWindowRect failed for {self.hwnd:#x}")
            return False

        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        # The virtual screen spans every monitor and its origin is negative when a
        # secondary monitor sits above or to the left of the primary one, so
        # "negative coordinate" on its own does not mean off-screen.
        if rect.left >= vx and rect.top >= vy and rect.right <= vx + vw and rect.bottom <= vy + vh:
            return True

        width = min(rect.right - rect.left, vw - 2 * margin)
        height = min(rect.bottom - rect.top, vh - 2 * margin)
        moved = user32.SetWindowPos(
            self.hwnd, None, vx + margin, vy + margin, width, height, SWP_SHOWWINDOW
        )
        logger.debug(
            f"ensure_onscreen: moved {self.hwnd:#x} from "
            f"({rect.left},{rect.top},{rect.right},{rect.bottom}) to "
            f"({vx + margin},{vy + margin}) size {width}x{height}: {bool(moved)}"
        )
        if not moved:
            return False

        after = RECT()
        if not user32.GetWindowRect(self.hwnd, ctypes.byref(after)):
            return False
        return after.left >= vx and after.top >= vy

    def move_to_current_desktop(self):
        """Moves this window to the currently active virtual desktop if pyvda is available."""
        try:
            from pyvda import AppView, VirtualDesktop

            current_desktop = VirtualDesktop.current()
            app_view = AppView(self.hwnd)
            app_view.move(current_desktop)
        except Exception as exc:
            logger.debug(f"move_to_current_desktop skipped: {exc}")

    def set_foreground(self, verify: bool = True, timeout: float = 2.0) -> bool:
        """
        Brings the window to the foreground cleanly using Win32 AttachThreadInput.
        Synchronizes thread input queues to reliably grant foreground activation permission.
        """
        attach_to_input_desktop()
        self.move_to_current_desktop()

        cur_thread = kernel32.GetCurrentThreadId()
        fg_hwnd = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
        target_thread = user32.GetWindowThreadProcessId(self.hwnd, None)

        attached_fg = False
        attached_target = False

        if fg_thread and fg_thread != cur_thread:
            attached_fg = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
        if target_thread and target_thread != cur_thread:
            attached_target = bool(user32.AttachThreadInput(cur_thread, target_thread, True))

        try:
            user32.ShowWindow(self.hwnd, SW_RESTORE)
            user32.SetForegroundWindow(self.hwnd)
            user32.SetActiveWindow(self.hwnd)
            user32.SetFocus(self.hwnd)
            user32.BringWindowToTop(self.hwnd)
        finally:
            if attached_fg:
                user32.AttachThreadInput(cur_thread, fg_thread, False)
            if attached_target:
                user32.AttachThreadInput(cur_thread, target_thread, False)

        if not verify:
            return True

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            fg = get_foreground_window()
            if fg == self.hwnd:
                return True
            time.sleep(0.05)
        return False

    def maximize(self, verify: bool = True, timeout: float = 5.0) -> bool:
        """Maximises this window, and confirms it happened.

        Sends the window `WM_SYSCOMMAND`/`SC_MAXIMIZE` — the message its own
        title-bar button sends — rather than calling `ShowWindow`. Both work on
        an ordinary Win32 window, but the message goes through the window's own
        handling, which is where a framework that manages its own layout is
        listening.

        `verify` polls `IsZoomed` afterwards rather than trusting the call. A
        window can decline: some applications restore a remembered size, and a
        window whose default rectangle is already larger than the screen looks
        maximised without being maximised. Guessing from a screenshot is how
        that gets missed.

        Returns whether the window ended up maximised, so a caller who does not
        care can carry on and one who does can say so.
        """
        user32.PostMessageW(self.hwnd, WM_SYSCOMMAND, SC_MAXIMIZE, 0)
        if not verify:
            return True

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if user32.IsZoomed(self.hwnd):
                return True
            time.sleep(0.2)
        logger.debug(f"Window {self.hwnd:#x} did not report as maximized within {timeout}s")
        return False

    def restore(self) -> None:
        """Undoes `maximize`, through the same message path."""
        user32.PostMessageW(self.hwnd, WM_SYSCOMMAND, SC_RESTORE, 0)

    def move_and_resize(self, x: int, y: int, width: int, height: int, repaint: bool = True):
        """Reposition window on screen."""
        user32.MoveWindow(self.hwnd, x, y, width, height, repaint)

    def close(self, force: bool = False, timeout: float = 3.0):
        """Closes window gracefully via WM_CLOSE or forces termination."""
        WM_CLOSE = 0x0010
        user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not user32.IsWindow(self.hwnd):
                return
            time.sleep(0.05)

        if force and self.pid:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(self.pid)], capture_output=True, check=False
            )

    @classmethod
    def find(
        cls,
        title_exact: str | None = None,
        title_pattern: str | None = None,
        class_name: str | None = None,
        pid: int | None = None,
        timeout: float = 5.0,
    ) -> Window:
        """
        Finds an existing visible top-level window matching ALL supplied criteria.

        Criteria are combined with AND: `find(title_pattern="Settings",
        class_name="#32770")` returns the dialog whose title matches, not merely the
        first `#32770` on the desktop. Pass `pid` to restrict the search to one
        process — the reliable way to target a window whose title is localized,
        empty, or duplicated across applications.
        """
        if not any([title_exact, title_pattern, class_name, pid]):
            raise ValueError("Window.find requires at least one search criterion")

        deadline = time.monotonic() + timeout
        compiled_re = re.compile(title_pattern, re.IGNORECASE) if title_pattern else None

        snapshots: list = []
        while time.monotonic() < deadline:
            snapshots = WindowCensus.capture()
            for snap in snapshots:
                if not snap.is_visible:
                    continue
                if title_exact is not None and snap.title != title_exact:
                    continue
                if compiled_re and not compiled_re.search(snap.title):
                    continue
                if class_name and snap.class_name.lower() != class_name.lower():
                    continue
                if pid is not None and snap.pid != pid:
                    continue
                return cls(snap.hwnd, snap.pid)
            time.sleep(0.1)

        # The library may not report an absence without saying what it looked at.
        # The last census is still in hand; printing it costs nothing and turns
        # "not found" into "not found, and here is what was there instead".
        raise WindowDiscoveryTimeoutError(
            f"Window not found (title_exact={title_exact}, title_pattern={title_pattern}, "
            f"class_name={class_name}, pid={pid})"
            f"{_describe_desktop_now(snapshots, launched=False)}"
        )

    @classmethod
    def launch_and_discover(
        cls,
        cmd: list[str] | str,
        timeout: float = 10.0,
        title_pattern: str | None = None,
        exclude_hwnds: set[int] | None = None,
        process_names: tuple[str, ...] | list[str] | None = None,
        window_classes: tuple[str, ...] | list[str] | None = None,
        require_all: bool = False,
    ) -> tuple[subprocess.Popen, Window]:
        """
        Launches an application and discovers its top-level window by diffing pre/post snapshots.
        Solves launcher PID != window PID issue and allows excluding existing HWNDs.

        Matching prefers locale-independent identities: `window_classes` (window class
        names) and `process_names` (executable basenames of the window's owning
        process). `title_pattern` remains as a last-resort fallback; a candidate
        window is accepted when ANY provided criterion matches.

        `require_all=True` demands that every criterion given match the same window,
        which is how you reject a dialog the app puts up *instead of* its window.
        `process_names` alone cannot: an app's own error dialog runs in the app's
        process, so the process name matches and a `#32770` is handed back as the
        app. Measured against Files 4.2.9 with its .NET runtime missing — discovery
        returned a dialog in 1.2s where the real window takes ~19s, and every
        element lookup afterwards failed against a window that looked fine.
        """
        attach_to_input_desktop()
        before = WindowCensus.capture()

        if isinstance(cmd, str):
            proc = subprocess.Popen(cmd, shell=True)
        else:
            proc = subprocess.Popen(cmd)

        try:
            window = cls.wait_for_new(
                before,
                timeout=timeout,
                title_pattern=title_pattern,
                exclude_hwnds=exclude_hwnds,
                process_names=process_names,
                window_classes=window_classes,
                require_all=require_all,
                context=f"cmd={cmd}",
            )
        except WindowDiscoveryTimeoutError as exc:
            # Best-effort: don't leak the launcher on timeout (a late-arriving
            # window can still outlive this -- session sanitization sweeps those
            # up).
            try:
                proc.kill()
            except Exception:
                pass
            # Same error, plus what the command was: a Store app behind an
            # execution alias fails to appear for a reason the desktop listing
            # cannot show.
            raise WindowDiscoveryTimeoutError(f"{exc}{_launch_target_note(cmd)}") from exc
        return proc, window

    @classmethod
    def wait_for_new(
        cls,
        before: list[WindowSnapshot],
        timeout: float = 10.0,
        title_pattern: str | None = None,
        exclude_hwnds: set[int] | None = None,
        process_names: tuple[str, ...] | list[str] | None = None,
        window_classes: tuple[str, ...] | list[str] | None = None,
        require_all: bool = False,
        context: str = "",
    ) -> Window:
        """Waits for a window that was not in `before` to appear, and returns it.

        The waiting half of `launch_and_discover`, for the windows something
        other than a launch opens. A Qt menu is the case this was split out for:
        the popup is a top-level `Qt<ver>QWindowPopup` rather than a UIA
        descendant of the item that opened it, so `MenuItem.sub_items()` comes
        back empty and the popup has to be found on the desktop instead.

        Callers were writing this themselves as snapshot, act, sleep for a
        guess, snapshot, take the first added window -- which skips everything
        below, and fails as a `StopIteration` on a line that cannot say what did
        not appear when the guess was short.

        Matching, `require_all` and readiness are exactly as in
        `launch_and_discover`, because they are now the same code.
        """
        excluded = exclude_hwnds or set()
        compiled_re = re.compile(title_pattern, re.IGNORECASE) if title_pattern else None
        proc_names = {p.lower() for p in process_names} if process_names else None
        classes = set(window_classes) if window_classes else None

        deadline = time.monotonic() + timeout
        saw_unready = False
        while True:
            snap, unready = _select_new_window(
                before,
                WindowCensus.capture(),
                excluded=excluded,
                classes=classes,
                proc_names=proc_names,
                title_re=compiled_re,
                require_all=require_all,
            )
            saw_unready = saw_unready or unready
            if snap is not None:
                return cls(snap.hwnd, snap.pid)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)

        detail = (
            " A matching window existed but never gained a title, so it was still "
            "coming up when the wait ran out."
            if saw_unready
            else ""
        )
        where = f" ({context})" if context else ""
        raise WindowDiscoveryTimeoutError(
            f"No new window appeared within {timeout}s{where} "
            f"(pattern={title_pattern}, classes={window_classes}, "
            f"process_names={process_names}).{detail}"
            f"{_describe_desktop_now(before)}"
        )
