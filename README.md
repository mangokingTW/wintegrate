# wintegrate

A Python library for driving Windows GUIs **from CI**, where no human is watching and the only evidence of what happened is whatever the run left behind.

## Why wintegrate?

Most Windows GUI automation frameworks (e.g. `pywinauto`) are built for interactive desktop use. In unattended CI runners (`windows-latest`, `windows-11-arm`), standard interactive assumptions break down:

- **Fail-safes break CI**: Moving the cursor into a corner panics interactive libraries and halts runs.
- **Stale COM wrappers**: Reusing resolved element references across seconds causes silent failures.
- **Launcher PID != Window PID**: Modern packaged Windows apps (like Notepad) launch a starter process that does not own the visible HWND.
- **Memory deadlocks**: In-memory frame capture exhausts RAM; streaming with unbuffered `stderr` pipes deadlocks subprocesses.
- **Unverified fire-and-forget**: Typing without post-condition assertion hides silent failures (e.g. counting `\n` while Notepad returns `\r`).

`wintegrate` is engineered specifically for CI reliability.

## Core Design Principles

1. **Verified actions**: Every input confirms its post-condition (line count deltas, buffer content, focus transitions) before returning.
2. **Direct UIA / Pure Python 3**: No Win32/MSAA legacy baggage, no Python 2 code, no control-type wrapper subclass bloat.
3. **Handle re-resolution**: Fresh UIA element binding from HWND on demand prevents stale COM pointers.
4. **Streaming diagnostics**: Frame-by-frame low-memory desktop video recording straight to FFmpeg (with `stderr` logged to disk).
5. **CI Runner hardening**: Automated sanitization of known CI runner hazards (orphan WSL update prompts, OOBE privacy screens).

## Quickstart

```python
from wintegrate import Session, SessionConfig

config = SessionConfig(
    artifact_dir="./ci-artifacts",
    record_video=True,
    fps=30,
)

with Session(config) as session:
    proc, win = session.launch_and_discover(["notepad.exe"], title_pattern=".*Notepad.*")
    try:
        root = win.re_resolve_element()
        editor = root.find_descendant(name_contains="Text Editor")
        
        # Types hardware keystrokes and verifies line count delta & text content
        editor.type_verified(
            "Hello from CI\nSecond line\n",
            expected_line_count_delta=2,
            verify_contains="Hello from CI\nSecond line",
        )
    finally:
        win.close(force=True)
```

## Running Tests

```powershell
pytest tests/ -v
```
