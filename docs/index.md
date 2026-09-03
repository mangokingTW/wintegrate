# wintegrate

**Integrate Windows desktop apps into modern CI.**

`wintegrate` (**Win**dows + **integrate**) is a Python library built to seamlessly integrate Windows GUI testing into unattended, headless CI pipelines — where no human is watching and the only evidence of what happened is whatever the run left behind.

```bash
pip install wintegrate
```

```python
from wintegrate import NOTEPAD, Session, SessionConfig

with Session(SessionConfig()) as session:
    with session.app(NOTEPAD) as app:          # cleanup guaranteed, even on failure
        editor = app.find_text_input()          # no localized control names
        editor.type_verified(                   # refuses to return unless it worked
            "hello from CI\n",
            expected_line_count_delta=1,
            verify_contains="hello from CI",
        )
```

## What makes it different

**Every action verifies itself.** `type_verified` does not return because it sent
keystrokes; it returns because the text is in the buffer. That distinction is the
whole library. On an ARM64 runner under load, synthesized input gets repeated or
dropped — `pywinauto` arriving as `uuuuuuuto` — and a line-count assertion stays
perfectly happy while the content is wrong.

**Nothing depends on the UI language.** Windows are found by process image name
and window class; controls by UIA control type and control id. A test written on
an English desktop runs unchanged on a Chinese one, because no assertion ever
touches a localized string.

**The failure leaves evidence.** A failing run writes a screen recording, a
failure screenshot, a before/after window census, and a structured event
timeline — the things you need when the failure happened hours ago on a machine
you cannot see.

**It expects CI, not a desktop.** Cold Store-app launches that take 30 seconds,
single-instance apps that open a tab instead of a window, foreground stolen by a
first-run popup, COM wrappers that go stale between calls: each of those has a
countermeasure here because each one broke a real run.

## Verified where it claims to work

Every pull request runs this suite against **real GUIs** — Notepad, Calculator,
and a Win32 dialog fixture — on Windows **x64 and ARM64**, across Python
**3.11 – 3.14**. No mocks stand in for the Windows APIs.

Releases are published through PyPI Trusted Publishing with
[PEP 740 attestations](https://peps.python.org/pep-0740/) and GitHub build
provenance:

```bash
gh attestation verify wintegrate-0.1.2-py3-none-any.whl --repo mangokingTW/wintegrate
```

## Where to go next

- [Getting started](getting-started.md) — install, extras, first script
- [What breaks in CI](pitfalls.md) — the failures this library was built against
- [Use cases & real-world guides](use-cases.md) — patterns for Tauri, Qt, .NET, WinUI, and system utilities
- [API reference](api.md) — generated from the source
