"""Test replacing pywinauto with wintegrate for Notepad automation workflow."""

from wintegrate import NOTEPAD, UiaElement, Window
from wintegrate.apps import sweep_processes_verified


class WintegrateNotepadWindow:
    """Demonstration of full pywinauto replacement for NotepadWindow using wintegrate."""

    def __init__(self, x: int = 100, y: int = 100, w: int = 500, h: int = 400):
        # 1. Sweep, then launch. "Find an existing one, else launch" reuses whatever
        # the previous test left behind — including its text, because Notepad
        # restores its last session — and races its teardown: if the find misses
        # while the old instance is still dying, the launch of a single-instance
        # app produces no new window at all and discovery times out on something
        # no timeout value can fix.
        sweep_processes_verified(
            NOTEPAD.process_names,
            NOTEPAD.window_classes,
            package_family_name=NOTEPAD.package_family_name,
            session_state_dirs=NOTEPAD.session_state_dirs,
        )
        self.proc, self.win = Window.launch_and_discover(
            ["notepad.exe"],
            timeout=30.0,
            process_names=NOTEPAD.process_names,
            window_classes=NOTEPAD.window_classes,
        )

        self.hwnd = self.win.hwnd
        self.win.move_and_resize(x, y, w, h)
        self.set_foreground()

    def set_foreground(self):
        self.win.set_foreground(verify=False)

    def get_editor_element(self) -> UiaElement:
        return self.win.find_text_input()

    def type_verified(self, text: str, expected_line_delta: int = 0):
        editor = self.get_editor_element()
        editor.type_verified(text, expected_line_count_delta=expected_line_delta)

    def get_text(self) -> str:
        editor = self.get_editor_element()
        return editor.get_value()

    def close(self):
        self.win.close(force=True)
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass


def test_replace_pywinauto_notepad_workflow():
    """Runs the full pywinauto-equivalent workflow live: launch, type, read back, close."""
    notepad = WintegrateNotepadWindow()
    try:
        notepad.type_verified("replacing pywinauto\n", expected_line_delta=1)
        assert "replacing pywinauto" in notepad.get_text()
    finally:
        notepad.close()


def test_the_typing_methods_can_focus_without_clicking(monkeypatch):
    """`click=False` reaches set_focus, and defaults stay True.

    The click is how these methods guarantee focus, so it has to remain the
    default; what was missing was any way to turn it off. A caller that has
    already focused deliberately was paying one physical click per typed phrase,
    and in a recording each one draws a marker -- which is how this was noticed,
    in ImeModePersistence's Store demo rather than in a test.

    Checked through the signature and a recorded call rather than by driving a
    real control, so it runs anywhere: what is under test is the plumbing of the
    argument, not what focus does.
    """
    import inspect

    from wintegrate.element import UiaElement

    for name in ("send_physical_keys", "send_keys", "type_verified"):
        params = inspect.signature(getattr(UiaElement, name)).parameters
        assert "click" in params, f"{name} takes no click argument"
        assert params["click"].default is True, f"{name} must still click by default"

    calls = []

    class Spy(UiaElement):
        def __init__(self):  # noqa: D107 - deliberately skips UiaElement.__init__
            pass

        def set_focus(self, verify=True, timeout=2.0, click=True):
            calls.append(click)
            return True

        def get_value(self):
            return ""

    # No real input. This used to inject "{ENTER}" twice into whatever held the
    # foreground -- and the test before it has just killed its Notepad, so that
    # was the desktop. On the arm64 runner those two Enters opened the Start menu
    # (run 33960623532: Start back on screen 0.4 s after the kill, every job),
    # and every foreground test after it failed against the Start menu. A test
    # about an argument's plumbing has no business typing.
    sent = []
    monkeypatch.setattr(
        "wintegrate.element.send_keys", lambda spec, *a, **k: sent.append(spec) or True
    )

    spy = Spy()
    for kwargs in ({}, {"click": False}):
        calls.clear()
        try:
            spy.send_keys("{ENTER}", **kwargs)
        except Exception:
            # The injection itself is not what this asserts; only that set_focus
            # was told what the caller asked for.
            pass
        assert calls, "send_keys did not call set_focus"
        assert calls[0] is kwargs.get("click", True), f"set_focus got click={calls[0]}"
    assert sent == ["{ENTER}", "{ENTER}"], (
        "the spec reached the (stubbed) sender, and nothing else did"
    )
