"""Result of Stage 4 — internal-part pattern discovery.

The discovered regex is consumed by Stage 3 (row assembly) as the
record-start sentinel and by Stage 5 (supplier extraction) to flag
`is_internal_author_part` on emitted supplier-part candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import regex

from bom_parser.models.bom import ParseWarning


@dataclass(frozen=True, slots=True)
class InternalPatternDiscovery:
    """Output of ``discover_internal_pattern``.

    ``pattern_source`` is the raw regex string (so it can land in
    ``ParseMetadata.discovered_internal_pattern`` in the output JSON
    verbatim — the operator can inspect it). ``pattern`` is the compiled
    object the rest of the pipeline matches against.
    """

    pattern: regex.Pattern[str]
    pattern_source: str
    accepted_shapes: tuple[str, ...]
    match_rate: float
    warnings: tuple[ParseWarning, ...] = field(default_factory=tuple[ParseWarning, ...])
