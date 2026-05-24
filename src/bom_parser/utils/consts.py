"""Tunable defaults and well-known string constants for the parser pipeline.

Every numeric threshold, tolerance, file path, filename, and external-API
field name used by the parser should have its canonical definition here.
Runtime callers may override the tunables via keyword argument;
configuration files (``config/heuristic_weights.yaml``) will override
them when wired in by later stages.

Grouped by purpose so the next reader can trace each constant back to
the plan or to the external interface it represents.
"""

from __future__ import annotations

from typing import Final

# ---- Project layout: directory and filename constants ----------------------

# Project subdirectories. The parser is generalized and never references
# these paths directly except through ``utils.discovery`` and the CLI.
RESOURCES_DIR_NAME: Final[str] = "Resources"
BOMS_DIR_NAME: Final[str] = "BoMs"
CONFIG_DIR_NAME: Final[str] = "config"
DOCS_DIR_NAME: Final[str] = "docs"

# Config filenames. Adding a new tunable config file means adding its
# filename here and a loader that consumes it.
HEADER_SYNONYMS_FILENAME: Final[str] = "header_synonyms.yaml"
SUPPLIER_ALIASES_FILENAME: Final[str] = "supplier_aliases.yaml"
HEURISTIC_WEIGHTS_FILENAME: Final[str] = "heuristic_weights.yaml"

# Glob pattern used by ``utils.discovery.discover_bom_pdfs``.
PDF_GLOB_PATTERN: Final[str] = "*.pdf"
PDF_GLOB_PATTERN_RECURSIVE: Final[str] = "**/*.pdf"


# ---- External API field names ---------------------------------------------

# pypdf returns the PDF document-information dictionary with these
# slash-prefixed keys (per PDF 32000-1:2008 §14.3.3).
PDF_META_TITLE_KEY: Final[str] = "/Title"
PDF_META_CREATOR_KEY: Final[str] = "/Creator"
PDF_META_PRODUCER_KEY: Final[str] = "/Producer"

# pdfplumber's ``Page.extract_words`` ``extra_attrs`` selectors.
PLUMBER_FONTNAME_ATTR: Final[str] = "fontname"
PLUMBER_SIZE_ATTR: Final[str] = "size"


# ---- Stage 1: ingestion -----------------------------------------------------

# ---- Stage 1: ingestion -----------------------------------------------------

# pdfplumber's per-character merging tolerances. The plan ships these
# defaults verbatim; loosen them only with a corresponding fixture change.
DEFAULT_X_TOLERANCE: Final[float] = 1.5
DEFAULT_Y_TOLERANCE: Final[float] = 2.0

# A page yielding fewer than this many text-layer words is flagged with
# an ``ocr_fallback_used`` warning. v1 does not actually run OCR — see
# docs/FUTURE_BOM_FORMATS.md. Reference BoMs always far exceed this.
DEFAULT_MIN_WORDS_FOR_TEXT_LAYER: Final[int] = 20


# ---- Shared utility: physical-line grouping --------------------------------

# Y-tolerance used to merge words into the same physical line, expressed as
# a fraction of the document's *observed* median line spacing. Per plan
# §Stage 3, the tolerance must be data-driven, not a magic number.
DEFAULT_LINE_GROUPING_RATIO: Final[float] = 0.4

# Fallback y-tolerance (in PDF points) used only when a page has too few
# distinct y-positions to estimate a median line spacing.
FALLBACK_LINE_TOLERANCE: Final[float] = 3.0


# ---- Stage 2: layout detection ---------------------------------------------

# Minimum number of canonical columns that must match header synonyms on a
# page before layout detection is considered successful. The plan's
# mandatory four (part_identifier, description, mfg_name, mfg_part) are
# additionally required regardless of this count.
DEFAULT_MIN_CANONICAL_HEADERS_MATCHED: Final[int] = 3

# Minimum x-axis gap (in PDF points) for a run of empty bins in the body
# projection to count as an inter-column gutter. Anything narrower is
# treated as intra-column whitespace (a space between words in one cell).
DEFAULT_MIN_GUTTER_WIDTH: Final[float] = 3.0


# ---- Stage 4: internal-part pattern discovery ------------------------------

# A token-shape (e.g. ``LLDDDDDD``) is accepted as an internal-part shape
# if it covers at least this fraction of all observed Part-Identifier
# column tokens.
DEFAULT_MIN_SHAPE_FREQUENCY: Final[float] = 0.05

# The synthesised regex must match at least this fraction of observed
# Part-Identifier tokens; otherwise the discovery falls back to the
# permissive default below and emits a warning.
DEFAULT_MIN_PATTERN_MATCH_RATE: Final[float] = 0.80

# Fallback regex used when shape inference doesn't yield a confident
# pattern. Deliberately permissive — covers most BoM author conventions
# while still excluding free-form text.
FALLBACK_INTERNAL_PATTERN: Final[str] = r"^[A-Z]{1,4}\d{3,8}$"


# ---- Stage 3: row assembly -------------------------------------------------

# Loose regex used to recognise "this token looks like a supplier part
# number". Allowed characters: ASCII alphanumerics plus the punctuation
# typically found inside MPNs (``-``, ``_``, ``.``, ``/``, ``#``). The
# heuristic scorer in Stage 5 applies a much stricter, weighted check;
# this one only needs to identify supplier rows during assembly.
PART_NUMBER_SHAPE_PATTERN: Final[str] = (
    r"^[A-Za-z0-9][A-Za-z0-9._/#-]{2,30}$"
)

# Hierarchy depth marker at the start of a description-data line, e.g.
# ``1`` (root child), ``.2`` (grandchild), ``..3`` (great-grandchild).
# The captured digit(s) are taken as the depth level. The ``$`` end
# anchor is essential — without it the previous ``\b`` boundary form
# matched ``80/20`` (the 80/20 Inc. supplier name) as a depth marker
# (capturing "80"), which caused 80/20 supplier rows to be misclassified
# as continuation lines and bleed into description text. Digit count is
# deliberately unbounded so deep hierarchies (``..15``, ``...100``) are
# still recognised.
DEPTH_MARKER_PATTERN: Final[str] = r"^(?:\.{0,9})(\d{1,2})$"

# Quantity-shaped token (BoM author writes them with 6 trailing zeros,
# e.g. ``1.000000``, ``13.000000``). Used to *recognise* quantities so we
# can pull them off description lines and avoid emitting them as
# supplier-part candidates.
QUANTITY_SHAPE_PATTERN: Final[str] = r"^\d+\.\d{4,}$"

# Date-shaped tokens that should be stripped from description text.
DATE_SHAPE_PATTERN: Final[str] = r"^\d{1,2}/\d{1,2}/\d{2,4}$"

# Minimum length for a token in the commodity band to be accepted as the
# commodity code. The BoM's Rev / ECN / Alternate-Part flags are
# single-letter or 2-letter uppercase codes ("A", "AA", "B") that
# coincidentally land in the commodity x-band on the data line; real
# commodity codes ("HDWARE", "ELECT", "HOSE", "MECH") are ≥ 3 chars.
DEFAULT_MIN_COMMODITY_LENGTH: Final[int] = 3


# ---- Stage 5: heuristic scoring -------------------------------------------

# Characters that disqualify a token from looking like a real supplier
# part number. Quotes, commas, question / asterisk / semicolon / colon
# are almost always extraction artefacts in this domain (description
# fragments slipping into the mfg_part bbox, OCR garbage, etc.).
SCORING_BAD_PUNCTUATION: Final[frozenset[str]] = frozenset(
    [",", '"', "'", "?", "*", ";", ":"]
)

# Punctuation that legitimately appears inside MPNs and should not be
# penalised (``-`` in ``596-00379``, ``/`` in ``B08-02-FL00``, ``.`` in
# ``F919-0106-44-4X12.00.1-SS``, ``#`` in catalog codes, ``_`` in some
# vendor schemes).
SCORING_ALLOWED_PUNCTUATION: Final[frozenset[str]] = frozenset(
    ["-", "_", "/", ".", "#"]
)


# ---- Stage 6: supplier normalisation ---------------------------------------

# rapidfuzz ``WRatio`` threshold for clustering an unknown supplier name
# with an existing canonical entry. Plan §Stage 6: 88 is the empirical
# plateau where abbreviation-style aliases ("McMaster-" / "McMaster") still
# fuse while unrelated companies sharing a stem ("North …") stay apart.
DEFAULT_SUPPLIER_FUZZY_MIN_WRATIO: Final[int] = 88

# Acronym candidates are uppercase, all-alpha, single-token, this long.
ACRONYM_MIN_LENGTH: Final[int] = 2
ACRONYM_MAX_LENGTH: Final[int] = 4

# Trailing punctuation stripped during pre-normalisation.
SUPPLIER_TRAILING_PUNCT: Final[str] = ".,;:!?"


# ---- Stage 7: export -------------------------------------------------------

# Flag-column tokens that bleed into description text on this BoM
# template (QC, UM, LTO, BT, SCR, ECN, Rev, PT, Alternate-Part).
# The pattern matches single uppercase letters, two-letter uppercase
# codes, and single digits — the shapes those flag fields take on
# the reference docs.
FLAG_TOKEN_PATTERN: Final[str] = r"^(?:[A-Z]{1,2}|\d)$"
