"""A caption drawn into recorded frames, for saying what is being tested.

A recording of a suite shows a series of applications being driven and gives no
indication of which test is driving them. Scrubbing to the moment a particular
test failed means counting windows and guessing, and the moment worth watching is
usually the one nobody can find.

The caption sits in the bottom-left corner, away from the keyboard HUD's
bottom-centre strip and from the pointer, and is composited after the screen grab
like the rest of the overlays: nothing on screen can cover it, including a topmost
window that would draw over an overlay window carrying `WS_EX_NOACTIVATE`.

The recorder owns the current text and a caller sets it. That keeps the knowledge
of *what the tests are called* out of this library: pytest, unittest and a plain
script all name their work differently, and none of that belongs here.
"""

from __future__ import annotations

import logging

from wintegrate.exceptions import DiagnosticPipelineError

logger = logging.getLogger(__name__)

#: Bottom-left, with the same margin the keyboard HUD uses for the bottom edge.
DEFAULT_MARGIN_X = 16
DEFAULT_MARGIN_Y = 12

#: Text size. Large enough to read in a browser-scaled recording, small enough that
#: a long test id does not become the recording.
DEFAULT_TEXT_SIZE = 16

#: How much of the frame's width the caption may take before its text is trimmed.
MAX_WIDTH_FRACTION = 0.55

_PANEL_FILL = (16, 18, 24, 215)
_TEXT_FILL = (255, 255, 255, 255)
_ACCENT_FILL = (120, 200, 255, 255)
_PADDING_X = 10
_PADDING_Y = 6
_CORNER_RADIUS = 6

_PILLOW_HINT = (
    "Captions need Pillow, which ships in the optional 'video' extra: "
    "pip install 'wintegrate[video]'"
)


def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise DiagnosticPipelineError(_PILLOW_HINT) from exc
    return Image, ImageDraw, ImageFont


def _font(size: int):
    """A clean TrueType face if the system has one, else Pillow's default."""
    _, _, ImageFont = _pil()
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(text: str, font, draw, limit: int) -> str:
    """Trims `text` from the left until it fits, keeping the tail.

    The tail, not the head: a test id like
    `test_powerocr_capture_modes.py::test_table_mode_returns_tab_separated_rows`
    is all suffix. Truncating the front loses the directory and keeps the name,
    which is the part a viewer is looking for.
    """
    if draw.textlength(text, font=font) <= limit:
        return text
    ellipsis = "…"
    trimmed = text
    while trimmed and draw.textlength(ellipsis + trimmed, font=font) > limit:
        trimmed = trimmed[1:]
    return ellipsis + trimmed if trimmed else ellipsis


def draw_caption(
    image,
    text: str,
    *,
    subtitle: str | None = None,
    margin_x: int = DEFAULT_MARGIN_X,
    margin_y: int = DEFAULT_MARGIN_Y,
    text_size: int = DEFAULT_TEXT_SIZE,
):
    """Draws `text` (and an optional `subtitle`) into the frame's bottom-left corner.

    Returns the image, modified in place. An empty `text` draws nothing, so a
    recorder can carry a caption that is only set for part of a run.
    """
    if not text or not text.strip():
        return image

    Image, ImageDraw, _ = _pil()
    font = _font(text_size)
    small = _font(max(10, text_size - 3))

    # Measured against a throwaway canvas: textlength needs a draw context, and the
    # panel cannot be sized before the text has been trimmed to fit inside it.
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    limit = int(image.width * MAX_WIDTH_FRACTION) - 2 * _PADDING_X
    line = _fit(text.strip(), font, probe, limit)
    second = _fit(subtitle.strip(), small, probe, limit) if subtitle and subtitle.strip() else None

    line_h = int(probe.textbbox((0, 0), line, font=font)[3])
    second_h = int(probe.textbbox((0, 0), second, font=small)[3]) if second else 0
    gap = 3 if second else 0
    width = (
        int(
            max(
                probe.textlength(line, font=font),
                probe.textlength(second, font=small) if second else 0,
            )
        )
        + 2 * _PADDING_X
    )
    height = line_h + gap + second_h + 2 * _PADDING_Y

    left = margin_x
    top = image.height - margin_y - height
    if top < 0 or left + width > image.width:
        logger.debug("caption does not fit the frame; skipped")
        return image

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(panel)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), _CORNER_RADIUS, fill=_PANEL_FILL)
    draw.text((_PADDING_X, _PADDING_Y), line, font=font, fill=_TEXT_FILL)
    if second:
        draw.text((_PADDING_X, _PADDING_Y + line_h + gap), second, font=small, fill=_ACCENT_FILL)

    patch = image.crop((left, top, left + width, top + height)).convert("RGBA")
    patch.alpha_composite(panel)
    image.paste(patch.convert(image.mode), (left, top))
    return image
