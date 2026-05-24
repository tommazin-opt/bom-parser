"""Stage 5 — heuristic scoring of supplier-part candidates.

The scorer answers a single question: *given a token that Stage 3
already determined to sit in the mfg_part column, how confidently is
it a real supplier part number versus a stray description fragment, a
date, or a flag glyph?*

The verdict is a clamped confidence in ``[0.0, 1.0]`` plus an optional
``rejection_reason``:

* ``date_shaped``        — ``m/d/yyyy`` or ``yyyy-mm-dd`` (hard 0.0)
* ``quantity_shaped``    — ``\\d+\\.\\d{4,}`` (hard 0.0)
* ``empty_token``        — blank / whitespace input (hard 0.0)
* ``below_min_confidence`` — passed shape checks but its weighted score
                           is under ``HeuristicWeights.min_confidence``

Weighted signals (defaults — tunable via
``config/heuristic_weights.yaml``):

  length in ``[4, 32]`` chars              reward (+0.50 baseline)
  length outside that range               penalty (−0.40)
  contains internal whitespace            penalty (−0.30 per space)
  contains lowercase letter               penalty (−0.10 once)
  contains banned punctuation             penalty (−0.20 per char)
  starts AND ends with [A-Z0-9]           reward (+0.10)

Per the revised plan, character-class signals like "all-digits",
"all-letters", and "mix of letters and digits" are explicitly **neutral**
— real MPNs come in every shape.

The ``is_internal_author_part`` flag is set whenever the candidate
matches the discovered internal pattern. It is *informational only*: it
does not gate emission, since Stage 3's row classifier already rules
out internal-pattern tokens from appearing in supplier rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import regex
import yaml

from bom_parser.models.scoring import HeuristicWeights, PartScoreResult
from bom_parser.utils.consts import (
    DATE_SHAPE_PATTERN,
    QUANTITY_SHAPE_PATTERN,
    SCORING_ALLOWED_PUNCTUATION,
    SCORING_BAD_PUNCTUATION,
)

_DATE_SHAPE = regex.compile(DATE_SHAPE_PATTERN)
_QUANTITY_SHAPE = regex.compile(QUANTITY_SHAPE_PATTERN)
# Also catch ISO-style ``2024-04-18``; the row-assembler's DATE_SHAPE_PATTERN
# is intentionally narrower (m/d/yyyy) for description-text scrubbing.
_ISO_DATE_SHAPE = regex.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")


def score_part_number(
    token: str,
    *,
    internal_pattern: regex.Pattern[str],
    weights: HeuristicWeights | None = None,
) -> PartScoreResult:
    """Score one supplier-part candidate. Pure function — safe for property tests."""
    w = weights or HeuristicWeights()
    stripped = token.strip()

    if not stripped:
        return PartScoreResult(
            confidence=0.0,
            is_internal_author_part=False,
            rejection_reason="empty_token",
        )

    is_internal = internal_pattern.match(stripped) is not None

    if _DATE_SHAPE.match(stripped) is not None or _ISO_DATE_SHAPE.match(stripped) is not None:
        return PartScoreResult(
            confidence=0.0,
            is_internal_author_part=is_internal,
            rejection_reason="date_shaped",
        )
    if _QUANTITY_SHAPE.match(stripped) is not None:
        return PartScoreResult(
            confidence=0.0,
            is_internal_author_part=is_internal,
            rejection_reason="quantity_shaped",
        )

    confidence = _weighted_confidence(stripped, weights=w)

    if confidence < w.min_confidence:
        return PartScoreResult(
            confidence=confidence,
            is_internal_author_part=is_internal,
            rejection_reason="below_min_confidence",
        )

    return PartScoreResult(
        confidence=confidence,
        is_internal_author_part=is_internal,
        rejection_reason=None,
    )


def _weighted_confidence(token: str, *, weights: HeuristicWeights) -> float:
    """Apply the weighted signals and clamp the result to ``[0.0, 1.0]``."""
    score = 0.0
    length = len(token)

    if weights.length_min <= length <= weights.length_max:
        score += weights.length_in_range_reward
    else:
        score += weights.length_out_of_range_penalty

    space_count = sum(1 for ch in token if ch.isspace())
    if space_count:
        # Multi-token parts are legitimate when most sub-tokens look
        # like real part components. Only penalise whitespace when the
        # clean-token ratio falls below the configured threshold
        # (default 80%) — and even then, at the *reduced* per-space
        # penalty so short multi-token parts still pass with a margin.
        sub_tokens = token.split()
        clean_count = sum(1 for t in sub_tokens if _is_clean_token(t))
        clean_ratio = clean_count / len(sub_tokens) if sub_tokens else 1.0
        if clean_ratio < weights.whitespace_clean_token_ratio_threshold:
            score += weights.whitespace_penalty_per_char * space_count

    if any(ch.islower() for ch in token):
        score += weights.lowercase_penalty

    bad_count = sum(1 for ch in token if ch in SCORING_BAD_PUNCTUATION)
    if bad_count:
        score += weights.bad_punct_penalty_per_char * bad_count

    if token and _is_alnum(token[0]) and _is_alnum(token[-1]):
        score += weights.boundary_alnum_reward

    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def _is_alnum(ch: str) -> bool:
    return ch.isalnum() and ch.isascii()


def _is_clean_token(token: str) -> bool:
    """Whether a whitespace-split sub-token looks like a genuine part component.

    Used by :func:`_weighted_confidence` to modulate the whitespace
    penalty: legitimate dimensional fragments inside multi-token parts
    (``"X"``, ``"HD"``, ``"36"``, ``"1010"``) are clean; description
    words (``"flying"``, ``"lead"``, ``"2meter"``) are not.

    Caller is expected to pass a non-empty sub-token from ``str.split()``
    (which discards empty entries and never preserves whitespace).

    Clean iff:
      * no bad-punctuation chars (comma, quote, etc.)
      * every char is alphanumeric or in the allowed-punct set
      * not all-lowercase, not lowercase-with-digits — description text
        gives itself away by having lowercase-only word shapes; real
        part components are uppercase / pure-numeric / mixed-case
    """
    if not token:
        return False
    for ch in token:
        if ch in SCORING_BAD_PUNCTUATION:
            return False
        if not (ch.isalnum() or ch in SCORING_ALLOWED_PUNCTUATION):
            return False
    has_upper = any(ch.isupper() for ch in token)
    has_lower = any(ch.islower() for ch in token)
    if has_lower and not has_upper:
        return False
    return True


def load_heuristic_weights(path: str | Path) -> HeuristicWeights:
    """Load weights from a YAML file.

    Missing keys fall back to the dataclass defaults. Unknown keys raise
    ``ValueError`` to catch typos early.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        ValueError: unknown key encountered.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"heuristic weights config not found: {source}")
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if raw is None:
        return HeuristicWeights()
    if not isinstance(raw, dict):
        raise ValueError(
            f"heuristic weights YAML must be a mapping at the top level: {source}"
        )

    defaults = HeuristicWeights()
    valid_fields = {f for f in defaults.__dataclass_fields__}
    raw_dict = cast(dict[Any, Any], raw)
    unknown = set(str(k) for k in raw_dict) - valid_fields
    if unknown:
        raise ValueError(
            f"unknown heuristic-weights keys in {source}: {sorted(unknown)}; "
            f"valid keys are {sorted(valid_fields)}"
        )

    return HeuristicWeights(
        **{
            str(k): _coerce_weight(str(k), v, defaults)
            for k, v in raw_dict.items()
        }
    )


def _coerce_weight(key: str, value: Any, defaults: HeuristicWeights) -> Any:
    """Coerce YAML scalars to the dataclass field's declared type."""
    field = defaults.__dataclass_fields__[key]
    if field.type in ("int", int):
        return int(value)
    if field.type in ("float", float):
        return float(value)
    return value
