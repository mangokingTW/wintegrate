# Changelog

All notable changes are recorded here. This project follows
[Semantic Versioning](https://semver.org/), with one honest qualification: while
the version is below 1.0, **any release may change the API**, patch releases
included. Every such change is called out under `### Changed` and says what to
do about it — that callout is the guarantee, not the version number.

## [Unreleased]

### Changed

- **A discovery timeout names a packaged (Store) app.** When the command that
  produced no window is an app execution alias, or an ordinary executable with an
  installed package of the same name (Windows 11's `System32\notepad.exe` hands
  off to `Microsoft.WindowsNotepad`; measured), the error says so, lists the
  installed package versions, and says what more than one version means: the Store applies a pending update the moment the app
  closes, and a launch during that swap shows no window. Measured on
  windows-11-arm, whose Notepad is the Store package: the test after the first
  one to close Notepad timed out with nothing on the desktop. The composite
  action's quiet step now sets the Store policy `AutoDownload=2` on hosted runners
  and prints the Notepad and Calculator package versions at job start.

## [0.5.15] — 2026-09-05

### Added

- **`protected_pids()` and `console_client_pids()`.** The set of processes this one
  depends on, each with the measured relation that protects it -- self, ancestors,
  the processes sharing this console, and their ancestors. Never a list of program
  names: `WindowsTerminal` ended a run in 0.5.12 because it hosted that run's
  console, and is an ordinary target in a run that is not attached to it.

  The console peers are the part that is easy to miss. A console is shared:
  measured on a hosted runner, `GetConsoleProcessList` returned `python.exe`,
  `pwsh.exe` and `Runner.Worker.exe`, so ending that console ends the agent
  reporting the job. The host itself cannot be reached from here -- it arrives
  through a DelegationConsole handoff and is in nobody's parent chain -- so this
  protects what *shares* the console rather than trying to name what serves it.

### Fixed

- **`AppHandle.close()` no longer force-kills a process it did not start.** It
  calls `Window.close(force=True)`, which ends the window's process, on every
  handle -- including one built around a `Window.find()` result, which can be the
  caller's own window, the shell's, or the terminal hosting this process's console.

  It now downgrades to `WM_CLOSE` alone when the window belongs to a protected
  process, says which relation stopped it, and fails closed when the relations
  cannot be measured. The primitive is unchanged: `Window.close(force=True)` still
  does exactly what it is told. A primitive that silently declines is worse than
  one that kills, because the caller cannot tell refusal from failure.

## [0.5.14] — 2026-09-05

### Fixed

- **The console check that 0.5.13 added does not work on a CI runner.**
  `GetConsoleWindow()` answers a narrower question than the one that matters --
  is there a console *window* -- and a ConPTY has none. That is exactly what a
  hosted runner hands a step, so the check returned "no console" for a process
  that was attached to one all along, the sweep went ahead, and it killed the
  terminal hosting the step. The symptom was unchanged from 0.5.12: a job that
  ended eleven seconds in, with a `KeyboardInterrupt` wherever the process
  happened to be.

  `GetConsoleProcessList()` answers the question that was meant and answers it
  for a windowless console. The window check is kept as the first branch, so
  this is strictly more conservative than 0.5.13.

  Measured on a `windows-latest` runner, from the step itself:

  ```
  GetConsoleWindow      = 0
  GetConsoleProcessList = 3 [python.exe, pwsh.exe, Runner.Worker.exe]
  has_console()         = False        <- 0.5.13 answering wrongly
  ```

  The three processes sharing that console are the point: `Runner.Worker.exe`
  is on it too, so killing the terminal took the runner's own worker with it.
  That is why the job reported the step as cancelled eleven seconds in rather
  than failed.

  The decision is now logged as well. It was wrong once and invisible while it
  was, which is most of why it took so long to find.

## [0.5.13] — 2026-09-05

### Fixed

- **The CI sweep no longer kills the terminal hosting the process that called
  it.** `sanitize_ci_runner_environment()` kills `WindowsTerminal` by name to
  clear leftover windows. On Windows 11 that is also the default console host,
  so a console-subsystem caller is attached to it -- and killing it destroyed
  the console and the caller with it, about a second after the call returned.

  Excluding it by pid is not possible. It is not an ancestor of the caller, and
  it is not the owner of `GetConsoleWindow()` either: with Windows Terminal as
  the host that window belongs to a brokered `OpenConsole.exe` whose parent
  chain runs `svchost -> wininit -> services`, with the terminal nowhere in it.

  So the answerable question is asked instead -- does this process have a
  console at all -- and terminal hosts are swept only when it does not, which
  is the ordinary case for a runner started by a service or a GUI.

  Measured on Windows 11 ARM64: killing the rest of the list left the caller
  running for a full 8-second watch; killing `WindowsTerminal` alone ended it
  before its next heartbeat. With this change the same run completes.

  Found from a GitHub Actions job that stopped mid-`import av` and could then
  only be cleared with Force cancel -- which destroys the runner VM, taking the
  logs and artifacts with it, so three rounds produced no evidence at all.

## [0.5.12] — 2026-09-05

### Added

- **`Window.wait_for_new()`.** The waiting half of `launch_and_discover`, for
  the windows something other than a launch opens. It was already written --
  inside `launch_and_discover`, where nothing else could reach it -- so callers
  who needed it wrote their own: snapshot, act, sleep for a guess, snapshot,
  take the first added window.

  That version skips every rule this one has learned. It hands back GDI+ and IME
  helper windows, and windows that are visible but still untitled and therefore
  not yet usable; it cannot say `require_all`; and when the guess is short it
  fails as a `StopIteration` on a line that cannot report what did not appear,
  rather than as a `WindowDiscoveryTimeoutError` naming the criteria and what
  was on the desktop instead.

  The case it was extracted for is a Qt menu: the popup is a top-level
  `Qt<ver>QWindowPopup` rather than a UIA descendant of the item that opened it,
  so `MenuItem.sub_items()` comes back empty and the popup has to be found on
  the desktop.

  ```python
  before = WindowCensus.capture()
  menu.items[4].expand()
  popup = Window.wait_for_new(before, window_classes=("Qt681QWindowPopup",))
  ```

### Changed

- **`launch_and_discover` is now launch plus `wait_for_new`**, so the two cannot
  drift. Behaviour is unchanged, with one dead branch dropped: it scanned the
  new windows twice, and the second pass was the same set under a stricter
  condition, so it could never match anything the first had not already
  rejected. Its timeout message keeps the command that failed, passed in as
  `context`.

## [0.5.11] — 2026-09-04

### Added

- **`DisplayAffinity`, `get_window_display_affinity()`, `Window.display_affinity`
  and `Window.is_excluded_from_capture`.** A window can call
  `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` and Windows will then
  withhold it from every capture path there is — GDI, DXGI Desktop Duplication,
  `Windows.Graphics.Capture`, DWM thumbnails. No flag on the capturing side
  overrides it, and the call only works on windows of the calling process, so
  nothing outside the application can clear it.

  It needed its own reading because every existing one answers wrongly: such a
  window is `is_visible`, has a cloak reason of 0, is `is_on_screen`, and a
  person watching the monitor sees it. Found on KeePassXC, which does this on
  purpose as an anti-screenshot feature: a session recording showed the
  credential prompt, the keystroke HUD and every step of the run with the
  application under test simply absent from the frame. A screenshot of the same
  moment taken from outside the guest contained it.

  `None` still means "could not be read", as with `get_window_cloak_reason` —
  "nothing is excluding this window" is a fact, "no idea" is not, and a caller
  that merges them promises a recording it cannot produce.

### Changed

- **`capture_window_image()` says when a window is excluded from capture.** It
  already fell back to cropping the desktop when `PrintWindow` returned nothing;
  for an excluded window that crop is a picture of the wallpaper, returned under
  the name of the window. It now logs a warning naming the reason. Still an
  image and not an exception: a diagnostic that fails a test run is worse than
  one that explains itself.
- **`Session.capture_screenshot()` records the exclusion in the event
  timeline.** An artifact that cannot contain the window it is named after has
  to say so where the artifacts are read.

## [0.5.10] — 2026-09-03

### Fixed

- **The touch delivery check no longer crashes the process.** 0.5.9 took an
  access violation inside `Touch.available()`, and the cause was one undeclared
  call: `kernel32.GetModuleHandleW` had no `restype`, so ctypes converted its
  `HMODULE` as `c_int` and dropped the top 32 bits. That truncated handle went
  to `RegisterClassW` and `CreateWindowExW`, and USER32 dereferenced it.

  It is now declared in `interop`, where every other handle-returning call
  already was. The check's window procedure and window class were also moved to
  module scope, since a registered class holds pointers into both for longer
  than the call that registers it.

  Worth stating plainly, because it is the reason this shipped: **pytest
  reported `212 passed` on every run that crashed.** A native crash reaches
  neither the exit code nor the summary line, and the tests it broke reported
  themselves as *skipped*.

### Changed

- **CI fails the run when a native crash appears in the test output.** Both
  pytest steps now search their own output for `Windows fatal exception` and
  `access violation` and fail the step, printing the matches. Validated against
  real logs before being trusted: the crashing runs match, a clean one does not.

- `Image.getdata()` → `get_flattened_data()` in the overlay tests. `getdata` is
  deprecated for removal in Pillow 14 and was producing warnings on every job.
  The two return an identical sequence in every mode this suite uses — RGB,
  RGBA, L and 1 — checked rather than assumed.

## [0.5.9] — 2026-09-03

### Added

- **Touch and multi-finger gestures**, via `Touch` in `src/wintegrate/touch.py`
  and `UiaElement.tap()`. Contacts are injected through a synthetic digitizer
  (`CreateSyntheticPointerDevice`, with the Windows 8 `InitializeTouchInjection`
  path as a fallback), and an injected tap makes Windows report a real `BUTTON`
  as clicked -- asserted in `tests/test_touch.py` on both architectures.

  `tap`, `double_tap`, `long_press`, `swipe`, `pinch`, `rotate`, and
  `contacts()` for hand-written multi-finger choreography:

  ```python
  with Touch() as touch:
      touch.tap(x, y)
      touch.swipe(x1, y1, x2, y2)
      with touch.contacts([(100, 100), (300, 300)]) as (a, b):
          a.move_to(120, 120)
          b.move_to(280, 280)
  ```

  **Nothing here is named after an outcome.** `pinch()` moves two contacts
  apart; whether the application zooms is the caller's assertion, because the
  thresholds -- distance, timing, `GESTURECONFIG` -- are system metrics that
  differ between machines. Windows has no gesture API, only contacts.

  **`Touch.available()` measures delivery rather than asking the API.** All
  three injection calls report success on a host where nothing receives the
  contact, so `available()` taps a small window of its own and checks the tap
  arrived. That distinction is not theoretical: on a fresh GitHub ARM runner a
  full-screen onboarding window owns the foreground, every injection succeeds,
  and no window sees anything -- and a plain mouse click is equally ignored, so
  the honest reading is "the desktop is covered", not "touch is unavailable".
  `Touch.available()` returning False logs which of the two it is.

  `double_tap` reads `GetDoubleClickTime` and clamps to it rather than assuming
  the default: the double-click interval is a user setting.

- `Contact.move_to()` restates every open contact, because an injected frame is
  the whole hand and not a delta -- a finger left out of a frame is a finger
  lifted.

- **`click=False` on the element typing methods.** `send_physical_keys`,
  `send_keys` and `type_verified` focus before typing, and that focus included an
  unconditional physical click. There was no way to ask them not to: a caller
  that had already focused deliberately still paid one click per typed phrase.

  Default stays `True` — the click is how these guarantee focus on controls that
  ignore UIA SetFocus, and there is no cheap way to know in advance which those
  are. What changes is that it can be turned off.

  Noticed in a product demo rather than a test: ImeModePersistence's Store
  recording collected one click marker per typed phrase, over an empty editor,
  in a demo with no mouse interaction to show.

### Changed

- **`Element.set_focus`'s docstring no longer calls the click a "fallback".** It
  is not one, and never was: when `click` is true the click happens before
  anything is verified, because that is what makes focus reliable. Reading it as
  a fallback is what made the extra clicks above hard to account for.

## [0.5.8] — 2026-09-03

### Fixed

- **The keyboard HUD no longer draws `0x00` for a keystroke that names no key.**
  `keybd_event(0, 0, 0, 0)` is the standard way to satisfy Windows' rules about
  which process may bring a window to the foreground: it injects an event naming
  neither a virtual key nor a scan code, purely so the caller counts as having
  received input. The HUD rendered each one as a keycap reading `0x00`, which in
  a recording looks like a real key with a broken label rather than like nothing
  having happened.

  Found in a product demo, not in a test: about half the visible keycaps in
  ImeModePersistence's Store recording were `0x00`, and they lined up with the
  window switches rather than with the typing.

- **A scan code that arrives with no virtual key is now resolved instead of
  printed in hex.** `SendInput` with `KEYEVENTF_SCANCODE` sets `wVk` to 0 --
  which is how this library's own `send_physical_keys` types -- and although
  Windows normally fills the virtual key in before the hook sees it, a scan code
  the active layout cannot map leaves it at 0. `MapVirtualKeyW` with
  `MAPVK_VSC_TO_VK_EX` recovers the key, keeping left and right modifiers
  distinct.

## [0.5.7] — 2026-09-03

### Added

- **`ContinuousRecorder` can caption its frames**, in the bottom-left corner, with
  whatever the caller puts in `recorder.caption` (and an optional
  `recorder.caption_subtitle` on a second line). Empty draws nothing.

  A recording of a suite shows a series of applications being driven and says
  nothing about which test is driving them. Finding the moment a particular test
  failed means counting windows and guessing, and that moment is the one worth
  watching.

  The recorder holds the text and the caller sets it, deliberately: pytest,
  unittest and a plain script all name their work differently, and none of that
  belongs in this library. With pytest the whole integration is two hooks --

  ```python
  def pytest_runtest_logstart(nodeid, location):
      recorder.caption = nodeid.split("::")[-1]
      recorder.caption_subtitle = location[0]
  ```

  Composited after the screen grab, like the pointer and keyboard overlays, so
  nothing on screen can cover it. A long test id is trimmed from the *front*: the
  tail is the part a viewer is looking for.

- `draw_caption()` is public, for a caller building its own frames.

- **This project's own suite is wired to it**, in `tests/conftest.py`, so every
  frame of `full-suite-{arch}.mp4` names the test that produced it. Worth stating
  because the alternative was shipping a feature with no caller: the unit tests
  assert that `draw_caption` puts pixels in the bottom-left of a synthetic frame,
  which is not the same claim as a recording of a real run carrying the name.

## [0.5.6] — 2026-09-02

### Added

- **Playwright-Style Locators**: Introduced `Locator` (`src/wintegrate/locators.py`) with auto-waiting, lazy evaluation, and chained queries:
  - `get_by_role("button" | "edit" | "textbox" | "checkbox" | "tab" | "menuitem" | "combobox" | "slider" | "treeitem")`
  - `get_by_text(text, exact=False)`
  - `get_by_automation_id(auto_id)`
  - `get_by_class(class_name)`
  - `.filter(has_text=..., automation_id=..., class_name=...)`
  - Slicing and counting: `.first`, `.last`, `.nth(i)`, `.count()`, `.all()`
  - Auto-waiting actions: `.click()`, `.right_click()`, `.double_click()`, `.middle_click()`, `.hover()`, `.type_verified()`, `.fill()`, `.text_content()`
  - Multi-type role mapping: `get_by_role("edit" | "textbox")` transparently resolves both Win32 `Edit` (50004) and WinUI / XAML `Document` (50030) controls.
- **Playwright-Style Mouse Controller**: Introduced `Mouse` (`src/wintegrate/mouse.py`), accessible via `session.mouse` and `app.mouse`:
  - `mouse.move(x, y, steps=1, delay=0.0)` with smooth multi-step trajectory interpolation.
  - `mouse.down(button=...)`, `mouse.up(button=...)`, `mouse.click(x, y, button=..., count=...)`, `mouse.dblclick(x, y)`.
  - `mouse.wheel(delta_y, delta_x)` for vertical and horizontal scroll events.
  - `mouse.drag(start_x, start_y, end_x, end_y, steps=10)`.
  - `mouse.position` for reading physical cursor screen coordinates.
- **Element & Locator Pointer Gestures**:
  - `element.hover()` / `locator.hover()`: Moves pointer to control center to trigger tooltips and hover menus.
  - `element.middle_click()` / `locator.middle_click()`: Auxiliary middle mouse button click.
  - `locator.drag_to(target_locator)`: Auto-waits for source and target elements and smoothly drags between them.
- **Rich Desktop Controls**:
  - `CheckBox` & `RadioButton`: `is_checked()`, `set_checked_verified(True / False)`
  - `TabControl` & `TabItem`: `tabs`, `active_tab`, `select_tab_verified(name_or_index)`
  - `Menu` & `MenuItem`: `select_cascade("File > Open...")`, `items`, `expand()`
  - `Slider` & `ProgressBar`: `value`, `minimum`, `maximum`, `set_value_verified()`
  - `ComboBox`: `select_item(name)`

## [0.5.5] — 2026-09-02

### Added

- **Recordings now show the keyboard HUD natively.** `ContinuousRecorder` now renders
  keystrokes and shortcut chords directly into captured frames (`key_hud=True`, on by
  default), eliminating the need for an external input visualizer (such as Keyviz).

  Drawing keystrokes into the frame after capture provides three critical advantages:
  1. **Zero desktop intrusion**: No OS GUI window is created, eliminating window focus
     stealing, z-order fighting (`WS_EX_TOPMOST`), and click-through bugs.
  2. **Correct `VK_PACKET` (0xE7) Unicode decoding**: Text injected via
     `SendInput(KEYEVENTF_UNICODE)` sends `VK_PACKET` with the UTF-16 character in
     `scanCode`. Unlike third-party tools that mistake `scanCode` for virtual key codes
     (rendering `'q'` as `F2` and `'a'` as `Num 1`), wintegrate decodes the character
     natively and accurately.
  3. **Low-overhead event streaming**: Intercepts events via `WH_KEYBOARD_LL`, tracking
     modifier states (`Ctrl`, `Alt`, `Shift`, `Win`), chords (e.g. `Ctrl + C`,
     `Win + Alt + Space`), and function keys with configurable linger time (default 2.5s).

- `KeyStrokeEvent`, `KeyTracker`, and `draw_keyboard_hud()` are public in
  `wintegrate.keyboard_overlay` for custom frame pipelines.

## [0.5.4] — 2026-09-01

### Added

- **Recordings now show the pointer and its clicks.** `ContinuousRecorder` draws the
  cursor into each frame and marks every click with an expanding, fading ring and a
  crosshair. Both are on by default (`draw_cursor`, `click_markers`).

  A BitBlt of the desktop contains no cursor — the pointer is composited by the
  system, not stored in the desktop bitmap — so until now a recording showed windows
  changing with nothing to say where the pointer was or that a click had happened.

  Drawing after the grab has a property no on-screen visualiser can match: nothing
  can cover it. An overlay window has to win a z-order fight it cannot always win.
  Measured with one variable — the same window and the same click point, only
  `WS_EX_TOPMOST` changing — a third-party visualiser's ring went from 2032 pixels to
  0, and a keystroke did not bring it back. `tests/test_pointer_overlay.py::
  test_a_topmost_window_cannot_cover_the_markers` asserts the new behaviour against
  that exact case.

  Clicks come from a `WH_MOUSE_LL` hook rather than from wintegrate's own injection
  sites, so a click from any source appears — including one a test did not mean to
  make. The crosshair marks the coordinate the click was sent to, which is what makes
  a wrong-coordinate bug visible rather than invisible.

- `capture_screen_image(draw_cursor=True)` for the same treatment on a single
  screenshot. It defaults to `False`: callers compare these images pixel by pixel, and
  a pointer wandering into frame would turn a passing assertion into a flake.

- `get_cursor_state()`, `ClickTracker`, `ClickEvent`, `draw_click_markers()`,
  `draw_pointer_overlay()` and `cursor_overlay_image()` are public, for a caller
  building its own frames.

### Fixed

- **GDI handles are now passed at pointer width.** `DeleteObject`, `SelectObject`,
  `CreateCompatibleDC`, `CreateCompatibleBitmap`, `GetDIBits`, `BitBlt`, `GetDC` and
  `ReleaseDC` had no declared signatures, so ctypes converted their handles as
  `c_int`. Any handle above 2^31 raised `OverflowError: int too long to convert` —
  which `GetIconInfo`'s bitmaps did here on the first run. The existing capture path
  survived because GDI hands out small values in practice; that was luck, not a
  contract.

## [0.5.3] — 2026-09-01

### Changed

- **`send_mouse_click()` now injects a mouse-move event as well**, not just
  `SetCursorPos`. `move_event=False` restores the older behaviour.

  `SetCursorPos` relocates the pointer without producing any input event, so
  nothing watching the mouse learns it moved: a low-level hook, an overlay drawing
  where clicks land, an application that only updates hover state on
  `WM_MOUSEMOVE`. They see a click at whatever position they last knew about.

  Found by pointing a keystroke visualiser at a wintegrate run: every click drew
  its marker at the top-left corner, where the cursor had been when the tool
  started, regardless of where the click actually went. The click itself was
  landing correctly the whole time, which is why nothing had failed.

  The injected move is `MOUSEEVENTF_MOVE | ABSOLUTE | VIRTUALDESK`. `VIRTUALDESK`
  matters: `ABSOLUTE` alone maps 0..65535 onto the *primary* monitor, so on a
  multi-monitor runner a click meant for a second screen would land on the first.
  `SetCursorPos` still runs, and last, because the move event's coordinates are
  quantized to 1/65535 of the virtual desktop — so the pointer ends up at exactly
  the requested pixel rather than the rounded one.

  This is also closer to a real click, which is always preceded by movement.

## [0.5.2] — 2026-09-01

Two gaps found the same way as the last release's four: by writing a reproduction
for an open upstream bug — this time PowerToys' Command Palette, whose default
hotkey could not be expressed and whose placeholder field could not be read
honestly.

**This release contains an API change.** See the note above about what below-1.0
means here.

### Changed

- **`UiaElement.get_value()` no longer returns the element's Name** when nothing
  else can answer. It raises `ValueUnavailableError` instead.

  UIA offers four sources, and they are not equally trustworthy: `TextPattern`,
  `ValuePattern`, `WM_GETTEXT` on a native handle, and `Name`. The first three are
  the control's contents; `Name` is its *label*. Substituting one for the other is
  silent, which is the whole problem — the reading comes back a plausible string
  and every assertion against it passes on nothing.

  The case that found it: a WinUI `TextBox` bound to a `{n}` placeholder, whose
  Name is `'n'`. An empty field read back as `'n'`, so "the field is not empty"
  held while the field was empty.

  If the Name is what you want — a ComboBox reflecting its selection, a grid cell —
  pass `allow_name_fallback=True`. If you would rather decide for yourself, use
  `read_value()`, which returns the text and its source and never raises.

  In practice this reaches very little code: every Win32 control has an HWND, so
  `WM_GETTEXT` answers and the Name path is unreachable. Only handle-less elements
  (WPF, WinUI, UWP below the top-level window) get that far.

- **An empty native control now reads as `''` rather than as its Name.**
  `WM_GETTEXTLENGTH` returning 0 was treated as "the query did not work" and fell
  through to the Name fallback. It is the answer "this window's text is empty";
  `DefWindowProc` answers it for every window, so there is no case where 0 means
  failure. Same `length > 0` mistake as using a non-empty result to mean a query
  succeeded.

### Added

- **`send_hotkey(spec)`** for chords: `send_hotkey("win+alt+space")`,
  `send_hotkey("ctrl+shift+p")`, `send_hotkey("ctrl+,")`.

  `send_keys` deliberately does not grow a Win-key modifier. Its grammar sends
  unrecognised characters as literal text, so claiming AutoHotkey's `#` would
  change what `send_keys("issue #123")` types, and `+` is already Shift. A chord
  is a different job from typing, so it gets a different grammar: the last token
  is the key and everything before it must be a modifier, which makes `"win"` and
  `"win+alt+space"` one rule rather than two special cases and rejects
  `"space+ctrl"` instead of sending something else.

  `parse_hotkey()` is exposed alongside it and is pure, so the grammar is testable
  off Windows.

- **`LWIN`, `RWIN` and `WIN` in `KEY_NAMES`**, and both Win keys added to the
  extended-key set — without `KEYEVENTF_EXTENDEDKEY` the shell does not recognise
  a Win chord at all, and nothing reports an error.

- **`UiaElement.read_value()`** returning a `ValueReading(text, source)`, where
  `source` is `"TextPattern"`, `"ValuePattern"`, `"WM_GETTEXT"` or `"Name"`. Never
  raises; the source says what the reading is worth.

- **`ValueUnavailableError`**, and `ValueReading`, exported from the package root.

- **Cloaking, because `IsWindowVisible` answers True for a window nobody can see.**
  DWM can cloak a window: it stays visible by every USER32 measure while being
  drawn nowhere. A WinUI or UWP app that has put itself away does it, and so does
  *any window on another virtual desktop*.

  `Window.is_cloaked`, `Window.cloak_reason` (a `CloakReason` IntFlag, so a
  diagnostic says `SHELL` rather than `2`), `Window.is_on_screen` — visible and
  not cloaked, which is the one to assert on — and
  `Window.exists(require_on_screen=True)`. `get_window_cloak_reason()` is the
  underlying read.

  `exists()` and `is_visible` keep their meanings: tightening them silently would
  change what every existing caller measures.

  Found because a probe of Command Palette used `IsWindowVisible` to check the
  palette had dismissed, and its *control* failed — Esc had demonstrably worked
  and the measurement said otherwise. `None` is deliberately distinct from
  `False`: "could not ask" and "not cloaked" are different answers.

## [0.5.1] — 2026-09-01

Four gaps found by using this library on somebody else's code — writing
reproductions for open upstream bugs inside Notepad++, DB Browser for SQLite and
Files' own repositories.

**This patch release contains an API change.** See the note above about what
below-1.0 means here; the change is the first entry.

### Changed

- **`UiaElement.click()` raises instead of doing nothing** when the element has
  no bounding rectangle. A physical click needs a coordinate; an element that is
  scrolled out of view, not yet laid out, or hosted somewhere that publishes no
  rectangle has none. Returning quietly cost four separate debugging rounds in
  one day, and the symptom was never recognisable: a WinUI flyout button with a
  `(0,0,0,0)` rectangle read as "focus never reached the rename box", tree nodes
  outside a dialog's viewport read as "none of the 27 pages had the control",
  and a list item on a smaller desktop read as "the selection was stolen".
  `click(require_rectangle=False)` restores the old behaviour where a missing
  rectangle is genuinely acceptable — `set_focus`'s fallback uses it.

### Added

- **`Window.maximize()` and `Window.restore()`.** Sends
  `WM_SYSCOMMAND`/`SC_MAXIMIZE`, the message the title-bar button sends, rather
  than calling `ShowWindow` from outside; that path goes through the window's
  own handling, where a framework managing its own layout is listening.
  `maximize` polls `IsZoomed` and returns whether it worked, because a window
  can decline — one whose default rectangle already exceeds the screen looks
  maximised without being maximised, and judging that from a screenshot is how
  it gets missed.

- **`find_packaged_app()` and `launch_packaged_app()`** for MSIX applications. A
  packaged app has no path to test for and cannot be launched by one; it is
  addressed by AUMID and started through the shell moniker. This was previously
  only in this project's own test support, so anyone driving a packaged app had
  to rediscover it.

- **`interop.find_child_by_control_id()`.** Not `GetDlgItem`: a property sheet
  keeps every page it has visited as a child of the same dialog, so an id
  resolves on pages that are not showing. Numeric control ids are also the one
  identifier on a Win32 dialog that is not translated.

- **A `restype` for `SendMessageW`.** The default is a 32-bit int, which
  truncates any message answering with a packed pair — `WM_MENUCHAR` returns its
  command in the high word. `argtypes` are deliberately *not* declared: lParam is
  genuinely polymorphic, `WM_GETTEXT` wants a buffer there and `SCI_*` wants an
  integer, and declaring either rejects the other.

## [0.5.0] — 2026-09-01

Everything here came from driving four real applications rather than from reading
documentation — Notepad++ (Scintilla), WinMerge (Win32/MFC), DB Browser for
SQLite (Qt) and Files (WinUI 3), one per generation of Windows UI technology.
Each entry names what was measured.

### Added

- **Scintilla editors are found and can be read.** `Scintilla` joins the
  text-input ladder, and a new `ScintillaView` answers the questions
  `WM_GETTEXT` cannot: caret position, selection range, line count, EOL mode, tab
  width, code page, modified flag. `find_text_input` on Notepad++ went from
  failing after a 20s ladder timeout to succeeding in 718ms.

  Only scalar `SCI_*` messages are exposed, deliberately. The ones that return
  text take a pointer into the *caller's* address space, and USER32 does not
  marshal custom messages across a process boundary — `VirtualAllocEx` in the
  target does not help either, since the receiving code dereferences the pointer
  as its own. Reading the buffer works anyway: `get_value()` falls through to
  `WM_GETTEXT`, which USER32 *does* marshal, because it is a system message.

- **Four real applications are now release test items**, one per generation of
  Windows UI technology: Notepad++ (Scintilla), WinMerge (Win32/MFC), DB Browser
  for SQLite (Qt) and Files (WinUI 3). 32 tests, each application on its own CI
  runner in parallel — not `pytest-xdist`, because these drive the real desktop
  and two of them on one machine would fight over foreground focus.

  `tests/test_scintilla.py` had existed since the Scintilla work above and **had
  never run on CI**: nothing installed Notepad++, and `skipif` is silent.
  `WINTEGRATE_REQUIRE_TARGET_APPS` turns "not installed" into a failure that names
  every path it tried.

  Each application runs on **both a client and a server SKU** —
  `windows-11-arm` is Windows 11 Enterprise, `windows-latest` is Windows Server
  2025 — because the two are not interchangeable for UI automation.

  DB Browser for SQLite runs on both for a second reason: v3.13.1 ships a
  different Qt per architecture, `Qt5Core.dll` in the win64 build and
  `Qt6Core.dll` in the arm64 one. Same application version, different
  accessibility surface — Qt 6 exposes `SelectionItem` on tab items and Qt 5
  exposes only `Invoke`/`Value`, with no readable selection state anywhere. The
  tests drive both: they select through the pattern where it exists and through a
  click where it does not, and verify through the tab bar's `name`, which reports
  the selected tab's label on both builds.

  Application versions are pinned — Chocolatey `--version=` in CI, a SHA-256 for
  the mirrored Files package — and each module declares the same version in
  `VERIFIED_VERSION` with one test checking it, so the pin and the assertions
  cannot drift apart silently. Everything asserted was measured against one build:
  a sampled highlight colour, a Qt version inside a window class, a set of
  automation ids. A floating version would turn any upstream release into a red
  release gate with nothing wrong on this side.
  `WINTEGRATE_TARGET_APP_ANY_VERSION=1` relaxes the check for trying a newer build
  deliberately.

  Buttons are clicked, not just located: WinMerge's `Next Difference` against the
  status bar changing, Files' `Up` and `Back` against the location it reports,
  Notepad++'s Find dialog through its Win32 control ids, and the new-tab button in
  Files against the tab count.

- **`Window.ensure_onscreen()`** moves a window back inside the virtual screen. A
  window outside it is visible, foreground and fully readable through UIA, and
  every click is silently discarded. Measured on DB Browser for SQLite, which
  restored `(0, 0, 820, 620)` onto an 800x600 screen: tab selection kept working
  through the SelectionItem pattern while no button click landed.

- **`Window.focus_content_island()`** puts keyboard focus inside a WinUI 3 /
  Windows App SDK content island. A freshly launched WinUI 3 window can be the
  foreground window with UIA focus still on the top-level HWND, in which case XAML
  accelerators are silently dropped while `GetForegroundWindow()` and
  `set_foreground()` both report success. Measured on Files 4.2.9: `Ctrl+T` did
  nothing until focus moved into the island. Nothing is clicked, so no selection
  or activation happens as a side effect. Returns `False` rather than raising on a
  window that has no island.

- **`launch_and_discover(..., require_all=True)`** demands that every matching
  criterion describe the same window. The default remains OR, so
  `process_names` alone accepts a dialog the app puts up *instead of* its window:
  Files with no .NET runtime shows a `#32770` owned by `Files.exe`, and discovery
  returned it in 1.2s where the real window takes ~19s.

- **`UiaElement.set_focus(..., click=False)`** drops the physical click fallback.
  The click is what makes `set_focus` reliable on controls that ignore UIA
  `SetFocus`, but on a container it lands on whatever is at the centre and can
  select or activate something.

- **`interop.find_child_windows(hwnd, class_substrings)`** — child HWNDs by class
  substring, since the framework-owned child classes this is needed for carry
  namespaced names of which only the prefix is stable.

- **A second upstream-bug reproduction: WinMerge #3015.** Choosing *Insert tabs*
  in Options and pressing OK stores *Insert spaces* on 2.16.52, and the setting
  on 2.16.52.2. `PropEditor.cpp` had a `std::clamp(v, 1, MAX_TABSIZE)` validator
  on the wrong option, so tab type 0 was clamped up to 1. The failure is
  asymmetric — the spaces direction works on both builds — so the test asserts
  that direction too, as the control that makes the other assertion mean
  something. Driving it needed two things worth writing down: WinMerge 2.16 has
  no `HMENU` (its MFC Feature Pack menu bar is a toolbar, and the menu item's
  UIA `Invoke()` succeeds while opening nothing), and the Options page is found
  by which page owns control `1038` rather than by its localised name.

- **The DB Browser #3735 reproduction is wired into the demo workflow**, on both
  architectures. 3.13.0 has no arm64 package, so both sides of the pair are the
  win64 (Qt 5) build and the arm64 job runs it under emulation — which is the
  only coverage here of driving an emulated process, and is still measured on
  arm64 hardware. The workflow now takes a `case` input, and a selection that
  matches no job fails rather than passing as an empty matrix.

- **A third upstream-bug reproduction: Files #18815.** Alt+Enter put
  `DefWindowProc` into its menu-tracking path, which sends `WM_MENUCHAR` looking
  for a mnemonic; nothing matched, the default answer is `MNC_IGNORE`, and that
  plays the Asterisk sound. 4.2.7.0 answers `0`, 4.2.9.0 answers `MNC_CLOSE`.
  **The entire symptom is a sound** — the two builds are pixel-identical, so no
  screenshot and no screen recording can show it, while one message's return
  value settles it with no UI interaction to time. `WM_MENUCHAR` is `0x0120`,
  below `WM_USER`, so USER32 dispatches it into the other process's window
  subclass; a custom message would have gone across as two integers.

- **A blocking dialog now says what it is asking.** When window discovery times
  out, any `#32770` / message-box class among the visible windows has its child
  controls' text printed under it, so the failure reads
  `Static: 要在網域或工作群組中...` / `Button: 確定` rather than just
  `title='System Properties'`. This came from a real ARM64 CI failure where a
  System Properties dialog held the foreground: the census could name the
  window, and nobody could look at the screen of a runner that no longer exists.
  Read via `WM_GETTEXT`, which USER32 marshals across the process boundary, so
  it needs no COM — it runs when something has already gone wrong.

### Changed

- **The upstream-bug demo now installs WSL on arm64, like the release gate
  does.** Without it the arm64 image's provisioning daemon spawns a terminal
  popup every 30 seconds, which takes the foreground away from whatever is being
  driven. The demo workflow was written without the step and WinMerge's arm64
  job failed roughly one run in three.

- **The .NET Desktop runtime is only installed when it is missing.** The runner
  images already carry `Microsoft.WindowsDesktop.App` in 8.x, 9.x and 10.x, so
  `choco install dotnet-desktopruntime` was re-installing something that was
  already there — 66 seconds of a 163-second job, on three jobs. The install is
  kept as a fallback rather than deleted, because when it *is* needed the
  symptom is a `#32770` message box that window discovery mistakes for the
  application.

- **Every target application is installed from a verified mirror.** Notepad++,
  WinMerge and DB Browser for SQLite now come from the same fixture release that
  Files already used, instead of from three separate publisher hosts on every
  push. Each asset is pinned by SHA-256, and by the publisher's full
  Authenticode subject where one exists — the arm64 DB Browser msi carries no
  signature upstream, so there the hash is the only check and the workflow says
  so rather than pretending otherwise. The three divergent
  download-and-install steps collapsed into one fetch step plus one install step
  per package format.

  Both builds of every upstream-bug pair are mirrored too — Notepad++ 8.7.9/8.8,
  DB Browser 3.13.0/3.13.1, WinMerge 2.16.52/2.16.52.2, Files 4.2.7.0/4.2.9.0.
  Those pin *old* releases, which are exactly the assets that quietly stop being
  served. `docs/test-fixtures.md` records what is mirrored and why.

### Fixed

- **`find_text_input` no longer returns an embedded WebView2's document.** A
  hosted WebView2 publishes its Chromium accessibility tree into the host's UIA
  tree, and its root is a UIA Document — which the ladder tries before Edit. On
  Files 4.2.9 the release-notes pane made `find_text_input` return the blog post
  instead of the path box, successfully and with no exception. The rungs are not
  reordered, because a rich-text editor's own control is a Document and would then
  lose to any unrelated search box; the browser root is rejected by automation id
  instead.

## [0.4.1] — 2026-08-31

### Fixed

- **`Window.ime_mode` now waits for the IME to report the mode it was given.**
  Callers were sleeping a fixed interval afterwards to cover the gap between
  sending `WM_IME_CONTROL` and the IME acting on it — which is what the
  `_verified` suffix exists to remove, and it belonged in the library rather than
  in every caller. Gives up quietly rather than raising: an unverifiable mode is
  not worth failing a caller over when the block is about to run either way.

- **A discovery timeout now says what was on the desktop instead**, newly-arrived
  windows first, titled windows before untitled ones. `Window failed to appear`
  states what did not happen; the next question is always what happened instead,
  and the census only ran at session start and end, so the state at the moment of
  failure — the one that matters — was never recorded. Tracked down today's CI
  failure by downloading the recording and sampling video frames; that should not
  have been necessary.

  It also distinguishes **"the launch produced no window"** — a single-instance app
  absorbed it — from "a window appeared and was rejected". Different causes,
  different fixes.

- **The WPF grid fixture now reports why its window never appeared.** Polling the
  PowerShell process turns "exited with a syntax error" into an immediate failure
  carrying the exit code and stderr, instead of a 90-second mystery about a
  window. Its launch timeout also went 40s → 90s: a cold Windows Server runner
  loading `PresentationFramework` for the first time exceeded 40s on one job while
  three others on the same image were fine.

### Added

- `Session.step` censuses each step boundary and records `windows_added` /
  `windows_removed` in the event timeline. The session-level before/after pair
  cannot see a window that appears **and goes** during a run, which is precisely
  the window worth knowing about.
- `get_toggle_key_state()` and `set_caps_lock()` are exported.

### Documentation

- A new use-cases page (Tauri, Qt, .NET/WinUI, system utilities) — and a
  correction to it: its IME section advertised `ImmIsIME` for "conversion mode
  verification". That function was removed in 0.3.0 precisely because it does not
  do what its name says. It also offered `set_keyboard_layout_verified` as a
  capability, which is unreliable across processes. Two code samples would not
  have run: `cell.select()` does not exist, and `get_cell` takes `row=` rather
  than `row_index=`. Every API reference in the page is now checked against `src/`
  mechanically rather than by eye.

### Tests

- Seven sleep-then-assert sites became polling waits. Root cause of a real CI
  failure: `assert 'hel' == 'hello'` on an x64 runner — two of five scan codes had
  not landed within the 0.3s the test slept for. The helper returns the last value
  rather than raising, so the caller's own assert still produces that diff, which
  is what identified the cause.
- `test_far_rows_are_genuinely_virtualized` asked whether a cell was virtualized
  after `get_cell()` had already realized it — a race that failed one of eight CI
  jobs.

## [0.4.0] — 2026-08-31

### Added

- **Context managers for state that has to be established and given back.**

  ```python
  with dialog.ime_mode(ImeConversion.ALPHANUMERIC):
      edit.send_physical_keys("hello")     # deterministic
  with dialog.foreground():
      ...                                   # returned to the previous window
  with session.step("submit the form"):
      submit.invoke()                       # named in the timeline and the artifacts
  ```

  `Window.ime_mode` also neutralises **Caps Lock**, which was not planned:
  refactoring the IME tests onto this API failed with `assert 'HELLO' == 'hello'`
  because an earlier probe had left the toggle latched. Caps Lock is
  desktop-global, owned by nobody, survives everything, and affects only letters —
  so function-key tests pass while typing tests fail, and the message points at
  the key injection rather than at a toggle nobody set on purpose.

  Restoration restores only what could be **read**. A `None` conversion mode means
  no IME window answered, which is not the same as alphanumeric; restoring a guess
  leaves the desktop in a state the caller never asked for.

  `Session.step` records the step's start, duration and outcome in the event
  timeline, captures a screenshot named after the step, and prefixes the exception
  message — on a machine you cannot connect to, *which step* is more useful than
  *which line*. The original exception is re-raised untouched.

- `ImeConversion`, an `IntFlag`: `9` reprs as `ImeConversion.NATIVE|FULLSHAPE`
  instead of `9`. Members are ints, so the `IME_CMODE_*` constants still work.
- `get_toggle_key_state()` and `set_caps_lock()`.
- `@requires_ime` and `@requires_windows_build(n)`, alongside the existing
  `@desktop_only`. `requires_ime` keys off the layout's *language*, not `ImmIsIME`
  — that function reports whether an HKL is loaded, which is why 0.3.0 removed the
  field built on it.
- `__repr__` on `Window` and `UiaElement`, so a failed assertion says what it was
  holding. Both tolerate a dead target: a repr that raises replaces a useful
  message with a traceback about formatting.

### Changed

- `UiaElement.__eq__` now goes through UIA's `CompareElements`. Two COM pointers
  to one element are not the same pointer, so the default `==` answered False for
  elements that are the same thing.
- **`UiaElement` is no longer hashable** (`__hash__ = None`). A handle that can go
  stale has no stable hash — two unequal elements can become equal — so a `set` or
  dict key would rely on a guarantee that does not exist. This is the breaking
  change in this release; it fails loudly rather than silently.

## [0.3.0] — 2026-08-31

Four defects, all shipped in 0.1.4 or earlier, none caught by CI. The common
thread is worth stating plainly: **GitHub's runners are en-US, and the first two
only execute on a non-English desktop.** They were found by re-running the suite
on a zh-TW Windows 11 ARM64 VM.

### Fixed

- **`set_ime_open` and `set_ime_conversion` did nothing when driving another
  process** — the only case an automation library exists for. Both began with
  `ImmGetContext`, which returns nothing across a process boundary and for any
  control routing text services through TSF, so they returned `False` and gave up
  silently. They now resolve the focused child with `GetGUIThreadInfo`, ask
  `ImmGetDefaultIMEWnd` for its IME window, and send `WM_IME_CONTROL`. Measured
  on a zh-TW ARM64 desktop, mode toggled from outside the target process:

  ```
  IME_CMODE_ALPHANUMERIC   send_physical_keys("hello") -> "hello"
  IME_CMODE_NATIVE         send_physical_keys("hello") -> ""
  ```

- **A "fresh" launch of a packaged app was not fresh.** Three defects compounded
  into an intermittent discovery timeout that read as a cold-start flake:

  1. The sweep was timed, not verified — `kill_processes()` then `sleep(0.3)`.
     Terminating a packaged app is asynchronous, and a window that outlives the
     guess makes the next launch of a single-instance app produce no new window
     at all. `sweep_processes_verified()` polls until nothing matching remains.
  2. Terminating was never enough: Store Notepad keeps its open tabs in
     `LocalState\TabState` and reopens them next launch, so a fresh window
     arrived holding a previous test's text — one leftover file was 1062 bytes of
     accumulated input. `AppSpec` gains `package_family_name` and
     `session_state_dirs`, cleared once the windows are gone, which is the only
     point at which those files are closed.
  3. Discovery accepted a window that was not ready. A top-level window becomes
     visible before it is populated — the class matches while the title is still
     empty — so the shell was handed back and failed 20s later inside
     `find_text_input`, on a window whose title printed as `''`. An untitled
     match now means "not yet", and the timeout message says so.

  Five consecutive full-suite runs on that VM: **3/3 runs had failures before,
  5/5 pass after**, and the suite went from 128s to a steady 80s.

- `test_far_rows_are_genuinely_virtualized` asked whether a cell was virtualized
  *after* `get_cell()` had already realized it — a race that failed one of eight
  CI jobs. It now asks the raw element before anything realizes it.

### Added

- `get_ime_conversion(hwnd)` — the conversion mode as the IME itself reports it.
- `sweep_processes_verified()` and `clear_package_session_state()`, exported for
  callers that launch outside `session.app()`.
- `AppSpec.package_family_name` and `AppSpec.session_state_dirs`.

### Removed

- **`layout_has_ime()`, `Window.keyboard_layout_has_ime`, and the
  `layout_has_ime` key of `get_ime_status()`.** They were built on `ImmIsIME`,
  which reports whether an HKL is *loaded*, not whether it is an IME: loading a
  plain en-GB layout alongside Bopomofo makes `ImmIsIME` answer true for en-GB
  too, so the field read `True` for every loaded layout on any machine with an
  IME installed. A field that is always true is worse than an absent one, because
  callers branch on it. **This is the breaking change in this release.**

  Use `set_ime_conversion` to *establish* the input state you need rather than
  trying to detect it. Detection was the wrong shape of the problem.

### Notes

- `set_keyboard_layout_verified` remains, and remains unreliable across
  processes by nature: a layout is loaded per process, and a window elsewhere
  rejects one it has never loaded. Post and send, with `SYSCHARSET`, `FORWARD`,
  no flag, `AttachThreadInput` + `ActivateKeyboardLayout`, and `HWND_BROADCAST`
  all left the target on `0x04040404`. Its failure now names the owning pid and
  says so. Prefer setting the conversion mode.

## [0.2.0] — 2026-08-31

### Removed

- **The external FFmpeg subprocess backend.** `ContinuousRecorder` now encodes
  only through PyAV. The fallback never covered the case it appeared to: PyAV is
  the only ffmpeg distribution on PyPI with a `win_arm64` wheel, so on ARM64 the
  subprocess path resolved only when the user had already installed an ffmpeg
  binary by hand — the exact situation this library exists to avoid, made to look
  handled. `resolve_ffmpeg_exe()` is gone along with it; it probed PATH, four
  hardcoded directories, two WinGet globs and `imageio_ffmpeg` on every recorder
  construction.
- `ContinuousRecorder.backend` can therefore no longer return `"ffmpeg"`; it is
  `"pyav"` or `None`. **This is the breaking change in this release.**

Without PyAV, `start()` returns `False` and logs, as before. A missing recording
must never be why a test run fails.

### Added

- **Whole-run recording.** A session-scoped pytest fixture records the entire
  test run to `recording-artifacts/full-suite-<arch>.mp4`, enabled with
  `WINTEGRATE_RECORD_SUITE=1`. The per-session clips each show one scenario; what
  they cannot show is the run, and on a CI runner the dialog that breaks a late
  test is usually one that appeared during an early one.

### Documentation

- The README now opens with the complete test suite running on both
  architectures — 108s on x64 and 125s on ARM64, unedited and uncropped, with the
  cold-start gap (0.10s vs 17.18s to a discoverable Notepad window) alongside.
- A CI/CD integration section with a workflow template and case study.
- `docs/pitfalls.md` records why there is deliberately no external-ffmpeg path.

## [0.1.4] — 2026-08-30

### Added

- **Grid and tree controls.** `DataGrid`, `DataGridRow`, `DataGridCell`,
  `TreeView`, and `TreeViewItem`, all exported from the package root, wrapping
  the UIA Grid/GridItem/Table/TableItem and ExpandCollapse patterns. Cells are
  addressable by column index *or* header name, and `TreeView.navigate_path_verified`
  walks and expands a path in one call.
- **Countermeasures for UI virtualization.** A WPF `DataGrid` does not create
  elements for rows you have not scrolled to, so a plain search finds nothing
  and the failure looks like a wrong query. `UiaElement.scroll_into_view()`,
  `.realize()`, `.ensure_available()`, and `.find_item_by_property()` bring an
  item into existence through ScrollItem/VirtualizedItem/ItemContainer first.
- `UiaElement.as_data_grid()`, `.as_tree_view()`, and `.as_tree_item()` to cast
  a found element into the wrappers above.
- `UiaElement.supported_patterns()` and `.describe()`. An unsupported-pattern
  error now reports what the element actually is, which is the difference
  between "GridPattern is unsupported" and knowing you are holding a `List`
  rather than a `DataGrid`.
- `DataGrid.get_column_headers()` falls back to `HeaderItem` children: a WPF
  `DataGrid` supports TablePattern but answers `GetCurrentColumnHeaders` with an
  empty collection, so the pattern alone reports no headers on the very control
  the pattern exists for.
- `Window.set_keyboard_layout_verified(layout_id)` — switches the window's thread
  to a keyboard layout and polls until it takes. A layout change is a request
  posted to another thread, so it is not in effect when the call returns; code
  that assumed otherwise typed into whichever layout won the race.
- `get_ime_status()` now reports `layout_has_ime`, and `Window` exposes
  `keyboard_layout_has_ime`. The status previously could not distinguish "no IME
  here" from "the IME is running through TSF" — a modern control gives IMM32 no
  context, so `has_context` is `False` in both cases while keystrokes are being
  swallowed.
- `layout_has_ime(hkl)` in `wintegrate.interop`.

### Fixed

- The scan-code input tests pinned themselves to a Latin layout. Under Bopomofo
  they were failing on ARM64 because `send_physical_keys` was working correctly:
  the IME took the unshifted letters, which is the reason that input path exists.

## [0.1.3] — 2026-08-30

### Added

- **Screenshots on demand.** Screenshot capture existed but only fired
  automatically on failure, was not exported, and covered the primary display
  only. Now:
  - `Session.capture_screenshot(name, window=None)` — saves into the session's
    artifact directory and returns the path
  - `Window.capture(path)` and `UiaElement.capture(path)`
  - `capture_screen_image(all_monitors=True)` and `capture_window_image(hwnd)`,
    both exported from the package root
- Window captures use `PrintWindow`, so they include parts of the window other
  windows are covering — on CI that is often the popup that broke the run, and a
  cropped screenshot would show the intruder rather than the window under test.
  `PrintWindow` returns black for some DWM/XAML windows, so the result is checked
  and falls back to cropping; a black rectangle would be an artifact that looks
  like evidence and shows nothing.

### Changed

- The automatic failure screenshot now captures the whole virtual desktop. The
  window under test is not always on the primary display, and a primary-only
  capture of a failure elsewhere is worse than none.

## [0.1.2] — 2026-08-30

### Fixed

Three defects in the `send_keys` spec parser, all of the kind that hide rather
than announce themselves:

- `{TAB 99999999999}` built one action per repeat and exhausted memory instead of
  reporting a typo. Repeat counts are now capped at `MAX_KEY_REPEAT` (1000).
- `{TAB -5}` parsed cleanly and expanded to **zero actions** — keystrokes were
  requested, none were sent, and nothing was reported. Counts below 1 now raise.
- `{TAB +3}` and `{TAB 1_000_000}` were accepted, inheriting Python's
  integer-literal syntax into a grammar that never promised it. Digits only.

### Added

- Property-based tests (Hypothesis) over the key-spec parser.

## [0.1.1] — 2026-08-30

### Fixed

- **Importing wintegrate no longer breaks other ctypes users in the same
  process.** `ctypes.windll` hands every importer the same DLL handle, whose
  function pointers are cached process-wide, so the `argtypes` pinned at import
  were visible to every other ctypes user. Since ctypes validates pointer
  arguments by type identity rather than layout, an unrelated library passing its
  own byte-identical `INPUT` struct to `SendInput` failed with
  `expected LP_INPUT instance instead of LP_Input`, raised from inside *that*
  library. Every DLL is now loaded through a private `ctypes.WinDLL` instance.

## [0.1.0] — 2026-08-30

First stable release.

### Added

- **Verified actions.** Every interaction asserts its post-condition before
  returning — line-count deltas, buffer content, focus transitions.
- **Managed app lifecycle** (`session.app`): discovery by process image name and
  window class rather than localized titles, single-instance safety for Store
  apps, cold-start headroom, guaranteed cleanup.
- **Classic Win32 and modern XAML**: dialog controls by control id, with Toggle /
  Selection / ExpandCollapse patterns, `exists()`, and `find_descendant(...,
  required=False)`.
- **IME support**: scan-code key injection an IME can intercept, IMM32 state
  queries, and per-thread keyboard layout resolution.
- **Diagnostic black-box**: in-process recording through PyAV, failure
  screenshots, window census diffs, structured event timelines.
- Core install depends on `comtypes` alone; recording and virtual-desktop
  isolation are optional extras.

### Changed since 0.1.0a1

- `Window.find` and `find_descendant` combine their criteria with **AND**. They
  previously returned on the first criterion that matched, so extra criteria
  widened the search instead of narrowing it — and handed back a wrong window or
  element silently. `Window.find` also gained `pid=`.
- `type_verified` always verifies the typed content, even when an
  `expected_line_count_delta` is given. A delta alone only proves some newlines
  arrived; ARM64 runners repeat or drop characters under load, which leaves the
  line count intact.
