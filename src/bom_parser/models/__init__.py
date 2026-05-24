"""Typed data models for the BoM parser pipeline.

`geometry` holds lightweight dataclasses passed *between* pipeline stages
(words, bboxes, column spans). `bom` holds the Pydantic models that define
the JSON output contract consumed by the downstream SERP-API script.
"""

from __future__ import annotations

from bom_parser.models.bom import (
    BomDocument,
    HardRejectedCandidate,
    Occurrence,
    ParseMetadata,
    ParseWarning,
    Part,
    Supplier,
)
from bom_parser.models.geometry import (
    Bbox,
    CanonicalColumn,
    PageLayout,
    PhysicalLine,
    Word,
    XSpan,
)
from bom_parser.models.ingestion import IngestedDocument, IngestedPage
from bom_parser.models.internal_pattern import InternalPatternDiscovery
from bom_parser.models.records import RawRecord, SupplierRow
from bom_parser.models.scoring import HeuristicWeights, PartScoreResult

__all__ = [
    "Bbox",
    "BomDocument",
    "CanonicalColumn",
    "HardRejectedCandidate",
    "HeuristicWeights",
    "IngestedDocument",
    "IngestedPage",
    "InternalPatternDiscovery",
    "Occurrence",
    "PageLayout",
    "ParseMetadata",
    "ParseWarning",
    "Part",
    "PartScoreResult",
    "PhysicalLine",
    "RawRecord",
    "Supplier",
    "SupplierRow",
    "Word",
    "XSpan",
]
