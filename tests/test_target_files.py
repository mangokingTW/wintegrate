"""Files (WinUI 3 / Windows App SDK), where every check passes and nothing works.

Three traps live in this one application, and all three return success:

- With its .NET runtime missing, Files shows a `#32770` message box owned by
  `Files.exe`. `process_names` matches it, so discovery hands back a dialog.
  `require_all=True` is the answer.
- A freshly launched window can be foreground with UIA focus still on the
  top-level HWND, in which case XAML accelerators are dropped while
  `GetForegroundWindow()` and `set_foreground()` both report success.
  `focus_content_island()` is the answer.
- `automation_id` is stable English for the chrome (`Back`, `Refresh`,
  `TabBarAddNewTabButton`) and the *localized label* for sidebar items
  (`aid='桌面'`). So "use automation_id and be locale-independent" is a rule to
  verify per container, not per application.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from target_apps import (
    assert_version,
    find_packaged_app,
    installed_package_version,
    launch_packaged_app,
)
from waits import settled

from wintegrate import Window, interop
from wintegrate.apps import sweep_processes_verified

pytestmark = [
    pytest.mark.target_app,
    pytest.mark.skipif(sys.platform != "win32", reason="drives a live WinUI 3 application"),
]

PACKAGE = "Files"
PROCESS = "Files.exe"
WINDOW_CLASS = "WinUIDesktopWin32WindowClass"
CONTROL_TYPE_TAB_ITEM = 50019

# The build every assertion below was measured against. Pinned by SHA-256 in the
# CI install step, so this check and that one have to be bumped together.
VERIFIED_VERSION = "4.2.9.0"

# Files restores the previous session by default, and after an update it opens a
# release-notes page in an embedded WebView2 — which is what made find_text_input
# return the blog post instead of the path box. This is the same problem as Store
# Notepad's TabState directory, expressed as settings keys instead of a folder.
#
# ShowRunningAsAdminPrompt defaults to true and matters more than it looks: a CI
# runner is elevated, so Files opens a modal ContentDialog ("Files is running as
# administrator") over its own window. Every element query still succeeds — the
# tab strip is right there behind the dialog — while Ctrl+T and even a synthesised
# click on the new-tab button do nothing, because a modal is swallowing input.
# Found from the CI recording after four tests failed with no visible reason.
DETERMINISTIC_STARTUP = {
    "ContinueLastSessionOnStartUp": False,
    "RestoreTabsOnStartup": False,
    "OpenSpecificPageOnStartup": False,
    "ShowRunningAsAdminPrompt": False,
    "ShowDataStreamsAreHiddenPrompt": False,
}


def _settings_path(package_family_name: str) -> Path:
    return (
        Path(os.environ["LOCALAPPDATA"])
        / "Packages"
        / package_family_name
        / "LocalState"
        / "settings"
        / "user_settings.json"
    )


@pytest.fixture
def files_app():
    """Files, started from a known-good startup configuration.

    The settings file is patched rather than deleted: it holds 112 keys and only
    three of them are about startup. Restored afterwards so a developer's own
    Files is left as it was.
    """
    aumid = find_packaged_app(PACKAGE)
    settings_file = _settings_path(aumid.split("!")[0])

    # Written even when the file does not exist yet. A first run on a fresh
    # machine has no settings file, and an earlier version of this fixture
    # skipped the patch in that case — which is exactly the CI machine, so none
    # of the determinism below was applied there and the admin-prompt modal
    # appeared. Every key Files does not find takes its own default, so a partial
    # file is safe.
    original = settings_file.read_text(encoding="utf-8") if settings_file.exists() else None
    settings = json.loads(original) if original else {}
    settings.update(DETERMINISTIC_STARTUP)
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")

    sweep_processes_verified((PROCESS,), ("Files",))
    proc, win = Window.launch_and_discover(
        launch_packaged_app(aumid),
        timeout=180.0,
        process_names=(PROCESS,),
        window_classes=(WINDOW_CLASS,),
        require_all=True,
    )
    try:
        with win.foreground(verify=False):
            # The window is discoverable before the XAML tree is populated, and a
            # cold start takes ~19s against ~0.7s warm. Waiting for the tab strip
            # is a positive signal; sleeping a fixed interval is a guess that was
            # wrong often enough to fail a run.
            assert settled(lambda: _tab_count(win), lambda n: n >= 1, timeout=90.0) >= 1, (
                "the tab strip never appeared — the XAML tree did not finish loading"
            )
            assert win.focus_content_island(timeout=15.0), (
                "focus never reached the XAML content island, so no accelerator "
                "sent by the tests below would have been delivered"
            )
            yield win
    finally:
        proc.terminate()
        sweep_processes_verified((PROCESS,), ("Files",))
        if original is not None:
            settings_file.write_text(original, encoding="utf-8")
        else:
            settings_file.unlink(missing_ok=True)


def _maybe(root, **criteria):
    """`find_descendant`, but absence is an answer rather than an exception."""
    try:
        return root.find_descendant(timeout=2.0, **criteria)
    except Exception:
        return None


def _tab_count(win: Window) -> int:
    """How many tabs the strip currently holds, or -1 while it cannot be read.

    Opening and closing tabs rebuilds part of the XAML tree, so a lookup made
    mid-transition raises. Returning a sentinel keeps `settled` polling instead of
    aborting, and -1 still shows up in the assertion message if the strip never
    comes back — so a genuine absence is reported rather than swallowed.
    """
    try:
        tab_list = win.re_resolve_element().find_descendant(
            automation_id="TabListView", timeout=1.0
        )
        return len(tab_list.find_all(control_type_id=CONTROL_TYPE_TAB_ITEM))
    except Exception:
        return -1


def _type_path_verified(win: Window, target: str) -> None:
    """Types a path into the path box and checks it arrived before pressing Enter.

    Character-by-character injection can drop one, and then Enter navigates
    somewhere else entirely and the failure surfaces as a wrong location several
    assertions later. Checking the buffer first localises it: the message says the
    typing failed and shows what the box actually holds.
    """
    box = win.re_resolve_element().find_descendant(automation_id="PART_TextBox", timeout=10.0)
    box.set_focus()

    def buffer_now() -> str:
        current = _maybe(win.re_resolve_element(), automation_id="PART_TextBox")
        return current.get_value() if current else ""

    # Recorded, not asserted. The reading only reflects the edit buffer once the
    # breadcrumb is in edit mode; at Home it answers with the display value
    # ('Home') whatever the buffer holds, so "the box is empty" is not reliably
    # observable.
    before_typing = buffer_now()

    def clear_and_type() -> str:
        interop.send_keys("^a")
        interop.send_keys("{DELETE}")
        for character in target:
            interop.send_char_input(character)
        return settled(buffer_now, lambda v: v == target, timeout=10.0)

    typed = clear_and_type()

    # Files' address bar is a breadcrumb until it enters edit mode, and until then
    # Ctrl+A has nothing to select: the clear silently does nothing and the path
    # is appended, giving 'HomeC:\Windows'. The transition is not directly
    # observable — the reading answers with the display value either way — so
    # instead of asserting a precondition that cannot be read, the *signature* of
    # having missed it is recognised and one more pass is made. The box is
    # certainly in edit mode by now, so the second pass is decisive rather than
    # hopeful, and it is reported rather than silent: a retry nobody can see is
    # how a flake becomes permanent.
    if typed != target and typed.endswith(target):
        print(
            f"path box was still showing the breadcrumb when Ctrl+A arrived "
            f"(read {typed!r}); clearing again now that it is in edit mode"
        )
        typed = clear_and_type()

    assert typed == target, (
        f"typing {target!r} into the path box left it reading {typed!r} "
        f"(it read {before_typing!r} before the first clear). Either a keystroke "
        "was dropped or the clear did not take even on the second pass, and Enter "
        "would then navigate somewhere unintended"
    )
    interop.send_keys("{ENTER}")


def test_discovery_rejects_the_apps_own_dialog(files_app):
    """`require_all=True` in the fixture is what this asserts.

    A cold start takes ~19s and the .NET error dialog is found in ~1s, so a run
    that accepted the dialog would look faster, not slower.
    """
    assert files_app.class_name == WINDOW_CLASS


def test_startup_is_deterministic(files_app):
    """One tab, and no WebView2 in the tree.

    `RootWebArea` is the Chromium accessibility root. Its presence means the
    release-notes pane is showing, and that pane changes what half of the UIA
    tree contains.
    """
    root = files_app.re_resolve_element()
    assert not root.find_all(automation_id="RootWebArea"), (
        "an embedded WebView2 is in the tree — the release-notes pane was not suppressed"
    )
    assert _tab_count(files_app) == 1


def test_find_text_input_is_the_path_box_not_a_browser(files_app):
    """Regression guard for the ladder's Document rung.

    The rung matches a WebView2's document root, which outranks the app's own Edit
    controls. Whatever this returns, it must not be a browser.
    """
    element = files_app.find_text_input(timeout=30.0)
    assert element.automation_id != "RootWebArea"
    assert element.automation_id, "the text input has no automation_id to check"


def test_new_tab_by_accelerator(files_app):
    """`Ctrl+T` — the case that does nothing without `focus_content_island()`."""
    before = _tab_count(files_app)
    interop.send_keys("^t")
    after = settled(lambda: _tab_count(files_app), lambda n: n == before + 1, timeout=15.0)
    assert after == before + 1, f"Ctrl+T did not open a tab ({before} -> {after})"


def test_new_tab_by_button_and_close_by_accelerator(files_app):
    """Clicking is the route that works regardless of focus; closing is not."""
    before = _tab_count(files_app)
    button = files_app.re_resolve_element().find_descendant(
        automation_id="TabBarAddNewTabButton", timeout=10.0
    )
    button.click()
    opened = settled(lambda: _tab_count(files_app), lambda n: n == before + 1, timeout=15.0)
    assert opened == before + 1, f"the new-tab button did not open a tab ({before} -> {opened})"

    interop.send_keys("^w")
    closed = settled(lambda: _tab_count(files_app), lambda n: n == before, timeout=15.0)
    assert closed == before, f"Ctrl+W did not close the tab ({opened} -> {closed})"


def test_navigation_updates_the_invariant_path(files_app):
    """Typing a path updates both fields, and the two are not interchangeable.

    At Home they disagree: `PART_TextBox` reads the invariant `Home` while
    `CurrentPathGet` reads the translated display name (`首頁` on a zh-TW
    machine). So `PART_TextBox` is the one to assert on for a *special* location.

    The reverse holds for navigation: `PART_TextBox` is the path editor's buffer
    and only tracks what was typed into it, so a button that navigates leaves it
    stale. `test_navigation_buttons_move_and_come_back` asserts on
    `CurrentPathGet` for exactly that reason.
    """
    root = files_app.re_resolve_element()
    path_box = root.find_descendant(automation_id="PART_TextBox", timeout=10.0)
    assert path_box.get_value() == "Home", (
        "PART_TextBox should hold the invariant location name at startup"
    )

    target = os.environ.get("SystemRoot", r"C:\Windows")
    _type_path_verified(files_app, target)

    def read_path() -> str:
        box = _maybe(files_app.re_resolve_element(), automation_id="PART_TextBox")
        return box.get_value() if box else ""

    final = settled(read_path, lambda value: value == target, timeout=20.0)
    assert final == target, f"navigation did not land on {target!r}, PART_TextBox reads {final!r}"


def test_sidebar_automation_ids_are_localized(files_app):
    """Characterisation: the exception to "automation_id is language-independent".

    Sidebar items use their display label as their id, so a suite that assumed the
    rule held app-wide would break on a translated machine. `SettingsButton` is
    the one stable id there, and it is the anchor this asserts on.

    The toggle button is optional: below a certain window width Files collapses
    the sidebar into a flyout and shows `SidebarPaneToggleButton`, and above it
    the sidebar is always open and the button does not exist. Requiring it failed
    on a 1024x768 runner while passing at 800x600.
    """
    toggle = _maybe(files_app.re_resolve_element(), automation_id="SidebarPaneToggleButton")
    if toggle is not None:
        toggle.click()

    def settings_item():
        items = files_app.re_resolve_element().find_all(control_type_id=50007)
        return [i for i in items if i.automation_id == "SettingsButton"]

    found = settled(settings_item, lambda items: bool(items), timeout=15.0)
    assert found, "the sidebar's SettingsButton item never appeared"
    # Every other sidebar item's id equals its (translated) name. Recorded rather
    # than relied on: if this stops holding, the rule got simpler, not harder.
    localized = [
        i.automation_id
        for i in files_app.re_resolve_element().find_all(control_type_id=50007)
        if i.automation_id and i.automation_id == i.name
    ]
    assert localized, (
        "no sidebar item uses its display name as its automation_id any more — "
        "check whether the locale caveat in this module still applies"
    )


def test_navigation_buttons_move_and_come_back(files_app):
    """`Up` and `Back` clicked, verified against the invariant path field.

    The one target where a toolbar button is both addressable by a stable
    English id and observable through a language-independent value. WinMerge's
    toolbar has to be matched on translated names and read through the status
    bar; sqlitebrowser's tool buttons share one id. Files has neither problem.
    """
    start = os.environ.get("SystemRoot", r"C:\Windows")
    _type_path_verified(files_app, start)

    # CurrentPathGet, not PART_TextBox. PART_TextBox is the path *editor's*
    # buffer: typing updates it, and button navigation does not. Measured — after
    # clicking Up from C:\Windows, CurrentPathGet read 'C:\' while PART_TextBox
    # still read 'C:\Windows' eight seconds later. CurrentPathGet is the current
    # location, and for an ordinary folder it is the real path rather than a
    # translated display name.
    # Compared case-insensitively: %SystemRoot% is 'C:\WINDOWS' while Files
    # reports the directory's real on-disk casing, 'C:\Windows'. Both name the
    # same folder, and Windows paths are case-insensitive.
    def same_path(a: str, b: str) -> bool:
        return os.path.normcase(a) == os.path.normcase(b)

    def read_location() -> str:
        box = _maybe(files_app.re_resolve_element(), automation_id="CurrentPathGet")
        return box.get_value() if box else ""

    landed = settled(read_location, lambda v: same_path(v, start), timeout=20.0)
    assert same_path(landed, start), f"could not get to {start!r} to begin with, at {landed!r}"

    parent = str(Path(start).parent)
    up = files_app.re_resolve_element().find_descendant(automation_id="Up", timeout=10.0)
    up.click()
    after_up = settled(read_location, lambda v: same_path(v, parent), timeout=20.0)
    assert same_path(after_up, parent), (
        f"Up should have gone to {parent!r}, location reads {after_up!r}"
    )

    back = files_app.re_resolve_element().find_descendant(automation_id="Back", timeout=10.0)
    back.click()
    after_back = settled(read_location, lambda v: same_path(v, start), timeout=20.0)
    assert same_path(after_back, start), (
        f"Back should have returned to {start!r}, location reads {after_back!r}"
    )


def test_files_is_the_verified_version():
    """Pins the build.

    The mirrored package in CI is pinned by hash, so a mismatch here means a
    locally-installed Files is a different build from the one under test.
    """
    assert_version("Files", installed_package_version(PACKAGE), VERIFIED_VERSION)
