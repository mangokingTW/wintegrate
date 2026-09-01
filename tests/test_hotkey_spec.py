"""Grammar tests for the hotkey spec parser.

`parse_hotkey` is pure, so the grammar is pinned here without touching Win32.

The reason this grammar exists at all rather than a Win-key prefix being added to
`parse_key_spec`: that grammar sends everything it does not recognise as literal
text, so claiming a character for the Win key would silently change what
`send_keys` types. `test_send_keys_grammar_still_types_the_hotkey_characters`
below is the regression guard for that.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wintegrate.interop import (
    _EXTENDED_VKS,
    _HOTKEY_MODIFIERS,
    KEY_NAMES,
    VK_CONTROL,
    VK_LWIN,
    VK_MENU,
    VK_RWIN,
    VK_SHIFT,
    parse_hotkey,
    parse_key_spec,
)


def test_the_chord_that_prompted_this():
    """Win+Alt+Space, Command Palette's default hotkey."""
    assert parse_hotkey("win+alt+space") == ((VK_LWIN, VK_MENU), 0x20)


def test_modifier_order_is_preserved():
    """Modifiers are pressed in the order written and released in reverse."""
    assert parse_hotkey("ctrl+shift+p") == ((VK_CONTROL, VK_SHIFT), ord("P"))
    assert parse_hotkey("shift+ctrl+p") == ((VK_SHIFT, VK_CONTROL), ord("P"))


def test_names_are_case_insensitive():
    assert parse_hotkey("WIN+ALT+SPACE") == parse_hotkey("win+alt+space")
    assert parse_hotkey("Ctrl+Shift+P") == parse_hotkey("ctrl+shift+p")


def test_surrounding_whitespace_is_ignored():
    assert parse_hotkey("  ctrl + a  ") == ((VK_CONTROL,), ord("A"))


def test_letters_and_digits_resolve_without_asking_the_system():
    """A-Z and 0-9 have virtual keys equal to their uppercase codepoint."""
    assert parse_hotkey("a") == ((), ord("A"))
    assert parse_hotkey("z") == ((), ord("Z"))
    assert parse_hotkey("win+9") == ((VK_LWIN,), ord("9"))


def test_layout_dependent_keys_come_back_as_characters():
    """`,` has no layout-independent virtual key, so send_hotkey maps it later."""
    assert parse_hotkey("ctrl+,") == ((VK_CONTROL,), ",")
    assert parse_hotkey("ctrl+/") == ((VK_CONTROL,), "/")


def test_a_lone_modifier_is_a_key():
    """The last token is the key, so "win" presses Win — it opens Start."""
    assert parse_hotkey("win") == ((), VK_LWIN)
    assert parse_hotkey("rwin") == ((), VK_RWIN)
    # And a modifier reached as the last token is the key, not a missing chord.
    assert parse_hotkey("ctrl+shift") == ((VK_CONTROL,), VK_SHIFT)


def test_named_keys_come_from_the_shared_table():
    assert parse_hotkey("f5") == ((), KEY_NAMES["F5"])
    assert parse_hotkey("alt+f4") == ((VK_MENU,), KEY_NAMES["F4"])
    assert parse_hotkey("ctrl+enter") == ((VK_CONTROL,), KEY_NAMES["ENTER"])


def test_plus_is_spellable_as_a_key():
    """Both spellings mean Ctrl-plus; requiring the separator would make "+" unspellable."""
    assert parse_hotkey("ctrl++") == ((VK_CONTROL,), "+")
    assert parse_hotkey("ctrl+") == ((VK_CONTROL,), "+")
    assert parse_hotkey("+") == ((), "+")
    assert parse_hotkey("ctrl+shift++") == ((VK_CONTROL, VK_SHIFT), "+")


def test_repeated_modifiers_are_collapsed():
    assert parse_hotkey("ctrl+ctrl+a") == ((VK_CONTROL,), ord("A"))
    assert parse_hotkey("win+super+r") == ((VK_LWIN,), ord("R"))


def test_win_aliases_agree():
    for alias in ("win", "lwin", "super", "meta"):
        assert parse_hotkey(f"{alias}+space") == ((VK_LWIN,), 0x20)


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "   ",
        "+++",
    ],
)
def test_specs_with_nothing_to_press_are_rejected(spec):
    with pytest.raises(ValueError):
        parse_hotkey(spec)


def test_a_non_modifier_before_the_key_is_rejected():
    """ "space+ctrl" is a mistake, and guessing at it would send the wrong chord."""
    with pytest.raises(ValueError, match="not a modifier"):
        parse_hotkey("space+ctrl")


def test_an_unknown_key_name_is_rejected():
    with pytest.raises(ValueError, match="Unknown key"):
        parse_hotkey("ctrl+nosuchkey")


def test_the_error_lists_what_would_have_worked():
    """A rejection that does not say what is valid just moves the guessing."""
    with pytest.raises(ValueError) as exc:
        parse_hotkey("hyper+a")
    assert "ctrl" in str(exc.value).lower()
    with pytest.raises(ValueError) as exc:
        parse_hotkey("ctrl+nosuchkey")
    assert "SPACE" in str(exc.value)


def test_both_win_keys_are_extended():
    """Without KEYEVENTF_EXTENDEDKEY the shell does not see a Win chord at all."""
    assert VK_LWIN in _EXTENDED_VKS
    assert VK_RWIN in _EXTENDED_VKS


def test_send_keys_grammar_still_types_the_hotkey_characters():
    """The regression guard for not putting a Win prefix into parse_key_spec.

    `#` is AutoHotkey's Win prefix and the obvious thing to reach for. Had it been
    added here, this text would have lost its `#` — and `+` is already Shift, so
    the hotkey separator cannot live in this grammar either.
    """
    assert parse_key_spec("issue #123") == [("char", ch, ()) for ch in "issue #123"]
    assert ("char", "#", ()) in parse_key_spec("#")


@given(
    st.lists(st.sampled_from(sorted(_HOTKEY_MODIFIERS)), min_size=1, max_size=4),
    st.sampled_from(sorted(KEY_NAMES)),
)
@settings(max_examples=300, deadline=None)
def test_any_modifiers_plus_any_named_key_parse(mods, key):
    modifiers, parsed_key = parse_hotkey("+".join([*mods, key]))
    assert parsed_key == KEY_NAMES[key]
    # Deduplicated, but every distinct modifier asked for is present.
    assert set(modifiers) == {_HOTKEY_MODIFIERS[m] for m in mods}
    assert len(modifiers) == len(set(modifiers))


@given(st.text(min_size=1, max_size=12))
@settings(max_examples=400, deadline=None)
def test_never_raises_anything_but_value_error(spec):
    """A malformed spec is a ValueError, not a TypeError or an IndexError."""
    try:
        parse_hotkey(spec)
    except ValueError:
        pass
