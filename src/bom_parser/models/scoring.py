"""Models for Stage 5 — heuristic scoring of supplier-part candidates.

``HeuristicWeights`` is a tunable bundle that controls the scorer's
verdict. Defaults match plan §Stage 5 after the review-pass revisions:
*all-digits / all-letters / alnum-mix character-class signals are
neutral* (per review — common in real MPNs and not evidence in either
direction); only length, whitespace, case, punctuation, and date /
quantity shape have weights.

``PartScoreResult`` is the verdict for one token: a clamped confidence
in ``[0.0, 1.0]``, an ``is_internal_author_part`` flag, and an optional
``rejection_reason`` that names *why* a token failed (date-shaped,
quantity-shaped, below the minimum confidence threshold, empty input).
"""

from __future__ import annotations

from dataclasses import dataclass

from bom_parser.models.bom import RejectionReason


@dataclass(frozen=True, slots=True)
class HeuristicWeights:
    """Scoring weights and threshold for supplier-part candidates.

    The whitespace penalty is *modulated* by the clean-token ratio of
    the candidate. Multi-token parts like ``"1010 X 36"`` or
    ``"DMP 331-110-P001-4-5-TAO-"`` are legitimate; the penalty is
    waived when at least
    ``whitespace_clean_token_ratio_threshold`` of the whitespace-split
    sub-tokens look like genuine part components (uppercase / digit /
    allowed-punct, length >= 2, no bad punct). Below the threshold —
    e.g. ``"1010 X 36"`` which scores 67% because the single-letter
    ``X`` doesn't count as clean — the penalty still applies but at
    a *reduced* magnitude so short multi-token parts still pass with
    a thin margin.
    """

    length_min: int = 4
    length_max: int = 32
    length_in_range_reward: float = 0.50
    length_out_of_range_penalty: float = -0.40
    whitespace_penalty_per_char: float = -0.10
    whitespace_clean_token_ratio_threshold: float = 0.80
    lowercase_penalty: float = -0.10
    bad_punct_penalty_per_char: float = -0.20
    boundary_alnum_reward: float = 0.10
    min_confidence: float = 0.35


@dataclass(frozen=True, slots=True)
class PartScoreResult:
    """Verdict for one candidate token.

    ``rejection_reason`` is ``None`` when the candidate clears every
    hard-reject check and the minimum-confidence threshold. The
    ``is_internal_author_part`` flag is *informational only*: per the
    revised plan it never gates emission, since in the reference BoMs
    an internal-pattern token never appears in a supplier row's mfg_part
    column anyway (the row classifier in Stage 3 already rules that out).
    """

    confidence: float
    is_internal_author_part: bool
    rejection_reason: RejectionReason | None

    @property
    def is_accepted(self) -> bool:
        return self.rejection_reason is None
