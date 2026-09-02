try:
    import pytest
except ImportError:
    pytest = None

import time

from wintegrate.interop import VK_PACKET
from wintegrate.keyboard_overlay import (
    DEFAULT_KEY_LINGER_SECONDS,
    KeyStrokeEvent,
    KeyTracker,
    draw_keyboard_hud,
)

try:
    from PIL import Image
except ImportError:
    Image = None


def _blank(width=800, height=600, colour=(255, 255, 255)):
    return Image.new("RGB", (width, height), colour)


# --- Key decoding & VK_PACKET tests ------------------------------------------


def test_decode_vk_packet_unicode_characters():
    tracker = KeyTracker()

    # VK_PACKET sends Unicode char in scanCode (e.g. ord('q') = 113)
    # Keyviz bug: mistook scanCode 113 for VK_F2 (0x71).
    # wintegrate: correctly decodes to 'q'.
    key, is_mod = tracker._decode_key(VK_PACKET, ord("q"), 0)
    assert key == "q"
    assert not is_mod

    key, is_mod = tracker._decode_key(VK_PACKET, ord("a"), 0)
    assert key == "a"
    assert not is_mod

    key, is_mod = tracker._decode_key(VK_PACKET, ord("1"), 0)
    assert key == "1"
    assert not is_mod

    # Non-ASCII Unicode character
    key, is_mod = tracker._decode_key(VK_PACKET, ord("好"), 0)
    assert key == "好"
    assert not is_mod


def test_decode_standard_keys_and_modifiers():
    tracker = KeyTracker()

    # Modifiers
    key, is_mod = tracker._decode_key(0x11, 0x1D, 0)  # VK_CONTROL
    assert key == "Ctrl"
    assert is_mod

    key, is_mod = tracker._decode_key(0x12, 0x38, 0)  # VK_MENU (Alt)
    assert key == "Alt"
    assert is_mod

    key, is_mod = tracker._decode_key(0x10, 0x2A, 0)  # VK_SHIFT
    assert key == "Shift"
    assert is_mod

    key, is_mod = tracker._decode_key(0x5B, 0x5B, 0)  # VK_LWIN
    assert key == "Win"
    assert is_mod

    # Special / Function keys
    key, is_mod = tracker._decode_key(0x0D, 0x1C, 0)  # VK_RETURN
    assert key == "Enter"
    assert not is_mod

    key, is_mod = tracker._decode_key(0x1B, 0x01, 0)  # VK_ESCAPE
    assert key == "Esc"
    assert not is_mod

    key, is_mod = tracker._decode_key(0x20, 0x39, 0)  # VK_SPACE
    assert key == "Space"
    assert not is_mod

    key, is_mod = tracker._decode_key(0x70, 0x3B, 0)  # VK_F1
    assert key == "F1"
    assert not is_mod

    key, is_mod = tracker._decode_key(0x43, 0x2E, 0)  # 'C'
    assert key == "C"
    assert not is_mod


# --- KeyStrokeEvent formatting -----------------------------------------------


def test_keystroke_event_display_text():
    # Single key
    ev1 = KeyStrokeEvent(at=time.monotonic(), label="Enter", modifiers=(), is_chord=False)
    assert ev1.display_text == "Enter"

    # Single chord: Ctrl + C
    ev2 = KeyStrokeEvent(at=time.monotonic(), label="C", modifiers=("Ctrl",), is_chord=True)
    assert ev2.display_text == "Ctrl + C"

    # Multi-modifier chord: Win + Alt + Space
    ev3 = KeyStrokeEvent(
        at=time.monotonic(), label="Space", modifiers=("Win", "Alt"), is_chord=True
    )
    assert ev3.display_text == "Win + Alt + Space"

    # Pure modifier tap
    ev4 = KeyStrokeEvent(at=time.monotonic(), label="Shift", modifiers=(), is_chord=False)
    assert ev4.display_text == "Shift"


# --- HUD Rendering on Image --------------------------------------------------


def test_draw_keyboard_hud_renders_capsule():
    img = _blank(800, 600, colour=(255, 255, 255))
    now = time.monotonic()
    events = [
        KeyStrokeEvent(at=now - 0.2, label="Ctrl", modifiers=(), is_chord=False),
        KeyStrokeEvent(at=now - 0.1, label="C", modifiers=("Ctrl",), is_chord=True),
        KeyStrokeEvent(at=now, label="Enter", modifiers=(), is_chord=False),
    ]

    # Before drawing: bottom area is purely white
    bottom_pixels_before = [
        img.getpixel((x, 560)) for x in range(300, 500)
    ]
    assert all(p == (255, 255, 255) for p in bottom_pixels_before)

    draw_keyboard_hud(img, events, now=now)

    # After drawing: dark pixels of the HUD capsule should be present
    bottom_pixels_after = [
        img.getpixel((x, 560)) for x in range(300, 500)
    ]
    dark_pixels = [p for p in bottom_pixels_after if p != (255, 255, 255)]
    assert len(dark_pixels) > 50, "HUD keycaps were not rendered on the image"


def test_draw_keyboard_hud_stale_events_draw_nothing():
    img = _blank(800, 600, colour=(255, 255, 255))
    now = time.monotonic()
    stale_events = [
        KeyStrokeEvent(
            at=now - DEFAULT_KEY_LINGER_SECONDS - 1.0,
            label="Enter",
            modifiers=(),
            is_chord=False,
        )
    ]

    draw_keyboard_hud(img, stale_events, now=now)

    # Everything must remain pure white
    all_pixels = list(img.getdata())
    assert all(p == (255, 255, 255) for p in all_pixels)


def test_draw_keyboard_hud_empty_events_noop():
    img = _blank(400, 300, colour=(128, 128, 128))
    res = draw_keyboard_hud(img, [])
    assert res is img


if __name__ == "__main__":
    test_decode_vk_packet_unicode_characters()
    print("✓ test_decode_vk_packet_unicode_characters passed")
    test_decode_standard_keys_and_modifiers()
    print("✓ test_decode_standard_keys_and_modifiers passed")
    test_keystroke_event_display_text()
    print("✓ test_keystroke_event_display_text passed")
    if Image is not None:
        test_draw_keyboard_hud_renders_capsule()
        print("✓ test_draw_keyboard_hud_renders_capsule passed")
        test_draw_keyboard_hud_stale_events_draw_nothing()
        print("✓ test_draw_keyboard_hud_stale_events_draw_nothing passed")
        test_draw_keyboard_hud_empty_events_noop()
        print("✓ test_draw_keyboard_hud_empty_events_noop passed")
    print("\nAll keyboard overlay tests passed!")
