"""Stage 1 — PDF ingestion.

Drives ``pdfplumber`` to pull word-level geometry off each page of a BoM
PDF. The output is the typed seed for every downstream stage:

    PDF file ─▶ ``IngestedDocument``
                 ├── per-page ``IngestedPage`` with a tuple of ``Word``s
                 └── document-level PDF metadata (title, creator, …)

Naive text extraction (``page.extract_text()``) collides numeric fields
with surrounding text (see plan §Context — e.g. ``Hazar4d/"1 7P/o2p024``
when a date and the word "Hazard" overlap by y-coordinate). Everything
downstream relies on the bbox-level words emitted here instead.

v1 does not implement the OCR fallback: pages with fewer than
``utils.consts.DEFAULT_MIN_WORDS_FOR_TEXT_LAYER`` text-layer words
emit an ``ocr_fallback_used`` warning so the operator sees what's
happening, but the parser continues with whatever sparse output it
has. See ``docs/FUTURE_BOM_FORMATS.md`` for the planned OCR approach
when it becomes in-scope. The reference BoMs ship with a real text
layer and never trip this threshold.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pdfplumber
from pdfplumber.page import Page as PlumberPage
from pypdf import PdfReader

from bom_parser.models.bom import ParseWarning
from bom_parser.models.geometry import Bbox, Word
from bom_parser.models.ingestion import IngestedDocument, IngestedPage
from bom_parser.utils.consts import (
    DEFAULT_MIN_WORDS_FOR_TEXT_LAYER,
    DEFAULT_X_TOLERANCE,
    DEFAULT_Y_TOLERANCE,
    PDF_META_CREATOR_KEY,
    PDF_META_PRODUCER_KEY,
    PDF_META_TITLE_KEY,
    PLUMBER_FONTNAME_ATTR,
    PLUMBER_SIZE_ATTR,
)


def ingest(
    path: str | Path,
    *,
    x_tolerance: float = DEFAULT_X_TOLERANCE,
    y_tolerance: float = DEFAULT_Y_TOLERANCE,
    min_words_for_text_layer: int = DEFAULT_MIN_WORDS_FOR_TEXT_LAYER,
) -> IngestedDocument:
    """Extract word-level geometry from every page of ``path``.

    Raises:
        FileNotFoundError: ``path`` does not exist.
    """
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(f"BoM PDF not found: {source_path}")

    warnings: list[ParseWarning] = []
    pages: list[IngestedPage] = []

    with pdfplumber.open(str(source_path)) as pdf:
        for page_index, plumber_page in enumerate(pdf.pages):
            page = _ingest_page(
                plumber_page,
                page_index=page_index,
                x_tolerance=x_tolerance,
                y_tolerance=y_tolerance,
                min_words_for_text_layer=min_words_for_text_layer,
                warnings=warnings,
            )
            pages.append(page)

    title, creator, producer = _read_pdf_metadata(source_path)

    return IngestedDocument(
        source_path=source_path,
        page_count=len(pages),
        pages=tuple(pages),
        pdf_title=title,
        pdf_creator=creator,
        pdf_producer=producer,
        warnings=tuple(warnings),
    )


def _ingest_page(
    plumber_page: PlumberPage,
    *,
    page_index: int,
    x_tolerance: float,
    y_tolerance: float,
    min_words_for_text_layer: int,
    warnings: list[ParseWarning],
) -> IngestedPage:
    raw_words = plumber_page.extract_words(
        extra_attrs=[PLUMBER_FONTNAME_ATTR, PLUMBER_SIZE_ATTR],
        keep_blank_chars=False,
        use_text_flow=False,
        x_tolerance=x_tolerance,
        y_tolerance=y_tolerance,
    )
    words = tuple(_word_from_plumber(w, page_index=page_index) for w in raw_words)

    if len(words) < min_words_for_text_layer:
        warnings.append(
            ParseWarning(
                code="ocr_fallback_used",
                detail=(
                    f"page {page_index} has only {len(words)} text-layer words "
                    f"(threshold {min_words_for_text_layer}); OCR fallback is "
                    "not implemented in v1 — see docs/FUTURE_BOM_FORMATS.md. "
                    "Parser will continue with whatever text-layer words are "
                    "available, which is likely insufficient."
                ),
                page=page_index,
            )
        )

    return IngestedPage(
        page_index=page_index,
        width=float(plumber_page.width),
        height=float(plumber_page.height),
        words=words,
    )


def _word_from_plumber(raw: dict[str, Any], *, page_index: int) -> Word:
    fontname_raw = raw.get("fontname")
    size_raw = raw.get("size")
    return Word(
        text=str(raw["text"]),
        bbox=Bbox(
            x0=float(raw["x0"]),
            x1=float(raw["x1"]),
            top=float(raw["top"]),
            bottom=float(raw["bottom"]),
        ),
        page_index=page_index,
        fontname=str(fontname_raw) if fontname_raw is not None else None,
        size=float(size_raw) if size_raw is not None else None,
    )


def _read_pdf_metadata(
    source_path: Path,
) -> tuple[str | None, str | None, str | None]:
    """Pull ``/Title``, ``/Creator``, ``/Producer`` via pypdf for richer metadata.

    pdfplumber exposes ``pdf.metadata`` too, but its values are sometimes
    bytes; pypdf normalizes them to strings.
    """
    try:
        reader = PdfReader(str(source_path))
        meta = reader.metadata
    except Exception:
        return None, None, None
    if meta is None:
        return None, None, None
    return (
        _coerce_optional_str(meta.get(PDF_META_TITLE_KEY)),
        _coerce_optional_str(meta.get(PDF_META_CREATOR_KEY)),
        _coerce_optional_str(meta.get(PDF_META_PRODUCER_KEY)),
    )


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return None
    text = str(value).strip()
    return text or None
