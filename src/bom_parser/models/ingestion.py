"""Intermediate data types emitted by Stage 1 (ingestion).

These are *not* the public output contract — they are the typed seed that
flows from Stage 1 (ingestion) into Stage 2 (layout detection). Kept as
plain ``slots=True`` dataclasses rather than Pydantic models because they
are constructed once per page and not validated at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from bom_parser.models.bom import ParseWarning
from bom_parser.models.geometry import Word


@dataclass(frozen=True, slots=True)
class IngestedPage:
    """Every word pdfplumber extracted from one page, plus page geometry."""

    page_index: int
    width: float
    height: float
    words: tuple[Word, ...]


@dataclass(frozen=True, slots=True)
class IngestedDocument:
    """The full PDF lifted into typed geometry."""

    source_path: Path
    page_count: int
    pages: tuple[IngestedPage, ...]
    pdf_title: str | None = None
    pdf_creator: str | None = None
    pdf_producer: str | None = None
    warnings: tuple[ParseWarning, ...] = field(
        default_factory=tuple[ParseWarning, ...]
    )

    @property
    def total_words(self) -> int:
        return sum(len(p.words) for p in self.pages)
