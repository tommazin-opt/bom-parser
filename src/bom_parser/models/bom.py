"""Pydantic v2 models defining the BoM parser's JSON output contract.

The downstream SERP-API price/availability script reads this JSON, so any
schema change here is a breaking change for that consumer. Treat this file
as the public interface of the parser.

The shape mirrors the example in the project plan: a ``BomDocument`` carries
``metadata`` plus a list of ``Part`` entries grouped by description. Each
``Part`` lists every supplier-and-part-number pair found for that
description, with the BoM author's *internal* part identifiers captured
separately under ``internal_author_parts`` / ``occurrences`` for downstream
traceability (never as the supplier-facing identifier).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RejectionReason = Literal[
    "date_shaped",
    "quantity_shaped",
    "below_min_confidence",
    "empty_token",
]

WarningCode = Literal[
    "non_adjacent_supplier_columns",
    "low_confidence_internal_pattern",
    "vertically_stacked_supplier_suspected",
    "combined_supplier_cell_suspected",
    "ocr_fallback_used",
]


class _Frozen(BaseModel):
    """Base for all output models — strict, forbids unknown fields, frozen."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class Supplier(_Frozen):
    """A single supplier-and-part-number candidate for a BoM line.

    ``name_raw`` is preserved exactly as extracted from the PDF for
    auditability. ``name_normalized`` is the canonical name produced by the
    supplier normalizer (alias-table + fuzzy fallback).
    """

    name_raw: str
    name_normalized: str
    part_number: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    is_internal_author_part: bool = False


class Occurrence(_Frozen):
    """One observed use of a part within the BoM hierarchy.

    A single ``Part`` (grouped by description) may appear multiple times in
    the BoM under different parents — each appearance becomes one
    ``Occurrence`` so consumers can later pivot on internal part number or
    parent assembly without re-parsing the PDF.

    ``occurrence_id`` is the source record's document-order index; it uniquely
    identifies *this* use even when a subassembly is re-exploded under several
    parents (same ``internal_author_part``, different ``occurrence_id``).
    ``parent_occurrence_id`` points at the specific parent *instance* — not just
    its part number — so the tree builder attaches each re-exploded
    subassembly's children to the correct copy. Both are internal plumbing:
    ``Occurrence`` is not serialized (the tree of ``PartNode`` is).
    """

    internal_author_part: str
    quantity: float = Field(ge=0.0)
    parent_internal_part: str | None = None
    occurrence_id: int = -1
    parent_occurrence_id: int | None = None


class HardRejectedCandidate(_Frozen):
    """A token that the heuristic scorer hard-rejected.

    Surfaced in ``ParseMetadata`` so an operator can see *why* a token a
    human might have expected didn't make it through, without re-running
    the parser in verbose mode.
    """

    token: str
    reason: RejectionReason
    page: int = Field(ge=0)
    row: int = Field(ge=0)


class ParseWarning(_Frozen):
    """A non-fatal observation about the parse worth surfacing to the operator."""

    code: WarningCode
    detail: str
    page: int | None = None


class Part(_Frozen):
    """A BoM line item grouped by description.

    Multiple BoM rows that share a description collapse into a single ``Part``.

    ``total_quantity`` is an **occurrence-sum**: the arithmetic sum of each
    occurrence's own line quantity, exactly as printed on the BoM row. It is
    deliberately *not* an effective/exploded quantity — parent quantities are
    **not** propagated into it (a child appearing twice at line-qty 1 has
    ``total_quantity`` 2 regardless of its parents' quantities). Consumers that
    need a purchasing rollup must multiply through the parent chain themselves
    using the tree.

    ``description_truncated`` is a data-quality flag: the source PDF overprints
    an effectivity date on the description baseline, and where the text ran into
    that column its tail was clipped out of the text layer. ``True`` means this
    description is known to be incomplete (see ``row_assembler._detect_truncation``).
    """

    description: str
    total_quantity: float = Field(ge=0.0)
    uom: str | None = None
    commodity: str | None = None
    internal_author_parts: list[str] = Field(default_factory=list[str])
    occurrences: list[Occurrence] = Field(default_factory=list[Occurrence])
    suppliers: list[Supplier] = Field(default_factory=list[Supplier])
    description_truncated: bool = False


class PartNode(_Frozen):
    """One node in the assembly explosion tree — a single *use* of a part.

    The serialized output is a hierarchy rather than a flat list: each node
    carries a part's substantive fields (description, suppliers, ...) plus the
    quantity of *this* occurrence and a ``children`` list of the parts consumed
    one level below it. Because a part can be consumed under several parents,
    it appears once per parent (a full BoM explosion) — ``parent_internal_part``
    disambiguates which use each node represents.

    The flat ``Part``/``Occurrence`` types above remain the parser's internal
    assembly representation; ``build_part_tree`` converts a list of ``Part``\\ s
    into the ``PartNode`` forest that ``BomDocument`` actually serializes.
    """

    internal_author_part: str
    description: str
    quantity: float = Field(ge=0.0)
    total_quantity: float = Field(ge=0.0)
    uom: str | None = None
    commodity: str | None = None
    parent_internal_part: str | None = None
    description_truncated: bool = False
    suppliers: list[Supplier] = Field(default_factory=list[Supplier])
    children: list["PartNode"] = Field(default_factory=list["PartNode"])


class ParseMetadata(_Frozen):
    """Provenance and parser-diagnostics for one BoM document."""

    source_file: str
    parser_version: str
    extracted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    page_count: int = Field(ge=0)
    discovered_internal_pattern: str
    new_supplier_candidates: list[str] = Field(default_factory=list[str])
    hard_rejected_candidates: list[HardRejectedCandidate] = Field(
        default_factory=list[HardRejectedCandidate]
    )
    warnings: list[ParseWarning] = Field(default_factory=list[ParseWarning])


class BomDocument(_Frozen):
    """Root output object — the JSON written to disk.

    ``parts`` is the assembly explosion *tree*: a forest of root ``PartNode``\\ s
    (the top-level units, plus any orphan whose parent wasn't captured), each
    nesting its sub-parts under ``children``.
    """

    metadata: ParseMetadata
    parts: list[PartNode] = Field(default_factory=list[PartNode])


# ``PartNode.children`` is a forward self-reference deferred by
# ``from __future__ import annotations``; resolve it now that the class exists.
PartNode.model_rebuild()
