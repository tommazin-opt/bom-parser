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
    FOOTER_LINE_MARKERS,
    NAME_BAD_PUNCTUATION,
    PACKAGING_ANNOTATION_PATTERN,
    PART_NUMBER_SHAPE_PATTERN,
    QUANTITY_SHAPE_PATTERN,
    SUPPLIER_PART_GAP_RATIO,
)

_PART_NUMBER_SHAPE = regex.compile(PART_NUMBER_SHAPE_PATTERN)
_DEPTH_MARKER = regex.compile(DEPTH_MARKER_PATTERN)
_QUANTITY_SHAPE = regex.compile(QUANTITY_SHAPE_PATTERN)
_DATE_SHAPE = regex.compile(DATE_SHAPE_PATTERN)
_PACKAGING_ANNOTATION = regex.compile(
    PACKAGING_ANNOTATION_PATTERN, regex.IGNORECASE
)
# Leading part-number label that some vendors prepend to the mfg-part value
# ("PN:30M-BC-SS-10", "P/N: 1234"). The trailing colon is required so genuine
# MPNs that merely start with "PN" (e.g. "PN1234") are not stripped.
_PART_LABEL_PREFIX = regex.compile(r"^(?:P/?N|MPN|PART\s*#?)\s*:\s*", regex.IGNORECASE)


def _strip_part_label(text: str) -> str:
    """Remove a leading ``PN:`` / ``P/N:`` / ``MPN:`` label from a part token."""
    return _PART_LABEL_PREFIX.sub("", text, count=1)

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
    body_lines = tuple(
        ln
        for ln in lines
        if ln.y_top >= layout.body_y_top and not _is_footer_line(ln)
    )

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
        elif kind == "drawing_number":
            continue  # Drawing#-column spillover — neither description nor supplier
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


def _is_footer_line(line: PhysicalLine) -> bool:
    """Whether ``line`` is part of the repeating page-footer block.

    The footer ("* Current Alternate BOM Code / Bill of Materials -
    Explosion/Implosion Reports, BOMRPT.RPT Opti Temp Inc SSzot …") sits at the
    bottom of every page and would otherwise be folded into the last record's
    description. Matched on markers unique to the footer so real descriptions
    never trip it.
    """
    joined = " ".join(w.text for w in line.words)
    return any(marker in joined for marker in FOOTER_LINE_MARKERS)


def _classify_line(
    line: PhysicalLine,
    layout: PageLayout,
    internal_pattern: regex.Pattern[str],
) -> str:
    if _is_record_start(line, layout, internal_pattern):
        return "record_start"
    if _is_supplier_row(line, layout):
        return "supplier_row"
    if _is_drawing_number_line(line, layout):
        return "drawing_number"
    return "continuation"


def _is_drawing_number_line(line: PhysicalLine, layout: PageLayout) -> bool:
    """Whether ``line`` is a lone Drawing#-column value with no supplier name.

    The Drawing# column can wrap to the top of the next page ahead of its
    supplier row (FI000101: a bare ``30M-BC-SS-10`` precedes
    ``Servapure PN:30M-BC-SS-10``). Such a line has a part-shaped token in the
    ``mfg_part`` band but **nothing to its left** — distinguishing it from a
    supplier row (which has a name) and from a description continuation (whose
    words start far left). It must be dropped, not folded into the description.
    """
    if not line.words:
        return False
    if _line_has_commodity_token(line, layout):
        return False
    mfg_part_band = layout.columns.get("mfg_part")
    if mfg_part_band is None:
        return False
    part_word = _leftmost_part_shape_word_in_band(line.words, mfg_part_band)
    if part_word is None:
        return False
    name_words = [w for w in line.words if w.bbox.x0 < part_word.bbox.x0]
    return not name_words


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
    """Column-bbox-aware classifier — a line is a supplier row iff:

    1. There's a part-shaped word in the ``mfg_part`` x-band (not
       date / quantity / depth-marker shaped).
    2. There's at least one word to the LEFT of that part word (the
       supplier name).
    3. The supplier name's leftmost word lands within (or just before)
       the ``mfg_name`` column band — description continuations start
       at depth-marker x-position which is well to the left of
       mfg_name, so this check rejects them. Tolerance is half the
       mfg_name band's own width, keeping the threshold relative to
       the document's detected layout rather than absolute points.
    4. No quantity-shaped token anywhere on the line (those indicate
       a description-data row, not a supplier row).

    Picking the *leftmost* part-shaped word in mfg_part (rather than
    the rightmost) sidesteps trailing qualifiers like ``"(24)"`` or
    ``"36"`` that often sit next to the real part number.

    A line carrying a commodity-band token is the description's wrap line
    (desc-line-2 ends with the commodity, e.g. ``… Tooth Lock Washer FITT``).
    Its description words can spill into the mfg_part x-band and otherwise look
    like a supplier row — but it is never one. Forcing such lines to
    ``continuation`` keeps the commodity (so ``_extract_commodity`` finds it)
    and prevents a phantom vendor.
    """
    if not line.words:
        return False
    if _line_has_commodity_token(line, layout):
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
    if not _name_starts_in_mfg_name_band(name_words, layout):
        return False
    if not _name_has_no_description_chars(name_words):
        return False
    return True


def _name_has_no_description_chars(name_words: list[Word]) -> bool:
    """Reject lines whose supplier-name area carries description punctuation.

    Description continuations that start near the mfg_name x-position
    (and so pass the leftmost-x sanity check) still give themselves
    away by carrying chars that real supplier names don't use — commas
    between description phrases, double-quotes for dimensional notation
    (``1/4"``, ``8"LD``), etc. Real supplier names (``BD Sensors``,
    ``OMEGA``, ``McMaster-Carr``, ``Bond FluidAire``, even apostrophe-
    bearing ``O'Brien Industries``) avoid this set entirely.

    The bad-char set is the curated ``NAME_BAD_PUNCTUATION`` in consts —
    *not* the broader scoring set — so apostrophes survive.
    """
    for word in name_words:
        for ch in word.text:
            if ch in NAME_BAD_PUNCTUATION:
                return False
    return True


def _name_starts_in_mfg_name_band(
    name_words: list[Word], layout: PageLayout
) -> bool:
    """Reject lines whose leftmost word is far left of the mfg_name column.

    Description continuations whose wrapped text happens to put an
    alphanumeric token in the mfg_part band would otherwise be
    misclassified as supplier rows — but their leftmost word sits at
    the depth-marker x-position (well left of mfg_name).

    The tolerance is half the mfg_name band's own width so the check
    is relative to the document's detected layout (no hardcoded
    points). When mfg_name isn't detected on this page, skip the
    check rather than false-reject.
    """
    mfg_name_band = layout.columns.get("mfg_name")
    if mfg_name_band is None:
        return True
    leftmost_x = min(w.bbox.x0 for w in name_words)
    tolerance = mfg_name_band.width * 0.5
    return leftmost_x + tolerance >= mfg_name_band.x_min


def _leftmost_part_shape_word_in_band(
    words: tuple[Word, ...], band: XSpan
) -> Word | None:
    """Find the leftmost word that (a) sits in ``band`` and (b) looks like
    a supplier part number — passes the part-number shape and is not a
    date- or quantity-shaped token.

    Matching is done against the label-stripped text so ``PN:30M-BC-SS-10``
    is recognised as the part ``30M-BC-SS-10``.
    """
    candidates: list[Word] = []
    for word in words:
        if not band.overlaps_bbox(word.bbox):
            continue
        candidate_text = _strip_part_label(word.text)
        if _PART_NUMBER_SHAPE.match(candidate_text) is None:
            continue
        if _DATE_SHAPE.match(candidate_text) is not None:
            continue
        if _QUANTITY_SHAPE.match(candidate_text) is not None:
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

    description = _join_description(continuation, layout)
    quantity = _extract_quantity(continuation, layout)
    uom = _extract_uom(continuation, layout)
    commodity = _extract_commodity(continuation, layout)
    description = _strip_trailing_uom(description, uom, commodity)
    description_truncated = _detect_truncation(continuation)

    record = RawRecord(
        internal_part=internal_part,
        description=description,
        quantity=quantity,
        uom=uom,
        commodity=commodity,
        depth=depth,
        parent_internal_part=parent_internal,
        description_truncated=description_truncated,
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


def _join_description(
    continuation: list[PhysicalLine], layout: PageLayout
) -> str:
    """Concatenate description text across continuation lines, skipping noise.

    Bound the text to the description *body* region: words whose x-centre sits
    at or right of the ``quantity`` band's left edge belong to the quantity /
    UoM / flag (``U EA 0 N 0 AA A``) / commodity columns, not the description,
    and are excluded. (Column bands are header-derived and don't tightly fit
    the body, but the quantity band's left edge is a reliable right boundary for
    the description text on this template.) The dimensional ``X`` separator and
    the date token both fall left of that boundary; the date is dropped by its
    own shape filter, the ``X`` is kept.
    """
    quantity_band = layout.columns.get("quantity")
    pieces: list[str] = []
    prev_ran_into_date = False
    for line in continuation:
        date_token = next(
            (w for w in line.words if _DATE_SHAPE.match(w.text) is not None), None
        )
        kept: list[Word] = []
        for word in line.words:
            text = word.text
            if quantity_band is not None and word.bbox.x_center >= quantity_band.x_min:
                continue
            if _DEPTH_MARKER.match(text) is not None and not pieces and not kept:
                # Skip the leading depth marker on the first data line.
                continue
            if _QUANTITY_SHAPE.match(text):
                continue
            if _DATE_SHAPE.match(text):
                continue
            kept.append(word)
        for position, word in enumerate(kept):
            if (
                position == 0
                and pieces
                and prev_ran_into_date
                and len(pieces[-1]) >= 3
                and _is_wrap_fragment(word.text)
            ):
                # The previous line's text ran into the date column and this
                # line opens with a 1–2-char lowercase fragment: a word that
                # wrapped mid-token (``Filte`` | ``r`` -> ``Filter``). Rejoin
                # without a space. The ``>= 3`` head guard keeps this to genuine
                # word-wraps and off date-clipped single-letter stubs ("A" | "c").
                pieces[-1] = pieces[-1] + word.text
            else:
                pieces.append(word.text)
        prev_ran_into_date = bool(
            kept and date_token is not None and kept[-1].bbox.x1 > date_token.bbox.x0
        )
    return " ".join(pieces).strip()


def _is_wrap_fragment(text: str) -> bool:
    """A 1–2 character lowercase-alpha token — the tail of a mid-wrapped word."""
    return 1 <= len(text) <= 2 and text.isalpha() and text.islower()


def _detect_truncation(continuation: list[PhysicalLine]) -> bool:
    """Flag descriptions clipped by the overprinted effectivity date.

    On this template the date is drawn *on top of* the description baseline; if
    the description text runs into the date column, the date overprints (clips)
    its tail, and those characters are not in the text layer (e.g. "Populated"
    survives only as "Po"). Detection is geometric and makes no attempt to
    recover the lost text: on a description line, take the rightmost non-date
    token that x-overlaps the (stripped) date token; if its left edge sits at or
    right of the date column's start — within ~2 character widths — the visible
    text *begins inside* the date column, the signature of a clipped tail. A
    complete word whose body sits left of the date column (its tail merely
    overdrawn) is not flagged.
    """
    for line in continuation:
        dates = [w for w in line.words if _DATE_SHAPE.match(w.text) is not None]
        for date in dates:
            char_width = (date.bbox.x1 - date.bbox.x0) / max(len(date.text), 1)
            tolerance = 2.0 * char_width
            overlapping = [
                w
                for w in line.words
                if _DATE_SHAPE.match(w.text) is None
                and w.bbox.x0 < date.bbox.x1
                and w.bbox.x1 > date.bbox.x0
            ]
            if not overlapping:
                continue
            rightmost = max(overlapping, key=lambda w: w.bbox.x1)
            if rightmost.bbox.x0 >= date.bbox.x0 - tolerance:
                return True
    return False


def _strip_trailing_uom(
    description: str, uom: str | None, commodity: str | None
) -> str:
    """Drop a duplicated unit-of-measure token left at the description's end.

    On label rows the desc-line-2 layout is ``<dims>, VINYL, EA HDWARE`` — a
    stray ``EA`` sits in the description x-band just before the commodity,
    distinct from the real UoM column and the (correctly parsed) commodity.
    We strip it only when it is a UoM-set token that *equals the record's own
    parsed UoM* and a commodity is present, so genuine trailing text (a "5 FT"
    dimension, or ``add EL000514``) is never touched.
    """
    if commodity is None or uom is None or not description:
        return description
    tokens = description.split()
    last = tokens[-1]
    if last == uom and _UOM_TOKEN.match(last) is not None:
        return " ".join(tokens[:-1]).strip()
    return description


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


def _is_commodity_token(word: Word, band: XSpan) -> bool:
    """An uppercase, alphabetic token of commodity length sitting in ``band``."""
    if not word.text.isalpha() or not word.text.isupper():
        return False
    if len(word.text) < DEFAULT_MIN_COMMODITY_LENGTH:
        return False
    return band.overlaps_bbox(word.bbox)


def _line_has_commodity_token(line: PhysicalLine, layout: PageLayout) -> bool:
    """Whether ``line`` carries a commodity-band token (its desc-line-2 marker)."""
    band = layout.columns.get("commodity")
    if band is None:
        return False
    return any(_is_commodity_token(w, band) for w in line.words)


def _extract_commodity(
    continuation: list[PhysicalLine], layout: PageLayout
) -> str | None:
    """Rightmost uppercase token whose x-centre falls in the commodity band."""
    band = layout.columns.get("commodity")
    if band is None:
        return None
    found: list[Word] = [
        word
        for line in continuation
        for word in line.words
        if _is_commodity_token(word, band)
    ]
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

    The part text spans every word from the leftmost part-shaped word
    in ``mfg_part`` rightward, stopping at the first horizontal gap
    large enough to indicate the next column. This captures multi-
    token parts like ``"1010 X 36"`` or ``"DMP 331-110-P001-4-5-TAO-"``
    without committing to a hardcoded right edge for the column.

    The name is every word to the LEFT of that leftmost part-shape
    word, joined with spaces.
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

    part_text = _collect_part_text(line, part_word, mfg_part_band)
    if not part_text:
        return None

    return SupplierRow(
        name_text=name_text,
        part_text=part_text,
        page_index=page_index,
        line_y=line.y_top,
    )


def _collect_part_text(
    line: PhysicalLine,
    part_word: Word,
    mfg_part_band: XSpan,
) -> str:
    """Walk rightward from ``part_word``, collecting words until a
    large horizontal gap signals the next column.

    Intra-part gaps between sub-tokens are tight (a few points), while
    the gap between the part column and the next rightward column
    (commodity, etc.) is much wider. The threshold is
    ``mfg_part_band.width * SUPPLIER_PART_GAP_RATIO`` (default 0.3)
    so the limit scales with the document's own column sizing — no
    hardcoded points, generalises across BoM templates.

    The walk also drops a *trailing packaging/quantity* parenthetical —
    ``(PACK OF 5)`` in ``53525K17 (PACK OF 5)`` — which is a note, not part of
    the MPN; left in, it yields a noisy multi-word part the scorer rejects,
    dropping an otherwise-valid supplier row. Other parentheticals are kept:
    length suffixes like ``(36)`` / ``(24)`` on extrusion part numbers
    (``1010-S (36)``) are meaningful and preserved.
    """
    max_gap = mfg_part_band.width * SUPPLIER_PART_GAP_RATIO
    rightward = sorted(
        (w for w in line.words if w.bbox.x0 >= part_word.bbox.x0),
        key=lambda w: w.bbox.x0,
    )
    collected: list[Word] = []
    prev_x1: float | None = None
    index = 0
    while index < len(rightward):
        word = rightward[index]
        if prev_x1 is not None and (word.bbox.x0 - prev_x1) > max_gap:
            break
        if word.text.startswith("("):
            group = _parenthetical_group(rightward, index)
            group_text = " ".join(w.text for w in group)
            if _PACKAGING_ANNOTATION.match(group_text) is not None:
                break  # drop the packaging note and everything after it
            collected.extend(group)
            prev_x1 = group[-1].bbox.x1
            index += len(group)
            continue
        collected.append(word)
        prev_x1 = word.bbox.x1
        index += 1
    return _strip_part_label(" ".join(w.text for w in collected).strip())


def _parenthetical_group(words: list[Word], start: int) -> list[Word]:
    """Return the run of words from the ``(`` at ``start`` through its ``)``.

    If the parenthetical never closes, returns the rest of the words — the
    caller still classifies that whole tail.
    """
    end = start
    while end < len(words) and not words[end].text.endswith(")"):
        end += 1
    return words[start : min(end + 1, len(words))]


# ---- small surface area for typing / external use ---- ---------------------


_REQUIRED_COLUMNS_FOR_ASSEMBLY: tuple[CanonicalColumn, ...] = (
    "part_identifier",
    "mfg_part",
)


def can_assemble(layout: PageLayout) -> bool:
    """Whether ``layout`` carries the minimum columns Stage 3 needs."""
    return all(col in layout.columns for col in _REQUIRED_COLUMNS_FOR_ASSEMBLY)
