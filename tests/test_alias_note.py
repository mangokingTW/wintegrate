"""A launch that produced no window says when the target was a packaged (Store) app.

Store apps update when they close; a launch during the swap shows no window, and
"No new window appeared within 30 s" does not point at that. On Windows 11
notepad.exe resolves to System32 and still hands off to the package (measured),
so the rule is about the installed package, not only the WindowsApps alias path.
"""

from wintegrate.window import _alias_note

ALIAS = r"C:\Users\runneradmin\AppData\Local\Microsoft\WindowsApps\notepad.exe"
SYSTEM32 = r"C:\WINDOWS\system32\notepad.exe"


def test_an_ordinary_executable_with_no_package_adds_nothing():
    assert _alias_note("cmd.exe", r"C:\Windows\System32\cmd.exe", []) == ""
    assert _alias_note("cmd.exe", r"C:\Windows\System32\cmd.exe", None) == ""
    assert _alias_note("cmd.exe", None, None) == ""


def test_system32_notepad_that_hands_off_to_a_package_is_explained():
    note = _alias_note(
        "notepad.exe", SYSTEM32, [("Microsoft.WindowsNotepad", "11.2607.14.0", "Ok")]
    )
    assert "hands off to the package" in note
    assert "applies a pending update the moment the app closes" in note
    assert "Microsoft.WindowsNotepad 11.2607.14.0 [Ok]" in note
    assert "More than one" not in note


def test_two_versions_present_are_called_a_staged_update():
    note = _alias_note(
        "notepad.exe",
        ALIAS,
        [
            ("Microsoft.WindowsNotepad", "11.2410.2.0", "Ok"),
            ("Microsoft.WindowsNotepad", "11.2504.50.0", "Ok"),
        ],
    )
    assert "app execution alias" in note
    assert "More than one version is present" in note


def test_an_alias_whose_package_could_not_be_read_still_says_what_it_is():
    note = _alias_note("notepad.exe", ALIAS, None)
    assert "app execution alias" in note and "could not be read" in note
