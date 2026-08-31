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
    win.set_foreground(verify=False)
    
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
    win.set_foreground(verify=False)

    # 2. Interact with Qt form fields with verified typing
    input_box = win.find_descendant(automation_id="usernameInput", required=False)
    if input_box:
        input_box.type_verified("admin_user")

    # 3. Inspect and select items in a virtualized Qt Table/DataGrid
    table_elem = win.find_descendant(control_type_id=50028)  # Table / DataGrid
    if table_elem:
        grid = DataGrid(table_elem)
        # Select row 5 safely even if virtualized/off-screen
        cell = grid.get_cell(row=5, column=0)
        cell.select_verified()

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

- Native Win32 `SendInput` with physical scan codes — the one input path an IME
  can intercept, which is what makes IME behaviour testable at all
- **Establishing** the IME conversion mode across a process boundary
  (`Window.ime_mode`, `get_ime_conversion`), via `WM_IME_CONTROL` to the default
  IME window. `ImmGetContext` returns nothing for a window in another process and
  for anything routing text services through TSF, so the context-based route
  silently does nothing in exactly the case automation cares about.
- Thread-level keyboard layout inspection (`get_keyboard_layout`,
  `get_keyboard_layout_list`)
- Multi-window isolation without PID/HWND collisions

!!! warning "Detecting an IME is not possible; establishing its mode is"

    There is deliberately no "is an IME active" query. `ImmIsIME` looks like that
    answer and is not — it reports whether an HKL is *loaded*, so it returns true
    for a plain en-GB layout the moment one is loaded next to Bopomofo. Switching
    the *layout* is also unreliable across processes: a layout is loaded per
    process, and a window elsewhere rejects one it has never loaded. Set the
    conversion mode instead.

### Example: Testing Physical Scan Codes & IME States

```python
from wintegrate import ImeConversion, Session, Window
from wintegrate.interop import get_keyboard_layout_list

with Session() as session:
    win = Window.find(title_pattern="System Tool", timeout=5.0)

    with win.foreground(verify=False):
        # The thread's layout, for the record — a scan code means whatever the
        # active layout says it means.
        assert win.keyboard_layout in get_keyboard_layout_list()

        # Alphanumeric mode, so the scan codes reach the control rather than the
        # IME's composition buffer. Restored on exit, along with Caps Lock.
        with win.ime_mode(ImeConversion.ALPHANUMERIC):
            edit = win.find_text_input()
            edit.send_physical_keys("Abc")

        # And the other direction: under a CJK layout in native mode, the same
        # call lands nothing, because the IME took the keystrokes. That is the
        # feature, not a failure.
        with win.ime_mode(ImeConversion.NATIVE):
            edit.send_physical_keys("hello")
```

---

## 5. Running Unattended in GitHub Actions CI

To run tests reliably in unattended CI without human intervention or RDP access:

1. **Automatic Video Recording**: Set `record_video=True` in `SessionConfig` to stream in-process video via PyAV directly to disk (`.mp4`).
2. **Window Census Diffs**: Capture `WindowCensus.capture()` before and after operations to track rogue popups or focus stealers.
3. **Automatic Cleanup**: Wrap tests with `Session` and `session.app()` context managers so all launched child windows and processes are terminated cleanly on completion or unexpected failure.
