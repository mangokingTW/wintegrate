"""CI-first diagnostic subsystem: streaming video recorder, desktop window census, and process tracking."""

from __future__ import annotations

import ctypes
import glob
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

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
    attach_to_input_desktop,
    gdi32,
    get_input_desktop_handle,
    get_window_class,
    get_window_pid,
    get_window_title,
    user32,
)

logger = logging.getLogger(__name__)

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


def capture_screen_image(all_monitors: bool = False):
    """
    Captures the desktop into a PIL Image.

    By default this is the primary display. `all_monitors=True` captures the whole
    virtual desktop instead — on a multi-monitor runner the window under test is
    quite often not on the primary one, and a primary-only screenshot of a failure
    that happened elsewhere is worse than none, because it looks like evidence.
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
        return _dib_to_image(hdc_mem, hbmp, w, h)
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

    logger.debug(
        f"PrintWindow gave no usable content for hwnd {hwnd}; cropping the desktop instead"
    )
    x0 = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y0 = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    desktop = capture_screen_image(all_monitors=True)
    return desktop.crop((rect.left - x0, rect.top - y0, rect.right - x0, rect.bottom - y0))


def resolve_ffmpeg_exe() -> str | None:
    """
    Finds a verified, existing ffmpeg executable across x64 and ARM64.
    Searches system PATH, standard Windows / Chocolatey / WinGet directories,
    and imageio_ffmpeg.
    """
    # 1. System PATH
    exe = shutil.which("ffmpeg")
    if exe and os.path.isfile(exe):
        return exe

    # 2. Common Windows and Package Manager directories
    common_patterns = [
        r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
        r"C:\Program Files\FFmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\tools\ffmpeg\bin\ffmpeg.exe",
        r"C:\Users\*\AppData\Local\Microsoft\WinGet\Packages\*\*\bin\ffmpeg.exe",
        r"C:\Users\*\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
    ]
    for pattern in common_patterns:
        matches = glob.glob(pattern)
        for m in matches:
            if os.path.isfile(m):
                return m

    # 3. imageio_ffmpeg fallback (verify file exists)
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception as exc:
        logger.debug(f"imageio_ffmpeg lookup failed ({type(exc).__name__}): {exc}")

    return None


class ContinuousRecorder:
    """
    Low-memory streaming desktop screen recorder.

    Encodes in-process through PyAV (which bundles FFmpeg) when it is installed,
    and otherwise streams raw frames to an external FFmpeg subprocess over stdin
    with stderr redirected to a disk log, so the pipe buffer cannot deadlock.

    PyAV is preferred because it is the only route that works everywhere the
    library runs: no ffmpeg build ships a `win_arm64` wheel on PyPI, so on Windows
    ARM64 the subprocess path depends on an ffmpeg the user installed themselves.
    """

    def __init__(self, output_path: str | Path, fps: int = 30):
        self.output_path = Path(output_path)
        self.fps = fps
        self.interval = 1.0 / fps
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._stderr_file = None
        self._frame_count = 0
        self._ffmpeg_exe = resolve_ffmpeg_exe()
        # PyAV encoding state
        self._av = None
        self._container = None
        self._stream = None
        self._t0 = 0.0
        self._size: tuple[int, int] = (0, 0)

    @property
    def backend(self) -> str | None:
        """ "pyav", "ffmpeg", or None when recording is not running."""
        if self._container is not None:
            return "pyav"
        if self._proc is not None:
            return "ffmpeg"
        return None

    def _start_pyav(self, w: int, h: int) -> bool:
        try:
            import av
        except ImportError:
            return False

        codec = "libx264" if self.output_path.suffix.lower() == ".mp4" else "libvpx-vp9"
        container = None
        try:
            container = av.open(str(self.output_path), mode="w")
            stream = container.add_stream(codec, rate=self.fps)
            stream.width, stream.height = w, h
            stream.pix_fmt = "yuv420p"
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
            self.stop_event.clear()
            self._frame_count = 0
            self._t0 = time.monotonic()
            self.thread = threading.Thread(target=self._record_loop, daemon=True)
            self.thread.start()
            logger.info(
                f"ContinuousRecorder started via PyAV (streaming to {self.output_path} "
                f"@ {self.fps} FPS)"
            )
            return True

        if not self._ffmpeg_exe:
            logger.warning(
                "No FFmpeg available: PyAV is not installed and no ffmpeg executable was "
                "found. Install the video extra (pip install 'wintegrate[video]') or put "
                "ffmpeg on PATH. ContinuousRecorder video disabled."
            )
            return False

        return self._start_ffmpeg_subprocess()

    def _start_ffmpeg_subprocess(self) -> bool:
        stderr_log_path = self.output_path.with_suffix(".ffmpeg.log")
        self._stderr_file = open(stderr_log_path, "w", encoding="utf-8")

        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)

        cmd = [
            self._ffmpeg_exe,
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{w}x{h}",
            "-pix_fmt",
            "rgb24",
            "-r",
            str(self.fps),
            "-i",
            "-",
            "-c:v",
            "libx264" if self.output_path.suffix.lower() == ".mp4" else "libvpx-vp9",
            "-pix_fmt",
            "yuv420p",
            str(self.output_path),
        ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_file,
            )
        except Exception as exc:
            self._stderr_file.close()
            logger.error(f"Failed to start FFmpeg subprocess ({type(exc).__name__}): {exc}")
            return False

        self.stop_event.clear()
        self._frame_count = 0
        self.thread = threading.Thread(target=self._record_loop, daemon=True)
        self.thread.start()
        logger.info(
            f"ContinuousRecorder started via FFmpeg subprocess "
            f"(streaming to {self.output_path} @ {self.fps} FPS)"
        )
        return True

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
                if self._container is not None:
                    self._encode_pyav_frame(img)
                    self._frame_count += 1
                elif self._proc and self._proc.stdin:
                    self._proc.stdin.write(img.tobytes())
                    self._frame_count += 1
            except (BrokenPipeError, OSError) as exc:
                logger.error(f"FFmpeg stdin pipe broken ({type(exc).__name__}): {exc}")
                break
            except Exception as exc:
                logger.debug(f"Screen capture frame skipped ({type(exc).__name__}): {exc}")

            elapsed = time.monotonic() - t0
            sleep_time = max(0.001, self.interval - elapsed)
            time.sleep(sleep_time)

    def stop(self, timeout: float = 5.0) -> int:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=timeout)

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

        if self._proc:
            try:
                if self._proc.stdin:
                    try:
                        self._proc.stdin.close()
                    except (OSError, BrokenPipeError):
                        pass
                self._proc.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, OSError, BrokenPipeError):
                try:
                    self._proc.kill()
                except Exception:
                    pass
            except Exception as exc:
                logger.debug(
                    f"ContinuousRecorder process cleanup warning ({type(exc).__name__}): {exc}"
                )
            finally:
                self._proc = None

        if self._stderr_file:
            try:
                self._stderr_file.close()
            except Exception:
                pass
            self._stderr_file = None

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
