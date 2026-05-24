"""End-to-end integration tests against the reference BoM PDFs.

Each test parameterises over both reference documents so we catch
regressions on either. The parsed ``BomDocument`` is built once per
PDF in a session-scoped fixture and shared across all assertions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import regex

from bom_parser.models.bom import BomDocument
from bom_parser.pipeline import parse_bom


@pytest.fixture(scope="session")
def parsed_456(pdf_456: Path, config_dir: Path) -> BomDocument:
    return parse_bom(pdf_456, config_dir=config_dir)


@pytest.fixture(scope="session")
def parsed_457(pdf_457: Path, config_dir: Path) -> BomDocument:
    return parse_bom(pdf_457, config_dir=config_dir)


@pytest.fixture(
    params=["parsed_456", "parsed_457"],
    ids=["UA000456AF", "UA000457AD"],
)
def parsed(request: pytest.FixtureRequest) -> BomDocument:
    return request.getfixturevalue(request.param)


# ---- Plan §Verification 2: non-empty output -------------------------------


def test_emits_at_least_one_supplier_part_pair(parsed: BomDocument) -> None:
    total_pairs = sum(len(p.suppliers) for p in parsed.parts)
    assert total_pairs > 0, (
        "Catastrophic regression: parser produced zero supplier-part pairs"
    )


def test_emits_multiple_parts(parsed: BomDocument) -> None:
    """Reference BoMs each have well over 100 parts."""
    assert len(parsed.parts) > 50


# ---- Plan §Verification 3: internal-pattern correctness -------------------


@pytest.mark.parametrize(
    "internal_token",
    ["LB000300", "EL000491", "M004375", "UA000456", "SA000683"],
)
def test_internal_pattern_matches_known_internal_ids(
    parsed: BomDocument, internal_token: str
) -> None:
    pattern = regex.compile(parsed.metadata.discovered_internal_pattern)
    assert pattern.match(internal_token) is not None, (
        f"Discovered internal pattern "
        f"{parsed.metadata.discovered_internal_pattern!r} should match "
        f"{internal_token!r}"
    )


@pytest.mark.parametrize(
    "supplier_part",
    ["596-00379", "TM172PDG28R", "5793T62", "F919-0106-44-4X12.00.1-SS"],
)
def test_internal_pattern_rejects_known_supplier_parts(
    parsed: BomDocument, supplier_part: str
) -> None:
    pattern = regex.compile(parsed.metadata.discovered_internal_pattern)
    assert pattern.match(supplier_part) is None, (
        f"Discovered internal pattern "
        f"{parsed.metadata.discovered_internal_pattern!r} should NOT match "
        f"supplier part {supplier_part!r}"
    )


# ---- Plan §Verification 4: no-internal-leak invariant ---------------------


def test_supplier_parts_matching_internal_pattern_are_flagged(
    parsed: BomDocument,
) -> None:
    """If a supplier-part happens to match the internal pattern, the
    ``is_internal_author_part`` flag must be ``True`` so downstream
    consumers can distinguish "the BoM author's own facility supplies
    this part" (a legitimate case — Trimet uses the M-prefix numbers
    as both internal IDs and supplier-part numbers) from a column
    mis-classification.
    """
    pattern = regex.compile(parsed.metadata.discovered_internal_pattern)
    mismatches: list[str] = []
    for part in parsed.parts:
        for supplier in part.suppliers:
            if pattern.match(supplier.part_number) is None:
                continue
            if not supplier.is_internal_author_part:
                mismatches.append(
                    f"{part.description[:40]!r} -> {supplier.part_number}: "
                    f"flag should be True"
                )
    assert not mismatches, (
        f"is_internal_author_part flag inconsistent with discovered "
        f"pattern for: {mismatches}"
    )


# ---- Metadata sanity ------------------------------------------------------


def test_metadata_has_required_fields(parsed: BomDocument) -> None:
    md = parsed.metadata
    assert md.source_file.endswith(".pdf")
    assert md.page_count > 0
    assert md.discovered_internal_pattern.startswith("^")
    assert md.parser_version
    # Date / quantity hard-rejects should be empty under happy-path
    # parsing of the reference BoMs.
    assert md.hard_rejected_candidates == []


def test_each_supplier_part_pair_has_confidence_in_range(
    parsed: BomDocument,
) -> None:
    for part in parsed.parts:
        for supplier in part.suppliers:
            assert 0.0 <= supplier.confidence_score <= 1.0


# ---- Plan §Verification 6: supplier-clustering correctness ----------------


def test_north_coast_variants_normalize_to_one_canonical_456(
    parsed_456: BomDocument,
) -> None:
    """The 456 fixture contains NCC, North Coast, and North Coast Com.

    All three must resolve to the same ``name_normalized`` in the
    emitted JSON — that's the headline §Verification 6 assertion.
    """
    raw_to_canonical: dict[str, str] = {}
    for part in parsed_456.parts:
        for supplier in part.suppliers:
            raw = supplier.name_raw
            if raw in {"NCC", "North Coast", "North Coast Com"}:
                raw_to_canonical[raw] = supplier.name_normalized

    # All variants we encountered must agree on a single canonical
    assert len(raw_to_canonical) > 0, (
        "Test fixture missing — the 456 BoM should expose at least one "
        "North-Coast-family raw name"
    )
    canonicals = set(raw_to_canonical.values())
    assert len(canonicals) == 1, (
        f"North-Coast variants should normalize to one canonical, got "
        f"{raw_to_canonical}"
    )


def test_specific_supplier_pair_present_456(parsed_456: BomDocument) -> None:
    """The danger-label group must list North Coast Components 596-00379."""
    found = False
    for part in parsed_456.parts:
        for supplier in part.suppliers:
            if supplier.part_number == "596-00379":
                assert "North Coast" in supplier.name_normalized
                found = True
    assert found, "Expected supplier-part 596-00379 missing from output"
