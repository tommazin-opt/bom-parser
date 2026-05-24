"""Unit tests for internal-pattern discovery."""

from __future__ import annotations

import regex

from bom_parser.services.internal_pattern import (
    _looks_like_part_id_candidate,  # pyright: ignore[reportPrivateUsage]
    _token_shape,  # pyright: ignore[reportPrivateUsage]
    discover_internal_pattern,
)
from bom_parser.utils.consts import FALLBACK_INTERNAL_PATTERN


# ---- helpers --------------------------------------------------------------


def test_token_shape_examples() -> None:
    assert _token_shape("LB000300") == "LLDDDDDD"
    assert _token_shape("M004375") == "LDDDDDD"
    assert _token_shape("UA000456") == "LLDDDDDD"
    assert _token_shape("596-00379") == "DDDXDDDDD"
    assert _token_shape("") == ""


def test_candidate_filter_accepts_real_internal_ids() -> None:
    for token in ["LB000300", "EL000491", "M004375", "UA000456", "SA000683"]:
        assert _looks_like_part_id_candidate(token), token


def test_candidate_filter_rejects_noise_tokens() -> None:
    for token in [".2", "..3", "1", "15", '"', "*", ",", "(2)", "AB", "x"]:
        assert not _looks_like_part_id_candidate(token), token


def test_candidate_filter_requires_letters_and_digits() -> None:
    assert not _looks_like_part_id_candidate("ABCDE")  # all letters
    assert not _looks_like_part_id_candidate("12345")  # all digits
    assert _looks_like_part_id_candidate("AB12")       # mixed
    assert _looks_like_part_id_candidate("X9Z")        # min length 3, mixed


# ---- discovery ------------------------------------------------------------


def test_discovers_reference_pattern_from_typical_tokens() -> None:
    """Mimic the typical Part-Identifier column content of the reference BoMs."""
    tokens = (
        ["LB000300"] * 10
        + ["EL000491"] * 8
        + ["M004375"] * 6
        + ["UA000456"]
        + ["SA000683"]
        # Plus the candidate filter will drop these noise tokens
        + [".2", "..3", "1", "15", '"', "*"] * 5
    )
    result = discover_internal_pattern(tokens)
    pattern = result.pattern

    # All real internal IDs match
    for t in ["LB000300", "EL000491", "M004375", "UA000456", "SA000683"]:
        assert pattern.match(t) is not None, t

    # No fallback warning when shapes hit the frequency cutoff
    assert result.warnings == ()
    assert result.match_rate == 1.0


def test_falls_back_when_only_one_shape_dominates_weakly() -> None:
    # All tokens fail the candidate filter → empty cleaned list
    tokens = [".2"] * 50 + ["..3"] * 30 + ["1"] * 20
    result = discover_internal_pattern(tokens)
    assert result.pattern_source == FALLBACK_INTERNAL_PATTERN
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "low_confidence_internal_pattern"


def test_falls_back_on_empty_input() -> None:
    result = discover_internal_pattern([])
    assert result.pattern_source == FALLBACK_INTERNAL_PATTERN
    assert result.accepted_shapes == ()
    assert len(result.warnings) == 1


def test_fallback_pattern_still_matches_typical_internal_ids() -> None:
    pattern = regex.compile(FALLBACK_INTERNAL_PATTERN)
    for t in ["LB000300", "M004375", "UA000456"]:
        assert pattern.match(t) is not None, t


def test_discovered_pattern_excludes_supplier_part_numbers() -> None:
    tokens = ["LB000300"] * 10 + ["M004375"] * 8 + ["UA000456"]
    result = discover_internal_pattern(tokens)
    # Supplier-part shaped tokens must NOT match
    for not_internal in [
        "596-00379",
        "TM172PDG28R",
        "5793T62",
        "F919-0106-44-4X12.00.1-SS",
        "HDR-30-24",
    ]:
        assert result.pattern.match(not_internal) is None, not_internal
