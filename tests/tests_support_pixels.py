"""Counting horizontal bands of a colour in a screenshot.

Ten lines of arithmetic, kept in the test suite rather than in the library on
purpose: an API here would immediately attract a tolerance argument, then a
region argument, then perceptual hashing, and there is no natural stopping point.
The library encodes Windows knowledge that is hard to rediscover; this is not
that. See `docs/pitfalls.md`.
"""

from __future__ import annotations


def rows_containing_colour(image, rgb: tuple[int, int, int], tolerance: int) -> list[int]:
    """Row indices where at least one pixel is within `tolerance` of `rgb`."""
    pixels = image.convert("RGB").load()
    width, height = image.size
    target_r, target_g, target_b = rgb
    hits = []
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            if (
                abs(r - target_r) <= tolerance
                and abs(g - target_g) <= tolerance
                and abs(b - target_b) <= tolerance
            ):
                hits.append(y)
                break
    return hits


def count_colour_bands(
    image, rgb: tuple[int, int, int], tolerance: int, min_band_height: int = 4
) -> int:
    """How many runs of consecutive rows contain `rgb`.

    `min_band_height` discards single-row hits: a stray pixel of a similar colour
    in an icon or in anti-aliased text would otherwise count as a band. A line of
    text is ~16 rows tall, so 4 is well clear of both.
    """
    rows = rows_containing_colour(image, rgb, tolerance)
    if not rows:
        return 0
    bands = 0
    run_start = rows[0]
    previous = rows[0]
    for row in rows[1:]:
        if row != previous + 1:
            if previous - run_start + 1 >= min_band_height:
                bands += 1
            run_start = row
        previous = row
    if previous - run_start + 1 >= min_band_height:
        bands += 1
    return bands
