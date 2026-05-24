"""Stage 3 — group page words into logical BoM records.

A single BoM record spans several physical lines (plan §Stage 3):

    LINE A: <internal part id>               ← record-start sentinel
    LINE B: <depth marker> <description …> <date> <quantity> <flags>
    LINE C: <description continuation …> <commodity>
    LINE D: <mfg name …> <mfg part>          ← supplier row (0..N)
    LINE A': <next internal part id>         ← next record begins

We classify each physical line by *content shape* rather than pure x-band
membership: the reference BoMs interleave a main-record grid and a
supplier-sub-row grid with overlapping x-ranges (see plan §Stage 2
"Column boundaries — midpoint-based primary"), so column membership
alone is ambiguous.

A line is:

* ``record_start``   — a single token matching the discovered internal
                       pattern in (or near) the part_identifier band.
* ``supplier_row``   — its rightmost token sits in the mfg_part band
                       and matches the part-number shape, with one or
                       more non-marker tokens to its left.
* otherwise          — description / data continuation belonging to the
                       current record.

Hierarchy (``parent_internal_part``) is resolved by a stack-based parent
tracker driven by the dot-level depth marker on the description-data
line of each record.
"""

from __future__ import annotations

from dataclasses import dataclass

import regex

from bom_parser.models.geometry import CanonicalColumn, PageLayout, PhysicalLine, Word
from bom_parser.models.ingestion import IngestedPage
from bom_parser.models.records import RawRecord, SupplierRow
from bom_parser.services.line_grouping import group_into_physical_lines
from bom_parser.utils.consts import (
    DATE_SHAPE_PATTERN,
    DEFAULT_MIN_COMMODITY_LENGTH,
    DEPTH_MARKER_PATTERN,
    PART_NUMBER_SHAPE_PATTERN,
    QUANTITY_SHAPE_PATTERN,
)

_PART_NUMBER_SHAPE = regex.compile(PART_NUMBER_SHAPE_PATTERN)
_DEPTH_MARKER = regex.compile(DEPTH_MARKER_PATTERN)
_QUANTITY_SHAPE = regex.compile(QUANTITY_SHAPE_PATTERN)
_DATE_SHAPE = regex.compile(DATE_SHAPE_PATTERN)

# Single-letter UoM codes the reference BoMs use ("U", "EA", "FT").
# Not in consts.py because they're a *recognition* heuristic specific to
# row assembly, not a tunable.
_UOM_TOKEN = regex.compile(r"^(?:U|EA|FT|LB|KG|M|MM|CM|IN|PC|RL|BX|BG)$")


@dataclass(frozen=True, slots=True)
class ParentTracker:
    """Stack tracking ``(depth, internal_part)`` to resolve parent links."""

    stack: tuple[tuple[int, str], ...] = ()

    def parent_of(self, depth: int) -> str | None:
        for d, internal_part in reversed(self.stack):
            if d < depth:
                return internal_part
        return None

    def push(self, depth: int, internal_part: str) -> ParentTracker:
        trimmed = tuple(s for s in self.stack if s[0] < depth)
        return ParentTracker(stack=(*trimmed, (depth, internal_part)))


def assemble_records(
    page: IngestedPage,
    layout: PageLayout,
    internal_pattern: regex.Pattern[str],
    *,
    parents: ParentTracker | None = None,
) -> tuple[tuple[RawRecord, ...], ParentTracker]:
    """Walk one page's words and emit ``RawRecord``s.

    Returns ``(records, parents_after_page)``. The caller threads the
    tracker across pages so a parent chain established on page N is
    still visible to records on page N+1.
    """
    parents = parents or ParentTracker()
    lines = group_into_physical_lines(page.words)
    body_lines = tuple(ln for ln in lines if ln.y_top >= layout.body_y_top)

    records: list[RawRecord] = []
    current_start: PhysicalLine | None = None
    current_continuation: list[PhysicalLine] = []
    current_suppliers: list[SupplierRow] = []

    for line in body_lines:
        kind = _classify_line(line, layout, internal_pattern)
        if kind == "record_start":
            if current_start is not None:
                record, parents = _finalize_record(
                    start=current_start,
                    continuation=current_continuation,
                    suppliers=current_suppliers,
                    layout=layout,
                    parents=parents,
                )
                records.append(record)
            current_start = line
            current_continuation = []
            current_suppliers = []
        elif kind == "supplier_row" and current_start is not None:
            supplier = _supplier_row_from_line(line, layout, page.page_index)
            if supplier is not None:
                current_suppliers.append(supplier)
        elif current_start is not None:
            current_continuation.append(line)
        # else: pre-record-start body content (page-header repeat, …) — skip

    if current_start is not None:
        record, parents = _finalize_record(
            start=current_start,
            continuation=current_continuation,
            suppliers=current_suppliers,
            layout=layout,
            parents=parents,
        )
        records.append(record)

    return tuple(records), parents


# ---- line classification ---------------------------------------------------


def _classify_line(
    line: PhysicalLine,
    layout: PageLayout,
    internal_pattern: regex.Pattern[str],
) -> str:
    if _is_record_start(line, layout, internal_pattern):
        return "record_start"
    if _is_supplier_row(line, layout):
        return "supplier_row"
    return "continuation"


def _is_record_start(
    line: PhysicalLine,
    layout: PageLayout,
    internal_pattern: regex.Pattern[str],
) -> bool:
    """Single token matching the internal pattern, sitting near part_identifier."""
    if len(line.words) != 1:
        return False
    word = line.words[0]
    if internal_pattern.match(word.text) is None:
        return False
    band = layout.columns.get("part_identifier")
    if band is None:
        return True  # no band ⇒ trust the pattern alone
    return band.overlaps_bbox(word.bbox)


def _is_supplier_row(line: PhysicalLine, layout: PageLayout) -> bool:
    """Rightmost token sits in mfg_part and matches the part-number shape."""
    if not line.words:
        return False
    mfg_part_band = layout.columns.get("mfg_part")
    if mfg_part_band is None:
        return False
    rightmost = line.words[-1]
    if not mfg_part_band.overlaps_bbox(rightmost.bbox):
        return False
    if _PART_NUMBER_SHAPE.match(rightmost.text) is None:
        return False
    # Must have non-depth-marker content to the left to be a real supplier row
    leftward = line.words[:-1]
    if not leftward:
        return False
    if _DEPTH_MARKER.match(leftward[0].text) is not None:
        return False
    # And the line must not contain a quantity-shaped token (those are
    # description-data rows, even if their rightmost word coincidentally
    # lands in the mfg_part band).
    if any(_QUANTITY_SHAPE.match(w.text) for w in line.words):
        return False
    return True


# ---- record finalisation ---------------------------------------------------


def _finalize_record(
    *,
    start: PhysicalLine,
    continuation: list[PhysicalLine],
    suppliers: list[SupplierRow],
    layout: PageLayout,
    parents: ParentTracker,
) -> tuple[RawRecord, ParentTracker]:
    internal_part = start.words[0].text
    page_index = start.words[0].page_index

    depth = _extract_depth(continuation)
    parent_internal = parents.parent_of(depth)
    new_parents = parents.push(depth, internal_part)

    description = _join_description(continuation)
    quantity = _extract_quantity(continuation, layout)
    uom = _extract_uom(continuation, layout)
    commodity = _extract_commodity(continuation, layout)

    record = RawRecord(
        internal_part=internal_part,
        description=description,
        quantity=quantity,
        uom=uom,
        commodity=commodity,
        depth=depth,
        parent_internal_part=parent_internal,
        suppliers=tuple(suppliers),
        page_index=page_index,
        line_y=start.y_top,
    )
    return record, new_parents


def _extract_depth(continuation: list[PhysicalLine]) -> int:
    """Read the leading depth marker (``.2``, ``..3`` …) from the data line."""
    for line in continuation:
        if not line.words:
            continue
        match = _DEPTH_MARKER.match(line.words[0].text)
        if match is not None:
            return int(match.group(1))
    return 0  # root level (no marker)


def _join_description(continuation: list[PhysicalLine]) -> str:
    """Concatenate description text across continuation lines, skipping noise."""
    pieces: list[str] = []
    for line in continuation:
        for word in line.words:
            text = word.text
            if _DEPTH_MARKER.match(text) is not None and not pieces:
                # Skip the leading depth marker on the first data line.
                continue
            if _QUANTITY_SHAPE.match(text):
                continue
            if _DATE_SHAPE.match(text):
                continue
            pieces.append(text)
    return " ".join(pieces).strip()


def _extract_quantity(
    continuation: list[PhysicalLine], layout: PageLayout
) -> float | None:
    band = layout.columns.get("quantity")
    for line in continuation:
        for word in line.words:
            if _QUANTITY_SHAPE.match(word.text) and (
                band is None or band.overlaps_bbox(word.bbox)
            ):
                try:
                    return float(word.text)
                except ValueError:
                    continue
    return None


def _extract_uom(
    continuation: list[PhysicalLine], layout: PageLayout
) -> str | None:
    """Look for a known UoM token (``U``, ``EA``, ``FT`` …) in the uom band.

    Returns the first one found, which on the reference BoMs is ``EA``
    (eaches) for most parts. Single ``U`` codes are skipped — they're
    the QC flag, not the UoM, and pdfplumber sometimes places them
    adjacent in the data row.
    """
    band = layout.columns.get("uom")
    candidates: list[str] = []
    for line in continuation:
        for word in line.words:
            if _UOM_TOKEN.match(word.text) is None:
                continue
            if band is not None and not band.overlaps_bbox(word.bbox):
                continue
            candidates.append(word.text)
    # Prefer multi-character UoM tokens over the single-letter ones
    multi = [t for t in candidates if len(t) > 1]
    if multi:
        return multi[0]
    return candidates[0] if candidates else None


def _extract_commodity(
    continuation: list[PhysicalLine], layout: PageLayout
) -> str | None:
    """Rightmost uppercase token whose x-centre falls in the commodity band."""
    band = layout.columns.get("commodity")
    if band is None:
        return None
    found: list[Word] = []
    for line in continuation:
        for word in line.words:
            if not word.text.isalpha() or not word.text.isupper():
                continue
            if len(word.text) < DEFAULT_MIN_COMMODITY_LENGTH:
                continue
            if band.overlaps_bbox(word.bbox):
                found.append(word)
    if not found:
        return None
    # Pick the rightmost qualifying token — the BoM template prints the
    # commodity code at the far right of the continuation row.
    return max(found, key=lambda w: w.bbox.x1).text


# ---- supplier-row extraction ----------------------------------------------


def _supplier_row_from_line(
    line: PhysicalLine,
    layout: PageLayout,
    page_index: int,
) -> SupplierRow | None:
    """Build a ``SupplierRow`` from a supplier-classified line.

    The rightmost token is the supplier part. Everything to its left
    (joined with spaces, with column-band-irrelevant words filtered) is
    the supplier name.
    """
    if not line.words:
        return None
    part_word = line.words[-1]
    name_words = line.words[:-1]
    if not name_words:
        return None
    name_text = _join_supplier_name(name_words, layout)
    if not name_text:
        return None
    return SupplierRow(
        name_text=name_text,
        part_text=part_word.text,
        page_index=page_index,
        line_y=line.y_top,
    )


def _join_supplier_name(words: tuple[Word, ...], layout: PageLayout) -> str:
    """Join words intended to be part of the supplier name.

    Drops any word that lands in the commodity column (in this BoM, a
    commodity code sometimes trails on supplier rows when the description
    word stream overruns).
    """
    commodity_band = layout.columns.get("commodity")
    kept: list[str] = []
    for w in words:
        if commodity_band is not None and commodity_band.overlaps_bbox(w.bbox):
            continue
        kept.append(w.text)
    return " ".join(kept).strip()


# ---- small surface area for typing / external use ---- ---------------------


_REQUIRED_COLUMNS_FOR_ASSEMBLY: tuple[CanonicalColumn, ...] = (
    "part_identifier",
    "mfg_part",
)


def can_assemble(layout: PageLayout) -> bool:
    """Whether ``layout`` carries the minimum columns Stage 3 needs."""
    return all(col in layout.columns for col in _REQUIRED_COLUMNS_FOR_ASSEMBLY)
