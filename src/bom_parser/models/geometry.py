"""Geometric primitives used across the parsing pipeline.

These are deliberately *not* Pydantic models — they are passed between
pipeline stages thousands of times per page and would pay an unnecessary
validation cost. Pydantic models live in `bom_parser.models.bom` and are
only constructed at the export boundary.

Coordinate system follows pdfplumber's convention:
    - origin at top-left of the page,
    - ``x`` increases to the right,
    - ``top`` / ``bottom`` increase downward (so ``bottom > top``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CanonicalColumn = Literal[
    "part_identifier",
    "description",
    "quantity",
    "uom",
    "mfg_name",
    "mfg_part",
    "commodity",
]

REQUIRED_CANONICAL_COLUMNS: tuple[CanonicalColumn, ...] = (
    "part_identifier",
    "description",
    "mfg_name",
    "mfg_part",
)


@dataclass(frozen=True, slots=True)
class Bbox:
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def x_center(self) -> float:
        return (self.x0 + self.x1) / 2.0


@dataclass(frozen=True, slots=True)
class Word:
    """A single token extracted from a PDF page with its geometry."""

    text: str
    bbox: Bbox
    page_index: int
    fontname: str | None = None
    size: float | None = None


@dataclass(frozen=True, slots=True)
class XSpan:
    """Horizontal extent of a column on a page."""

    x_min: float
    x_max: float

    def contains(self, x: float) -> bool:
        return self.x_min <= x <= self.x_max

    def overlaps_bbox(self, bbox: Bbox) -> bool:
        return bbox.x1 > self.x_min and bbox.x0 < self.x_max

    @property
    def width(self) -> float:
        return self.x_max - self.x_min


@dataclass(frozen=True, slots=True)
class PhysicalLine:
    """A group of words sharing approximately the same y-coordinate.

    Both Stage 2 (layout detection — where header lines live) and Stage 3
    (row assembly — where multi-line BoM records live) consume these.
    Words are stored sorted left-to-right by ``x0``.
    """

    words: tuple[Word, ...]
    y_top: float
    y_bottom: float

    @property
    def y_center(self) -> float:
        return (self.y_top + self.y_bottom) / 2.0


@dataclass(frozen=True, slots=True)
class PageLayout:
    """Result of Stage 2 (layout detection) for a single page."""

    page_index: int
    columns: dict[CanonicalColumn, XSpan]
    header_y_band: tuple[float, float]
    body_y_top: float
    body_y_bottom: float
    column_order: tuple[CanonicalColumn, ...] = field(default_factory=tuple)

    def column_rank(self, column: CanonicalColumn) -> int | None:
        """Return the left-to-right index of ``column`` among detected columns.

        Used by the adjacency invariant — ``Mfg Name`` and ``Mfg Part`` should
        sit at adjacent ranks in a well-formed BoM.
        """
        try:
            return self.column_order.index(column)
        except ValueError:
            return None
