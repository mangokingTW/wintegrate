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

## Touch injection reports success when nothing receives it

`CreateSyntheticPointerDevice`, `InitializeTouchInjection` and every
`InjectSyntheticPointerInput` call can all return success on a host where no
window sees a contact. Measured on a fresh `windows-11-arm` runner: the device
was created, `GetPointerDevices` went from 0 to 2, DOWN, UPDATE and UP were all
accepted, and the listener received nothing.

The cause turned out not to be touch at all — a full-screen `Shell_OOBEProxy`
window owned the foreground, and **a plain mouse click was ignored there too**.
Three rounds of measurement concluded "touch is undeliverable on ARM runners"
before a mouse control case was added and showed the desktop was simply covered.

So `Touch.available()` does not ask the API. It taps a small window of its own
and checks the tap arrived:

```python
with Touch() as touch:
    if not touch.available():
        pytest.skip("touch is not delivered on this host")
    touch.tap(x, y)
```

Two rules follow, and the second is the one that costs time:

- **Never gate touch on the injection return value, or on `SM_DIGITIZER`.**
  That metric reads `0` on every host measured, including the ones where touch
  works.
- **Give any input experiment a control that is known to work** — the same
  coordinate clicked with `SendInput` first. Without it, "the feature does not
  work" cannot be told from "I aimed at nothing", and `WindowFromPoint` plus
  `GetDlgCtrlID` printed before the press is the cheapest version of that check.

## A covered desktop makes every input test lie, and ARM runners start covered

`windows-11-arm` boots with two things fighting for the foreground: a full-screen
onboarding window (`Shell_OOBEProxy` / `Windows.UI.Core.CoreWindow`, titled
*Microsoft account*, covering the whole screen) and a `wsl.exe` terminal that the
provisioning daemon respawns every 30 seconds until WSL is updated.

A window underneath either of them receives **no input at all**. Not touch, not
a mouse click, not a keystroke — and nothing in the failure says so. The symptom
is whatever the test happened to assert.

`.github/actions/setup-windows-gui-test` does both preparations, gated on
`runner.arch == 'ARM64'`. Reuse it rather than reimplementing:

```yaml
- uses: mangokingTW/wintegrate/.github/actions/setup-windows-gui-test@main
  with:
    wintegrate-version: ">=0.5.10"
```

The WSL step must stay ARM-only. Run unconditionally, it hangs on the x64 image
— there is no `wsl.exe` to update — until the job is cancelled and every later
step is skipped.

## A native crash does not fail the test run

An access violation inside a ctypes callback is printed by `faulthandler` and
then execution continues. `pytest` reports it as neither a failure nor an error:
a run that crashed seven times finished `211 passed, 5 skipped`, exit code 0, and
the two tests it broke reported themselves as **skipped**.

Nothing about the crash reaches the exit code. Search the output for it:

```yaml
- run: |
    pytest tests/ -v -s 2>&1 | Tee-Object -FilePath pytest-output.txt
    $failed = $LASTEXITCODE
    if (Select-String -Path pytest-output.txt -Pattern 'Windows fatal exception|access violation') {
      Write-Host "::error::a native crash happened during the tests"
      exit 1
    }
    if ($failed -ne 0) { exit $failed }
```

The usual cause is a lifetime one. **Anything the OS keeps a pointer to must
outlive the call that handed it over** — a `WNDPROC`, and the `WNDCLASSW` that
registered it, because a window class holds pointers *into* that struct
including its class-name string. Build either as a local and the registered class
reads freed memory later.

The other cause is a truncated handle: an undeclared `restype` makes ctypes
convert a 64-bit `HANDLE` as `c_int`. `kernel32.GetModuleHandleW` was the one
that got through here, and passing its truncated `HMODULE` to `RegisterClassW`
and `CreateWindowExW` crashed inside window creation. Declare every
handle-returning call — see also *`ctypes.windll` is process-global shared
state*, which is the reason to declare it on a private handle.

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

## An app's error dialog belongs to the app's process

`process_names` cannot tell an application apart from a dialog the application
puts up *instead of* starting. Files 4.2.9 with its .NET runtime missing shows a
standard `#32770` message box titled `Files.exe`, owned by `Files.exe` — so the
process name matches and discovery hands back the dialog. Every element lookup
afterwards fails against a window that reports no problem.

The tell is timing: the dialog was found in 1.2s where the real window takes
about 19s. *Faster than expected* is a signal.

Criteria in `launch_and_discover` are OR by default (any one matching accepts the
window), so adding `window_classes` alongside `process_names` widens the search
rather than narrowing it. Pass `require_all=True` to demand that every criterion
describe the same window:

```python
Window.launch_and_discover(
    cmd, process_names=("Files.exe",),
    window_classes=("WinUIDesktopWin32WindowClass",), require_all=True,
)
```

## A window can be off-screen and behave normally until you click

A window positioned outside the virtual screen is still visible, still
foreground, and its UIA tree still resolves. Patterns that do not involve a
pointer keep working — `select_verified()` goes through SelectionItem and
succeeds. What stops is anything that clicks: a synthesised click at a negative
coordinate lands nowhere, `click()` returns without complaint, and the test fails
on its post-condition with no hint that the cursor never arrived.

Measured on DB Browser for SQLite, which restores its last window position: on an
800x600 screen it came back at `(0, 0, 820, 620)`, and elements reported
coordinates like `(-701, -525, -494, -469)`. Applications that remember their
geometry will restore a position saved on a machine with a different screen
layout, so this is worth checking rather than assuming.

`window.ensure_onscreen()` moves it back, and returns True when the window is
on-screen afterwards — including when it already was.

Note that a negative coordinate alone does not mean off-screen: the virtual
screen's origin is negative when a monitor sits above or to the left of the
primary one. The comparison has to be against `SM_XVIRTUALSCREEN` and friends.

Hidden widgets can report off-screen rectangles for a different reason. Qt gives
a collapsed dock's children coordinates far outside the window, so "the rectangle
is off-screen" can mean "this control is not currently shown" rather than "the
window is misplaced".

## A modal dialog leaves every element query working

An application that opens a modal over its own window keeps its whole UIA tree
readable. Elements resolve, names and values come back, and every assertion about
structure passes — while keyboard accelerators and synthesised clicks go to the
modal instead of the control.

Files does this on an elevated machine, which every CI runner is: it shows a
"Files is running as administrator" dialog on first launch. Four tests failed with
`Ctrl+T did not open a tab (1 -> 1)` and `the new-tab button did not open a tab`,
and nothing in the failures pointed at a dialog. The CI recording did, in one
frame.

Two things follow. Suppress the prompts an application shows on first run — Files
has `ShowRunningAsAdminPrompt` in its settings JSON, alongside the session-restore
keys. And record the run: a screenshot answered in seconds what the assertion
messages could not answer at all.

## Being the foreground window is not the same as having focus

WinUI 3 and Windows App SDK apps host their XAML in a
`Microsoft.UI.Content.DesktopChildSiteBridge` child window. A freshly launched
one can be foreground with UIA focus still on the *top-level HWND*: XAML
accelerators are dropped, while `GetForegroundWindow()` returns the window and
`set_foreground()` reports success. Measured on Files 4.2.9, `Ctrl+T` did nothing
in that state and worked immediately once focus moved into the island.

`window.focus_content_island()` moves focus there without clicking. Two routes
that look right and are not:

- `SetFocus()` on the top-level window's own UIA element leaves focus untouched,
  as does sending `Tab`.
- Focusing the first keyboard-focusable descendant lands on the caption's
  `InputNonClientPointerSource` input sink. Focus *does* leave the top-level
  window, so a naive "did focus move?" check passes — and the accelerator is
  still dropped.

Verifying it requires walking the whole ancestor chain of the focused element,
not stopping at the first ancestor that owns a window: `InputSiteWindowClass`
owns a handle and sits *below* the bridge.

## An embedded WebView2 outranks the app's own text box

A hosted WebView2 publishes its Chromium accessibility tree into the host's UIA
tree, and its root node is a UIA **Document**. `find_text_input`'s ladder tries
Document before Edit, so on Files 4.2.9 the release-notes pane made it return the
blog post's document instead of the path box — a wrong element, returned
successfully.

Reordering the two rungs only moves the problem onto rich-text editors, whose
own control *is* a Document and would then lose to any unrelated search box. The
browser root is the part that is never the answer, so the ladder rejects it by
automation id (`RootWebArea`) and carries on downward.

## COM wrappers go stale

A resolved UIA element held across seconds of interaction stops working — and
often stops *raising* too, so calls quietly do nothing. Always re-resolve from the
window handle (`window.re_resolve_element()`) rather than reusing an element you
found earlier.

## `KEYEVENTF_UNICODE` bypasses the IME

Unicode injection hands the codepoint straight to the control. An IME never sees
it, so composition never starts and no candidate window appears. Testing IME
behaviour requires scan-code input (`send_physical_keys`).

## A scan code means whatever the active layout says it means

The flip side of the above, and it looks exactly like a bug. On a zh-TW desktop
with Bopomofo active, `send_physical_keys("hello")` leaves the field **empty** —
the letters became phonetic keys and went into composition. The same call on the
same control under en-US types `hello`. Nothing was dropped; the IME took them,
which is the whole point of the scan-code path.

So a test that types Latin text through `send_physical_keys` and asserts the
result is really asserting that whoever runs it has no IME installed. Pin the
layout first:

```python
window.set_keyboard_layout_verified("00000409")  # en-US
```

The `_verified` matters: a layout change is a *request* posted to another
thread, so it is not in effect when the call returns. This one loads the layout,
posts `WM_INPUTLANGCHANGEREQUEST`, and polls until the thread reports it.

## `has_context: False` does not mean "no IME here"

`get_ime_status()` reports `has_context` from IMM32, and a modern control routes
text services through TSF instead — so IMM32 hands out no context *even while an
IME is actively swallowing keystrokes*. Read that field alone and you conclude
the opposite of the truth.

There is no companion field, and that is deliberate. `ImmIsIME` looks like the
answer and is not: measured on a zh-TW desktop, it returns true for a plain en-GB
layout the moment that layout is loaded. It reports whether an HKL is *loaded*,
so a field built on it reads True for every layout on any machine that has an
IME installed — always True, which is worse than absent because callers branch
on it.

Nothing here predicts interception anyway: whether a Bopomofo layout swallows a
given letter also depends on its conversion mode at that moment, and with no
IMM32 context there is nothing for `set_ime_conversion` to act on.

**Do not try to detect an IME. Pin the layout instead** — and be ready for that
to fail too (see below).

## Some controls expose nothing at all, and pixels are the only evidence

Scintilla answers no UIA pattern but still responds to `WM_GETTEXT`, so
`get_value()` works. Not everything is that kind. WinMerge's diff panes are
window class `Afx:00007FF6C8380000:8` — a name containing the module base
address, so it is not even a stable identifier — and they answer **no pattern and
no `WM_GETTEXT`**:

```
UIA: <Pane class='Afx:00007FF6C8380000:8' name='' id='59648' patterns=[]>
patterns: []
get_value(): ''
```

For a *visual diff tool*, checking only the output file misses the entire point:
the question is whether the difference was **displayed**. When the control tells
you nothing, the rendered pixels are the only evidence left — and `Window.capture()`
and `UiaElement.capture()` already hand you a Pillow image.

**Assert colour structure, not pixel equality.** A screenshot compared against a
baseline breaks on a font update, a DPI change, a theme change, or antialiasing,
and the baseline then has to be regenerated. Asking "is this colour present, and
in how many contiguous bands" survives all of those. Measured against WinMerge,
comparing a 12-line file:

| changed lines | highlight rows | contiguous bands |
|---|---|---|
| 0 | 0 | 0 |
| 1 | 16 | 1 |
| 2 | 32 | 2 |
| 3 | 48 | 3 |

Its highlight is exactly `(239, 203, 5)`, absent from an identical comparison and
12.4% of the pane with two changed lines. One band per difference, sixteen rows
each — one line of text.

```python
HILITE = (239, 203, 5)

def highlight_bands(image, colour):
    """Contiguous horizontal runs of `colour`, as inclusive (first_row, last_row)."""
    rgb = image.convert("RGB")
    spans, start = [], None
    for y in range(rgb.height):
        row = rgb.crop((0, y, rgb.width, y + 1))
        present = any(v == colour for _n, v in (row.getcolors(1 << 24) or []))
        if present and start is None:
            start = y
        elif not present and start is not None:
            spans.append((start, y - 1))
            start = None
    if start is not None:
        spans.append((start, rgb.height - 1))
    return spans

bands = highlight_bands(pane.capture(), HILITE)
assert len(bands) == 2          # two differences, and bands says which lines
```

Two details in those ten lines are worth having in front of you rather than
hidden in a library: touching runs merge into one band, and an open run has to be
flushed at the bottom edge. `getcolors` on a one-pixel-high crop keeps the scan
in C, which matters on a full-screen capture.

**This is deliberately not part of wintegrate.** Everything the library does
encode is Windows knowledge that is hard to rediscover — that `WM_GETTEXT` is
marshalled across processes while `SCI_*` is not, that an IME's mode is set
through `WM_IME_CONTROL`, that a sweep has to wait for processes and not just
windows. Counting same-coloured rows is not that: it is ten lines of arithmetic
with no trap in it, and putting it behind an API would invite tolerance
parameters, then regions, then perceptual hashing — none of which has a natural
stopping point.

## The selection state after focus is undefined

Focusing a Win32 `EDIT` through UIA selects its entire contents, so the next
keystroke *replaces* the field. Arriving by click instead places the caret and
selects nothing. `set_focus` does both — UIA `SetFocus`, then a click — and which
one wins is timing-dependent: the same test produced an empty field on x64 and
`ab` on ARM64.

So never send a bare destructive key after focusing. Collapse the selection
explicitly first — `{HOME}` or `{END}` — and the behaviour stops depending on
which path won.

Also: `Ctrl+A` does **not** select all in a classic `EDIT` — that is RichEdit. An
assertion built on it passes for the wrong reason. Use `{HOME}` then `+{END}`.

## No ffmpeg on PyPI ships a `win_arm64` wheel

Checked across every release of `imageio-ffmpeg`: all 16 are `win32` / `win_amd64`
only, and its sdist contains no binary at all, so an ARM64 install resolves to an
empty shell. `pyffmpeg` is `win_amd64` only; `static-ffmpeg` and
`ffmpeg-downloader` are pure-Python downloaders.

`av` (PyAV) bundles FFmpeg and **does** publish `cp311-abi3-win_arm64`, which is
why recording here encodes in-process through PyAV and has no external-binary
path at all. A subprocess fallback would look like a safety net while quietly
making recording an ARM64-only-if-the-user-installed-ffmpeg feature.

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

## An element's Name is its label, not its text

UIA gives four ways to read what a control holds, and they are not equally
trustworthy: `TextPattern`, `ValuePattern`, `WM_GETTEXT` on a native handle, and
finally `Name`. The first three are the control's contents. `Name` is its
**label**, and reaching for it when the others are silent produces a reading that
looks like an answer and is not one.

The case that made this concrete: a WinUI `TextBox` bound to a bookmark's `{n}`
placeholder. Its Name is `'n'`. An empty field read back as `'n'`, so every check
of the form "the field is not empty" passed while the field was empty.

`read_value()` reports the source, and `get_value()` refuses the weak one:

```python
reading = element.read_value()
if reading.source == "Name":
    ...              # this is a label; decide what that means here

element.get_value()                             # raises ValueUnavailableError
element.get_value(allow_name_fallback=True)     # opt in, for a ComboBox or a grid cell
```

Reachability is worth knowing when writing a test for this: **every Win32 control
has an HWND**, so `WM_GETTEXT` always answers and `Name` is unreachable. Only
handle-less elements — WPF, WinUI, UWP below the top-level window — get that far.

A related trap in the same code: `WM_GETTEXTLENGTH` returning 0 is the answer
"this window's text is empty", not "the query did not work". `DefWindowProc`
answers it for every window, so there is no case where 0 means failure. Treating
it as a miss is what used to pass an empty native control on to the `Name`
fallback.

## `send_keys` has no Win key, on purpose

`send_keys`'s grammar sends anything it does not recognise as literal text. Giving
a character a modifier meaning is therefore not additive — AutoHotkey spells the
Win key `#`, and claiming `#` would silently change what `send_keys("issue #123")`
types. `+` is already Shift, so the usual hotkey separator cannot live there
either.

Chords go through a separate function, with a separate grammar:

```python
send_hotkey("win+alt+space")     # Command Palette
send_hotkey("ctrl+shift+p")
send_hotkey("ctrl+,")            # layout-dependent keys are resolved at send time
send_hotkey("win")               # the last token is the key, so this opens Start
```

The rule is that the last token is the key and everything before it must be a
modifier, which makes `"win"` and `"win+alt+space"` one grammar instead of two
special cases, and rejects `"space+ctrl"` rather than sending something else.

Both Win keys also need `KEYEVENTF_EXTENDEDKEY`: without it the shell does not
recognise the chord at all, and nothing reports an error.

## `IsWindowVisible` answers True for a window nobody can see

DWM can *cloak* a window: it stays visible by every USER32 measure while being
drawn nowhere. Two common ways in:

- a WinUI or UWP app that has put itself away — Command Palette dismissing on
  Esc, a Store app suspending;
- **any window on another virtual desktop**, which Windows cloaks itself.

So `IsWindowVisible` cannot tell "dismissed" from "showing", and neither can
`Window.is_visible` or the default `Window.exists()`. A test waiting for such a
window to disappear waits forever; a test asserting it is gone passes while it is
still there. That second one is the dangerous shape, and it has already produced a
*control* that failed — a keystroke which demonstrably worked was reported as
having done nothing.

```python
win.is_visible        # IsWindowVisible: True even when cloaked
win.is_cloaked        # True / False / None when it cannot be read
win.cloak_reason      # CloakReason.SHELL, CloakReason.APP, CloakReason(0), None
win.is_on_screen      # visible AND not cloaked -- the one to assert on
win.exists(require_on_screen=True)
```

`exists()` keeps its old meaning by default, because tightening it silently would
change what every existing caller measures.

The reason is worth reading, not just the boolean: `SHELL` usually means another
virtual desktop, which `move_to_current_desktop()` can fix, while `APP` means the
application put it away and only the application will bring it back. And `None`
is not `False` — "could not ask" and "not cloaked" are different answers, and
collapsing them is how a window you cannot see gets treated as on screen.

## `SetCursorPos` moves the pointer without telling anyone

`SetCursorPos` is not an input event. The pointer relocates, but nothing that
watches the mouse is notified — a `WH_MOUSE_LL` hook, an overlay drawing where
clicks land, an application that updates hover state on `WM_MOUSEMOVE`. They all
carry on believing the cursor is wherever they last saw it.

The click that follows still lands in the right place, which is what makes this
hard to notice: nothing fails. It surfaced only when a keystroke visualiser was
pointed at a run and drew every click marker in the top-left corner, where the
cursor had been when *it* started.

So `send_mouse_click()` injects an absolute move first. Two details:

- **`MOUSEEVENTF_VIRTUALDESK`, not just `MOUSEEVENTF_ABSOLUTE`.** Absolute
  coordinates without it map 0..65535 onto the primary monitor, so a click aimed
  at a second screen lands on the first one.
- **`SetCursorPos` still runs, last.** The move event's coordinates are quantized
  to 1/65535 of the virtual desktop; this way the final position is the exact
  pixel that was asked for.

Verify a move like this through a real hook, not by reading the cursor position
afterwards — `SetCursorPos` on its own satisfies that check, which is precisely
the bug.

## A scan code means whatever the *input state* says it means

Related to the layout pitfall above, and the distinction is worth separating:
switching the **keyboard layout** and switching the **IME mode** are different
actions, and only one of them is what a user does.

With a Bopomofo layout active, `send_physical_keys("hey")` puts nothing in the
field — `h` is a phonetic key there. That is not a fault in the injection; it is
what a real keyboard does. The fix is not to swap the layout to en-US, which no
user would do to type a word; it is to put the IME into alphanumeric mode, the
way pressing Shift does:

```python
with dialog.ime_mode(ImeConversion.ALPHANUMERIC):
    send_physical_keys("hey")        # 'hey' lands; the layout never changes
```

Measured on a zh-TW machine: without the block the field stays empty, with it the
field reads `hey`, and `get_keyboard_layout()` returns `0x04040404` throughout.

And the mode has to be **established, not detected**: `get_ime_status()` reports
`has_context: False` for that window — no IMM32 context answers — while
`WM_IME_CONTROL` still takes effect. So there is nothing to branch on; set it
unconditionally.
