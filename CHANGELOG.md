# Changelog

All notable changes are recorded here. This project follows
[Semantic Versioning](https://semver.org/); while the version is below 1.0 the
API may still change between minor versions, and any such change is called out
below.

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
