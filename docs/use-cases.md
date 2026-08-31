# Use Cases & Real-World Guides

`wintegrate` is a black-box Windows GUI automation and diagnostics framework. Because it operates through native Win32 APIs and Windows UI Automation (UIA), it works with **any compiled Windows desktop application**, regardless of what language or UI framework it is built with.

This guide shows practical patterns for testing different kinds of Windows applications in CI.

---

## 1. Testing Rust / Tauri Desktop Applications

Tauri applications use Web technologies for their inner UI, but rely on Rust and Windows APIs for OS-level integration. While browser-based tools (like Playwright or `tauri-driver`) can test the inner HTML DOM, `wintegrate` is used to test **OS-level boundaries**:

- Native system tray icons and context menus
- Standard Windows file pickers (`OpenFileDialog`, `SaveFileDialog`)
- Window minimization to the notification area
- Global hotkeys and background daemons

### Example: Testing System Tray & Native Dialogs

```python
from wintegrate import Session, SessionConfig, Window

config = SessionConfig(
    artifact_dir="./artifacts",
    record_video=True,    # Records full video trace for CI
)

with Session(config) as session:
    # 1. Launch the compiled Tauri application executable
    proc, win = Window.launch_and_discover(
        ["target/release/my-tauri-app.exe"],
        window_classes=["CabinetWClass", "Chrome_WidgetWin_1", "TauriAppClass"],
        timeout=15.0,
    )
    
    # 2. Interact with the main window
    win.set_foreground()
    
    # 3. Trigger an action that opens a native Windows file dialog
    browse_btn = win.find_descendant(name_contains="Open File", control_type_id=50000)
    if browse_btn:
        browse_btn.invoke()
        
    # 4. Automate the native Windows OpenFileDialog (#32770)
    dialog = Window.find(class_name="#32770", timeout=5.0)
    file_edit = dialog.find_descendant(control_type_id=50004)  # Edit control
    file_edit.type_verified("C:\\test\\sample.json")
    
    open_btn = dialog.find_descendant(name_exact="Open", control_type_id=50000)
    open_btn.invoke()
    
    # 5. Clean teardown
    win.close(force=True)
```

---

## 2. Testing C++ & Qt Desktop Applications

Qt applications (using either Qt Widgets or QML) expose their complete control hierarchy to Windows UI Automation out of the box via `QAccessibleBridge`. You can automate release builds without needing commercial instrumentation agents or debug symbols.

### Example: Testing Qt Forms, Tables, and TreeViews

```python
from wintegrate import Session, Window
from wintegrate.controls import DataGrid, TreeView

with Session() as session:
    # 1. Launch the Qt application
    proc, win = Window.launch_and_discover(
        ["bin/release/my-qt-app.exe"],
        timeout=10.0,
    )
    win.set_foreground()

    # 2. Interact with Qt form fields with verified typing
    input_box = win.find_descendant(automation_id="usernameInput", required=False)
    if input_box:
        input_box.type_verified("admin_user")

    # 3. Inspect and select items in a virtualized Qt Table/DataGrid
    table_elem = win.find_descendant(control_type_id=50028)  # Table / DataGrid
    if table_elem:
        grid = DataGrid(table_elem)
        # Select row 5 safely even if virtualized/off-screen
        cell = grid.get_cell(row_index=5, column=0)
        cell.select()

    # 4. Clean exit
    win.close()
```

---

## 3. Testing .NET, WPF, and WinUI 3 Applications

Modern Windows 11 applications (including WinUI 3, Windows App SDK, and WPF) frequently use **XAML Islands** and decoupled launcher processes. `wintegrate` handles these automatically:

- Resolves decoupled launcher PIDs to the visible window HWND
- Traverses nested child HWND boundaries via bounded `RawViewWalker` fallbacks
- Automates virtualized scroll lists and multi-column grids

### Example: Testing WinUI 3 / Modern Notepad

```python
from wintegrate import NOTEPAD, Session, SessionConfig

config = SessionConfig(
    artifact_dir="./ci-artifacts",
    record_video=True,
)

with Session(config) as session:
    # session.app() sweeps leftovers, handles cold-start timeouts, and guarantees cleanup
    with session.app(NOTEPAD) as app:
        # Automatically resolves Win11 RichEditD2DPT / Win32 Edit across child HWND boundaries
        editor = app.find_text_input()
        
        # Types hardware keystrokes and verifies line count delta
        editor.type_verified(
            "Line 1: Configuration loaded\nLine 2: Server active\n",
            expected_line_count_delta=2,
            verify_contains="Server active",
        )
```

---

## 4. Testing Windows System Utilities & IME Tools

For low-level system utilities (such as keyboard layout switchers, status bars, tray helpers, or IME managers like [`ImeModePersistence`](https://github.com/mangokingTW/ImeModePersistence)), `wintegrate` provides specialized hardware input and layout verification:

- Native Win32 `SendInput` with physical scan codes (allowing IME interception)
- Thread-level keyboard layout inspection and switching (`get_keyboard_layout`, `set_keyboard_layout_verified`)
- IME conversion mode verification (`ImmGetConversionStatus`, `ImmIsIME`)
- Multi-window isolation without PID/HWND collisions

### Example: Testing Physical Scan Codes & IME States

```python
from wintegrate import Session, Window
from wintegrate.interop import get_keyboard_layout, get_keyboard_layout_list

with Session() as session:
    win = Window.find(title_pattern="System Tool", timeout=5.0)
    win.set_foreground()
    
    # Verify the thread keyboard layout is active
    hkl = win.keyboard_layout
    layouts = get_keyboard_layout_list()
    assert hkl in layouts
    
    # Send physical scan codes through the Windows message pipeline
    edit = win.find_text_input()
    edit.send_physical_keys("Abc")
```

---

## 5. Running Unattended in GitHub Actions CI

To run tests reliably in unattended CI without human intervention or RDP access:

1. **Automatic Video Recording**: Set `record_video=True` in `SessionConfig` to stream in-process video via PyAV directly to disk (`.mp4`).
2. **Window Census Diffs**: Capture `WindowCensus.capture()` before and after operations to track rogue popups or focus stealers.
3. **Automatic Cleanup**: Wrap tests with `Session` and `session.app()` context managers so all launched child windows and processes are terminated cleanly on completion or unexpected failure.
