# Changelog

All notable changes are recorded here. This project follows
[Semantic Versioning](https://semver.org/); while the version is below 1.0 the
API may still change between minor versions, and any such change is called out
below.

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
