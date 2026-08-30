"""Grammar tests for the send_keys spec parser.

parse_key_spec is pure, so the grammar is pinned here without touching Win32.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wintegrate.interop import (
    KEY_NAMES,
    MAX_KEY_REPEAT,
    VK_CONTROL,
    VK_MENU,
    VK_RETURN,
    VK_SHIFT,
    VK_TAB,
    parse_key_spec,
)


def test_literal_text_is_characters():
    assert parse_key_spec("hi") == [("char", "h", ()), ("char", "i", ())]


def test_named_keys():
    assert parse_key_spec("{ENTER}") == [("vk", VK_RETURN, ())]
    assert parse_key_spec("{esc}") == [("vk", 0x1B, ())]
    assert parse_key_spec("{F5}") == [("vk", 0x74, ())]


def test_repeat_count():
    assert parse_key_spec("{TAB 3}") == [("vk", VK_TAB, ())] * 3


def test_modifiers_apply_to_next_key_only():
    assert parse_key_spec("^a") == [("char", "a", (VK_CONTROL,))]
    assert parse_key_spec("^ab") == [("char", "a", (VK_CONTROL,)), ("char", "b", ())]
    assert parse_key_spec("+{TAB}") == [("vk", VK_TAB, (VK_SHIFT,))]
    assert parse_key_spec("%{F4}") == [("vk", 0x73, (VK_MENU,))]


def test_stacked_modifiers():
    assert parse_key_spec("^+{END}") == [("vk", 0x23, (VK_CONTROL, VK_SHIFT))]


def test_escaped_braces_are_literal():
    assert parse_key_spec("{{}}") == [("char", "{", ()), ("char", "}", ())]


def test_mixed_text_and_keys():
    assert parse_key_spec("ok{ENTER}") == [
        ("char", "o", ()),
        ("char", "k", ()),
        ("vk", VK_RETURN, ()),
    ]


@pytest.mark.parametrize("spec", ["{ENTER", "{}", "{NOPE}", "{TAB x}"])
def test_invalid_specs_raise(spec):
    with pytest.raises(ValueError):
        parse_key_spec(spec)


@pytest.mark.parametrize(
    "name,vk",
    [("IME_ON", 0x16), ("IME_OFF", 0x1A), ("KANJI", 0x19), ("HANGUL", 0x15), ("CONVERT", 0x1C)],
)
def test_ime_control_keys_are_addressable(name, vk):
    """IME control keys reach the IME itself rather than the focused control."""
    assert KEY_NAMES[name] == vk
    assert parse_key_spec("{" + name + "}") == [("vk", vk, ())]


# --- Property-based coverage -------------------------------------------------
#
# The three defects fixed in 0.1.2 (unbounded repeat counts, negative counts
# silently expanding to nothing, Python integer-literal syntax leaking into the
# grammar) are exactly what a fuzzer looks for in a parser: crashes, hangs, and
# silent no-ops. Hypothesis gets at the same class of bug from inside the normal
# test run, which is a better fit than a fuzzing harness for a pure-Python
# function of this size.


@given(st.text(max_size=60))
@settings(max_examples=500, deadline=None)
def test_never_raises_anything_other_than_value_error(spec):
    """Malformed input is a caller error, and must arrive as one."""
    try:
        parse_key_spec(spec)
    except ValueError:
        pass


@given(st.text(max_size=60))
@settings(max_examples=500, deadline=None)
def test_output_cannot_be_amplified_beyond_the_repeat_cap(spec):
    """No input expands without bound: that is the OOM the repeat cap closes."""
    try:
        actions = parse_key_spec(spec)
    except ValueError:
        return
    assert len(actions) <= len(spec) * MAX_KEY_REPEAT


@given(st.text(alphabet=st.characters(blacklist_characters="{}^+%"), max_size=40))
@settings(max_examples=300, deadline=None)
def test_plain_text_round_trips_character_for_character(text):
    """Text with no syntax in it is typed exactly as given, in order."""
    actions = parse_key_spec(text)
    assert actions == [("char", ch, ()) for ch in text]


@given(st.sampled_from(sorted(KEY_NAMES)), st.integers(min_value=1, max_value=MAX_KEY_REPEAT))
@settings(max_examples=200, deadline=None)
def test_any_named_key_repeats_exactly_as_requested(name, count):
    actions = parse_key_spec("{" + f"{name} {count}" + "}")
    assert actions == [("vk", KEY_NAMES[name], ())] * count
