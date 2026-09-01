# Changelog

All notable changes are recorded here. This project follows
[Semantic Versioning](https://semver.org/); while the version is below 1.0 the
API may still change between minor versions, and any such change is called out
below.

## [Unreleased]

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
