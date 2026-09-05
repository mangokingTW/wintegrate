"""CI-first diagnostic subsystem: streaming video recorder, desktop window census, and process tracking."""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from wintegrate import caption_overlay, keyboard_overlay, pointer_overlay
from wintegrate.exceptions import DiagnosticPipelineError
from wintegrate.interop import (
    BITMAPINFOHEADER,
    PW_RENDERFULLCONTENT,
    RECT,
    SM_CXSCREEN,
    SM_CXVIRTUALSCREEN,
    SM_CYSCREEN,
    SM_CYVIRTUALSCREEN,
    SM_XVIRTUALSCREEN,
    SM_YVIRTUALSCREEN,
    SRCCOPY,
    WNDENUMPROC,
    DisplayAffinity,
    attach_to_input_desktop,
    gdi32,
    get_input_desktop_handle,
    get_window_class,
    get_window_display_affinity,
    get_window_pid,
    get_window_title,
    user32,
)

logger = logging.getLogger(__name__)

# Where `Window.launch_and_discover` sends a child's stdout/stderr while a
# Session is open. Set by the Session; None means no session, and the child's
# output goes to DEVNULL. A child must never inherit this process's stdio: an
# orphan holding the step's pipes is what keeps a CI step `in_progress` after
# the process that started it has died.
_launch_output_dir: Path | None = None
_launch_output_lock = threading.Lock()
_launch_seq = 0


def set_launch_output_dir(path: Path | None) -> None:
    global _launch_output_dir
    with _launch_output_lock:
        _launch_output_dir = path


def launch_output_paths() -> tuple[Path, Path] | None:
    """A fresh (stdout, stderr) file pair in the session's artifact dir, or None without a session."""
    global _launch_seq
    with _launch_output_lock:
        if _launch_output_dir is None:
            return None
        _launch_seq += 1
        base = _launch_output_dir / f"launched_{_launch_seq:02d}"
    return base.with_suffix(".out"), base.with_suffix(".err")


_PILLOW_HINT = (
    "Screen capture needs Pillow, which ships in the optional 'video' extra: "
    "pip install 'wintegrate[video]'"
)


def _load_pil_image():
    """Imports PIL.Image on demand so the core install does not require Pillow."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise DiagnosticPipelineError(_PILLOW_HINT) from exc
    return Image


def _dib_to_image(hdc_mem, hbmp, w: int, h: int):
    """Reads a device-independent bitmap out of a memory DC into a PIL Image."""
    Image = _load_pil_image()
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h  # top-down DIB
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")


def capture_screen_image(all_monitors: bool = False, draw_cursor: bool = False):
    """
    Captures the desktop into a PIL Image.

    By default this is the primary display. `all_monitors=True` captures the whole
    virtual desktop instead — on a multi-monitor runner the window under test is
    quite often not on the primary one, and a primary-only screenshot of a failure
    that happened elsewhere is worse than none, because it looks like evidence.

    `draw_cursor=True` adds the pointer, which a BitBlt of the desktop never
    contains. It defaults to False because callers compare these images pixel by
    pixel; a pointer wandering into frame would turn a passing assertion into a
    flake. `ContinuousRecorder` turns it on for video, where the opposite is true.
    """
    if all_monitors:
        x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    else:
        x = y = 0
        w = user32.GetSystemMetrics(SM_CXSCREEN)
        h = user32.GetSystemMetrics(SM_CYSCREEN)

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)
    # Source origin is the virtual-screen origin, which is negative when a monitor
    # sits left of or above the primary one.
    gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, SRCCOPY)
    try:
        img = _dib_to_image(hdc_mem, hbmp, w, h)
        if draw_cursor:
            # (x, y) is the capture origin, negative when a monitor sits left of or
            # above the primary one; cursor coordinates are screen-absolute.
            pointer_overlay.draw_cursor(img, origin=(x, y))
        return img
    finally:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)
        gdi32.DeleteObject(hbmp)


def _looks_blank(img) -> bool:
    """True when the image has no non-black pixel at all."""
    # getbbox() bounds the non-zero region and returns None for an all-black image.
    return img.getbbox() is None


def capture_window_image(hwnd: int):
    """
    Captures a single window into a PIL Image, including parts of it that other
    windows are covering.

    Cropping a screenshot would capture whatever is on top — on CI that is often
    the very popup that broke the test, so the evidence shows the intruder rather
    than the window under test. PrintWindow asks the window to render itself
    instead.

    PrintWindow returns an all-black bitmap for some DWM-composited and XAML
    windows, so the result is checked and falls back to cropping the desktop.
    Returning a black rectangle would be the worst outcome: an artifact that looks
    like a capture and shows nothing.
    """
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise DiagnosticPipelineError(f"GetWindowRect failed for hwnd {hwnd}")
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise DiagnosticPipelineError(f"Window {hwnd} has an empty rectangle ({w}x{h})")

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)
    try:
        ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        img = _dib_to_image(hdc_mem, hbmp, w, h) if ok else None
    finally:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)
        gdi32.DeleteObject(hbmp)

    if img is not None and not _looks_blank(img):
        return img

    # Said out loud, because the fallback below cannot help here and the result
    # would otherwise be a picture of the desktop presented as a picture of the
    # window. A window that has called SetWindowDisplayAffinity is withheld from
    # every capture path there is, so neither PrintWindow nor a crop will ever
    # contain it -- and it is visible, uncloaked and on screen the whole time,
    # which is what makes it look like a capture bug.
    affinity = get_window_display_affinity(hwnd)
    if affinity not in (None, DisplayAffinity.NONE):
        logger.warning(
            f"hwnd {hwnd:#x} is excluded from capture ({affinity.name}): it will not appear "
            "in screenshots or recordings no matter which capture API is used, because the "
            "application asked Windows to withhold it. Only that application can allow it."
        )

    logger.debug(
        f"PrintWindow gave no usable content for hwnd {hwnd}; cropping the desktop instead"
    )
    x0 = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y0 = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    desktop = capture_screen_image(all_monitors=True)
    return desktop.crop((rect.left - x0, rect.top - y0, rect.right - x0, rect.bottom - y0))


class ContinuousRecorder:
    """
    Low-memory streaming desktop screen recorder.

    Encodes in-process through PyAV, which bundles FFmpeg and is the only route
    that works everywhere this library runs: no other ffmpeg distribution on PyPI
    ships a `win_arm64` wheel, so shelling out to an external binary would mean
    asking Windows ARM64 users to install one themselves.

    Recording is a diagnostic, never the thing under test. When PyAV is missing,
    `start()` reports failure and the caller carries on without a video rather
    than a run failing over a missing artifact.
    """

    def __init__(
        self,
        output_path: str | Path,
        fps: int = 30,
        draw_cursor: bool = True,
        click_markers: bool = True,
        key_hud: bool = True,
        caption: str = "",
    ):
        self.output_path = Path(output_path)
        self.fps = fps
        self.interval = 1.0 / fps
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        # On by default: a recording is watched, not diffed, and one with no pointer
        # cannot answer the first question anybody asks of it -- where did it click.
        # The markers are drawn after the screen grab, so unlike an on-screen
        # visualiser no window can cover them.
        self.draw_cursor = draw_cursor
        self.click_markers = click_markers
        self.key_hud = key_hud
        # Settable while recording: a caller that knows what it is doing -- a pytest
        # hook, say -- assigns this as it moves from one thing to the next, and the
        # frames from that point on say so. Empty draws nothing.
        self.caption = caption
        self.caption_subtitle = ""
        self._clicks: pointer_overlay.ClickTracker | None = None
        self._keys: keyboard_overlay.KeyTracker | None = None
        self._frame_count = 0
        # PyAV encoding state
        self._av = None
        self._container = None
        self._stream = None
        self._t0 = 0.0
        self._wall0 = 0.0
        self._size: tuple[int, int] = (0, 0)

    @property
    def backend(self) -> str | None:
        """ "pyav", or None when recording is not running."""
        return "pyav" if self._container is not None else None

    def anchor(self) -> dict | None:
        """How to map a timestamp in the event timeline onto the recording.

        Frames are stamped `pts_ms = (monotonic - monotonic_start) * 1000`, and
        events carry both `monotonic` and a wall clock, so
        `video_ms = (event.monotonic - monotonic_start) * 1000`. Also says what the
        frame covers: the primary monitor at its native size, not the virtual
        desktop that the screenshots cover -- a mismatch that is otherwise written
        nowhere.
        """
        if self._container is None:
            return None
        return {
            "backend": "pyav",
            "wall_start": self._wall0,
            "monotonic_start": self._t0,
            "fps": self.fps,
            "width": self._size[0],
            "height": self._size[1],
            "covers": "primary monitor",
            "video_ms": "(event.monotonic - monotonic_start) * 1000",
        }

    def _start_pyav(self, w: int, h: int) -> bool:
        try:
            import av
        except ImportError:
            return False

        codec = "libx264" if self.output_path.suffix.lower() == ".mp4" else "libvpx-vp9"
        container = None
        try:
            # Fragmented MP4, so the file plays back even if this process is
            # killed mid-write: with the default layout the index is written at
            # close, and a recording whose process never closed it is unreadable
            # -- which is exactly the recording anyone wants to watch. One
            # fragment per keyframe, and a keyframe every second (gop_size), so
            # the tail lost to a kill is bounded by a second rather than by
            # libx264's default ~8s.
            container = av.open(
                str(self.output_path),
                mode="w",
                options={
                    "movflags": "frag_keyframe+empty_moov+default_base_moof",
                    "flush_packets": "1",
                },
            )
            stream = container.add_stream(codec, rate=self.fps)
            stream.width, stream.height = w, h
            stream.pix_fmt = "yuv420p"
            try:
                stream.codec_context.gop_size = int(self.fps)
            except Exception as exc:  # noqa: BLE001 - keeps recording without it
                logger.debug(f"gop_size not set ({type(exc).__name__}): {exc}")
            # Wall-clock presentation timestamps in milliseconds. Screen capture
            # rarely sustains the nominal frame rate, and encoding at a fixed rate
            # would then play the recording back faster than the run happened.
            stream.codec_context.time_base = Fraction(1, 1000)
        except Exception as exc:
            logger.warning(f"PyAV encoder unavailable ({type(exc).__name__}): {exc}")
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass
            return False

        self._av = av
        self._container = container
        self._stream = stream
        self._size = (w, h)
        return True

    def start(self) -> bool:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        # libx264 requires even dimensions; odd desktop resolutions are rare but real.
        w = user32.GetSystemMetrics(0) & ~1
        h = user32.GetSystemMetrics(1) & ~1

        if self._start_pyav(w, h):
            if self.click_markers:
                tracker = pointer_overlay.ClickTracker()
                # A hook this cannot install costs the markers, not the recording.
                self._clicks = tracker if tracker.start() else None
            if self.key_hud:
                key_tracker = keyboard_overlay.KeyTracker()
                self._keys = key_tracker if key_tracker.start() else None
            self.stop_event.clear()
            self._frame_count = 0
            self._t0 = time.monotonic()
            self._wall0 = time.time()
            self.thread = threading.Thread(target=self._record_loop, daemon=True)
            self.thread.start()
            logger.info(
                f"ContinuousRecorder started via PyAV (streaming to {self.output_path} "
                f"@ {self.fps} FPS)"
            )
            return True

        logger.warning(
            "PyAV is not installed, so no encoder is available "
            "(pip install 'wintegrate[video]'). ContinuousRecorder video disabled."
        )
        return False

    def _encode_pyav_frame(self, img) -> None:
        w, h = self._size
        frame = self._av.VideoFrame.from_image(img)
        if (frame.width, frame.height) != (w, h):
            frame = frame.reformat(w, h, "yuv420p")
        else:
            frame = frame.reformat(format="yuv420p")
        frame.pts = int((time.monotonic() - self._t0) * 1000)
        frame.time_base = Fraction(1, 1000)
        for packet in self._stream.encode(frame):
            self._container.mux(packet)

    def _record_loop(self):
        attach_to_input_desktop()
        while not self.stop_event.is_set():
            t0 = time.monotonic()
            try:
                img = capture_screen_image()
                if self.click_markers and self._clicks is not None:
                    pointer_overlay.draw_click_markers(img, self._clicks.recent())
                if self.key_hud and self._keys is not None:
                    keyboard_overlay.draw_keyboard_hud(img, self._keys.recent())
                if self.caption:
                    caption_overlay.draw_caption(img, self.caption, subtitle=self.caption_subtitle)
                if self.draw_cursor:
                    pointer_overlay.draw_cursor(img)
                if self._container is not None:
                    self._encode_pyav_frame(img)
                    self._frame_count += 1
            except Exception as exc:
                logger.debug(f"Screen capture frame skipped ({type(exc).__name__}): {exc}")

            elapsed = time.monotonic() - t0
            sleep_time = max(0.001, self.interval - elapsed)
            time.sleep(sleep_time)

    def stop(self, timeout: float = 5.0) -> int:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=timeout)
        if self._clicks is not None:
            # After the capture thread, or a frame can read a hook being torn down.
            self._clicks.stop()
            self._clicks = None
        if self._keys is not None:
            self._keys.stop()
            self._keys = None

        if self._container is not None:
            # Flush the encoder before closing, or the tail of the recording — the
            # part covering the failure being diagnosed — never reaches the file.
            try:
                for packet in self._stream.encode(None):
                    self._container.mux(packet)
            except Exception as exc:
                logger.debug(f"PyAV encoder flush warning ({type(exc).__name__}): {exc}")
            try:
                self._container.close()
            except Exception as exc:
                logger.debug(f"PyAV container close warning ({type(exc).__name__}): {exc}")
            self._container = None
            self._stream = None
            logger.info(f"ContinuousRecorder stopped. Total frames recorded: {self._frame_count}")
            return self._frame_count

        logger.info(f"ContinuousRecorder stopped. Total frames recorded: {self._frame_count}")
        return self._frame_count


@dataclass
class WindowSnapshot:
    hwnd: int
    title: str
    class_name: str
    pid: int
    is_visible: bool


@dataclass
class CensusDiff:
    added: list[WindowSnapshot] = field(default_factory=list)
    removed: list[WindowSnapshot] = field(default_factory=list)
    persisted: list[WindowSnapshot] = field(default_factory=list)


class WindowCensus:
    """Takes instant snapshots of all top-level desktop windows and diffs them."""

    @staticmethod
    def capture() -> list[WindowSnapshot]:
        attach_to_input_desktop()
        snapshots: list[WindowSnapshot] = []

        def enum_proc(hwnd, _):
            is_vis = bool(user32.IsWindowVisible(hwnd))
            title = get_window_title(hwnd)
            cls = get_window_class(hwnd)
            pid = get_window_pid(hwnd)
            snapshots.append(
                WindowSnapshot(
                    hwnd=hwnd,
                    title=title,
                    class_name=cls,
                    pid=pid,
                    is_visible=is_vis,
                )
            )
            return True

        cb = WNDENUMPROC(enum_proc)
        h_desk = get_input_desktop_handle()
        if h_desk:
            user32.EnumDesktopWindows(h_desk, cb, 0)
        else:
            user32.EnumWindows(cb, 0)
        return snapshots

    @classmethod
    def diff(cls, before: list[WindowSnapshot], after: list[WindowSnapshot]) -> CensusDiff:
        before_map = {w.hwnd: w for w in before}
        after_map = {w.hwnd: w for w in after}

        added = [w for hwnd, w in after_map.items() if hwnd not in before_map]
        removed = [w for hwnd, w in before_map.items() if hwnd not in after_map]
        persisted = [w for hwnd, w in after_map.items() if hwnd in before_map]

        return CensusDiff(added=added, removed=removed, persisted=persisted)
