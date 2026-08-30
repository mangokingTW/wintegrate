# What breaks in CI

Every countermeasure in this library exists because a specific failure happened on
a real runner. They are collected here because most of them are worth knowing even
if you never use wintegrate — they will bite any Windows automation on CI.

## Synthesized input gets mangled on ARM64

On `windows-11-arm` under load, injected characters are repeated or dropped.
`pywinauto` arrived as `uuuuuuuto`, `iiiinauto`, `ooooooooo` on different runs.

The dangerous part is not the corruption — it is that **a line-count assertion
still passes**. The text has the right shape and the wrong content.

`type_verified` therefore always verifies content, deriving the expectation from
the text you asked it to type when you do not supply one. A `expected_line_count_delta`
is an additional check, never the only one.

## Store apps are single-instance

Launch Notepad while an instance is already running and Windows opens a **tab** in
the existing window instead of a new top-level window. Discovery then waits for a
window that will never appear and times out with no clue why.

`session.app(..., fresh="auto")` sweeps leftover instances before launching on CI.
When you deliberately want a second window, pass `fresh=False` and
`exclude_hwnds={first.hwnd}`.

Related: Store Notepad **restores its previous session's content**, so a
find-or-launch handoff can hand you a window carrying the last test's text. Assert
on containment rather than equality, or start fresh.

## Cold starts take longer than anyone budgets

A first Store-app launch regularly exceeds 12 seconds on ARM64 runners; warm
launches take about 2. A discovery timeout tuned on a warm machine fails on a cold
one, intermittently, in a way that reads like a flaky test rather than a timeout.

Managed launches default to 30 seconds.

## Windows discovery catches helper windows

Modern Notepad creates an invisible top-level window titled `GDI+ Window
(Notepad.exe)` before the real one becomes visible. A title pattern of
`.*Notepad.*` matches it, and automation then drives an empty shell — silently,
because every call succeeds and does nothing.

Discovery requires visibility, filters known helper classes, and prefers process
image name and window class over titles.

## COM wrappers go stale

A resolved UIA element held across seconds of interaction stops working — and
often stops *raising* too, so calls quietly do nothing. Always re-resolve from the
window handle (`window.re_resolve_element()`) rather than reusing an element you
found earlier.

## `KEYEVENTF_UNICODE` bypasses the IME

Unicode injection hands the codepoint straight to the control. An IME never sees
it, so composition never starts and no candidate window appears. Testing IME
behaviour requires scan-code input (`send_physical_keys`).

## Focusing an EDIT selects all of it

Standard Win32 behaviour, and it surprises everyone: after focus, the next
keystroke *replaces* the field. Since `send_keys` focuses first, `{BACKSPACE}`
clears the field. Collapse the selection first.

Also: `Ctrl+A` does **not** select all in a classic `EDIT` — that is RichEdit. An
assertion built on it passes for the wrong reason. Use `{HOME}` then `+{END}`.

## No ffmpeg on PyPI ships a `win_arm64` wheel

Checked across every release of `imageio-ffmpeg`: all 16 are `win32` / `win_amd64`
only, and its sdist contains no binary at all, so an ARM64 install resolves to an
empty shell. `pyffmpeg` is `win_amd64` only; `static-ffmpeg` and
`ffmpeg-downloader` are pure-Python downloaders.

`av` (PyAV) bundles FFmpeg and **does** publish `cp311-abi3-win_arm64`, which is
why recording here encodes in-process through PyAV rather than shelling out.

## `ctypes.windll` is process-global shared state

`ctypes.windll.user32` is one object for the whole interpreter, and its function
pointers are cached. Pin `argtypes` on it and every other ctypes user in the
process sees them — and since ctypes validates pointers by **type identity, not
layout**, a library passing its own byte-identical `INPUT` struct fails with
`expected LP_INPUT instance instead of LP_Input`, raised from inside *that*
library.

Load private handles instead:

```python
user32 = ctypes.WinDLL("user32", use_last_error=True)
```

This one is worth knowing regardless of this library: any package that pins
argtypes on `ctypes.windll` is a hazard to everything else in the process.
