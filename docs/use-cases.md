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

---

## 6. Coming from AutoHotkey

AutoHotkey is the tool most Windows desktop automation starts with, and for good
reason: it is a single executable, the language is built around window and input
operations, and a useful script is often five lines. If you already drive an
application with AHK, the question is not which tool is better but **which parts
of your script change when nobody is watching the screen.**

The two are built for different situations. AutoHotkey targets a machine with a
human at it — hotkeys, text expansion, a repetitive task you want a key for.
`wintegrate` targets an unattended runner where the run has already finished by
the time anyone looks, and the only thing left is whatever it wrote to disk.

### The same check, both ways

A real one: Notepad++ #16326, where `Ctrl+Shift+D` inserted an invisible `EOT`
(0x04) into the document. Nothing rendered, so the bug was invisible on screen.

```ahk
; AutoHotkey v2
#Requires AutoHotkey v2.0

Run 'notepad++.exe -nosession -multiInst -noPlugin'
WinWait 'ahk_class Notepad++'
WinActivate
SendText 'abc'
Send '^+d'
Sleep 500
text := ControlGetText('Scintilla1')
if (text != 'abc')
    throw Error('document holds ' StrLen(text) ' chars, expected 3')
```

```python
# wintegrate
proc, win = Window.launch_and_discover(
    [npp, "-nosession", "-multiInst", "-noPlugin"],
    window_classes=("Notepad++",), process_names=("notepad++.exe",),
    require_all=True,
)
with win.foreground():
    edit = win.find_text_input(timeout=30.0)
    edit.set_focus()
    for character in "abc":
        send_char_input(character)
    settled(edit.get_value, lambda v: v == "abc", timeout=10.0)

    view = ScintillaView.from_element(edit)
    before = view.length
    send_keys("^+d")
    after = settled(lambda: view.length, lambda n: n != before, timeout=3.0)
    assert after == before, f"Ctrl+Shift+D grew the document to {edit.get_value()!r}"
```

`settled` in that snippet is not part of the library — it is a small helper in
this project's own test suite (`tests/waits.py`) that polls a callable until a
predicate holds and **returns the last value it saw** either way, so a failed
wait still puts the real state into the assertion message. It is shown
here because a wait is the honest comparison to `Sleep 500`, not because it ships
with `wintegrate`.

**Both find the bug.** `ControlGetText` reads a Scintilla control fine —
`WM_GETTEXT` is a system message, so USER32 marshals the buffer across the
process boundary for you. Anyone claiming this needs UI Automation is wrong.

The difference is in the three lines around the check.

### Where it actually diverges

**Waiting.** The AHK script sleeps 500 ms. Pick too short and it fails on a busy
runner; pick too long and 40 tests cost 20 seconds of nothing. `settled` polls a
condition and returns as soon as it holds — and returns the last value it saw
when it does not, so the assertion message shows the real state instead of
"timed out". AutoHotkey can do this too (`WinWait`, a `Loop` around
`ControlGetText`); the difference is that here it is what the API already does,
so the fast path is also the correct one.

**Evidence.** When the AHK script throws at 3am, you have a message box on a
machine nobody is looking at, and the runner is destroyed. `wintegrate` records
the whole run to one video, snapshots the visible windows before and after, logs
each step with a float timestamp, and writes a screenshot on failure. That is not
a capability AutoHotkey lacks so much as one it does not assume you need —
because on a desk you can just look.

Two failures from this project's own CI make the point. Four tests failed with
`Ctrl+T did not open a tab (1 -> 1)` while every element query succeeded; the
answer was a modal dialog sitting over the app, and it was found in one frame of
the recording. Another had keystrokes vanishing entirely; the answer was a system
prompt holding the foreground, and it came from a failure message that names
whichever window does.

**Being one test among many.** An AHK script is a program: it runs, it exits with
a code. Getting 40 of them into a report, running them in parallel, sharing
setup, and skipping the ones whose application is not installed is work you do
yourself. `wintegrate` is a library, so pytest already does that —
fixtures, `-k`, parametrisation, and a summary line CI can read.

**The desktop you are standing on.** Both tools hit this, and neither warns you.
Input goes to the *thread's* desktop, which in a service, an SSH session or a
scheduled task is not the one on screen — every call succeeds and nothing
happens. `wintegrate` calls `attach_to_input_desktop()` before it tries to bring
a window forward; an AHK script run the same way needs the same treatment.
See [What breaks in CI](pitfalls.md).

### When AutoHotkey is the better answer

- **A hotkey, a text expansion, a macro for yourself.** This is what it is for,
  and nothing here competes.
- **No Python on the target machine.** A compiled `.ahk` is one file.
- **Something to hand a non-programmer colleague.** A ten-line script beats a
  package, a virtualenv and a test runner.
- **Driving your own machine while you watch.** The diagnostics this library
  spends most of its code on exist because nobody is watching. If someone is,
  they are overhead.

The dividing line is not the automation. It is whether a failure has to explain
itself to someone who was not there.

## 7. Touch and multi-finger gestures

Windows has no API for "pinch" or "swipe". It has one for **contacts**: where
each finger is, frame by frame, and whether it is down, moving or lifted. Every
gesture here is a trajectory of contacts, and the recogniser that turns one into
a pinch lives in the system and the application.

That is why nothing in `Touch` is named after an outcome. `pinch()` moves two
contacts apart; whether the application zooms is your assertion to make, because
the thresholds involved — distance, timing, `GESTURECONFIG` — are system metrics
that differ between machines.

```python
from wintegrate import Touch

with Touch() as touch:
    if not touch.available():
        pytest.skip("touch is not delivered on this host")

    touch.tap(x, y)
    touch.double_tap(x, y)          # clamped to GetDoubleClickTime
    touch.long_press(x, y)          # re-sends frames, so the finger stays down
    touch.swipe(x1, y1, x2, y2)
    touch.pinch(cx, cy, start_radius=40, end_radius=160)
    touch.rotate(cx, cy, radius=80, degrees=90)
```

`UiaElement.tap()` covers the common case and needs a rectangle for the same
reason `click()` does — a contact is a coordinate.

```python
button = root.find_all(automation_id="SingleLineToggleButton")[0]
before = button.toggle_state
button.tap()
assert button.toggle_state != before      # read back, never assumed
```

### Hand-written gestures

An injected frame is the **whole hand**, not a delta: a finger left out of a
frame is a finger lifted. `contacts()` keeps that bookkeeping, so moving one
contact restates the others.

```python
with touch.contacts([(100, 100), (300, 300)]) as (a, b):
    a.move_to(120, 120)
    b.move_to(280, 280)
```

### Verify at the layer that matters

"The contact was accepted" is the weakest possible claim — see *Touch injection
reports success when nothing receives it*. Assert what the application did:

```python
# The strongest form: a real control reporting that Windows decided it was pressed.
touch.tap(*button_centre)
assert host.clicks > before, "the tap did not press the button"
```

This is worth doing because touch is a genuinely different code path from the
mouse, not a synonym for it. `WM_POINTER` carries a different pointer type, and
Windows synthesises the mouse messages separately — which is why an injected tap
satisfied a PowerToys checklist item that no mouse gesture could:
*click a single word without dragging*, where five mouse deliveries produced
nothing and a tap returned the word.

