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

from bom_parser.models.geometry import (
    CanonicalColumn,
    PageLayout,
    PhysicalLine,
    Word,
    XSpan,
)
from bom_parser.models.ingestion import IngestedPage
from bom_parser.models.records import InProgressRecord, RawRecord, SupplierRow
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
    in_progress: InProgressRecord | None = None,
) -> tuple[tuple[RawRecord, ...], ParentTracker, InProgressRecord]:
    """Walk one page's words and emit ``RawRecord``s.

    Returns ``(records, parents_after_page, in_progress_after_page)``.
    The caller threads both ``parents`` and ``in_progress`` across pages.

    ``in_progress`` carries any record whose body is still open at the
    end of the previous page — that is, no next-record sentinel has
    been seen yet. Supplier rows or continuation lines appearing at the
    top of *this* page therefore attach to the prior-page record
    instead of being dropped.

    The trailing in-progress record at the end of the document is *not*
    finalised here — call :func:`finalize_in_progress` after the last
    page to emit it.
    """
    parents = parents or ParentTracker()
    in_progress = in_progress or InProgressRecord()
    lines = group_into_physical_lines(page.words)
    body_lines = tuple(ln for ln in lines if ln.y_top >= layout.body_y_top)

    records: list[RawRecord] = []
    current_start: PhysicalLine | None = in_progress.start
    current_continuation: list[PhysicalLine] = list(in_progress.continuation)
    current_suppliers: list[SupplierRow] = list(in_progress.suppliers)
    current_page_index: int = (
        in_progress.page_index if in_progress.is_active else page.page_index
    )
    current_layout: PageLayout = (
        in_progress.layout if in_progress.is_active and in_progress.layout is not None
        else layout
    )

    for line in body_lines:
        kind = _classify_line(line, layout, internal_pattern)
        if kind == "record_start":
            if current_start is not None:
                record, parents = _finalize_record(
                    start=current_start,
                    continuation=current_continuation,
                    suppliers=current_suppliers,
                    layout=current_layout,
                    parents=parents,
                )
                records.append(record)
            current_start = line
            current_continuation = []
            current_suppliers = []
            current_page_index = page.page_index
            current_layout = layout
        elif kind == "supplier_row" and current_start is not None:
            supplier = _supplier_row_from_line(line, layout, page.page_index)
            if supplier is not None:
                current_suppliers.append(supplier)
        elif current_start is not None:
            current_continuation.append(line)
        # else: pre-record-start body content (page-header repeat, …) — skip

    if current_start is not None:
        new_in_progress = InProgressRecord(
            start=current_start,
            continuation=tuple(current_continuation),
            suppliers=tuple(current_suppliers),
            page_index=current_page_index,
            layout=current_layout,
        )
    else:
        new_in_progress = InProgressRecord()

    return tuple(records), parents, new_in_progress


def finalize_in_progress(
    in_progress: InProgressRecord,
    parents: ParentTracker,
) -> tuple[tuple[RawRecord, ...], ParentTracker]:
    """Emit the document's final record after the last page has been processed.

    No-op when no record is in progress.
    """
    if not in_progress.is_active:
        return (), parents
    assert in_progress.start is not None  # for type narrowing
    assert in_progress.layout is not None
    record, parents = _finalize_record(
        start=in_progress.start,
        continuation=list(in_progress.continuation),
        suppliers=list(in_progress.suppliers),
        layout=in_progress.layout,
        parents=parents,
    )
    return (record,), parents


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
    """Column-bbox-aware classifier — a line is a supplier row iff it has:

    1. At least one word in the ``mfg_part`` x-band whose text matches
       the part-number shape (and is not date- or quantity-shaped).
    2. At least one non-marker word to the LEFT of that part word (the
       supplier name; may span multiple words and overflow leftward
       through the mfg_name band into the description band).
    3. No quantity-shaped token anywhere on the line (those indicate a
       description-data row, not a supplier row).

    Crucially this does *not* require the rightmost word to be the part
    number — the supplier-part column often carries a trailing qualifier
    (``"(24)"``, ``"36"``) after the actual part number. Picking the
    leftmost part-shaped word in the mfg_part band sidesteps that.
    """
    if not line.words:
        return False
    mfg_part_band = layout.columns.get("mfg_part")
    if mfg_part_band is None:
        return False

    part_word = _leftmost_part_shape_word_in_band(line.words, mfg_part_band)
    if part_word is None:
        return False

    name_words = [w for w in line.words if w.bbox.x0 < part_word.bbox.x0]
    if not name_words:
        return False
    if _DEPTH_MARKER.match(name_words[0].text) is not None:
        return False
    if any(_QUANTITY_SHAPE.match(w.text) for w in line.words):
        return False
    return True


def _leftmost_part_shape_word_in_band(
    words: tuple[Word, ...], band: XSpan
) -> Word | None:
    """Find the leftmost word that (a) sits in ``band`` and (b) looks like
    a supplier part number — passes the part-number shape and is not a
    date- or quantity-shaped token."""
    candidates: list[Word] = []
    for word in words:
        if not band.overlaps_bbox(word.bbox):
            continue
        if _PART_NUMBER_SHAPE.match(word.text) is None:
            continue
        if _DATE_SHAPE.match(word.text) is not None:
            continue
        if _QUANTITY_SHAPE.match(word.text) is not None:
            continue
        candidates.append(word)
    if not candidates:
        return None
    return min(candidates, key=lambda w: w.bbox.x0)


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
    """Build a ``SupplierRow`` using the same column-bbox logic as the classifier.

    The part is the leftmost part-shaped word in the ``mfg_part`` band
    (so a trailing qualifier like ``"(24)"`` or ``"36"`` next to the
    real part doesn't get picked up as the part). The name is every
    word to the *left* of that part word, joined with spaces — no
    column-based filtering, because such filters are template-specific
    and hurt generalisation to BoMs with different column layouts.
    """
    if not line.words:
        return None
    mfg_part_band = layout.columns.get("mfg_part")
    if mfg_part_band is None:
        return None
    part_word = _leftmost_part_shape_word_in_band(line.words, mfg_part_band)
    if part_word is None:
        return None

    name_words = tuple(w for w in line.words if w.bbox.x0 < part_word.bbox.x0)
    if not name_words:
        return None
    name_text = " ".join(w.text for w in name_words).strip()
    if not name_text:
        return None
    return SupplierRow(
        name_text=name_text,
        part_text=part_word.text,
        page_index=page_index,
        line_y=line.y_top,
    )


# ---- small surface area for typing / external use ---- ---------------------


_REQUIRED_COLUMNS_FOR_ASSEMBLY: tuple[CanonicalColumn, ...] = (
    "part_identifier",
    "mfg_part",
)


def can_assemble(layout: PageLayout) -> bool:
    """Whether ``layout`` carries the minimum columns Stage 3 needs."""
    return all(col in layout.columns for col in _REQUIRED_COLUMNS_FOR_ASSEMBLY)
