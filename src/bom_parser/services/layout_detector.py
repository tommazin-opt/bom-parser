"""Stage 2 — layout detection.

For each page, find the BoM table's header band, recognise which
canonical columns it carries, and infer each column's horizontal x-band
from the *body* word distribution (gap-based, not midpoint-based — see
plan §Stage 2).

Generalisation strategy: nothing here is hard-coded to "Opti Temp"
formats. The synonym map (``config/header_synonyms.yaml``) tells us
which header labels mean which canonical column; the gutter detector
tells us where each column's body content actually lives. Add a label
to YAML to teach the parser a new BoM format — no code change required.

If a page's header is unrecognisable (fewer than the required canonical
columns match, including the mandatory four), the function raises
``HeaderDetectionError``. The error carries enough context — page index,
the suspected header band's word strings, which columns are missing —
that the operator can add synonyms and rerun.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, get_args

import yaml

from bom_parser.models.bom import ParseWarning
from bom_parser.models.geometry import (
    REQUIRED_CANONICAL_COLUMNS,
    CanonicalColumn,
    PageLayout,
    PhysicalLine,
    Word,
    XSpan,
)
from bom_parser.models.ingestion import IngestedPage
from bom_parser.services.line_grouping import group_into_physical_lines
from bom_parser.utils.consts import (
    CONFIG_DIR_NAME,
    DEFAULT_MIN_CANONICAL_HEADERS_MATCHED,
    DEFAULT_MIN_GUTTER_WIDTH,
    FALLBACK_LINE_TOLERANCE,
    HEADER_SYNONYMS_FILENAME,
)

# Derive the set of valid canonical-column names directly from the
# Literal type so it stays in sync automatically when columns are added.
_VALID_CANONICAL_COLUMNS: frozenset[str] = frozenset(get_args(CanonicalColumn))

_DEFAULT_CONFIG_HINT: str = f"{CONFIG_DIR_NAME}/{HEADER_SYNONYMS_FILENAME}"


class HeaderDetectionError(ValueError):
    """Raised when a page's header band cannot be unambiguously recognised.

    Carries diagnostic data so the operator can decide which synonym(s)
    to add to ``config/header_synonyms.yaml`` and rerun.
    """

    def __init__(
        self,
        *,
        page_index: int,
        suspected_header_words: tuple[str, ...],
        matched_columns: tuple[CanonicalColumn, ...],
        missing_required_columns: tuple[CanonicalColumn, ...],
        config_path: Path | None,
    ) -> None:
        self.page_index = page_index
        self.suspected_header_words = suspected_header_words
        self.matched_columns = matched_columns
        self.missing_required_columns = missing_required_columns
        self.config_path = config_path

        config_hint = str(config_path) if config_path else _DEFAULT_CONFIG_HINT
        message = (
            f"Failed to detect a valid BoM header on page {page_index}.\n"
            f"  Mandatory canonical columns missing: "
            f"{list(missing_required_columns)}\n"
            f"  Canonical columns that did match:    {list(matched_columns)}\n"
            f"  Words found in the suspected header band:\n"
            f"    {list(suspected_header_words)}\n"
            f"Add the unrecognised header label(s) to {config_hint} and "
            f"rerun the parser."
        )
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class HeaderMatch:
    """One synonym hit on the page's header band."""

    column: CanonicalColumn
    words: tuple[Word, ...]
    x0: float
    x1: float

    @property
    def synonym_text(self) -> str:
        return " ".join(w.text for w in self.words)


def load_header_synonyms(path: str | Path) -> dict[CanonicalColumn, list[str]]:
    """Load and validate the synonym YAML file.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        ValueError: the YAML uses unknown canonical-column keys or has the
            wrong shape.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"header synonyms config not found: {source}")
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(
            f"header synonyms YAML must be a mapping at the top level: {source}"
        )

    parsed: dict[CanonicalColumn, list[str]] = {}
    for key, value in cast(dict[Any, Any], raw).items():
        key_str = str(key)
        if key_str not in _VALID_CANONICAL_COLUMNS:
            raise ValueError(
                f"unknown canonical column {key_str!r} in {source}; "
                f"expected one of {sorted(_VALID_CANONICAL_COLUMNS)}"
            )
        if not isinstance(value, list):
            raise ValueError(
                f"synonyms for {key_str!r} must be a list of strings in {source}"
            )
        parsed[cast(CanonicalColumn, key_str)] = [str(v) for v in cast(list[Any], value)]
    return parsed


def detect_page_layout(
    page: IngestedPage,
    synonyms: dict[CanonicalColumn, list[str]],
    *,
    min_canonical_headers_matched: int = DEFAULT_MIN_CANONICAL_HEADERS_MATCHED,
    min_gutter_width: float = DEFAULT_MIN_GUTTER_WIDTH,
    config_path: Path | None = None,
) -> tuple[PageLayout, list[ParseWarning]]:
    """Return the detected ``PageLayout`` and any non-fatal warnings.

    Raises:
        HeaderDetectionError: header band missing or under-matched.
    """
    lines = group_into_physical_lines(page.words)
    band_start, band_end, header_matches = _find_header_band(lines, synonyms)

    best_per_column = _select_best_match_per_column(header_matches)
    matched_columns: tuple[CanonicalColumn, ...] = tuple(best_per_column.keys())
    missing_required: tuple[CanonicalColumn, ...] = tuple(
        c for c in REQUIRED_CANONICAL_COLUMNS if c not in best_per_column
    )

    if (
        missing_required
        or len(matched_columns) < min_canonical_headers_matched
    ):
        suspected_words = _flatten_band_words(lines, band_start, band_end)
        raise HeaderDetectionError(
            page_index=page.page_index,
            suspected_header_words=suspected_words,
            matched_columns=matched_columns,
            missing_required_columns=missing_required,
            config_path=config_path,
        )

    header_y_band = _header_y_band(lines, band_start, band_end)
    body_words = tuple(w for w in page.words if w.bbox.top >= header_y_band[1])

    columns = _compute_column_boundaries(
        body_words,
        anchors=best_per_column,
        page_width=page.width,
        min_gutter_width=min_gutter_width,
    )
    column_order: tuple[CanonicalColumn, ...] = tuple(
        col for col, _ in sorted(columns.items(), key=lambda kv: kv[1].x_min)
    )

    warnings: list[ParseWarning] = []
    adjacency_warning = _check_adjacency_invariant(
        columns=columns, column_order=column_order, page_index=page.page_index
    )
    if adjacency_warning is not None:
        warnings.append(adjacency_warning)

    body_y_bottom = (
        max((w.bbox.bottom for w in body_words), default=page.height)
        if body_words
        else page.height
    )

    layout = PageLayout(
        page_index=page.page_index,
        columns=columns,
        header_y_band=header_y_band,
        body_y_top=header_y_band[1],
        body_y_bottom=body_y_bottom,
        column_order=column_order,
    )
    return layout, warnings


# ---- header band identification --------------------------------------------


def _find_header_band(
    lines: tuple[PhysicalLine, ...],
    synonyms: dict[CanonicalColumn, list[str]],
) -> tuple[int, int, list[HeaderMatch]]:
    """Find the contiguous run of lines that constitute the header.

    Strategy: identify *all* contiguous runs of lines that each contain at
    least one synonym match, then pick the run that covers the most
    *distinct* canonical columns. This naturally rejects page-title rows
    that happen to contain one stray synonym phrase (e.g.
    ``Multi-Level Explosion By Parent Part Identifier`` matching
    ``part_identifier``) — the real header row matches many more columns
    and wins.

    Per plan §Stage 2 "Multi-line headers — generalized, not hardcoded":
    supports 1-line, 2-line, or N-line headers without code changes.

    Returns ``(start_inclusive, end_inclusive, matches)``. If no line
    matches, returns ``(-1, -1, [])``.
    """
    matches_per_line: list[list[HeaderMatch]] = [
        _find_header_matches_on_line(line, synonyms) for line in lines
    ]
    break_threshold = _row_break_threshold(lines)

    runs: list[tuple[int, int, set[CanonicalColumn]]] = []
    i = 0
    while i < len(matches_per_line):
        if not matches_per_line[i]:
            i += 1
            continue
        j = i
        while (
            j + 1 < len(matches_per_line)
            and matches_per_line[j + 1]
            and (lines[j + 1].y_top - lines[j].y_bottom) <= break_threshold
        ):
            j += 1
        columns: set[CanonicalColumn] = set()
        for k in range(i, j + 1):
            for m in matches_per_line[k]:
                columns.add(m.column)
        runs.append((i, j, columns))
        i = j + 1

    if not runs:
        return -1, -1, []

    start, end, _ = max(runs, key=lambda r: (len(r[2]), -(r[1] - r[0])))
    combined: list[HeaderMatch] = []
    for k in range(start, end + 1):
        combined.extend(matches_per_line[k])
    return start, end, combined


def _row_break_threshold(lines: tuple[PhysicalLine, ...]) -> float:
    """Y-gap above which two consecutive lines are treated as visually unrelated.

    Used by ``_find_header_band`` to keep title rows out of the header
    band: the title sits well above the real header (~20 PDF points of
    whitespace) whereas the two header rows are tight against each other
    (~4 PDF points). We pick a threshold of ``1.5 × median row gap``.
    """
    if len(lines) < 3:
        return FALLBACK_LINE_TOLERANCE * 4.0
    gaps = [
        lines[i + 1].y_top - lines[i].y_bottom for i in range(len(lines) - 1)
    ]
    positive_gaps = [g for g in gaps if g > 0]
    if not positive_gaps:
        return FALLBACK_LINE_TOLERANCE * 4.0
    return max(statistics.median(positive_gaps) * 1.5, FALLBACK_LINE_TOLERANCE * 2.0)


def _find_header_matches_on_line(
    line: PhysicalLine,
    synonyms: dict[CanonicalColumn, list[str]],
) -> list[HeaderMatch]:
    """All synonym hits on one line. A line can contribute several columns."""
    matches: list[HeaderMatch] = []
    normalized_tokens: tuple[str, ...] = tuple(
        _normalize_token(w.text) for w in line.words
    )

    for column, phrases in synonyms.items():
        for phrase in phrases:
            phrase_tokens = _normalize_phrase(phrase)
            if not phrase_tokens:
                continue
            hit = _find_subsequence(normalized_tokens, phrase_tokens)
            if hit is None:
                continue
            start, stop = hit
            words = line.words[start:stop]
            matches.append(
                HeaderMatch(
                    column=column,
                    words=words,
                    x0=words[0].bbox.x0,
                    x1=words[-1].bbox.x1,
                )
            )
    return matches


def _select_best_match_per_column(
    matches: list[HeaderMatch],
) -> dict[CanonicalColumn, HeaderMatch]:
    """If multiple synonyms hit one column, prefer the one with more words.

    "Item Number" is more specific than the bare "Number" — picking the
    longer match avoids accidental clustering of unrelated header tokens.
    """
    best: dict[CanonicalColumn, HeaderMatch] = {}
    for m in matches:
        existing = best.get(m.column)
        if existing is None or len(m.words) > len(existing.words):
            best[m.column] = m
    return best


def _normalize_token(text: str) -> str:
    return text.strip().lower()


def _normalize_phrase(phrase: str) -> tuple[str, ...]:
    return tuple(t for t in (p.strip().lower() for p in phrase.split()) if t)


def _find_subsequence(
    haystack: tuple[str, ...],
    needle: tuple[str, ...],
) -> tuple[int, int] | None:
    """Return ``(start, stop)`` slice indices of the first hit, or ``None``."""
    if not needle or len(needle) > len(haystack):
        return None
    last = len(haystack) - len(needle) + 1
    for i in range(last):
        if haystack[i : i + len(needle)] == needle:
            return i, i + len(needle)
    return None


def _flatten_band_words(
    lines: tuple[PhysicalLine, ...],
    band_start: int,
    band_end: int,
) -> tuple[str, ...]:
    """All word strings inside the (possibly empty) header band.

    When no header was detected at all (``band_start == -1``) we surface
    the topmost line on the page as the best-guess "where would the
    header have been" diagnostic.
    """
    if band_start == -1:
        if not lines:
            return ()
        return tuple(w.text for w in lines[0].words)
    collected: list[str] = []
    for i in range(band_start, band_end + 1):
        collected.extend(w.text for w in lines[i].words)
    return tuple(collected)


def _header_y_band(
    lines: tuple[PhysicalLine, ...],
    band_start: int,
    band_end: int,
) -> tuple[float, float]:
    top = lines[band_start].y_top
    bottom = lines[band_end].y_bottom
    return top, bottom


# ---- column boundaries -----------------------------------------------------


def _compute_column_boundaries(
    body_words: tuple[Word, ...],  # noqa: ARG001  (reserved for future gap-based refinement)
    *,
    anchors: dict[CanonicalColumn, HeaderMatch],
    page_width: float,
    min_gutter_width: float,  # noqa: ARG001
) -> dict[CanonicalColumn, XSpan]:
    """Midpoint-based column bands derived from header anchor positions.

    Plan §Stage 2 originally specified a gap-based algorithm (project
    body words onto x-axis, find empty gutters, expand each anchor
    outward to the nearest gutter). That assumes a single column grid.
    The reference BoMs interleave **two** grids in the body — main data
    rows (LLC | Part Identifier | Description | Quantity | …) and
    supplier sub-rows (Mfg Name | Mfg Part | Commodity | …) — whose body
    x-ranges overlap, so no clean gutters exist between adjacent
    anchors and the gap-based algorithm produces nonsensical or
    negative-width bands.

    The plan's documented fallback (§Stage 2.4 — "fall back to the
    midpoint heuristic only for that pair") applies to **every** pair in
    this layout, so we promote it to the primary algorithm: anchors
    sorted by x-centre, each band runs from the midpoint with its left
    neighbour to the midpoint with its right neighbour, with the page
    edges as outer bounds.

    Arguments ``body_words`` and ``min_gutter_width`` are retained for
    a future gap-based refinement (e.g. per-grid gutter detection once
    Stage 3 classifies rows) without changing this function's caller.
    """
    if not anchors:
        return {}

    anchors_sorted: list[tuple[CanonicalColumn, HeaderMatch]] = sorted(
        anchors.items(), key=lambda kv: (kv[1].x0 + kv[1].x1) / 2.0
    )
    page_max = page_width if page_width > 0 else max(m.x1 for _, m in anchors_sorted)

    bands: dict[CanonicalColumn, XSpan] = {}
    for i, (column, match) in enumerate(anchors_sorted):
        if i == 0:
            x_min = 0.0
        else:
            prev_match = anchors_sorted[i - 1][1]
            x_min = (prev_match.x1 + match.x0) / 2.0
        if i + 1 == len(anchors_sorted):
            x_max = float(page_max)
        else:
            next_match = anchors_sorted[i + 1][1]
            x_max = (match.x1 + next_match.x0) / 2.0
        bands[column] = XSpan(x_min=x_min, x_max=x_max)
    return bands


# ---- adjacency invariant ---------------------------------------------------


def _check_adjacency_invariant(
    *,
    columns: dict[CanonicalColumn, XSpan],
    column_order: tuple[CanonicalColumn, ...],
    page_index: int,
) -> ParseWarning | None:
    """Per plan: ``mfg_name`` and ``mfg_part`` are *generally* adjacent.

    Distance >1 in left-to-right rank produces a soft warning so the
    operator can sanity-check, but the parse continues.
    """
    if "mfg_name" not in columns or "mfg_part" not in columns:
        return None
    name_rank = column_order.index("mfg_name")
    part_rank = column_order.index("mfg_part")
    distance = abs(name_rank - part_rank)
    if distance <= 1:
        return None
    name_span = columns["mfg_name"]
    part_span = columns["mfg_part"]
    return ParseWarning(
        code="non_adjacent_supplier_columns",
        detail=(
            f"mfg_name and mfg_part are {distance} columns apart "
            f"(mfg_name x={name_span.x_min:.0f}-{name_span.x_max:.0f}, "
            f"mfg_part x={part_span.x_min:.0f}-{part_span.x_max:.0f}). "
            "Continuing — but operator should verify the column mapping."
        ),
        page=page_index,
    )
