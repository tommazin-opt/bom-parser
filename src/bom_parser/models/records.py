"""Intermediate types emitted by Stage 3 (row assembly).

A ``RawRecord`` is one logical BoM line item — keyed by an internal part
identifier, possibly nested under a parent record, possibly carrying
several alternative ``SupplierRow``s. These flow into Stage 6
(supplier normalisation) and Stage 7 (exporter) for grouping by
description and emission to JSON.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SupplierRow:
    """One alternative supplier listed under a BoM record.

    ``name_text`` is the raw concatenation of words found left of the
    mfg_part column on this line (e.g. ``"North Coast Com"``,
    ``"McMaster- Carr"``). Normalisation to a canonical name happens
    later, in Stage 6.

    ``part_text`` is the single token in the mfg_part column
    (``"596-00379"``, ``"TM172PDG28R"``). Confidence scoring happens
    later, in Stage 5.
    """

    name_text: str
    part_text: str
    page_index: int
    line_y: float


@dataclass(frozen=True, slots=True)
class RawRecord:
    """One BoM line item, assembled from a multi-line record block.

    ``depth`` mirrors the dot-level marker the BoM author used to
    indicate hierarchy (``.2``, ``..3``); the root record is depth 0.
    ``parent_internal_part`` is resolved by a parent-tracker that walks
    the stream of records, popping deeper levels when a shallower one
    appears.
    """

    internal_part: str
    description: str
    quantity: float | None
    uom: str | None
    commodity: str | None
    depth: int
    parent_internal_part: str | None
    suppliers: tuple[SupplierRow, ...]
    page_index: int
    line_y: float
