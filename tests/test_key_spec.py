"""Grammar tests for the send_keys spec parser.

parse_key_spec is pure, so the grammar is pinned here without touching Win32.
"""

from __future__ import annotations

import pytest

from wintegrate.interop import (
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
