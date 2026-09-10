# wintegrate Guidelines for AI Coding Agents and Developers

`wintegrate` (Windows + integrate) is a CI-first Python automation library engineered to reliably test and drive Windows desktop applications (Win32, WinForms, WPF, Qt, and WinUI 3 / Windows App SDK) on unattended headless runners without flakiness, window focus stealing, or localized string dependencies.

A built-in Agent Skill is bundled with wintegrate and can be installed into any workspace via `python -m wintegrate.skills install`. Claude Code configuration is in `CLAUDE.md`.

---

## Quick Start

Always wrap applications inside `Session` and `session.app(AppSpec)` context managers (or manage `Window` handles directly within a `Session` context manager) to guarantee resource cleanup, process isolation, and diagnostic recording. Use Playwright-style lazy locators with auto-wait:

```python
from wintegrate import NOTEPAD, Session, SessionConfig

with Session(SessionConfig()) as session:
    with session.app(NOTEPAD) as app:
        # Locate editor via Playwright-style get_by_role (auto-waits for editor element to mount)
        editor = app.get_by_role("edit").first
        assert editor.is_visible(timeout=5.0)

        # type_verified blocks until text is physically verified in the control buffer
        editor.type_verified(
            "Hello from wintegrate!\n",
            expected_line_count_delta=1,
            verify_contains="Hello from wintegrate!",
        )
```

---

## Core Rules for AI Coding Agents

### 1. Never Invent Custom Polling Loops or Sleep Delays
Unattended CI runners exhibit wide variance in system load and cold-start times. Arbitrary sleeps cause either intermittent test timeouts or unnecessarily inflated pipeline runtimes.

* **[Incorrect]**:
  ```python
  deadline = time.monotonic() + 10
  while not is_ready():
      time.sleep(0.5)
  ```
* **[Correct]**:
  ```python
  # Built-in auto-wait on locators and windows
  Window.find(title_exact="Document Editor", timeout=10.0)
  Window.wait_for_new(census_before, timeout=10.0, process_names=("editor.exe",))
  locator.wait_for(state="visible", timeout=10.0)
  ```

> [!NOTE]
> When a timeout occurs, `wintegrate` automatically captures the desktop window census delta, logs it to the journal, and reports the failure signature. Minimal inter-key delays (e.g., `0.05s` to `0.1s`) during physical keyboard typing are permitted strictly when visual pacing is required for human video review. For assertions and synchronization, always use post-condition checks or polling utilities such as `settled()`.

### 2. Never Rely on Localized UI Names
Windows UI strings change across system display languages and input configurations. Always prefer locale-independent identifiers:

* **[Incorrect]**:
  ```python
  app.find_button(name="確定")
  win.get_by_role("button", name="OK")
  ```
* **[Correct]**:
  ```python
  app.get_by_automation_id("PrimaryButton")
  app.get_by_class("Button")
  win.get_by_role("edit").first
  ```

### 3. Always Verify Actions Instead of Blind Sleeps
* **[Incorrect]**:
  ```python
  send_keys("Text")
  time.sleep(2.0)
  button.click()
  time.sleep(1.0)
  ```
* **[Correct]**:
  ```python
  editor.type_verified("Text\n", verify_contains="Text")
  submit_btn.wait_for(state="visible", timeout=10.0)
  submit_btn.click(timeout=5.0)
  ```

### 4. WinUI 3 Requires Content Island Focus
WinUI 3 and Windows App SDK host their visual XAML tree inside an internal child island window (`DesktopChildSiteBridge`).
If focus remains on the top-level HWND, XAML accelerators and Tab progression fail even if `GetForegroundWindow()` matches.

* **Route Focus Directly**: Call `win.focus_content_island(timeout=5.0)` or `app.focus_content_island(timeout=5.0)` to synchronize foreground thread input and place keyboard focus inside the XAML island without synthetic clicks.
* **Caution with Overlays**: Do not call Win32 `set_foreground()` on layered, transparent, or topmost popup overlay windows (e.g., Shortcut Guide, transient HUDs, context menus). Win32 `set_foreground()` internally issues `ShowWindow(SW_RESTORE)`, which strips layered window attributes and causes WinUI 3 composition corruption. Call `focus_content_island()` directly on such windows.

### 5. Choose the Appropriate Input Route and Handle CJK / IME Safely
`wintegrate` provides three distinct paths for synthetic keyboard input:

| Interaction Need | Recommended Route | wintegrate API |
|---|---|---|
| IME composition, candidate lists, Chinese/Japanese input | Scan Code (Physical) | `send_physical_keys(...)` |
| Shortcuts, accelerators, system navigation keys | Virtual Key (VK) | `send_vk_input(...)`, `send_hotkey(...)` |
| Fast direct text entry into input fields | Unicode Injection | `send_char_input(...)`, `send_keys(...)` |

#### Unicode Injection Behavior
`send_char_input` dispatches `KEYEVENTF_UNICODE` events. Windows marks the virtual key as `VK_PACKET` (`0xE7`) and places the 16-bit UTF-16 code unit into `wScan`. This directly targets the focused control and completely bypasses active keyboard layouts and IME state machines. Use Unicode injection for fast, robust data entry when IME logic is not under test.

#### CJK and IME Handling
When physical keystrokes must be dispatched on non-English keyboard layouts (such as Bopomofo `zh-TW` or Japanese IME), toggle alphanumeric mode to prevent unexpected composition behavior:

```python
from wintegrate import ImeConversion

with app.ime_mode(ImeConversion.ALPHANUMERIC):
    app.send_physical_keys("input_command")
```

### 6. Session Lifecycle and Exception Propagation (The Pytest Fixture Gotcha)
Never wrap `Session` in a standard pytest generator fixture (`yield` inside `@pytest.fixture`).

* **Why standard fixtures fail**: Pytest intercepts exceptions thrown in the test body before invoking generator fixture teardown. When teardown runs via `next()`, `Session.__exit__` is called with `exc_type=None`. `Session` assumes the test succeeded, suppresses failure diagnostics, records `failed: False`, and `pytest_runtest_makereport` misses the failure session.
* **Recommended Pattern A (Context Manager Helper)**:
  ```python
  from contextlib import contextmanager
  from wintegrate import Session, SessionConfig

  @contextmanager
  def app_session(name: str):
      with Session(SessionConfig(artifact_dir=f"recording-artifacts/{name}", record_video=True)) as session:
          try:
              yield session
          finally:
              pass  # cleanup hooks here
  ```
* **Recommended Pattern B (Direct Session in Test)**:
  ```python
  def test_scenario():
      with Session(SessionConfig()) as session:
          with session.app(NOTEPAD) as app:
              # Test operations...
              pass
  ```

### 7. Sanitize Processes Reliably
Do not execute shell commands such as `taskkill /F /IM app.exe`. These commands fail silently if process names vary or permissions conflict, and they do not verify termination.

* **[Incorrect]**:
  ```python
  subprocess.run(["taskkill", "/F", "/IM", "target.exe"])
  ```
* **[Correct]**:
  ```python
  from wintegrate.apps import sweep_processes_verified

  sweep_processes_verified(
      process_names=("target.exe",),
      app_patterns=("target",),
      timeout=5.0,
  )
  ```

### 8. Continuous Video Recording and CI Diagnostics
Always configure continuous recording for GUI test suites executed in CI pipelines:

```python
from wintegrate import Session, SessionConfig

config = SessionConfig(
    artifact_dir="recording-artifacts/test_case_1",
    record_video=True,
    fps=15,
    draw_cursor=True,      # Renders real cursor position & crosshairs
    click_markers=True,    # Renders expanding click rings
    key_hud=True,          # Renders dark pill keyboard visualizer HUD
)

with Session(config=config) as session:
    with session.step("initialization"):
        # Steps are logged to session_events.json and visual timeline
        pass
```

`wintegrate` automatically embeds mouse cursor movements, click ripple animations, coordinate crosshairs, and a keyboard visualizer HUD directly into recorded video frames without adding disruptive on-screen windows.

---

## Common Automation Patterns

### 1. Launching Custom Applications with `AppSpec`
Define applications using `AppSpec` to encapsulate executable paths, process names, and window classes:

```python
from wintegrate import AppSpec, Session, SessionConfig

CUSTOM_APP = AppSpec(
    name="custom_app",
    command=(r"C:\Program Files\ExampleApp\app.exe", "--headless-test"),
    process_names=("app.exe",),
    window_classes=("ExampleAppMainWindowClass",),
)

with Session(SessionConfig()) as session:
    with session.app(CUSTOM_APP) as app:
        # Interacting with custom app
        main_win = app.window
        assert main_win.is_visible()
```

### 2. Finding and Interacting with Controls via Locators
`Window` and `UiaElement` expose lazy locators with automatic waiting:

```python
# Buttons
button = app.get_by_automation_id("SubmitBtn")
button.click()

# Text input & Editing
edit = app.get_by_automation_id("SearchBox")
edit.wait_for(state="visible", timeout=10.0)
edit.type_verified("query text\n", verify_contains="query text")

# Dropdowns / ComboBox
combo = app.get_by_role("combobox")
combo.click()

# Lists / Items / Slicing / Filtering
nav_items = app.get_by_role("listitem")
first_item = nav_items.first
second_item = nav_items.nth(1)
filtered = nav_items.filter(has_text="Settings")
```

### 3. Window Discovery and Census Tracking
Use `Window.find` for deterministic discovery, or `WindowCensus` to detect popups and secondary dialogs:

```python
from wintegrate import Window, WindowCensus

# Direct window search with timeout
window = Window.find(
    title_exact="Document Editor",
    class_name="EditorWindowClass",
    timeout=10.0,
)

# Detecting new windows created by an action
before = WindowCensus.capture()
trigger_action()
new_window = Window.wait_for_new(
    before,
    timeout=10.0,
    process_names=("editor.exe",),
)

# Inspecting census differences
after = WindowCensus.take()
diff = before.diff(after)
print(f"New handles opened: {diff.opened_hwnds}")
```

### 4. Asynchronous State Polling (`settled` Pattern)
Because synthetic keyboard input returns as soon as the Windows message queue accepts the event, use the `settled` polling pattern to assert on asynchronous state changes (such as focus transitions):

```python
import time
from collections.abc import Callable
from typing import Any
from wintegrate import UiaElement
from wintegrate.interop import send_keys

def settled(
    read: Callable[[], Any],
    matches: Callable[[Any], bool],
    timeout: float = 3.0,
    poll_interval: float = 0.05,
) -> Any:
    """Polls read() until matches(value) is True, returning the last value."""
    deadline = time.monotonic() + timeout
    val = read()
    while not matches(val) and time.monotonic() < deadline:
        time.sleep(poll_interval)
        val = read()
    return val

# Example: Assert focus moves to the expected control after Tab
send_keys("{TAB}")
focused = settled(
    UiaElement.get_focused,
    lambda el: el is not None and el.automation_id == "CloseButton",
    timeout=3.0,
)
assert focused is not None and focused.automation_id == "CloseButton"
```

### 5. Sending Shortcut Chords and Navigation Keys
```python
from wintegrate.interop import send_char_input, send_hotkey, send_keys

# Shortcut chords
send_hotkey("ctrl+a")
send_hotkey("ctrl+c")
send_hotkey("win+r")

# Special navigation keys
send_keys("{ENTER}")
send_keys("{ESC}")
send_keys("{TAB}")
send_keys("{UP}")
send_keys("{DOWN}")

# Direct Unicode injection
send_char_input("Multibyte text: 測試")
```

### 6. Clean-Room Virtual Desktop Isolation (Windows 11)
Run UI automation on a temporary virtual desktop to prevent background desktop noise from interfering with test windows:

```python
from wintegrate import Session, SessionConfig, NOTEPAD

config = SessionConfig(isolated_virtual_desktop=True)
with Session(config) as session:
    with session.app(NOTEPAD) as app:
        # Isolated test execution
        pass
```

---

## Writing Bug Reproduction Tests (`pytest`)

Use strict `xfail` markers when tracking known upstream issues:

```python
import pytest
from wintegrate import NOTEPAD, Session, SessionConfig

@pytest.mark.xfail(
    strict=True,
    reason="Upstream issue: Selection gets cleared after modal close",
)
def test_reproduce_upstream_issue():
    with Session(SessionConfig()) as session:
        with session.app(NOTEPAD) as app:
            editor = app.get_by_role("edit").first
            editor.type_verified("Test", verify_contains="Test")
            
            # Assertion reflects intended fixed behavior:
            # If bug remains: assertion fails -> test marked XFAIL (suite passes)
            # When bug is resolved: assertion passes -> test marked XPASS (flags for marker removal)
            assert editor.text_content() == "Expected Fixed Text"
```

---

## CI Pipeline and Reporting Integration

### GitHub Actions Workflow
Run tests with `pytest-html` and `pytest-github-actions-annotate-failures`:

```yaml
- uses: mangokingTW/wintegrate/.github/actions/setup-windows-gui-test@main
  with:
    wintegrate-version: ">=0.6.4"

- name: Install reporting dependencies
  shell: pwsh
  run: |
    pip install "wintegrate[all]>=0.6.4" pytest-html pytest-github-actions-annotate-failures

- name: Execute UI Tests
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

- name: Upload Verification Artifacts
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: verification-artifacts
    path: recording-artifacts/
    retention-days: 14
```

### Native Built-in Reporting Features
Do not hand-craft custom scripts to parse `junit.xml` or format `$GITHUB_STEP_SUMMARY`. `wintegrate` provides native CI reporting:

1. **Failure Callouts**: Failed sessions render high-priority callout blocks containing the failing step, error signature, and stack trace.
2. **Collapsible Summaries**: Passing test sessions collapse neatly with elapsed execution durations.
3. **Window Leak Detection**: Warns if unclosed window handles remain open at session teardown.
4. **Suite Overview Table**: Summarizes total sessions, pass/fail counts, and pointers to artifact directories.
5. **Annotated Failures**: Automatically annotates exact failure lines on GitHub pull requests.
