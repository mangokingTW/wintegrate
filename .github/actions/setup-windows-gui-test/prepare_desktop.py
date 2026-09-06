"""Clears a hosted runner's desktop before a GUI test drives it, and says what it did.

Runs after wintegrate is installed (it uses wintegrate.interop). Three things a
hosted Windows runner puts on screen that belong to no test, each handled the
way a person would and re-measured afterwards:

- the OOBE privacy page / "Microsoft account" CoreWindow that holds the foreground
  on a fresh windows-11-arm image;
- the paging-file error's dialogs: "System Properties" first, and "Performance
  Options", which appears only after the first one is closed -- so this loops
  until a pass finds nothing;
- the hosted agent's own console (class ConsoleWindowClass, titled with the
  agent's path), which fills the arm64 desktop. Hidden, never closed: the
  process behind it is the agent reporting this job.

Also importable: `prepare_desktop()` returns the record; `classify(cls, title)`
is the pure rule. A diagnostic never fails the job; the exit code is 0.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

DIALOG_TITLES = ("System Properties", "Performance Options")
SHELL_POPUPS = ("Search", "Start")


def classify(cls: str, title: str) -> str | None:
    """'close' | 'hide' | None for a visible top-level window. Pure."""
    if cls == "#32770" and any(t in (title or "") for t in DIALOG_TITLES):
        return "close"
    if cls == "ConsoleWindowClass":
        return "hide"
    if cls == "Windows.UI.Core.CoreWindow" and (title or "") in SHELL_POPUPS:
        return "hide"
    return None


def _foreground() -> dict:
    from wintegrate.interop import get_foreground_window, get_window_class, get_window_title

    try:
        hwnd = get_foreground_window()
        return {"hwnd": hwnd, "class": get_window_class(hwnd), "title": get_window_title(hwnd)}
    except Exception as exc:  # noqa: BLE001 - a probe never raises
        return {"error": f"{type(exc).__name__}: {exc}"}


def _clear_pass() -> list[dict]:
    """One EnumWindows pass; returns one record per window acted on."""
    import ctypes
    from ctypes import wintypes

    from wintegrate.interop import SW_HIDE, WNDENUMPROC, get_window_class, get_window_title, user32

    WM_CLOSE = 0x0010
    SMTO_ABORTIFHUNG = 0x0002
    try:
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
    except Exception:  # noqa: BLE001
        pass

    actions: list[dict] = []

    def enum_proc(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            cls, title = get_window_class(hwnd), get_window_title(hwnd)
            action = classify(cls, title)
            if action == "close":
                result = ctypes.c_size_t()
                user32.SendMessageTimeoutW(
                    hwnd, WM_CLOSE, 0, 0, SMTO_ABORTIFHUNG, 3000, ctypes.byref(result)
                )
            elif action == "hide":
                user32.ShowWindow(hwnd, SW_HIDE)
            if action:
                actions.append(
                    {"action": action, "hwnd": hwnd, "class": cls, "title": (title or "")[:80]}
                )
        except Exception as exc:  # noqa: BLE001
            actions.append(
                {"action": "error", "hwnd": hwnd, "error": f"{type(exc).__name__}: {exc}"}
            )
        return True

    user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
    if actions:
        time.sleep(0.5)
        for a in actions:
            if a["action"] in ("close", "hide"):
                try:
                    a["visible_after"] = bool(user32.IsWindow(a["hwnd"])) and bool(
                        user32.IsWindowVisible(a["hwnd"])
                    )
                except Exception:  # noqa: BLE001
                    a["visible_after"] = None
    return actions


def clear_runner_desktop(seconds: float = 6.0) -> list[dict]:
    """Passes until one finds nothing or the budget is spent. Performance Options
    only exists after System Properties is closed, so a single pass misses it."""
    deadline = time.monotonic() + seconds
    actions: list[dict] = []
    while True:
        found = _clear_pass()
        actions += found
        if not any(a["action"] in ("close", "hide") for a in found) or time.monotonic() > deadline:
            return actions
        time.sleep(1.0)


def close_start_menu(seconds: float = 6.0) -> list[dict]:
    """Esc while the Start/Search CoreWindow holds the foreground, re-measured.

    SW_HIDE on that window does not take (measured: eight hides, foreground
    unchanged each time); Escape is what a person does.
    """
    from wintegrate.interop import send_vk_input

    VK_ESCAPE = 0x1B
    actions: list[dict] = []
    deadline = time.monotonic() + seconds
    presses = 0
    while time.monotonic() < deadline and presses < 3:
        fg = _foreground()
        if fg.get("class") == "Windows.UI.Core.CoreWindow" and fg.get("title") in SHELL_POPUPS:
            try:
                send_vk_input(VK_ESCAPE)
                presses += 1
                time.sleep(0.7)
                after = _foreground()
                actions.append({"action": "escape", **fg, "foreground_after": after})
                if after.get("hwnd") != fg["hwnd"]:
                    return actions
            except Exception as exc:  # noqa: BLE001
                actions.append({"action": "error", "hwnd": fg.get("hwnd"), "error": str(exc)})
                return actions
        time.sleep(0.25)
    return actions


def prepare_desktop(oobe_timeout: float = 15.0) -> dict:
    record: dict = {"foreground_before": _foreground(), "started": time.time()}
    try:
        from wintegrate.session import try_dismiss_oobe_privacy_screen

        record["oobe_dismissed"] = bool(try_dismiss_oobe_privacy_screen(timeout=oobe_timeout))
    except Exception as exc:  # noqa: BLE001
        record["oobe_dismissed"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["actions"] = clear_runner_desktop()
    # The OOBE dismissal ends by opening Start, a beat after the pass above.
    record["actions"] += close_start_menu()
    record["seconds"] = round(time.time() - record["started"], 2)
    record["foreground_after"] = _foreground()
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", help="write the JSON record here as well")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("prepare_desktop: not Windows, nothing to do")
        return 0
    record = prepare_desktop()
    for a in record["actions"]:
        print(
            f"  {a.get('action')}: {a.get('class')!r} {a.get('title')!r} -> "
            f"visible_after={a.get('visible_after', a.get('foreground_after'))}"
        )
    fg = record["foreground_after"]
    print(
        f"oobe_dismissed={record['oobe_dismissed']} actions={len(record['actions'])} "
        f"foreground now: {fg.get('class')!r} {fg.get('title')!r} ({record['seconds']} s)"
    )
    if fg.get("class") == "Windows.UI.Core.CoreWindow":
        print(f"::warning::a shell CoreWindow still holds the foreground: {fg.get('title')!r}")
    if args.out:
        from pathlib import Path

        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
