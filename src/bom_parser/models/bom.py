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
    """

    internal_author_part: str
    quantity: float = Field(ge=0.0)
    parent_internal_part: str | None = None


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

    Multiple BoM rows that share a description collapse into a single
    ``Part``. ``total_quantity`` sums the quantities across every
    occurrence so a downstream purchasing pipeline has the rolled-up
    number directly available.
    """

    description: str
    total_quantity: float = Field(ge=0.0)
    uom: str | None = None
    commodity: str | None = None
    internal_author_parts: list[str] = Field(default_factory=list[str])
    occurrences: list[Occurrence] = Field(default_factory=list[Occurrence])
    suppliers: list[Supplier] = Field(default_factory=list[Supplier])


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
    """Root output object — the JSON written to disk."""

    metadata: ParseMetadata
    parts: list[Part] = Field(default_factory=list[Part])
