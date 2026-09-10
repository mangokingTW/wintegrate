---
name: wintegrate
description: >-
  CI-first Windows UI automation, testing, and verification framework.
  Use when writing, debugging, running, or diagnosing automated GUI tests on Windows,
  locating elements with Playwright-style locators, inspecting window trees,
  focusing WinUI 3 content islands, injecting keyboard/mouse inputs,
  capturing video recordings, and analyzing CI failure reports.
---

# wintegrate Automation and Verification Skill

`wintegrate` (Windows + integrate) is a CI-first Python automation framework engineered to reliably test Windows desktop applications (Win32, WinForms, WPF, Qt, WinUI 3 / Windows App SDK) on unattended runners (e.g. GitHub Actions `windows-latest`) without flakiness, window focus stealing, or localized string dependencies.

---

## Core Philosophy and Golden Rules

1. **Never Invent Custom Polling Loops or Sleep Delays**:
   * **[Incorrect]**: Do not write arbitrary `time.sleep(0.5)` for synchronization or manual while-loops.
   * **[Correct]**: Use `Window.find(...)`, `Window.wait_for_new(...)`, `locator.wait_for(state="visible", timeout=...)`, or the `settled(...)` polling pattern. If a timeout occurs, `wintegrate` automatically captures the desktop window census delta, logs it to the journal, and reports the failure signature.
   * *Exception*: Minimal delays (e.g. `time.sleep(0.1)`) between successive physical keyboard keystrokes are permitted strictly for human-eye visual tracking during video review.

2. **Never Hand-Craft CI Summary or Reporting Scripts**:
   * **[Incorrect]**: Do not write custom PowerShell or Python scripts to parse `junit.xml`, scrape logs, or append markdown tables to `$GITHUB_STEP_SUMMARY`. Custom PowerShell string interpolation also risks `ParserError` on escape sequences.
   * **[Correct]**: Wintegrate provides native built-in reporting for GitHub Actions (`$GITHUB_STEP_SUMMARY`) and pytest-html:
     - `Session._write_step_summary()`: Writes high-priority failure callouts, step durations, and collapsible details for passing sessions.
     - `pytest_plugin.pytest_sessionfinish`: Writes the `## wintegrate: run overview` table summarizing total sessions, pass/fail counts, and pointers to artifact directories.
     - `pytest-github-actions-annotate-failures`: Automatically adds native GitHub Actions annotations for test failures on PRs and Workflow Summaries.

3. **Session Lifecycle and Exception Propagation (The Pytest Fixture Gotcha)**:
   * **[Anti-Pattern]**: Wrapping `Session` inside a standard pytest generator fixture (`@pytest.fixture def session(): with Session(...) as s: yield s`).
     - *Root Cause*: Pytest catches test body exceptions outside the fixture and resumes the fixture with `next()` during teardown without passing the exception to `Session.__exit__` (`exc_type=None`). `Session` assumes the test succeeded, suppresses failure callouts, records `failed: False` in `RECENT_SESSIONS`, and `pytest_runtest_makereport` misses the session entirely.
   * **[Pattern A (Context Manager Helper)]**: Wrap `Session` in a `@contextmanager` with `try ... finally` and invoke it directly in the test function (`with app_session() as (session, win):`). Test exceptions bubble cleanly into `Session.__exit__`.
   * **[Pattern B (Direct Context Manager)]**: Instantiate `with Session(...) as session:` directly inside the test body.

4. **WinUI 3 Windows and Overlay Focus**:
   * WinUI 3 / Windows App SDK hosts its XAML tree inside a child island (`DesktopChildSiteBridge`).
   * **[Correct]**: Call `win.focus_content_island(timeout=5.0)` to route keyboard focus directly into the XAML island.
   * **[Caution with Overlays]**: Do not call `win.set_foreground()` on layered, transparent, or topmost popup overlay windows (e.g. Shortcut Guide, context menus). Win32 `set_foreground()` sends `ShowWindow(SW_RESTORE)` which strips overlay window styles and breaks WinUI 3 composition rendering. Use `focus_content_island()` directly.

5. **Never Rely on Localized Strings**:
   * **[Incorrect]**: `app.find_button(name="確定")` or `win.get_by_role("button", name="OK")`
   * **[Correct]**: `win.get_by_automation_id("PrimaryButton")` or `win.get_by_role("edit").first` or `win.get_by_class("Button")`

6. **Clean Up Processes Reliably**:
   * **[Incorrect]**: `subprocess.run(['taskkill', '/F', '/IM', 'app.exe'])`
   * **[Correct]**: `sweep_processes_verified(('app.exe',), ('app',))`

---

## Essential API Patterns

### 1. App Lifecycle and Session Management

#### Pattern A: `@contextmanager` Helper (Recommended for suites with custom process setup/teardown)

```python
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
from wintegrate import Window, Session, SessionConfig
from wintegrate.apps import sweep_processes_verified


@contextmanager
def app_session(test_name: str | None = None):
    if test_name is None:
        current = os.environ.get("PYTEST_CURRENT_TEST", "")
        test_name = current.split("::")[-1].split(" ")[0] or "ui_test"

    rec_dir = os.environ.get("WINTEGRATE_RECORD_DIR", "recording-artifacts")
    artifact_dir = Path(rec_dir) / test_name
    artifact_dir.mkdir(parents=True, exist_ok=True)

    with Session(
        config=SessionConfig(artifact_dir=artifact_dir, record_video=True, fps=15)
    ) as session:
        with session.step("prepare_environment"):
            sweep_processes_verified(("target_app.exe",))

        with session.step("launch_process"):
            proc = subprocess.Popen(["target_app.exe"])

        with session.step("wait_for_window"):
            win = Window.find(title_exact="Target App", pid=proc.pid, timeout=15.0)
            win.focus_content_island(timeout=5.0)

        try:
            # Yield session and window directly into test body
            yield session, win
        finally:
            with session.step("teardown"):
                try:
                    proc.terminate()
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
                sweep_processes_verified(("target_app.exe",))


def test_feature():
    with app_session("test_feature") as (session, win):
        with session.step("verify_feature"):
            assert win.is_visible()
```

#### Pattern B: Built-in `AppSpec` and `session.app()`

```python
from pathlib import Path
from wintegrate import AppSpec, Session, SessionConfig

NOTEPAD_SPEC = AppSpec(
    name="notepad",
    command=("notepad.exe",),
    process_names=("notepad.exe",),
    window_classes=("Notepad", "RichEditD2DPT"),
)


def test_notepad():
    config = SessionConfig(artifact_dir=Path("recording-artifacts/notepad"), record_video=True)
    with Session(config=config) as session:
        with session.app(NOTEPAD_SPEC) as app:
            editor = app.get_by_role("edit").first
            assert editor.is_visible(timeout=5.0)
            editor.type_verified("Hello, wintegrate!\n", verify_contains="Hello, wintegrate!")
```

---

### 2. Window Discovery and Waiting

```python
from wintegrate import Window, WindowCensus

# Option A: Find existing or launched window with auto-wait
win = Window.find(
    title_exact="Shortcut Guide",
    class_name="WinUIDesktopWin32WindowClass",
    pid=proc.pid,
    timeout=10.0,
)

# Option B: Wait for a new window that was not present prior to action
before = WindowCensus.capture()
trigger_action()
new_win = Window.wait_for_new(
    before,
    timeout=10.0,
    process_names=("PowerToys.ShortcutGuide.exe",),
    window_classes=("WinUIDesktopWin32WindowClass",),
)

# Option C: Session-level discovery (attaches diagnostic stdout/stderr pipes)
win = session.find_window(title_exact="Settings", timeout=10.0)
```

---

### 3. Playwright-Style Locators and Auto-Wait

`Window` and `UiaElement` provide Playwright-style lazy locators with auto-wait:

```python
# Create lazy locators
search_box = win.get_by_role("edit").first
nav_items = win.get_by_role("listitem")
submit_btn = win.get_by_automation_id("SubmitBtn")

# Auto-wait for visibility
search_box.wait_for(state="visible", timeout=10.0)

# Verified typing into text box / input
search_box.type_verified("Explorer", verify_contains="Explorer")

# Auto-waiting clicks
submit_btn.click(timeout=5.0)
nav_items.first.right_click()

# Slicing and filtering
first_item = nav_items.first
last_item = nav_items.last
third_item = nav_items.nth(2)
filtered = nav_items.filter(has_text="General", automation_id="NavRail_General")

# Checks
assert search_box.is_visible(timeout=5.0)
assert submit_btn.is_enabled(timeout=5.0)
```

---

### 4. WinUI 3 Content Island Focus

For WinUI 3 / Windows App SDK applications:

```python
# 1. Bring window to foreground reliably via AttachThreadInput synchronization
win.set_foreground(timeout=5.0)

# 2. Transfer keyboard focus into DesktopChildSiteBridge without synthetic clicks or side effects
win.focus_content_island(timeout=5.0)
```

---

### 5. Asynchronous State Polling (`settled` Pattern)

`send_keys` returns as soon as the OS accepts the event into the message queue. For asserting on focus transitions or asynchronous UI state changes without sleeping:

```python
from collections.abc import Callable
from typing import Any
import time
from wintegrate import UiaElement
from wintegrate.interop import send_keys


def settled(
    read: Callable[[], Any],
    matches: Callable[[Any], bool],
    timeout: float = 3.0,
    poll_interval: float = 0.05,
) -> Any:
    """Polls read() until matches(value), returning the last value either way.

    Returning the value rather than raising allows pytest's own assertion
    to output the exact diff.
    """
    deadline = time.monotonic() + timeout
    val = read()
    while not matches(val) and time.monotonic() < deadline:
        time.sleep(poll_interval)
        val = read()
    return val


# Example: Waiting for focus after TAB
send_keys("{TAB}")
focused = settled(
    UiaElement.get_focused,
    lambda el: el is not None and el.automation_id == "CloseButton",
    timeout=3.0,
)
assert focused is not None and focused.automation_id == "CloseButton"
```

---

### 6. Process Sanitization and Artifact Verification

```python
from pathlib import Path
from wintegrate.apps import sweep_processes_verified
from wintegrate.artifacts import expect_artifact

# Sweeps processes and verifies windows/processes are terminated before/after tests
sweep_processes_verified(
    process_names=("PowerToys.ShortcutGuide.exe",),
    app_patterns=("PowerToys.ShortcutGuide",),
    timeout=5.0,
)

# Wait for generated output file
report_file = expect_artifact(Path("output/report.pdf"), timeout=15.0)
```

---

### 7. Keyboard and Mouse Input Chords

```python
from wintegrate.interop import send_char_input, send_hotkey, send_keys

# Key chords
send_hotkey("ctrl+shift+esc")
send_hotkey("win+r")

# Special keys
send_keys("{ENTER}")
send_keys("{ESC}")
send_keys("{TAB}")
send_keys("{UP}")
send_keys("{DOWN}")

# Direct character injection (bypasses active IME via VK_PACKET)
send_char_input("Multibyte text: 測試")
```

---

## Pytest and GitHub Actions CI Workflow

### CI Workflow Configuration

Install `wintegrate[all]>=0.6.4`, `pytest-html`, and `pytest-github-actions-annotate-failures`:

```yaml
- uses: mangokingTW/wintegrate/.github/actions/setup-windows-gui-test@main
  with:
    wintegrate-version: ">=0.6.4"

- name: Install Wintegrate reporting suite
  shell: pwsh
  run: |
    pip install "wintegrate[all]>=0.6.4" pytest-html pytest-github-actions-annotate-failures

- name: Run UI Tests
  shell: pwsh
  env:
    WINTEGRATE_RECORD_DIR: recording-artifacts
  run: |
    $reportPath = "recording-artifacts/report.html"
    New-Item -ItemType Directory -Force -Path "recording-artifacts" | Out-Null
    python -m pytest tests/ `
      -v -s `
      --html=$reportPath `
      --self-contained-html `
      --junitxml=recording-artifacts/junit.xml
    exit $LASTEXITCODE

- name: Upload Verification Recording Artifacts
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: verification-artifacts
    path: recording-artifacts/
    retention-days: 14
```

### GitHub Step Summary and CI Reporting Features (Native)
* **High-priority failure callouts**: Failed sessions automatically display the error signature, failing step name, and traceback.
* **Collapsible passed sessions**: Passing test sessions collapse neatly with step counts and execution durations.
* **Window leak warnings**: Warns if unclosed window handles remained open at session exit based on `window_census.json`.
* **Suite-level overview table (`## wintegrate: run overview`)**: Summarizes total sessions, passed/failed counts, and paths to failure artifact directories.
* **pytest-html report**: Single self-contained HTML report with timeline thumbnails, error logs, and embedded video recordings.
* **Failure Annotations**: Annotates exact test failure lines on the GitHub PR / Action Summary view without extra scripts.
