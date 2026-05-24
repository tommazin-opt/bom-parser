"""Property-based and example tests for the heuristic scorer.

Plan §Verification 5 lists the invariants the scorer must honour. We
encode each as a hypothesis property over a constrained input space
(plain alphanumeric tokens, length 4-32) so the property holds across
thousands of generated examples.
"""

from __future__ import annotations

import regex
from hypothesis import given, strategies as st

from bom_parser.models.scoring import HeuristicWeights
from bom_parser.services.heuristic_scorer import score_part_number

_INTERNAL_PATTERN = regex.compile(r"^[A-Z]{1,4}\d{3,8}$")


# Plain alphanumeric tokens (no spaces, no lowercase, no banned
# punctuation) within the scorer's "in-range" length band.
_safe_token = st.text(
    alphabet=st.sampled_from("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_/.#"),
    min_size=4,
    max_size=32,
).filter(lambda s: s[0].isalnum() and s[-1].isalnum())


@given(token=_safe_token, ws_run=st.integers(min_value=1, max_value=5))
def test_adding_whitespace_never_increases_score(
    token: str, ws_run: int
) -> None:
    base = score_part_number(
        token, internal_pattern=_INTERNAL_PATTERN
    ).confidence
    with_ws = score_part_number(
        token + (" " * ws_run), internal_pattern=_INTERNAL_PATTERN
    ).confidence
    assert with_ws <= base


@given(token=_safe_token, lc_count=st.integers(min_value=1, max_value=3))
def test_adding_lowercase_never_increases_score(
    token: str, lc_count: int
) -> None:
    base = score_part_number(
        token, internal_pattern=_INTERNAL_PATTERN
    ).confidence
    with_lc = score_part_number(
        token + ("a" * lc_count), internal_pattern=_INTERNAL_PATTERN
    ).confidence
    assert with_lc <= base


@given(
    token=_safe_token,
    bad=st.sampled_from([",", '"', "'", "?", "*", ";", ":"]),
)
def test_adding_banned_punctuation_never_increases_score(
    token: str, bad: str
) -> None:
    base = score_part_number(
        token, internal_pattern=_INTERNAL_PATTERN
    ).confidence
    with_bad = score_part_number(
        token + bad, internal_pattern=_INTERNAL_PATTERN
    ).confidence
    assert with_bad <= base


@given(
    digits=st.integers(min_value=4, max_value=32),
)
def test_all_digit_tokens_are_not_specially_penalized(digits: int) -> None:
    """Char-class neutrality: an all-digit token in-range gets the
    baseline reward, not a penalty (per revised plan)."""
    token = "1" * digits
    result = score_part_number(token, internal_pattern=_INTERNAL_PATTERN)
    # No char-class penalty applies, so confidence equals length-reward
    # + boundary-alnum-reward = 0.5 + 0.1 = 0.6.
    assert result.confidence == 0.6


@given(letters=st.integers(min_value=4, max_value=32))
def test_all_letter_tokens_are_not_specially_penalized(letters: int) -> None:
    token = "A" * letters
    result = score_part_number(token, internal_pattern=_INTERNAL_PATTERN)
    assert result.confidence == 0.6


def test_alphanumeric_mix_equals_pure_classes() -> None:
    """All-digit, all-letter, and mixed tokens of the same length and
    boundary class must produce identical confidences — character-class
    composition is explicitly neutral in the revised rubric."""
    all_digit = score_part_number(
        "12345678", internal_pattern=_INTERNAL_PATTERN
    ).confidence
    all_letter = score_part_number(
        "ABCDEFGH", internal_pattern=_INTERNAL_PATTERN
    ).confidence
    mix = score_part_number(
        "ABCD1234", internal_pattern=_INTERNAL_PATTERN
    ).confidence
    assert all_digit == all_letter == mix


# ---- Hard-reject behaviour ------------------------------------------------


def test_us_date_hard_rejects_with_reason() -> None:
    result = score_part_number(
        "4/18/2024", internal_pattern=_INTERNAL_PATTERN
    )
    assert result.confidence == 0.0
    assert result.rejection_reason == "date_shaped"
    assert not result.is_accepted


def test_iso_date_hard_rejects_with_reason() -> None:
    result = score_part_number(
        "2024-04-18", internal_pattern=_INTERNAL_PATTERN
    )
    assert result.confidence == 0.0
    assert result.rejection_reason == "date_shaped"


def test_quantity_hard_rejects_with_reason() -> None:
    result = score_part_number(
        "1.000000", internal_pattern=_INTERNAL_PATTERN
    )
    assert result.confidence == 0.0
    assert result.rejection_reason == "quantity_shaped"


def test_empty_token_rejects() -> None:
    result = score_part_number("", internal_pattern=_INTERNAL_PATTERN)
    assert result.rejection_reason == "empty_token"


def test_whitespace_only_rejects() -> None:
    result = score_part_number("   ", internal_pattern=_INTERNAL_PATTERN)
    assert result.rejection_reason == "empty_token"


# ---- Internal-pattern flag is informational, not gating -------------------


def test_internal_pattern_match_flags_but_does_not_reject() -> None:
    """A token that happens to look like an internal ID still passes the
    scorer with its weighted confidence; only the flag is set."""
    result = score_part_number(
        "LB000300", internal_pattern=_INTERNAL_PATTERN
    )
    assert result.is_internal_author_part is True
    assert result.is_accepted is True
    assert result.confidence > 0.0


# ---- Custom weights are honoured ------------------------------------------


def test_custom_weights_change_verdict() -> None:
    strict = HeuristicWeights(min_confidence=0.9)
    result = score_part_number(
        "596-00379", internal_pattern=_INTERNAL_PATTERN, weights=strict
    )
    # Default rubric scores this ~0.6, well below 0.9.
    assert result.rejection_reason == "below_min_confidence"
