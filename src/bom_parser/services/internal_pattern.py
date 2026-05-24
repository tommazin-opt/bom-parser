"""Stage 4 — discover the BoM author's internal-part regex from the document.

The BoM author uses a private numbering scheme for their own line items
(in our reference docs: ``LB000300``, ``EL000491``, ``M004375``,
``UA000456`` …). Each shop has its own. We refuse to hard-code one — per
plan §Generalization, the parser learns the shape at runtime from the
document's own ``Part Identifier`` column.

Algorithm (plan §Stage 4):

    1. Collect every token from the Part-Identifier column.
    2. Abstract each to its L/D/X shape:
         letters → 'L', digits → 'D', anything else → 'X'.
         ``LB000300`` ↦ ``LLDDDDDD``
         ``M004375``  ↦ ``LDDDDDD``
    3. Accept any shape covering ≥ ``DEFAULT_MIN_SHAPE_FREQUENCY``
       of column tokens.
    4. Synthesise a regex union: one alternation branch per accepted
       shape, runs of identical characters compressed (``LLDDDDDD`` →
       ``[A-Z]{2}\\d{6}``).
    5. Validate that the synthesised regex matches ≥
       ``DEFAULT_MIN_PATTERN_MATCH_RATE`` of input tokens. If not, fall
       back to a permissive default and emit a
       ``low_confidence_internal_pattern`` warning.

The discovered regex flows downstream to Stage 3 (record-start sentinel)
and Stage 5 (``is_internal_author_part`` flagging).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

import regex

from bom_parser.models.bom import ParseWarning
from bom_parser.models.internal_pattern import InternalPatternDiscovery
from bom_parser.utils.consts import (
    DEFAULT_MIN_PATTERN_MATCH_RATE,
    DEFAULT_MIN_SHAPE_FREQUENCY,
    FALLBACK_INTERNAL_PATTERN,
)


def discover_internal_pattern(
    tokens: Iterable[str],
    *,
    min_shape_frequency: float = DEFAULT_MIN_SHAPE_FREQUENCY,
    min_match_rate: float = DEFAULT_MIN_PATTERN_MATCH_RATE,
) -> InternalPatternDiscovery:
    """Learn the internal-part regex from a stream of column tokens.

    Returns a fully populated ``InternalPatternDiscovery``; tokens that
    happen to be spaces, blank strings, or otherwise empty are silently
    ignored (they are not real part identifiers).
    """
    cleaned: list[str] = [
        t.strip() for t in tokens if t and _looks_like_part_id_candidate(t.strip())
    ]
    if not cleaned:
        return _fallback_discovery(
            reason=(
                "no alphanumeric Part-Identifier column tokens observed "
                "(internal IDs are expected to contain at least one letter "
                "and at least one digit)"
            ),
            match_rate=0.0,
        )

    shape_counts: Counter[str] = Counter(_token_shape(t) for t in cleaned)
    total = len(cleaned)
    accepted_shapes = tuple(
        shape
        for shape, count in shape_counts.most_common()
        if (count / total) >= min_shape_frequency
    )
    if not accepted_shapes:
        return _fallback_discovery(
            reason=(
                f"no token shape covered the {min_shape_frequency:.0%} "
                f"frequency threshold across {total} tokens"
            ),
            match_rate=0.0,
        )

    pattern_source = _build_pattern(accepted_shapes)
    compiled = regex.compile(pattern_source)
    match_count = sum(1 for t in cleaned if compiled.match(t))
    match_rate = match_count / total

    if match_rate < min_match_rate:
        return _fallback_discovery(
            reason=(
                f"discovered pattern {pattern_source!r} only matched "
                f"{match_rate:.0%} of {total} tokens "
                f"(threshold {min_match_rate:.0%})"
            ),
            match_rate=match_rate,
        )

    return InternalPatternDiscovery(
        pattern=compiled,
        pattern_source=pattern_source,
        accepted_shapes=accepted_shapes,
        match_rate=match_rate,
    )


def _looks_like_part_id_candidate(token: str) -> bool:
    """Cheap filter for "could this be an internal part identifier?".

    Internal part numbers by convention are alphanumeric shop-internal
    IDs that mix letters and digits (``LB000300``, ``EL000491``,
    ``M004375``, ``UA000456``). The Part-Identifier column band on a
    page also contains noise tokens that aren't IDs at all — dot-level
    markers like ``.2`` / ``..3``, lone numerics like ``1`` / ``15``,
    stray punctuation, and description fragments wrapping to the left
    margin. Filtering those out before shape inference is essential
    because shape inference's >= 5% frequency rule is easily drowned
    out by ``XD`` / ``X`` / ``XXD`` noise.

    Returns truthy if ``token`` is alphanumeric (no punctuation),
    contains at least one letter AND at least one digit, and is at
    least 3 characters long.
    """
    if len(token) < 3:
        return False
    if not token.isalnum():
        return False
    has_letter = False
    has_digit = False
    for ch in token:
        if ch.isalpha():
            has_letter = True
        elif ch.isdigit():
            has_digit = True
        if has_letter and has_digit:
            return True
    return False


def _token_shape(token: str) -> str:
    """``LB000300`` → ``LLDDDDDD``; ``M-100`` → ``LXDDD``."""
    chars: list[str] = []
    for ch in token:
        if ch.isalpha():
            chars.append("L")
        elif ch.isdigit():
            chars.append("D")
        else:
            chars.append("X")
    return "".join(chars)


def _build_pattern(shapes: tuple[str, ...]) -> str:
    """Compress each L/D/X shape into a regex branch and union them."""
    branches: list[str] = []
    seen: set[str] = set()
    for shape in shapes:
        branch = _shape_to_regex(shape)
        if branch in seen:
            continue
        seen.add(branch)
        branches.append(branch)
    union = "|".join(branches)
    return f"^({union})$"


def _shape_to_regex(shape: str) -> str:
    """``LLDDDDDD`` → ``[A-Z]{2}\\d{6}``."""
    if not shape:
        return ""
    pieces: list[str] = []
    current_char = shape[0]
    current_count = 1
    for ch in shape[1:]:
        if ch == current_char:
            current_count += 1
        else:
            pieces.append(_chunk(current_char, current_count))
            current_char = ch
            current_count = 1
    pieces.append(_chunk(current_char, current_count))
    return "".join(pieces)


def _chunk(char_class: str, count: int) -> str:
    base = {"L": r"[A-Z]", "D": r"\d", "X": r"[^A-Za-z0-9]"}[char_class]
    return base if count == 1 else f"{base}{{{count}}}"


def _fallback_discovery(
    *, reason: str, match_rate: float
) -> InternalPatternDiscovery:
    """Build a discovery result anchored on the permissive fallback regex."""
    compiled = regex.compile(FALLBACK_INTERNAL_PATTERN)
    warning = ParseWarning(
        code="low_confidence_internal_pattern",
        detail=(
            f"Falling back to default regex {FALLBACK_INTERNAL_PATTERN!r}: "
            f"{reason}."
        ),
        page=None,
    )
    return InternalPatternDiscovery(
        pattern=compiled,
        pattern_source=FALLBACK_INTERNAL_PATTERN,
        accepted_shapes=(),
        match_rate=match_rate,
        warnings=(warning,),
    )
