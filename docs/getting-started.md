# Getting started

## Install

```bash
pip install wintegrate          # core
uv add wintegrate
```

The core install depends on `comtypes` alone. The heavier pieces are optional:

```bash
pip install 'wintegrate[video]'    # screen recording + failure screenshots
pip install 'wintegrate[desktop]'  # virtual desktop clean-room isolation
pip install 'wintegrate[all]'
```

Using a feature without its extra raises an error naming the extra to install, so
a missing dependency never turns into a silently skipped diagnostic.

!!! note "Windows only, but importable everywhere"
    The package imports on macOS and Linux — so cross-platform tooling, linting
    and `env` checks work — while every Win32 and UIA call raises a clear
    unsupported-platform error rather than failing obscurely.

## A first script

```python
from pathlib import Path
from wintegrate import NOTEPAD, Session, SessionConfig

config = SessionConfig(artifact_dir=Path("ci-artifacts"), record_video=True)

with Session(config) as session:
    with session.app(NOTEPAD) as app:
        editor = app.find_text_input()
        editor.type_verified(
            "hello from CI\n",
            expected_line_count_delta=1,
            verify_contains="hello from CI",
        )
        assert "hello from CI" in editor.get_value()
```

On exit the session writes `window_census.json`, `session_events.json`, the
recording, and — if the block raised — `failure_screenshot.png`.

Runnable versions live in
[`examples/`](https://github.com/mangokingTW/wintegrate/tree/main/examples).

## Finding things

Prefer identities that do not change with the UI language.

```python
from wintegrate import Window

# Criteria are ANDed: this is the Settings dialog, not the first #32770 around.
dialog = Window.find(title_pattern="Settings", class_name="#32770")

# pid= is the reliable way to reach a window whose title is localized,
# empty, or shared with another application.
win = Window.find(class_name="Notepad", pid=proc.pid)

root = dialog.re_resolve_element()      # always re-resolve; COM wrappers go stale
edit = root.find_descendant(automation_id="1002")
maybe = root.find_descendant(automation_id="9999", required=False)   # None, no raise
```

## Typing, and why there are two ways

```python
editor.type_verified("abc", verify_contains="abc")   # Unicode injection
editor.send_physical_keys("abc")                     # scan codes
editor.send_keys("{HOME}+{END}{DELETE}")             # named keys and modifiers
```

`type_verified` injects Unicode codepoints. They reach the control directly and
**never pass through an IME**, so composition never starts. That is the right
call when you just need characters in a field.

`send_physical_keys` delivers scan codes, which is the only input an IME
intercepts. Reach for it when the IME itself is what you are testing.

!!! warning "Collapse the selection before a destructive key"
    Focusing a classic Win32 `EDIT` through UIA selects its entire contents;
    arriving by click places the caret instead. `send_keys` focuses first and does
    both, so whether a bare `{BACKSPACE}` clears the field or deletes one
    character is timing-dependent. Send `{HOME}` or `{END}` first and the question
    stops mattering.

## Screenshots

```python
session.capture_screenshot("after-login")            # whole virtual desktop
session.capture_screenshot("dialog", window=dialog)  # that window only
window.capture("window.png")
element.capture("control.png")
```

A window capture uses `PrintWindow`, so it includes the parts of the window that
other windows are covering. On CI the thing on top is frequently the popup that
broke the run, and a cropped screenshot would show the intruder instead of the
window you were driving.

A failure screenshot is still written automatically when the `Session` block
raises — but only then, which is why an explicit call is worth having: by the time
something fails, the state that explains it is often gone.

## Dialog controls

```python
check = root.find_descendant(automation_id="1011")
check.set_toggle_verified(True)          # Toggle() only cycles; this drives to a state

combo = root.find_descendant(automation_id="1003")
combo.expand_verified(True)
combo.find_all(control_type_id=50007)[1].select_verified()
combo.get_value()                        # a Win32 combo reports selection as its value

items = root.find_descendant(automation_id="1001").children()
items[1].select_verified()
```
