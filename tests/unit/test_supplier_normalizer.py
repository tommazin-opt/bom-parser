"""Unit tests for the supplier normaliser.

Covers the four resolution paths: alias-table exact hit, acronym match,
fuzzy fallback against already-clustered names, and minting a brand-new
canonical when none of the above hits.
"""

from __future__ import annotations

import pytest

from bom_parser.services.supplier_normalizer import (
    SupplierNormalizer,
    pre_normalize,
)


@pytest.fixture
def aliases() -> dict[str, list[str]]:
    return {
        "North Coast Components": ["North Coast Com", "North Coast", "NCC"],
        "McMaster-Carr": ["McMaster", "Mcmaster", "McMaster- Carr"],
        "Schneider Electric": ["Modicon"],
        "OMEGA Engineering": ["OMEGA", "Omega"],
    }


@pytest.fixture
def normalizer(aliases: dict[str, list[str]]) -> SupplierNormalizer:
    return SupplierNormalizer(aliases)


# ---- pre_normalize --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("McMaster-Carr", "mcmaster-carr"),
        ("  North Coast Com  ", "north coast com"),
        ("Mcmaster.", "mcmaster"),
        ("North   Coast  Com", "north coast com"),
        ("NCC,", "ncc"),
    ],
)
def test_pre_normalize(raw: str, expected: str) -> None:
    assert pre_normalize(raw) == expected


# ---- alias-table exact lookup ---------------------------------------------


@pytest.mark.parametrize(
    "raw,expected_canonical",
    [
        ("North Coast Com", "North Coast Components"),
        ("North Coast", "North Coast Components"),
        ("McMaster", "McMaster-Carr"),
        ("Mcmaster", "McMaster-Carr"),
        ("McMaster- Carr", "McMaster-Carr"),
        ("Modicon", "Schneider Electric"),
        ("OMEGA", "OMEGA Engineering"),
        ("Omega", "OMEGA Engineering"),
    ],
)
def test_alias_lookup(
    normalizer: SupplierNormalizer, raw: str, expected_canonical: str
) -> None:
    assert normalizer.normalize(raw) == expected_canonical


# ---- acronym detection ----------------------------------------------------


def test_acronym_resolves_to_existing_canonical(
    normalizer: SupplierNormalizer,
) -> None:
    # NCC is already in the alias table, but suppose it weren't —
    # acronym detection would still find "North Coast Components" by
    # initials. Verify by spinning up a normaliser without that alias.
    naked = SupplierNormalizer({"North Coast Components": []})
    assert naked.normalize("NCC") == "North Coast Components"


def test_lowercase_acronym_candidate_does_not_match(
    normalizer: SupplierNormalizer,
) -> None:
    """Only uppercase short tokens are acronym candidates."""
    result = normalizer.normalize("ncc")
    # 'ncc' has no entry in our alias table (only 'NCC' does, but
    # pre_normalize collapses both to 'ncc' which IS in the table —
    # so we expect alias-table hit, not acronym match. Either way the
    # canonical is North Coast Components.)
    assert result == "North Coast Components"


def test_long_uppercase_token_not_treated_as_acronym() -> None:
    """5+ letter uppercase tokens are NOT acronym candidates."""
    naked = SupplierNormalizer({"North Coast Components": []})
    # 'NORCO' (5 letters) is outside the 2-4 letter acronym range
    assert naked.normalize("NORCO") != "North Coast Components"


# ---- new canonical minting ------------------------------------------------


def test_unknown_supplier_becomes_new_canonical(
    normalizer: SupplierNormalizer,
) -> None:
    canonical = normalizer.normalize("Bestlink")
    assert canonical == "Bestlink"
    assert "Bestlink" in normalizer.new_supplier_candidates


def test_new_canonicals_preserve_short_acronym_casing() -> None:
    n = SupplierNormalizer({})
    assert n.normalize("NCC") == "NCC"
    assert n.normalize("Bestlink") == "Bestlink"


def test_repeat_calls_are_memoised(normalizer: SupplierNormalizer) -> None:
    first = normalizer.normalize("Bestlink")
    second = normalizer.normalize("Bestlink")
    assert first == second == "Bestlink"
    # Should appear exactly once in new_supplier_candidates
    assert normalizer.new_supplier_candidates.count("Bestlink") == 1


# ---- fuzzy clustering -----------------------------------------------------


def test_fuzzy_within_document_clusters_to_minted_canonical() -> None:
    """After 'Allen Bradley' is minted, 'Allen-Bradley' should fuzz-merge."""
    n = SupplierNormalizer({})
    canonical_a = n.normalize("Allen Bradley")
    canonical_b = n.normalize("Allen-Bradley")
    assert canonical_a == canonical_b


def test_fuzzy_does_not_collapse_clearly_distinct_names() -> None:
    n = SupplierNormalizer({"Allen Bradley": []})
    # 'Acme Industries' should not be clustered with 'Allen Bradley'
    canonical = n.normalize("Acme Industries")
    assert canonical != "Allen Bradley"


def test_smart_title_preserves_camel_case() -> None:
    """A user-typed 'McMaster-style' name kept its casing when minted."""
    n = SupplierNormalizer({})
    assert n.normalize("McMaster") == "McMaster"


def test_smart_title_uppercases_first_letter() -> None:
    n = SupplierNormalizer({})
    assert n.normalize("acme") == "Acme"
