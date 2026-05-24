"""Pipeline orchestrator — runs all stages against one BoM PDF.

This is the single public entry point for the library. Callers pass a
PDF path and a config directory; everything else (layout detection,
internal-pattern discovery, row assembly, scoring, normalisation,
export) is wired up internally.

    pdf path ──▶ ingest ──▶ per-page layout detection ──▶ internal-pattern
              discovery (whole document) ──▶ per-page row assembly
              (threading ParentTracker across pages) ──▶ exporter
              (group by description, score + normalise suppliers) ──▶
              ``BomDocument``

The CLI (``bom_parser.cli``) is a thin Typer wrapper around
``parse_bom``.
"""

from __future__ import annotations

from pathlib import Path

from bom_parser.models.bom import BomDocument, ParseWarning
from bom_parser.models.geometry import PageLayout
from bom_parser.models.records import RawRecord
from bom_parser.services.exporter import build_bom_document
from bom_parser.services.heuristic_scorer import load_heuristic_weights
from bom_parser.services.ingestion import ingest
from bom_parser.services.internal_pattern import discover_internal_pattern
from bom_parser.services.layout_detector import (
    detect_page_layout,
    load_header_synonyms,
)
from bom_parser.services.row_assembler import ParentTracker, assemble_records
from bom_parser.services.supplier_normalizer import load_supplier_aliases
from bom_parser.utils.consts import (
    CONFIG_DIR_NAME,
    HEADER_SYNONYMS_FILENAME,
    HEURISTIC_WEIGHTS_FILENAME,
    SUPPLIER_ALIASES_FILENAME,
)


def parse_bom(
    pdf_path: str | Path,
    *,
    config_dir: str | Path = CONFIG_DIR_NAME,
) -> BomDocument:
    """Parse one BoM PDF end-to-end and return a ``BomDocument``.

    Raises:
        FileNotFoundError: ``pdf_path`` or a required config file is missing.
        HeaderDetectionError: any page's header cannot be recognised
            (operator action required — see error message for which
            synonyms to add).
    """
    pdf = Path(pdf_path)
    config = Path(config_dir)
    synonyms_path = config / HEADER_SYNONYMS_FILENAME
    weights_path = config / HEURISTIC_WEIGHTS_FILENAME
    aliases_path = config / SUPPLIER_ALIASES_FILENAME

    synonyms = load_header_synonyms(synonyms_path)
    weights = load_heuristic_weights(weights_path)
    aliases = load_supplier_aliases(aliases_path)

    ingestion = ingest(pdf)

    # Per-page layout + collected warnings.
    page_layouts: list[PageLayout] = []
    all_layout_warnings: list[ParseWarning] = []
    for page in ingestion.pages:
        layout, warnings = detect_page_layout(
            page, synonyms, config_path=synonyms_path
        )
        page_layouts.append(layout)
        all_layout_warnings.extend(warnings)

    # Internal-pattern discovery — pool every Part-Identifier-column
    # body token across the whole document.
    column_tokens: list[str] = []
    for page, layout in zip(ingestion.pages, page_layouts):
        band = layout.columns.get("part_identifier")
        if band is None:
            continue
        for word in page.words:
            cx = (word.bbox.x0 + word.bbox.x1) / 2.0
            if band.contains(cx) and word.bbox.top >= layout.body_y_top:
                column_tokens.append(word.text)
    discovery = discover_internal_pattern(column_tokens)

    # Row assembly — thread the ParentTracker across pages so a parent
    # established on page N is visible to a child on page N+1.
    parents = ParentTracker()
    all_records: list[RawRecord] = []
    for page, layout in zip(ingestion.pages, page_layouts):
        page_records, parents = assemble_records(
            page, layout, discovery.pattern, parents=parents
        )
        all_records.extend(page_records)

    return build_bom_document(
        tuple(all_records),
        ingestion=ingestion,
        discovery=discovery,
        layout_warnings=tuple(all_layout_warnings),
        weights=weights,
        supplier_aliases=aliases,
    )
