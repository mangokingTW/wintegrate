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
