# `wintegrate` Guidelines for AI Agents & Developers

`wintegrate` (**Win**dows + **integrate**) is a CI-first Python automation library designed to reliably drive Windows desktop applications (Win32, WinForms, WPF, Qt, WinUI 3 / Windows App SDK) without flakiness, window focus stealing, or localized string dependencies.

---

## ⚡ Quick Start Pattern

Always wrap applications inside `Session` and `session.app(AppSpec)` context managers to guarantee cleanup and process isolation. Use Playwright-style lazy locators with auto-wait:

```python
from wintegrate import NOTEPAD, Session, SessionConfig

with Session(SessionConfig()) as session:
    with session.app(NOTEPAD) as app:
        # Locate editor via Playwright-style get_by_role (auto-waits for editor element to mount)
        editor = app.get_by_role("edit").first
        assert editor.is_visible(timeout=5.0)

        # type_verified refuses to return until text is physically verified in the control buffer
        editor.type_verified(
            "Hello from wintegrate!\n",
            expected_line_count_delta=1,
            verify_contains="Hello from wintegrate!",
        )
```

---

## 🛡️ Core Rules for AI Coding Assistants

1. **Never invent custom polling loops**:
   * ❌ `deadline = time.monotonic() + 10; while ...: time.sleep(0.5)`
   * ✅ `Window.find(...)`, `Window.wait_for_new(...)`, or `locator.wait_for(state="visible", timeout=...)`. On timeout, `wintegrate` captures the desktop window census delta, logs it to the journal, and reports the failure signature.
2. **Never rely on localized UI names**:
   * ❌ `app.find_button(name="確定")` or `name="OK"`
   * ✅ `app.get_by_automation_id("PrimaryButton")` or `app.get_by_class("Button")`
3. **Always verify actions instead of blind sleeps**:
   * Use `locator.type_verified(...)` rather than `send_keys(...) + time.sleep(...)`.
   * Use `locator.wait_for(state="visible")` or `settled()` helper.
4. **WinUI 3 requires Content Island focus**:
   * WinUI 3 / Windows App SDK hosts its XAML tree inside a child island (`DesktopChildSiteBridge`).
   * If focus is on the top-level HWND, XAML accelerators and Tab progression fail even if `GetForegroundWindow()` matches.
   * ✅ Call `win.focus_content_island(timeout=5.0)` before sending keyboard inputs.
5. **Handle CJK / IME layouts safely**:
   * If typing physical ASCII keys on non-English layouts (e.g. `zh-TW` Bopomofo / Japanese IME):
     ```python
     from wintegrate import ImeConversion
     with app.ime_mode(ImeConversion.ALPHANUMERIC):
         app.send_physical_keys("my_input")
     ```
   * Or use Unicode character injection (bypasses IME directly via `VK_PACKET` 0xE7):
     ```python
     from wintegrate.interop import send_char_input
     for ch in "Text to insert":
         send_char_input(ch)
     ```
6. **Clean up processes reliably**:
   * ❌ `subprocess.run(['taskkill', '/F', '/IM', 'app.exe'])`
   * ✅ `sweep_processes_verified(('app.exe',), ('app',))`
7. **Always enable continuous recording in CI**:
   ```python
   from wintegrate import Session, SessionConfig

   config = SessionConfig(
       record_video=True,
       fps=15,
       draw_cursor=True,      # Renders real cursor position & crosshairs
       click_markers=True,    # Renders expanding click rings
       key_hud=True,          # Renders dark pill keyboard visualizer HUD
   )
   with Session(config) as session:
       # Run automation test...
       pass
   ```

---

## 🔍 Common Automation Patterns

### 1. Launching Custom Applications with `AppSpec`

```python
from wintegrate import AppSpec, Session, SessionConfig

MY_APP = AppSpec(
    name="myapp",
    command=(r"C:\Program Files\MyApp\app.exe",),
    process_names=("app.exe",),
    window_classes=("MyAppMainWindowClass",),
)

with Session(SessionConfig()) as session:
    with session.app(MY_APP) as app:
        # Automation steps...
        pass
```

### 2. Finding & Interacting with Controls via Locators

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

# Lists / Items / Slicing
nav_items = app.get_by_role("listitem")
first_item = nav_items.first
second_item = nav_items.nth(1)
filtered = nav_items.filter(has_text="Settings")
```

### 3. WinUI 3 Content Island Focus

```python
# Transfers focus into DesktopChildSiteBridge without synthetic clicks or side effects
app.focus_content_island(timeout=5.0)
```

### 4. Sending Shortcut Chords & Navigation Keys

```python
from wintegrate.interop import send_hotkey, send_keys

# Common chords (Ctrl+A, Ctrl+C, Ctrl+V, Win+R, Alt+F4)
send_hotkey("ctrl+a")
send_hotkey("ctrl+c")
send_hotkey("win+r")

# Special navigation keys
send_keys("{ENTER}")
send_keys("{ESC}")
send_keys("{TAB}")
send_keys("{DOWN}")
```

### 5. Inspecting Window Census (Detecting Modals / Dialogs)

```python
from wintegrate import WindowCensus

census_before = WindowCensus.capture()
# perform action that opens a window...
census_after = WindowCensus.capture()

# Wait for new window using wait_for_new
from wintegrate import Window
new_win = Window.wait_for_new(
    census_before,
    timeout=10.0,
    process_names=("app.exe",),
)
```

### 6. Asynchronous State Polling (`settled` Pattern)

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
    deadline = time.monotonic() + timeout
    val = read()
    while not matches(val) and time.monotonic() < deadline:
        time.sleep(poll_interval)
        val = read()
    return val

send_keys("{TAB}")
focused = settled(
    UiaElement.get_focused,
    lambda el: el is not None and el.automation_id == "CloseButton",
    timeout=3.0,
)
assert focused.automation_id == "CloseButton"
```

### 7. Clean-Room Virtual Desktop Isolation (Windows 11)

```python
from wintegrate import Session, SessionConfig

# Creates a temporary clean virtual desktop, executes isolated GUI tests, and destroys it on exit
config = SessionConfig(isolated_virtual_desktop=True)
with Session(config) as session:
    with session.app(NOTEPAD) as app:
        # Isolated execution away from human desktop background noise
        pass
```

---

## 🧪 Writing Robust Bug Reproduction Tests (`pytest`)

```python
import pytest
from wintegrate import Session, SessionConfig, NOTEPAD

# For bugs that are currently reproducing on an open upstream issue:
@pytest.mark.xfail(
    strict=True,
    reason="Upstream issue: Selection gets cleared after modal close"
)
def test_reproduce_upstream_issue():
    with Session(SessionConfig()) as session:
        with session.app(NOTEPAD) as app:
            editor = app.get_by_role("edit").first
            editor.type_verified("Test", verify_contains="Test")
            
            # Assert desired/fixed behavior:
            # If bug still exists, assertion fails -> marked as XFAIL (Green run)
            # If bug is fixed, assertion passes -> marked as XPASS (Turns red, notifying author)
            assert editor.text_content() == "Expected Fixed Text"
```
