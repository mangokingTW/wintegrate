# `wintegrate` Guidelines for AI Agents & Developers

`wintegrate` (**Win**dows + **integrate**) is a CI-first Python automation library designed to reliably drive Windows desktop applications (Win32, WinForms, WPF, Qt, WinUI 3) without flakiness, window focus stealing, or localized string dependencies.

---

## ⚡ Quick Start Pattern

Always wrap applications inside `Session` and `session.app()` context managers to guarantee cleanup and process isolation.

```python
from wintegrate import NOTEPAD, Session, SessionConfig

with Session(SessionConfig()) as session:
    with session.app(NOTEPAD) as app:
        # Find control without localized names
        editor = app.find_text_input()
        
        # type_verified refuses to return until text is physically verified in the control buffer
        editor.type_verified(
            "Hello from wintegrate!\n",
            expected_line_count_delta=1,
            verify_contains="Hello from wintegrate!",
        )
```

---

## 🛡️ Core Rules for AI Coding Assistants

1. **Never rely on localized UI names**:
   * ❌ `app.find_button(name="確定")` or `name="OK"`
   * ✅ `app.find_button(automation_id="PrimaryButton")` or `app.find_element(control_type="Button", class_name="Button")`
2. **Always verify actions instead of blind sleeps**:
   * Use `editor.type_verified(...)` rather than `send_keys(...) + time.sleep(...)`.
   * Use `app.wait_for_condition(...)` or `app.find_element_verified(...)`.
3. **Handle CJK / IME layouts safely**:
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
4. **Always enable continuous recording in CI**:
   ```python
   from wintegrate.diagnostics import ContinuousRecorder
   
   rec = ContinuousRecorder(
       "output.mp4",
       fps=15,
       draw_cursor=True,      # Renders real cursor position
       click_markers=True,    # Renders expanding click rings & coordinate crosshair
       key_hud=True,          # Renders native dark pill keyboard visualizer HUD
   )
   rec.start()
   try:
       # Run automation test...
       pass
   finally:
       rec.stop()
   ```

---

## 🔍 Common Automation Patterns

### 1. Launching Custom Applications
```python
from wintegrate import AppConfig, Session, SessionConfig

MY_APP = AppConfig(
    executable=r"C:\Program Files\MyApp\app.exe",
    process_name="app.exe",
    window_class="MyAppMainWindowClass",
    launch_timeout_seconds=30.0,
    fresh="auto",  # Automatically terminates lingering instances from prior runs
)

with Session(SessionConfig()) as session:
    with session.app(MY_APP) as app:
        # Automation steps...
        pass
```

### 2. Finding & Interacting with Controls
```python
# Buttons
button = app.find_button(automation_id="SubmitBtn")
button.click()

# Text input & Editing
edit = app.find_text_input(automation_id="SearchBox")
edit.click()
edit.type_verified("query text\n", verify_contains="query text")

# Dropdowns / ComboBox
combo = app.find_combobox(automation_id="SettingsDropdown")
combo.select_item("Option 1")

# Trees / Lists / Grids
tree_item = app.find_element(control_type="TreeItem", name="Database")
tree_item.expand()
```

### 3. Sending Shortcut Chords & Navigation Keys
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

### 4. Inspecting Window Census (Detecting Modals / Dialogs)
```python
from wintegrate.diagnostics import WindowCensus

census_before = WindowCensus.take()
# perform action that might pop up a modal dialog...
census_after = WindowCensus.take()

diff = census_before.diff(census_after)
print(f"New windows opened: {diff.opened_hwnds}")
```

### 5. Clean-Room Virtual Desktop Isolation (Windows 11)
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
    reason="Upstream issue #12345: Selection gets cleared after modal close"
)
def test_reproduce_issue_12345():
    with Session(SessionConfig()) as session:
        with session.app(NOTEPAD) as app:
            editor = app.find_text_input()
            editor.type_verified("Test", verify_contains="Test")
            
            # Assert desired/fixed behavior:
            # If bug still exists, assertion fails -> marked as XFAIL (Green run)
            # If bug is fixed, assertion passes -> marked as XPASS (Turns red, notifying author)
            assert editor.get_text() == "Expected Fixed Text"
```
