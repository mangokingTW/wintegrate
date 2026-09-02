"""Pointer and click markers drawn into captured frames.

A screen grab through BitBlt contains no pointer: the cursor is composited by the
system rather than stored in the desktop bitmap. So a recording of a wintegrate run
shows windows changing with nothing to say where the pointer was or that a click
happened at all.

Drawing the pointer here, after the grab, has a property no on-screen overlay can
match: nothing can cover it. An overlay window has to win a z-order fight it cannot
always win -- an activated topmost window draws above anything that stays
unactivated, and a visualiser that took focus would dismiss the very thing being
recorded. Measured with one variable, the same window and the same click point with
only WS_EX_TOPMOST changing: 2032 ring pixels became 0.

Clicks come from a low-level mouse hook rather than from wintegrate's own injection
sites, so a click from any source lands in the recording -- including one the test
did not mean to make.
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
    BITMAPINFOHEADER,
    DI_NORMAL,
    MSLLHOOKSTRUCT,
    RECT,
    SM_CXCURSOR,
    SM_CYCURSOR,
    WH_MOUSE_LL,
    WM_LBUTTONDOWN,
    WM_MBUTTONDOWN,
    WM_QUIT,
    WM_RBUTTONDOWN,
    gdi32,
    get_cursor_state,
    kernel32,
    user32,
)

logger = logging.getLogger(__name__)

#: Left, right and middle get different colours so a recording distinguishes them.
BUTTON_COLOURS: dict[int, tuple[int, int, int]] = {
    WM_LBUTTONDOWN: (255, 45, 85),
    WM_RBUTTONDOWN: (45, 140, 255),
    WM_MBUTTONDOWN: (255, 200, 0),
}

#: How long a click marker stays on screen. 1.5s is 15 frames at the ~10 fps a
#: runner actually sustains; the 0.5s animation typical of desktop visualisers
#: falls between samples often enough to look like the click never happened.
CLICK_LINGER_SECONDS = 1.5

_RING_START_RADIUS = 24
_RING_END_RADIUS = 60
_RING_WIDTH = 5
_CROSSHAIR_ARM = 9
_CROSSHAIR_WIDTH = 3

#: 2x by default: a 32px cursor is a few pixels of thin outline once a recording is
#: scaled down to fit in a browser.
DEFAULT_CURSOR_SCALE = 2

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_HOOKPROC = _WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(MSLLHOOKSTRUCT)
)

_PILLOW_HINT = (
    "Pointer overlays need Pillow, which ships in the optional 'video' extra: "
    "pip install 'wintegrate[video]'"
)


class ClickEvent(NamedTuple):
    at: float  # time.monotonic()
    x: int
    y: int
    button: int


def _pil():
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise DiagnosticPipelineError(_PILLOW_HINT) from exc
    return Image, ImageChops, ImageDraw, ImageFilter


class ClickTracker:
    """Records every mouse-button press through a low-level hook.

    The hook needs a thread with a message loop, so this owns one. Failure to
    install is reported and survivable: a recording without click markers is worth
    more than a run that fails over its own diagnostics.
    """

    def __init__(self, capacity: int = 64):
        self.events: deque[ClickEvent] = deque(maxlen=capacity)
        self._thread: threading.Thread | None = None
        self._hook = None
        self._proc = None
        self._tid = 0
        self._ready = threading.Event()
        self._setup_error: BaseException | None = None

    @property
    def installed(self) -> bool:
        return bool(self._hook)

    @property
    def setup_error(self) -> BaseException | None:
        return self._setup_error

    def _pump(self):
        def callback(code, wparam, lparam):
            try:
                if code >= 0 and int(wparam) in BUTTON_COLOURS:
                    info = lparam[0]
                    self.events.append(
                        ClickEvent(time.monotonic(), int(info.pt.x), int(info.pt.y), int(wparam))
                    )
            except Exception:  # noqa: BLE001 - a hook callback must always return
                pass
            # CallNextHookEx wants an LPARAM, and lparam arrives as a typed
            # pointer; the address is what has to be passed through.
            return user32.CallNextHookEx(
                None, code, wparam, ctypes.cast(lparam, ctypes.c_void_p).value or 0
            )

        try:
            self._proc = _HOOKPROC(callback)
            self._tid = kernel32.GetCurrentThreadId()
            self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        except BaseException as exc:  # noqa: BLE001 - stored and reported, not swallowed
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
        self._thread = threading.Thread(target=self._pump, daemon=True, name="wintegrate-clicks")
        self._thread.start()
        if not self._ready.wait(timeout):
            logger.warning("the click hook thread never reported back; click markers disabled")
            return False
        if self._setup_error is not None:
            logger.warning(
                f"click markers disabled: {type(self._setup_error).__name__}: {self._setup_error}"
            )
            return False
        if not self._hook:
            logger.warning(
                f"SetWindowsHookExW(WH_MOUSE_LL) returned NULL "
                f"(GetLastError={ctypes.get_last_error()}); click markers disabled"
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

    def recent(self, linger: float = CLICK_LINGER_SECONDS) -> list[ClickEvent]:
        now = time.monotonic()
        return [e for e in list(self.events) if now - e.at <= linger]


def _cursor_bitmap(handle: int, scale: int):
    """The cursor as an RGBA image, with a dark halo so it reads on any background.

    Windows offers no way to read a cursor's alpha directly for every cursor type,
    so it is drawn twice, once on black and once on white. Where the two agree the
    pixel is opaque; where they differ the background showed through.
    """
    Image, ImageChops, _, ImageFilter = _pil()

    width = user32.GetSystemMetrics(SM_CXCURSOR) * scale
    height = user32.GetSystemMetrics(SM_CYCURSOR) * scale

    renders = []
    for fill in (0x00000000, 0x00FFFFFF):
        hdc_screen = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
        old = gdi32.SelectObject(hdc_mem, hbmp)
        brush = gdi32.CreateSolidBrush(fill)
        rect = RECT(0, 0, width, height)
        user32.FillRect(hdc_mem, ctypes.byref(rect), brush)
        gdi32.DeleteObject(brush)
        user32.DrawIconEx(hdc_mem, 0, 0, handle, width, height, 0, None, DI_NORMAL)

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = width
        bmi.biHeight = -height
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0
        buf = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf, ctypes.byref(bmi), 0)
        renders.append(
            Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1).convert("RGB")
        )

        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)

    on_black, on_white = renders
    # point() and difference() do this at C speed; a per-pixel Python loop here
    # would cost more than encoding the frame.
    alpha = (
        ImageChops.difference(on_black, on_white).convert("L").point(lambda v: 255 if v == 0 else 0)
    )

    halo_mask = alpha.filter(ImageFilter.MaxFilter(5))
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    out.paste((20, 20, 20, 235), (0, 0), halo_mask)
    cursor = on_black.convert("RGBA")
    cursor.putalpha(alpha)
    out.alpha_composite(cursor)
    return out


_cursor_cache: dict[tuple[int, int], object] = {}


def cursor_overlay_image(handle: int, scale: int = DEFAULT_CURSOR_SCALE):
    """`_cursor_bitmap`, cached: the shape changes only when the cursor does."""
    key = (int(handle), int(scale))
    cached = _cursor_cache.get(key)
    if cached is None:
        cached = _cursor_bitmap(handle, scale)
        # Small enough that a bound is a formality, but a run that hovers over many
        # controls does churn cursors.
        if len(_cursor_cache) > 32:
            _cursor_cache.clear()
        _cursor_cache[key] = cached
    return cached


def draw_click_markers(
    image,
    clicks,
    now: float | None = None,
    origin: tuple[int, int] = (0, 0),
    linger: float = CLICK_LINGER_SECONDS,
):
    """Draws an expanding, fading ring and a crosshair for each recent click.

    Expanding rather than static so a reader can order two clicks, and so a
    double-click shows as two rings of different size instead of one.

    Composites per ring into a local box rather than over the whole frame: at 30 fps
    a full-size RGBA round trip costs more than everything else here put together.
    """
    Image, _, ImageDraw, _ = _pil()
    if now is None:
        now = time.monotonic()

    ox, oy = origin
    for click in clicks:
        age = now - click.at
        if age < 0 or age > linger:
            continue
        progress = min(1.0, age / linger)
        radius = int(_RING_START_RADIUS + (_RING_END_RADIUS - _RING_START_RADIUS) * progress)
        alpha = int(235 * (1.0 - progress))
        if alpha <= 0:
            continue
        colour = BUTTON_COLOURS.get(click.button, (255, 45, 85)) + (alpha,)

        cx, cy = click.x - ox, click.y - oy
        pad = radius + _RING_WIDTH
        box = (
            max(0, cx - pad),
            max(0, cy - pad),
            min(image.width, cx + pad + 1),
            min(image.height, cy + pad + 1),
        )
        if box[0] >= box[2] or box[1] >= box[3]:
            continue

        patch = image.crop(box).convert("RGBA")
        marks = Image.new("RGBA", patch.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(marks)
        lx, ly = cx - box[0], cy - box[1]
        draw.ellipse(
            (lx - radius, ly - radius, lx + radius, ly + radius),
            outline=colour,
            width=_RING_WIDTH,
        )
        draw.line(
            (lx - _CROSSHAIR_ARM, ly, lx + _CROSSHAIR_ARM, ly), fill=colour, width=_CROSSHAIR_WIDTH
        )
        draw.line(
            (lx, ly - _CROSSHAIR_ARM, lx, ly + _CROSSHAIR_ARM), fill=colour, width=_CROSSHAIR_WIDTH
        )
        patch.alpha_composite(marks)
        image.paste(patch.convert(image.mode), box[:2])

    return image


def draw_cursor(image, origin: tuple[int, int] = (0, 0), scale: int = DEFAULT_CURSOR_SCALE):
    """Pastes the live cursor onto a captured frame at its screen position."""
    state = get_cursor_state()
    if state is None:
        return image
    try:
        overlay = cursor_overlay_image(state.handle, scale)
    except Exception as exc:  # noqa: BLE001 - a missing pointer must not lose the frame
        logger.debug(f"cursor bitmap unavailable ({type(exc).__name__}): {exc}")
        return image

    ox, oy = origin
    x = state.position[0] - ox - state.hotspot[0] * scale
    y = state.position[1] - oy - state.hotspot[1] * scale
    image.paste(overlay.convert(image.mode), (x, y), overlay.split()[-1])
    return image


def draw_pointer_overlay(
    image,
    clicks=(),
    now: float | None = None,
    origin: tuple[int, int] = (0, 0),
    scale: int = DEFAULT_CURSOR_SCALE,
    linger: float = CLICK_LINGER_SECONDS,
):
    """Click markers first, then the cursor on top of them."""
    draw_click_markers(image, clicks, now=now, origin=origin, linger=linger)
    draw_cursor(image, origin=origin, scale=scale)
    return image
