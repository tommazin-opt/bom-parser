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
from bom_parser.utils.consts import FLAG_TOKEN_PATTERN

_FLAG_TOKEN = regex.compile(FLAG_TOKEN_PATTERN)


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

    return BomDocument(metadata=metadata, parts=parts)


# ---- per-part assembly -----------------------------------------------------


def _build_part(
    *,
    description: str,
    records: list[RawRecord],
    normalizer: SupplierNormalizer,
    internal_pattern: regex.Pattern[str],
    weights: HeuristicWeights,
    hard_rejected: list[HardRejectedCandidate],
) -> Part:
    total_quantity = sum(r.quantity or 0.0 for r in records)
    uom = next((r.uom for r in records if r.uom), None)
    commodity = next((r.commodity for r in records if r.commodity), None)

    internal_parts = list(
        dict.fromkeys(r.internal_part for r in records)
    )  # preserves order, dedupes
    occurrences = [
        Occurrence(
            internal_author_part=r.internal_part,
            quantity=r.quantity or 0.0,
            parent_internal_part=r.parent_internal_part,
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
    """Strip residual flag-column tokens and the commodity from description.

    Row assembly already removed depth markers, quantities, and dates.
    What remains tends to be the real description plus the BoM's flag
    columns ("U EA 0 N 0 AA A") trailing the data row, and the
    commodity code on the wrap line — neither belongs in the part's
    user-facing description.
    """
    pieces: list[str] = []
    for token in raw.split():
        if _FLAG_TOKEN.match(token):
            continue
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
