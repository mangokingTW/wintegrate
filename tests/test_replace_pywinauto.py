"""Test replacing pywinauto with wintegrate for Notepad automation workflow."""

from wintegrate import NOTEPAD, UiaElement, Window
from wintegrate.exceptions import WindowDiscoveryTimeoutError


class WintegrateNotepadWindow:
    """Demonstration of full pywinauto replacement for NotepadWindow using wintegrate."""

    def __init__(self, x: int = 100, y: int = 100, w: int = 500, h: int = 400):
        # 1. Discover or launch Notepad window. Discovery keys off the window class,
        # which — unlike the window title — does not change with the UI language.
        try:
            self.win = Window.find(class_name="Notepad", timeout=2.0)
            self.proc = None
        except WindowDiscoveryTimeoutError:
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
