"""Keyboard visualizer HUD drawn into captured frames.

Provides an in-process, zero-dependency alternative to external on-screen overlays
(like Keyviz). Keystrokes and shortcut chords are intercepted via a low-level
Windows keyboard hook (`WH_KEYBOARD_LL`) and composited directly onto captured frames
in memory.

Properties & Advantages over external on-screen visualizers:
1. Zero Desktop Intrusion: No real OS GUI window created, eliminating focus
   stealing, z-order fighting (WS_EX_TOPMOST), and transparency/click-through bugs.
2. Correct VK_PACKET (0xE7) Decoding: Native decoding of Unicode characters sent
   via SendInput(KEYEVENTF_UNICODE), avoiding Keyviz's bug where scanCode is mistaken
   for a Virtual Key code (e.g. 'q' becoming 'F2').
3. CI-Optimized Rendering: Designed with high contrast and configurable linger
   duration, ensuring every keystroke is crisp and legible even at 10-30 fps.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections import deque
from ctypes import wintypes
from typing import NamedTuple

from wintegrate.exceptions import DiagnosticPipelineError
from wintegrate.interop import (
    KBDLLHOOKSTRUCT,
    LLKHF_ALTDOWN,
    LLKHF_UP,
    VK_PACKET,
    WH_KEYBOARD_LL,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_QUIT,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    kernel32,
    user32,
)

logger = logging.getLogger(__name__)

#: How long a key visualizer token remains on screen.
#: 2.5s corresponds to 25 frames at 10 fps, ensuring readability on CI recordings.
DEFAULT_KEY_LINGER_SECONDS = 2.5

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_HOOKPROC = _WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT)
)

_PILLOW_HINT = (
    "Keyboard overlay needs Pillow, which ships in the optional 'video' extra: "
    "pip install 'wintegrate[video]'"
)

# Virtual Key code to human-readable string mapping
_VK_MAP: dict[int, str] = {
    0x08: "Backspace",
    0x09: "Tab",
    0x0D: "Enter",
    0x13: "Pause",
    0x14: "CapsLock",
    0x1B: "Esc",
    0x20: "Space",
    0x21: "PageUp",
    0x22: "PageDown",
    0x23: "End",
    0x24: "Home",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2C: "PrtScn",
    0x2D: "Insert",
    0x2E: "Delete",
    0x5B: "Win",
    0x5C: "Win",
    0x5D: "Apps",
    0x70: "F1",
    0x71: "F2",
    0x72: "F3",
    0x73: "F4",
    0x74: "F5",
    0x75: "F6",
    0x76: "F7",
    0x77: "F8",
    0x78: "F9",
    0x79: "F10",
    0x7A: "F11",
    0x7B: "F12",
    0x90: "NumLock",
    0x91: "ScrollLock",
    # OEM keys (US Standard)
    0xBA: ";",
    0xBB: "=",
    0xBC: ",",
    0xBD: "-",
    0xBE: ".",
    0xBF: "/",
    0xC0: "`",
    0xDB: "[",
    0xDC: "\\",
    0xDD: "]",
    0xDE: "'",
}

# Add standard alphanumeric keys
for _c in range(ord("0"), ord("9") + 1):
    _VK_MAP[_c] = chr(_c)
for _c in range(ord("A"), ord("Z") + 1):
    _VK_MAP[_c] = chr(_c)
for _i in range(10):
    _VK_MAP[0x60 + _i] = f"Num {_i}"

_MODIFIER_VKS = {
    0x10: "Shift",
    0xA0: "Shift",
    0xA1: "Shift",
    0x11: "Ctrl",
    0xA2: "Ctrl",
    0xA3: "Ctrl",
    0x12: "Alt",
    0xA4: "Alt",
    0xA5: "Alt",
    0x5B: "Win",
    0x5C: "Win",
}


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover
        raise DiagnosticPipelineError(_PILLOW_HINT) from exc
    return Image, ImageDraw, ImageFont


class KeyStrokeEvent(NamedTuple):
    at: float  # time.monotonic()
    label: str
    modifiers: tuple[str, ...]
    is_chord: bool

    @property
    def display_text(self) -> str:
        if self.modifiers:
            mods = " + ".join(self.modifiers)
            return f"{mods} + {self.label}" if self.label else mods
        return self.label


class KeyTracker:
    """Interceptors keyboard events through a low-level hook (WH_KEYBOARD_LL).

    Maintains active modifier states and translates VK codes / VK_PACKET into
    formatted keystroke tokens and chords.
    """

    def __init__(self, capacity: int = 32):
        self.events: deque[KeyStrokeEvent] = deque(maxlen=capacity)
        self._thread: threading.Thread | None = None
        self._hook = None
        self._proc = None
        self._tid = 0
        self._ready = threading.Event()
        self._setup_error: BaseException | None = None

        # Modifier state tracking
        self._ctrl = False
        self._alt = False
        self._shift = False
        self._win = False
        self._lock = threading.Lock()

    @property
    def installed(self) -> bool:
        return bool(self._hook)

    @property
    def setup_error(self) -> BaseException | None:
        return self._setup_error

    def _get_active_modifiers(self) -> tuple[str, ...]:
        mods = []
        if self._ctrl:
            mods.append("Ctrl")
        if self._alt:
            mods.append("Alt")
        if self._shift:
            mods.append("Shift")
        if self._win:
            mods.append("Win")
        return tuple(mods)

    def _decode_key(self, vk: int, scan: int, flags: int) -> tuple[str, bool]:
        """Returns (key_label, is_modifier)."""
        # VK_PACKET: scanCode is UTF-16 Unicode character
        if vk == VK_PACKET:
            try:
                return chr(scan), False
            except ValueError:
                return f"\\u{scan:04x}", False

        # Modifiers
        if vk in _MODIFIER_VKS:
            return _MODIFIER_VKS[vk], True

        return _VK_MAP.get(vk, f"0x{vk:02X}"), False

    def _pump(self):
        def callback(code, wparam, lparam):
            try:
                if code >= 0:
                    msg = int(wparam)
                    info = lparam[0]
                    vk = int(info.vkCode)
                    scan = int(info.scanCode)
                    flags = int(info.flags)
                    is_up = bool(flags & LLKHF_UP) or msg in (WM_KEYUP, WM_SYSKEYUP)
                    is_down = not is_up and msg in (WM_KEYDOWN, WM_SYSKEYDOWN)

                    with self._lock:
                        # Update modifier flags
                        if vk in (0x11, 0xA2, 0xA3):
                            self._ctrl = is_down
                        elif vk in (0x12, 0xA4, 0xA5) or (flags & LLKHF_ALTDOWN and is_down):
                            self._alt = is_down
                        elif vk in (0x10, 0xA0, 0xA1):
                            self._shift = is_down
                        elif vk in (0x5B, 0x5C):
                            self._win = is_down

                        if is_down:
                            key_label, is_mod = self._decode_key(vk, scan, flags)
                            active_mods = self._get_active_modifiers()

                            # If a non-modifier key is pressed, or a modifier is pressed alone
                            if not is_mod:
                                is_chord = len(active_mods) > 0
                                # If Shift is the only modifier and it's a single character,
                                # we can just show the key or chord
                                self.events.append(
                                    KeyStrokeEvent(
                                        at=time.monotonic(),
                                        label=key_label,
                                        modifiers=active_mods,
                                        is_chord=is_chord,
                                    )
                                )
                            elif not active_mods or active_mods == (key_label,):
                                # Single modifier press (e.g. just tapping Shift or Ctrl)
                                self.events.append(
                                    KeyStrokeEvent(
                                        at=time.monotonic(),
                                        label=key_label,
                                        modifiers=(),
                                        is_chord=False,
                                    )
                                )
            except Exception:  # noqa: BLE001 - low-level hook must never throw
                pass

            return user32.CallNextHookEx(
                None, code, wparam, ctypes.cast(lparam, ctypes.c_void_p).value or 0
            )

        try:
            self._proc = _HOOKPROC(callback)
            self._tid = kernel32.GetCurrentThreadId()
            self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        except BaseException as exc:  # noqa: BLE001
            self._setup_error = exc
            self._ready.set()
            return

        self._ready.set()
        if not self._hook:
            return

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def start(self, timeout: float = 5.0) -> bool:
        self._thread = threading.Thread(target=self._pump, daemon=True, name="wintegrate-keys")
        self._thread.start()
        if not self._ready.wait(timeout):
            logger.warning("the keyboard hook thread never reported back; key HUD disabled")
            return False
        if self._setup_error is not None:
            logger.warning(
                f"key HUD disabled: {type(self._setup_error).__name__}: {self._setup_error}"
            )
            return False
        if not self._hook:
            logger.warning(
                f"SetWindowsHookExW(WH_KEYBOARD_LL) returned NULL "
                f"(GetLastError={ctypes.get_last_error()}); key HUD disabled"
            )
            return False
        return True

    def stop(self, timeout: float = 3.0):
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=timeout)

    def recent(self, linger: float = DEFAULT_KEY_LINGER_SECONDS) -> list[KeyStrokeEvent]:
        now = time.monotonic()
        with self._lock:
            return [e for e in list(self.events) if now - e.at <= linger]


def _get_font(size: int = 15):
    """Loads a clean TrueType font if available, or falls back to Pillow's default."""
    _, _, ImageFont = _pil()
    for font_name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttf"):
        try:
            return ImageFont.truetype(font_name, size)
        except (OSError, ImportError):
            continue
    return ImageFont.load_default()


def draw_keyboard_hud(
    image,
    keys: list[KeyStrokeEvent],
    now: float | None = None,
    linger: float = DEFAULT_KEY_LINGER_SECONDS,
    max_items: int = 5,
):
    """Renders a sleek, modern, bottom-centered Keycap HUD onto the given image.

    Only re-composites the bounded badge area onto the frame for sub-millisecond
    in-memory performance.
    """
    if not keys:
        return image

    Image, ImageDraw, _ = _pil()
    if now is None:
        now = time.monotonic()

    # Filter and take the most recent items
    recent_keys = [k for k in keys if 0 <= (now - k.at) <= linger][-max_items:]
    if not recent_keys:
        return image

    font = _get_font(15)

    # Compute sizes for each keycap badge
    badges: list[tuple[str, int, int, float]] = []  # (text, w, h, opacity_factor)
    for k in recent_keys:
        text = k.display_text
        age = now - k.at
        # Progress from 0 (new) to 1 (expiring)
        progress = min(1.0, max(0.0, age / linger))
        alpha_mult = 1.0 - (progress * 0.6)  # Older keys fade slightly but remain legible

        # Measure text
        dummy_img = Image.new("RGBA", (1, 1))
        d = ImageDraw.Draw(dummy_img)
        bbox = d.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        badge_w = max(32, text_w + 18)
        badge_h = max(28, text_h + 12)
        badges.append((text, badge_w, badge_h, alpha_mult))

    # Total HUD container size
    spacing = 8
    total_w = sum(b[1] for b in badges) + spacing * (len(badges) - 1) + 20
    total_h = max(b[2] for b in badges) + 16

    # Position: bottom-center with 20px bottom margin
    hud_x = max(10, (image.width - total_w) // 2)
    hud_y = max(10, image.height - total_h - 20)

    box = (
        hud_x,
        hud_y,
        min(image.width, hud_x + total_w),
        min(image.height, hud_y + total_h),
    )
    if box[0] >= box[2] or box[1] >= box[3]:
        return image

    patch = image.crop(box).convert("RGBA")
    overlay = Image.new("RGBA", patch.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Draw outer dark translucent pill container
    container_rect = (0, 0, patch.width - 1, patch.height - 1)
    draw.rounded_rectangle(
        container_rect,
        radius=8,
        fill=(16, 18, 24, 215),
        outline=(255, 255, 255, 45),
        width=1,
    )

    # Draw individual keycaps
    cur_x = 10
    for text, bw, bh, alpha_mult in badges:
        cur_y = (patch.height - bh) // 2
        krect = (cur_x, cur_y, cur_x + bw, cur_y + bh)

        bg_alpha = int(245 * alpha_mult)
        border_alpha = int(90 * alpha_mult)
        text_alpha = int(255 * alpha_mult)

        # Keycap background
        draw.rounded_rectangle(
            krect,
            radius=5,
            fill=(40, 44, 52, bg_alpha),
            outline=(200, 205, 215, border_alpha),
            width=1,
        )

        # Center text inside keycap
        tbbox = draw.textbbox((0, 0), text, font=font)
        tw = tbbox[2] - tbbox[0]
        th = tbbox[3] - tbbox[1]
        tx = cur_x + (bw - tw) // 2 - tbbox[0]
        ty = cur_y + (bh - th) // 2 - tbbox[1]

        draw.text((tx, ty), text, font=font, fill=(255, 255, 255, text_alpha))
        cur_x += bw + spacing

    patch.alpha_composite(overlay)
    image.paste(patch.convert(image.mode), box[:2])
    return image
