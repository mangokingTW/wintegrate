# wintegrate

A Python library for driving Windows GUIs **from CI**, where no human is watching and the only evidence of what happened is whatever the run left behind.

[![CI UI Automation Tests](https://github.com/mangokingTW/wintegrate/actions/workflows/ci.yml/badge.svg)](https://github.com/mangokingTW/wintegrate/actions/workflows/ci.yml)
[![Python 3.11 | 3.12 | 3.13 | 3.14](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://pypi.org/project/wintegrate/)
[![Architecture x64 | ARM64](https://img.shields.io/badge/architecture-x64%20%7C%20ARM64-brightgreen)](https://github.com/mangokingTW/wintegrate)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

---

## Why wintegrate?

Most Windows GUI automation frameworks (e.g. `pywinauto`, `pyautogui`) are built for interactive desktop use on developer workstations. In unattended CI runners (`windows-latest`, `windows-11-arm`), standard interactive assumptions break down:

- **Fail-safes panic in CI**: Moving the cursor into a corner panics interactive libraries and halts test runs.
- **Stale COM wrappers**: Reusing resolved UI element references across seconds causes silent `COMError` failures.
- **Launcher PID != Window PID**: Modern packaged Windows apps (like Notepad, Terminal) launch a starter shim process that does not own the visible HWND.
- **Memory & Pipe Deadlocks**: In-memory frame capture exhausts RAM; streaming with unbuffered `stderr` pipes deadlocks subprocesses.
- **ARM64 Scancode & Input Drops**: DirectX scan-code simulation fails on Windows ARM64 virtualization; keypresses are dropped without verification.
- **Multi-Window Discovery Collision**: Launching multiple instances of identical apps causes discovery diffs to select the existing instance rather than the new one.
- **Unverified fire-and-forget**: Typing without post-condition assertions hides silent failures (e.g. counting `\n` while Notepad returns `\r\n` or `\r`).

`wintegrate` is engineered specifically for **unattended CI reliability** across both **x64 and ARM64 Windows**.

---

## Core Features & Architecture

### 1. Verified Actions & Post-Conditions
Every UI interaction confirms its post-condition (line count deltas, buffer content, focus transitions) before returning:
```python
editor.type_verified(
    "Hello from CI\nSecond line\n",
    expected_line_count_delta=2,
    verify_contains="Hello from CI\nSecond line",
    delay_per_char=0.03,
)
```

### 2. Universal x64 & ARM64 Native Unicode Input
Replaced DirectX scan-code simulation with native Win32 `SendInput` utilizing `KEYEVENTF_UNICODE`. Seamlessly injects special characters, linebreaks, and multibyte Unicode across both Intel/AMD x64 and Windows 11 ARM64 virtualization runners.

### 3. Multi-Window Isolation & Discovery Exclusion
Supports launching and discovering multiple concurrent instances of the same application without PID/HWND collisions:
```python
with session.app(NOTEPAD) as app_a:
    # exclude_hwnds guarantees discovery finds the new window, not the existing one.
    # fresh=False: the leftover sweep would otherwise kill Window A.
    with session.app(NOTEPAD, fresh=False, exclude_hwnds={app_a.window.hwnd}) as app_b:
        app_a.find_text_input().type_verified("Window A\n", expected_line_count_delta=1)
        app_b.find_text_input().type_verified("Window B\n", expected_line_count_delta=1)
```

### 4. Non-Invasive Thread Input Focus (`AttachThreadInput`)
Switches foreground focus cleanly using `AttachThreadInput` synchronization between the automation thread and the target window thread. Grants activation authority without invasive keystrokes or Z-order corruption.

### 5. Windows 11 Virtual Desktop Clean-Room Isolation (`pyvda`)
Supports dynamic virtual desktop isolation. When `isolated_virtual_desktop=True`, `wintegrate` creates a clean virtual desktop on the fly, switches to it, executes test operations in isolation away from background noise, and automatically destroys the test desktop on exit.

### 6. Streaming Video & Diagnostic Pipeline
- **Low-Memory Streaming Recorder**: Streams desktop frames directly to FFmpeg subprocess (`ContinuousRecorder`) with `stderr` redirected to disk logs.
- **Automatic Failure Artifacts**: Automatically dumps full-screen screenshots and pre/post `window_census.json` diffs on assertion failure.
- **Event Timeline Logging**: Logs action timestamps and targets to human-readable `.log` and structured `.json`.

### 7. Managed App Lifecycle & Locale-Independent Discovery (`session.app`)
Modern Windows apps break naive launch-and-poll automation in specific, repeatable ways.
`session.app()` packages the countermeasures:

```python
from wintegrate import NOTEPAD, Session, SessionConfig

with Session(SessionConfig()) as session:
    with session.app(NOTEPAD) as app:                # cleanup guaranteed, even on failure
        editor = app.find_text_input()               # locale-independent control ladder
        editor.type_verified("hello\n", expected_line_count_delta=1)
```

- **No localized strings**: windows are matched by process image name (`Notepad.exe`),
  window class (`Notepad`), and UIA control types — none of which change with the UI
  language. Title regexes are a last-resort fallback and live once, in the `AppSpec`.
- **Single-instance safety**: Store apps (Notepad) reuse a running instance — a leaked
  instance makes the next launch open a *tab* instead of a new window, and discovery
  times out mysteriously. `fresh="auto"` sweeps leftovers before launching (CI only).
- **Cold-start headroom**: first launches of Store apps regularly exceed 10s on CI
  runners (ARM64 especially); managed launches default to a 30s discovery timeout.
- **No leaks**: the context manager closes the window and kills the process on exit,
  and a timed-out discovery kills its own launcher.

---

## Quickstart

```python
from wintegrate import NOTEPAD, Session, SessionConfig

config = SessionConfig(
    artifact_dir="./ci-artifacts",
    record_video=True,
    fps=30,
)

with Session(config) as session:
    # Managed lifecycle: locale-independent discovery, single-instance safety,
    # generous cold-start timeout, and guaranteed cleanup on exit.
    with session.app(NOTEPAD) as app:
        editor = app.find_text_input()

        # Types hardware keystrokes and verifies line count delta & text content
        editor.type_verified(
            "Hello from CI\nSecond line\n",
            expected_line_count_delta=2,
            verify_contains="Hello from CI\nSecond line",
        )
```

---

## Installation

```bash
pip install wintegrate          # core: window/element automation, verified input
uv add wintegrate
```

The core install depends on `comtypes` alone. Two optional extras pull in the
heavier pieces only if you use them:

```bash
pip install 'wintegrate[video]'    # screen recording + failure screenshots (Pillow, ffmpeg)
pip install 'wintegrate[desktop]'  # virtual desktop clean-room isolation (pyvda)
pip install 'wintegrate[all]'
```

Using a feature without its extra raises an error naming the extra to install, so a
missing dependency never turns into a silently skipped diagnostic.

Pre-release versions (`0.1.0aN`) are not installed by default — ask for them explicitly:
```bash
pip install --pre wintegrate
uv add --prerelease=allow wintegrate
```

Or install from source with development dependencies:
```bash
git clone https://github.com/mangokingTW/wintegrate.git
cd wintegrate
pip install -e .[dev]
```

Windows only: the package imports on other platforms (so cross-platform tooling and
`env` checks work), but every Win32/UIA call raises a clear unsupported-platform error.

---

## Running Tests

Run full test suite locally:
```powershell
pytest tests/ -v -s
```

Run static code analysis & formatting check:
```powershell
ruff check src/ tests/
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
