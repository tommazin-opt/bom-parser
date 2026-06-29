"""Stage 7 — build the final ``BomDocument`` from assembled records.

Responsibilities (plan §Stage 7):

* Run Stage 5 scoring on every supplier-part candidate, dropping
  hard-rejects and below-threshold candidates (collecting hard-rejects
  for ``ParseMetadata``).
* Run Stage 6 normalisation on every accepted candidate's supplier
  name.
* Group ``RawRecord``s by their *cleaned* description into ``Part``s.
  Multiple BoM occurrences of the same sub-component (``LB000200``
  appearing under ``LB000300/301/302/303``) collapse into a single
  ``Part`` with summed ``total_quantity`` and a per-occurrence breakdown
  for downstream traceability.
* Deduplicate suppliers inside each group by
  ``(name_normalized, part_number)``, retaining the highest confidence
  seen.
* Assemble ``ParseMetadata`` (page count, discovered internal pattern,
  new supplier candidates, hard-rejected tokens, deduped warnings) and
  emit the top-level ``BomDocument``.
"""

from __future__ import annotations

import regex

from bom_parser import __version__ as PARSER_VERSION
from bom_parser.models.bom import (
    BomDocument,
    HardRejectedCandidate,
    Occurrence,
    ParseMetadata,
    ParseWarning,
    Part,
    Supplier,
)
from bom_parser.models.ingestion import IngestedDocument
from bom_parser.models.internal_pattern import InternalPatternDiscovery
from bom_parser.models.records import RawRecord, SupplierRow
from bom_parser.models.scoring import HeuristicWeights
from bom_parser.services.heuristic_scorer import score_part_number
from bom_parser.services.supplier_normalizer import SupplierNormalizer
from bom_parser.services.tree_builder import build_part_tree


def build_bom_document(
    records: tuple[RawRecord, ...],
    *,
    ingestion: IngestedDocument,
    discovery: InternalPatternDiscovery,
    layout_warnings: tuple[ParseWarning, ...],
    weights: HeuristicWeights,
    supplier_aliases: dict[str, list[str]],
) -> BomDocument:
    """Convert assembled raw records into the final ``BomDocument``."""
    normalizer = SupplierNormalizer(supplier_aliases)
    hard_rejected: list[HardRejectedCandidate] = []

    # Resolve each record's document-order instance and its parent *instance*
    # (BUG 5/6). ``records`` is already in document order; a depth stack with
    # the same ``< depth`` rule as ParentTracker yields, for each record, the
    # index of the nearest shallower preceding record — the specific parent
    # copy, even when a subassembly is re-exploded under several parents.
    occurrence_ids, parent_occurrence_ids = _resolve_instance_ids(records)

    # First pass: group records by cleaned-description key.
    groups: dict[str, list[RawRecord]] = {}
    cleaned_descriptions: dict[str, str] = {}
    for record in records:
        cleaned = _clean_description(record.description, record.commodity)
        key = _description_key(cleaned)
        if not key:
            continue  # skip records whose cleaned description is empty
        groups.setdefault(key, []).append(record)
        cleaned_descriptions.setdefault(key, cleaned)

    parts: list[Part] = []
    for key, group_records in groups.items():
        part = _build_part(
            description=cleaned_descriptions[key],
            records=group_records,
            normalizer=normalizer,
            internal_pattern=discovery.pattern,
            weights=weights,
            hard_rejected=hard_rejected,
            occurrence_ids=occurrence_ids,
            parent_occurrence_ids=parent_occurrence_ids,
        )
        parts.append(part)

    metadata = ParseMetadata(
        source_file=ingestion.source_path.name,
        parser_version=PARSER_VERSION,
        page_count=ingestion.page_count,
        discovered_internal_pattern=discovery.pattern_source,
        new_supplier_candidates=list(normalizer.new_supplier_candidates),
        hard_rejected_candidates=hard_rejected,
        warnings=_dedupe_warnings(
            (*ingestion.warnings, *layout_warnings, *discovery.warnings)
        ),
    )

    # Restructure the flat grouped parts into the nested explosion tree that
    # BomDocument serializes (parsing logic above is unchanged).
    return BomDocument(metadata=metadata, parts=build_part_tree(parts))


# ---- per-part assembly -----------------------------------------------------


def _resolve_instance_ids(
    records: tuple[RawRecord, ...],
) -> tuple[dict[int, int], dict[int, int | None]]:
    """Map each record (by identity) to its document-order occurrence id and
    its parent record's occurrence id.

    Mirrors ``ParentTracker``'s depth logic but keeps the parent *instance*
    index rather than just the part number, so re-exploded subassemblies are
    disambiguated. Keyed by ``id(record)`` — every record object is distinct and
    held alive by ``records`` for the duration of the export.
    """
    occurrence_ids: dict[int, int] = {}
    parent_occurrence_ids: dict[int, int | None] = {}
    stack: list[tuple[int, int]] = []  # (depth, occurrence_id)
    for index, record in enumerate(records):
        while stack and stack[-1][0] >= record.depth:
            stack.pop()
        parent_occurrence_ids[id(record)] = stack[-1][1] if stack else None
        occurrence_ids[id(record)] = index
        stack.append((record.depth, index))
    return occurrence_ids, parent_occurrence_ids


def _build_part(
    *,
    description: str,
    records: list[RawRecord],
    normalizer: SupplierNormalizer,
    internal_pattern: regex.Pattern[str],
    weights: HeuristicWeights,
    hard_rejected: list[HardRejectedCandidate],
    occurrence_ids: dict[int, int],
    parent_occurrence_ids: dict[int, int | None],
) -> Part:
    # Occurrence-sum (not parent-multiplied) — see Part.total_quantity docstring.
    total_quantity = sum(r.quantity or 0.0 for r in records)
    uom = next((r.uom for r in records if r.uom), None)
    commodity = next((r.commodity for r in records if r.commodity), None)
    description_truncated = any(r.description_truncated for r in records)

    internal_parts = list(
        dict.fromkeys(r.internal_part for r in records)
    )  # preserves order, dedupes
    occurrences = [
        Occurrence(
            internal_author_part=r.internal_part,
            quantity=r.quantity or 0.0,
            parent_internal_part=r.parent_internal_part,
            occurrence_id=occurrence_ids[id(r)],
            parent_occurrence_id=parent_occurrence_ids[id(r)],
        )
        for r in records
    ]

    # Dedupe suppliers by (name_normalized, part_number), keeping max
    # confidence. Per plan §Stage 7 step 4.
    supplier_index: dict[tuple[str, str], Supplier] = {}
    for record in records:
        for row_position, row in enumerate(record.suppliers):
            supplier = _scored_supplier(
                row=row,
                normalizer=normalizer,
                internal_pattern=internal_pattern,
                weights=weights,
                row_position=row_position,
                hard_rejected=hard_rejected,
            )
            if supplier is None:
                continue
            key = (supplier.name_normalized, supplier.part_number)
            existing = supplier_index.get(key)
            if existing is None or supplier.confidence_score > existing.confidence_score:
                supplier_index[key] = supplier
    suppliers = list(supplier_index.values())

    return Part(
        description=description,
        total_quantity=total_quantity,
        uom=uom,
        commodity=commodity,
        internal_author_parts=internal_parts,
        occurrences=occurrences,
        suppliers=suppliers,
        description_truncated=description_truncated,
    )


def _scored_supplier(
    *,
    row: SupplierRow,
    normalizer: SupplierNormalizer,
    internal_pattern: regex.Pattern[str],
    weights: HeuristicWeights,
    row_position: int,
    hard_rejected: list[HardRejectedCandidate],
) -> Supplier | None:
    """Score, normalise, and (if accepted) build a ``Supplier``.

    Hard-rejected tokens (date-shaped, quantity-shaped) are recorded in
    ``hard_rejected`` so the operator sees them in
    ``ParseMetadata.hard_rejected_candidates``. Below-confidence drops
    are silent — they're noise by definition.
    """
    verdict = score_part_number(
        row.part_text,
        internal_pattern=internal_pattern,
        weights=weights,
    )
    if verdict.rejection_reason in ("date_shaped", "quantity_shaped"):
        hard_rejected.append(
            HardRejectedCandidate(
                token=row.part_text,
                reason=verdict.rejection_reason,
                page=row.page_index,
                row=row_position,
            )
        )
        return None
    if not verdict.is_accepted:
        return None

    canonical_name = normalizer.normalize(row.name_text)
    return Supplier(
        name_raw=row.name_text,
        name_normalized=canonical_name,
        part_number=row.part_text,
        confidence_score=verdict.confidence,
        is_internal_author_part=verdict.is_internal_author_part,
    )


# ---- description cleaning + key derivation ---------------------------------


def _clean_description(raw: str, commodity: str | None) -> str:
    """Normalise whitespace and drop a residual commodity token.

    Row assembly now bounds the description to the body region by x-position,
    so depth markers, quantities, dates, the flag columns ("U EA 0 N 0 AA A"),
    and the commodity are already excluded. We deliberately do **not** strip
    short uppercase tokens here: the dimensional separator ``X`` in
    ``1.35" X 2.75"`` is a legitimate description token, not a flag. The
    commodity removal below is a belt-and-suspenders no-op on cleanly bounded
    descriptions.
    """
    pieces: list[str] = []
    for token in raw.split():
        if commodity is not None and token == commodity:
            continue
        pieces.append(token)
    return " ".join(pieces).strip()


def _description_key(description: str) -> str:
    """Stable group-by key: lowercased, whitespace-collapsed description."""
    return " ".join(description.lower().split())


# ---- warning deduplication -------------------------------------------------


def _dedupe_warnings(
    warnings: tuple[ParseWarning, ...],
) -> list[ParseWarning]:
    """Keep at most one warning per ``code`` (the first occurrence)."""
    seen: set[str] = set()
    deduped: list[ParseWarning] = []
    for warning in warnings:
        if warning.code in seen:
            continue
        seen.add(warning.code)
        deduped.append(warning)
    return deduped
