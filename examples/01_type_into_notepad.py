"""Launch Notepad, type into it, and verify the text arrived.

    python examples/01_type_into_notepad.py

Everything this library is for is visible in twenty lines: the window is found
without matching a localized title, the editor is located without naming a
control, the typing asserts its own result, and the app is cleaned up even if the
assertion fails.
"""

from __future__ import annotations

from pathlib import Path

from wintegrate import NOTEPAD, Session, SessionConfig


def main() -> None:
    config = SessionConfig(
        artifact_dir=Path("example-artifacts"),
        record_video=False,  # set True (and install wintegrate[video]) to record
    )

    with Session(config) as session:
        # Discovery keys off the process image name and window class, so this
        # works on a Chinese or Japanese Windows without changing anything.
        with session.app(NOTEPAD) as app:
            editor = app.find_text_input()

            # Sends real keystrokes, then refuses to return until the text is
            # actually in the buffer.
            editor.type_verified(
                "hello from wintegrate\n",
                expected_line_count_delta=1,
                verify_contains="hello from wintegrate",
            )

            print("buffer now:", repr(editor.get_value()))
        # Window closed and process killed here, even on an exception above.

    print("artifacts written to:", config.artifact_dir)


if __name__ == "__main__":
    main()
