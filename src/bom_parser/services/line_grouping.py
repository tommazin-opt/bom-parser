"""Group bbox-tagged words into physical lines.

A *physical line* is a y-axis cluster of words whose ``top`` coordinates
fall within a tolerance derived from the document's own line spacing.
Both Stage 2 (layout detection — header band identification) and Stage 3
(row assembly — multi-line BoM records) consume this primitive, so it
lives in its own sub-stage module rather than inside either consumer.

The tolerance is **data-driven**, not a magic number — per plan §Stage 3,
we measure the median spacing between successive distinct ``top`` values
on the page and multiply by ``DEFAULT_LINE_GROUPING_RATIO``. A sparse
page that yields no stable median falls back to a small fixed tolerance.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable

from bom_parser.models.geometry import PhysicalLine, Word
from bom_parser.utils.consts import (
    DEFAULT_LINE_GROUPING_RATIO,
    FALLBACK_LINE_TOLERANCE,
)


def group_into_physical_lines(
    words: Iterable[Word],
    *,
    tolerance_ratio: float = DEFAULT_LINE_GROUPING_RATIO,
    fallback_tolerance: float = FALLBACK_LINE_TOLERANCE,
) -> tuple[PhysicalLine, ...]:
    """Cluster ``words`` into physical lines using an adaptive y-tolerance.

    Lines are returned top-to-bottom; words within each line are sorted
    left-to-right. Empty input yields an empty tuple.
    """
    word_list = sorted(words, key=lambda w: (w.bbox.top, w.bbox.x0))
    if not word_list:
        return ()

    tolerance = _estimate_y_tolerance(
        word_list,
        tolerance_ratio=tolerance_ratio,
        fallback_tolerance=fallback_tolerance,
    )

    lines: list[PhysicalLine] = []
    current: list[Word] = [word_list[0]]
    current_top = word_list[0].bbox.top

    for word in word_list[1:]:
        if abs(word.bbox.top - current_top) <= tolerance:
            current.append(word)
        else:
            lines.append(_finalize_line(current))
            current = [word]
            current_top = word.bbox.top
    lines.append(_finalize_line(current))

    return tuple(lines)


def _finalize_line(words: list[Word]) -> PhysicalLine:
    sorted_words = tuple(sorted(words, key=lambda w: w.bbox.x0))
    y_top = min(w.bbox.top for w in sorted_words)
    y_bottom = max(w.bbox.bottom for w in sorted_words)
    return PhysicalLine(words=sorted_words, y_top=y_top, y_bottom=y_bottom)


def _estimate_y_tolerance(
    words: list[Word],
    *,
    tolerance_ratio: float,
    fallback_tolerance: float,
) -> float:
    """Return ``median_line_spacing * tolerance_ratio``, or the fallback."""
    tops = sorted({round(w.bbox.top, 2) for w in words})
    if len(tops) < 2:
        return fallback_tolerance
    diffs = [b - a for a, b in zip(tops, tops[1:]) if (b - a) > 0.5]
    if not diffs:
        return fallback_tolerance
    return statistics.median(diffs) * tolerance_ratio
