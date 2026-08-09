# Developer Guide

**For a developer picking this project up. Assumes you can read Python; assumes nothing about
PDFs, this codebase, or its domain.**

By the end of this you should be able to find your way around, understand why each piece
exists, make a change safely, and know where the sharp edges are.

---

## Table of contents

1. [Orientation](#1-orientation)
2. [Environment setup](#2-environment-setup)
3. [Repository map](#3-repository-map)
4. [The core problem: why parsing a PDF is hard](#4-the-core-problem-why-parsing-a-pdf-is-hard)
5. [The pipeline, stage by stage](#5-the-pipeline-stage-by-stage)
6. [The SERP enrichment subsystem](#6-the-serp-enrichment-subsystem)
7. [The data model](#7-the-data-model)
8. [The configuration system](#8-the-configuration-system)
9. [Code conventions](#9-code-conventions)
10. [Running the tests](#10-running-the-tests)
11. [Debugging playbook](#11-debugging-playbook)
12. [Recipes: how to make common changes](#12-recipes-how-to-make-common-changes)
13. [Known issues and technical debt](#13-known-issues-and-technical-debt)
14. [Where to look first](#14-where-to-look-first)

---

## 1. Orientation

### What the project does

Two things, in sequence:

1. **Parse.** Read a Bill of Materials PDF and produce structured JSON: an assembly tree of
   parts, each with descriptions, quantities, and supplier/manufacturer part-number pairs.
2. **Enrich.** Take that JSON, run a Google search for every supplier part via the Bright
   Data SERP API, and write an Excel workbook with the result URLs.

They're deliberately separate. Parsing is deterministic, offline, and free. Enrichment is
networked, non-deterministic, and costs money per request. Keep that boundary intact.

### The stack

| Concern | Library | Why |
| --- | --- | --- |
| PDF geometry | `pdfplumber` | Gives per-word bounding boxes, which the whole design depends on. |
| PDF metadata | `pypdf` | Title/creator/producer from the document info dictionary. |
| Validation | `pydantic` v2 | The output contract, enforced at the serialisation boundary only. |
| Regex | `regex` | Used instead of stdlib `re` for its richer engine; the codebase is consistent about it. |
| Fuzzy matching | `rapidfuzz` | Supplier-name clustering. |
| CLI | `typer` + `rich` | Subcommands and formatted terminal output. |
| Logging | `structlog` | Structured events during the SERP run. |
| HTTP | `aiohttp` + `truststore` | Concurrent SERP requests, verified against the OS trust store. |
| Excel | `openpyxl` | Workbook output. |

### The one idea to hold onto

**Nothing is hard-coded to a specific BoM format.** Not the column headings, not the column
positions, not the part-number scheme. Every one of those is either learned from the document
at runtime or read from a YAML file. When you're tempted to add `if "Opti Temp" in ...`, stop
and find the generalisable signal instead. The existing code holds this line carefully, and
the one place it doesn't ([footer detection](#13-known-issues-and-technical-debt)) is a known
wart.

---

## 2. Environment setup

Python 3.10 or newer. The checked-in virtual environment runs 3.14.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

`-e` is an *editable* install: the `bom-parser` command points at `src/`, so your edits take
effect immediately with no reinstall. `[dev]` adds pytest, hypothesis, ruff, black, pyright,
and pre-commit. There's also an `[ocr]` extra declaring `pdf2image` and `pytesseract` — they
are **not used by any code yet**, reserved for a future scanned-PDF path.

Verify:

```powershell
bom-parser --help
pytest tests/unit          # should pass without any sample PDFs
```

### Running the CLI

Three equivalent routes:

```powershell
bom-parser parse file.pdf -o out.json                      # console script (needs activation)
python -m bom_parser parse file.pdf -o out.json            # module entry point
.\.venv\Scripts\python.exe -m bom_parser parse file.pdf -o out.json   # no activation needed
```

In VS Code, `Ctrl+Shift+D` gives you pre-built configurations for each subcommand with
breakpoints working — see [`.vscode/launch.json`](../.vscode/launch.json). The editor's plain
▶ button runs the open file with no arguments, which is only useful for standalone scripts.

---

## 3. Repository map

```
src/bom_parser/
├── cli.py                 Typer commands. Thin — argument handling and printing only.
├── pipeline.py            parse_bom(). The single public entry point. Read this first.
├── models/                Typed data structures, no logic
│   ├── bom.py             ★ The output contract. Pydantic. Changing it breaks consumers.
│   ├── geometry.py        Bbox, Word, XSpan, PhysicalLine, PageLayout. Plain dataclasses.
│   ├── ingestion.py       IngestedDocument / IngestedPage
│   ├── records.py         RawRecord, SupplierRow, InProgressRecord — the internal shapes
│   ├── internal_pattern.py InternalPatternDiscovery
│   ├── scoring.py         HeuristicWeights, PartScoreResult
│   └── serp.py            EnrichmentRow, SerpConfig
├── services/              One module per stage. This is where the logic lives.
│   ├── ingestion.py       Stage 1 — PDF to words with coordinates
│   ├── line_grouping.py   Shared primitive — words to physical lines
│   ├── layout_detector.py Stage 2 — find the table's columns
│   ├── internal_pattern.py Stage 4 — learn the internal part-number regex
│   ├── row_assembler.py   Stage 3 — physical lines to logical records  (largest file)
│   ├── heuristic_scorer.py Stage 5 — is this token really a part number?
│   ├── supplier_normalizer.py Stage 6 — cluster supplier name variants
│   ├── exporter.py        Stage 7 — records to BomDocument
│   ├── tree_builder.py    Stage 7 — flat records to nested tree
│   ├── serp_flattener.py  Enrichment — tree to rows + queries
│   ├── serp_client.py     Enrichment — async Bright Data client
│   └── serp_exporter.py   Enrichment — rows to .xlsx
└── utils/
    ├── consts.py          ★ Every threshold, tolerance, pattern, and magic string
    └── discovery.py       Find PDFs in a directory
```

Two files to read before anything else: [`pipeline.py`](../src/bom_parser/pipeline.py) for
the shape of the whole thing, and [`utils/consts.py`](../src/bom_parser/utils/consts.py) for
the tuning surface. `consts.py` is unusually well commented — several constants carry the
history of the bug that produced their current value. Read those comments before changing a
number.

---

## 4. The core problem: why parsing a PDF is hard

A PDF has no tables. It has drawing instructions: *place this glyph at this coordinate*. What
looks like a table to a human is just text that happens to be aligned.

The naive approach, `page.extract_text()`, concatenates text in roughly reading order — and
fails badly here. When a date overlaps a description on the same baseline, you get output
like `Hazar4d/"1 7P/o2p024` where "Hazard" and a date have been interleaved character by
character. That single failure mode is why the entire design is geometry-based: every stage
works with words that carry `(x0, x1, top, bottom)` coordinates, and text is only joined
together once the code knows which column and which line a word belongs to.

Consequences you'll run into:

- **Column boundaries are inferred, not given.** Found by looking for vertical gaps in the
  body text, not by trusting header positions.
- **One logical record spans several physical lines.** A part's identifier, description,
  continuation text, and supplier rows are four or more separate lines.
- **Records span pages.** A part's supplier list can start on page 3 and finish on page 4.
- **Overlapping print destroys text.** Where the PDF prints an effectivity date across a
  description, the covered characters are simply absent from the file. Unrecoverable — the
  parser flags it via `description_truncated` rather than pretending otherwise.

---

## 5. The pipeline, stage by stage

[`pipeline.py`](../src/bom_parser/pipeline.py) wires everything together in ~120 lines,
docstring included. Read it alongside this section.

```
PDF
 │
 ▼  Stage 1  ingestion.py
Words with bounding boxes, per page
 │
 ▼  Stage 2  layout_detector.py   (per page)
Column x-bands + header/body y-boundaries
 │
 ▼  Stage 4  internal_pattern.py  (whole document)
A regex describing the author's internal part numbers
 │
 ▼  Stage 3  row_assembler.py     (per page, threading state across pages)
RawRecords: internal id, description, quantity, depth, supplier rows
 │
 ▼  Stage 7  exporter.py  →  heuristic_scorer.py (Stage 5)
                          →  supplier_normalizer.py (Stage 6)
                          →  tree_builder.py
BomDocument  →  JSON
```

> **The numbering is not the execution order.** Stage numbers come from the original design
> document; stage 4 runs before stage 3 because row assembly needs the learned pattern to
> recognise where records start, and stages 5 and 6 are invoked *by* stage 7 rather than
> standing alone. The arrows above are what actually happens. Don't let the numbers mislead
> you.

### Stage 1 — Ingestion

[`services/ingestion.py`](../src/bom_parser/services/ingestion.py) →
`IngestedDocument`

Drives pdfplumber's `extract_words` with `x_tolerance=1.5` and `y_tolerance=2.0` (both in
`consts.py`) to get every word with its bounding box, font name, and size. Also pulls
document metadata via pypdf.

A page yielding fewer than 20 words is almost certainly a scan. The parser emits an
`ocr_fallback_used` warning and continues with whatever it got — **there is no actual OCR in
this version**, despite the warning's name.

### Stage 2 — Layout detection

[`services/layout_detector.py`](../src/bom_parser/services/layout_detector.py) →
`PageLayout` per page

Runs for every page independently, because page layouts can drift.

1. Group words into physical lines (see the shared primitive below).
2. Find the header band by matching line text against
   [`config/header_synonyms.yaml`](../config/header_synonyms.yaml), case-insensitively and
   whitespace-collapsed.
3. **Infer each column's true x-band from the body, not the header.** Header labels are often
   narrower or offset relative to the data beneath them. The detector projects body words
   onto the x-axis, finds runs of empty bins at least `DEFAULT_MIN_GUTTER_WIDTH` (3.0 points)
   wide, and treats those as gutters between columns.
4. Validate. At least `DEFAULT_MIN_CANONICAL_HEADERS_MATCHED` (3) columns must match, **and**
   all four of `part_identifier`, `description`, `mfg_name`, `mfg_part` must be present.
   Otherwise it raises `HeaderDetectionError`, which carries the page index, the words it
   found in the suspected header, and exactly which columns are missing — so the operator can
   fix the YAML without opening a debugger.

The seven canonical columns are a `Literal` type in
[`models/geometry.py`](../src/bom_parser/models/geometry.py), and the YAML loader validates
its keys against `get_args()` of that type — so adding a column to the `Literal` is enough to
make the config accept it.

#### Shared primitive: line grouping

[`services/line_grouping.py`](../src/bom_parser/services/line_grouping.py)

Clusters words whose `top` coordinates are close into a `PhysicalLine`. The tolerance is
**data-driven**: the median gap between successive distinct `top` values on the page,
multiplied by `DEFAULT_LINE_GROUPING_RATIO` (0.4). A page too sparse to yield a stable median
falls back to a fixed 3.0 points. This matters — a hard-coded tolerance breaks the moment a
BoM uses a different font size.

### Stage 4 — Internal-pattern discovery

[`services/internal_pattern.py`](../src/bom_parser/services/internal_pattern.py) →
`InternalPatternDiscovery`

Every shop numbers its own parts differently (`LB000300`, `M004375`, `UA000456`). Rather than
hard-code a scheme, the parser learns it:

1. Pool every Part-Identifier-column token from the **whole document** (done in
   `pipeline.py`, not the service — the service takes a token stream).
2. Abstract each to a shape: letters → `L`, digits → `D`, anything else → `X`. So `LB000300`
   becomes `LLDDDDDD`.
3. Accept shapes covering at least `DEFAULT_MIN_SHAPE_FREQUENCY` (5%) of tokens.
4. Synthesise a regex, compressing runs: `LLDDDDDD` → `[A-Z]{2}\d{6}`, joined by alternation.
5. Validate the result matches at least `DEFAULT_MIN_PATTERN_MATCH_RATE` (80%) of tokens. If
   not, fall back to the permissive `^[A-Z]{1,4}\d{3,8}$` and emit
   `low_confidence_internal_pattern`.

The learned pattern flows into Stage 3 (recognising where a record starts) and Stage 5
(flagging `is_internal_author_part`).

### Stage 3 — Row assembly

[`services/row_assembler.py`](../src/bom_parser/services/row_assembler.py) → `RawRecord`s

The hardest module in the project, and the biggest at ~770 lines. It turns physical lines
into logical records.

A record looks like this on the page:

```
LINE A:  LB000300                                   ← record start
LINE B:  .2  DANGER Label, 1.35" X 2.75"  4/18/24  1.000000  U EA 0 N
LINE C:  VINYL, 225/roll                            LABEL      ← continuation + commodity
LINE D:  North Coast Com          596-00379         ← supplier row
LINE D': McMaster-Carr            5793T62           ← another supplier row
LINE A': EL000491                                   ← next record starts
```

Lines are classified by **content shape**, not purely by which column they fall in. The
reference BoMs interleave a main grid and a supplier sub-grid whose x-ranges overlap, so
position alone is ambiguous. The classifier asks:

- **record start** — a single token matching the learned internal pattern, at or near the
  `part_identifier` band.
- **supplier row** — the rightmost token sits in the `mfg_part` band and matches
  `PART_NUMBER_SHAPE_PATTERN`, with at least one non-marker token to its left (the name).
- **otherwise** — continuation text belonging to the current record.

Two pieces of state thread through the whole document:

- **`ParentTracker`** — a stack of `(depth, internal_part)`. Depth comes from the dot markers
  the BoM prints (`1`, `.2`, `..3`). For a record at depth *d*, the parent is the nearest
  preceding entry with depth `< d`. Frozen dataclass; `push` returns a new instance rather
  than mutating.
- **`InProgressRecord`** — cross-page carry. When a record's supplier rows spill onto the next
  page, this holds the partial state so those suppliers attach to the right record instead of
  becoming orphans. It captures the `PageLayout` from where the record *started*, since the
  next page's columns may differ slightly. `pipeline.py` calls `finalize_in_progress` after
  the last page to flush whatever is still open.

Several regexes in `consts.py` exist specifically to defend this stage against
misclassification, and their comments record the exact bug each one fixed:

- `DEPTH_MARKER_PATTERN` is anchored with `$` because an earlier `\b` form matched the `80`
  in supplier name `80/20` and swallowed those rows as continuations.
- `QUANTITY_SHAPE_PATTERN` requires the decimal part to end in three or more zeros, because a
  looser `\d{4,}` form ate the genuine part number `763020.0220`.

Change these only with a test that demonstrates the new case, and re-run the golden snapshots.

### Stage 5 — Heuristic scoring

[`services/heuristic_scorer.py`](../src/bom_parser/services/heuristic_scorer.py) →
`PartScoreResult`

`score_part_number()` is a **pure function** — same inputs, same output, no I/O — which makes
it ideal for property-based testing, and there are hypothesis tests exercising it.

Hard rejections score 0.0 immediately: date-shaped (`m/d/yyyy` or ISO), quantity-shaped,
empty. Everything else accumulates weighted signals from
[`config/heuristic_weights.yaml`](../config/heuristic_weights.yaml):

| Signal | Default |
| --- | --- |
| Length within 4–32 characters | +0.50 |
| Length outside that range | −0.40 |
| Internal whitespace | −0.10 per space, *modulated* (see below) |
| Contains a lowercase letter | −0.10 once |
| Contains banned punctuation (`, ? * ; :`) | −0.20 per character |
| Starts and ends with `[A-Z0-9]` | +0.10 |

Result is clamped to `[0, 1]` and compared against `min_confidence` (0.35).

Two subtleties worth knowing:

- **Character-class signals are deliberately neutral.** All-digits, all-letters, and mixed
  are equally plausible for real MPNs, so none of them scores. Resist adding them back.
- **The whitespace penalty is modulated by a "clean token ratio".** Legitimate multi-token
  parts exist (`1010 X 36`, `DMP 331-110-P001-4-5-TAO-`). The scorer computes what fraction
  of whitespace-separated sub-tokens look like genuine part components; at or above 80% the
  penalty is waived entirely, below it the penalty applies at a reduced rate so short
  multi-token parts still squeeze through.

### Stage 6 — Supplier normalisation

[`services/supplier_normalizer.py`](../src/bom_parser/services/supplier_normalizer.py)

`North Coast Com`, `North Coast`, and `NCC` are one company. Resolution is a cascade,
first match wins:

1. **Pre-normalise** for lookup: `unidecode`, strip trailing punctuation, collapse
   whitespace, lowercase. Original casing is retained separately for step 3.
2. **Alias table** — exact match against pre-normalised entries from
   [`config/supplier_aliases.yaml`](../config/supplier_aliases.yaml).
3. **Acronym detection** — a short all-uppercase all-alpha token (2–4 chars) matches any
   canonical whose word initials equal it. `NCC` → `North Coast Components`.
4. **Fuzzy fallback** — `rapidfuzz.process.extractOne` against both the canonical names and
   every raw name already seen in this document, threshold `WRatio ≥ 88`. That number is an
   empirical plateau: abbreviations still fuse, while unrelated companies sharing a stem
   ("North …") stay apart.
5. **New canonical** — the name becomes its own canonical and is recorded in
   `new_supplier_candidates` for operator triage.

The normaliser is **stateful within one document**: names seen earlier become fuzzy-match
targets for later ones, so clusters build up as the parse proceeds. One instance per
document; don't share it across files.

### Stage 7 — Export

[`services/exporter.py`](../src/bom_parser/services/exporter.py) +
[`services/tree_builder.py`](../src/bom_parser/services/tree_builder.py) → `BomDocument`

1. Assign every record a document-order `occurrence_id`, and resolve its
   `parent_occurrence_id` using the same depth-stack rule as `ParentTracker`. This identifies
   the parent **instance**, not just the parent part number — essential when a subassembly is
   exploded under several parents, so each copy collects only its own children.
2. Group records by cleaned, lowercased description. Multiple BoM rows sharing a description
   collapse into one `Part`.
3. Score and normalise every supplier row; drop hard-rejects (recorded in metadata) and
   below-threshold candidates (dropped silently — they're noise by definition).
4. Deduplicate suppliers within a part by `(name_normalized, part_number)`, keeping the
   highest confidence.
5. `build_part_tree` converts the flat parts into the nested `PartNode` forest. It's a full
   explosion: a part used under three parents appears three times. Roots are occurrences with
   no parent, **plus orphans whose parent wasn't captured** — so nothing is ever silently
   dropped. There's a cycle guard via an ancestor set, so a malformed BoM can't recurse
   forever.
6. Assemble metadata and emit. Warnings are deduplicated to one per code.

---

## 6. The SERP enrichment subsystem

Three modules, cleanly separated, driven by `cli.py`'s `enrich` command.

### `serp_flattener.py` — tree to queries

Walks the serialized `PartNode` forest depth-first (the same order `summary` uses) into a flat
list of `EnrichmentRow`, tracking `level` and `parent_part`.

Query construction, and the credit-saving rules baked into it:

- `Q1 = "{name_raw} {part_number} {description}"`
- `Q2 = "{name_normalized} {part_number} {description}"`
- If raw and normalized are identical, **only Q1** is emitted.
- Suppliers flagged `is_internal_author_part` are **skipped entirely** — those are the BoM
  author's own IDs misattributed to a supplier, and searching them is wasted money.
- A node with no eligible supplier still yields one bare row, so the hierarchy stays visible
  in the spreadsheet.

`unique_queries()` then deduplicates while preserving order. This matters: sub-parts recur
under many parents, so the raw row count is meaningfully higher than the number of API calls.
On the reference BoMs it's roughly 300 rows → 208 queries.

### `serp_client.py` — the async client

`run_searches(queries, cfg)` returns `{query: [url, ...]}`.

- Concurrency bounded by an `asyncio.Semaphore` at `cfg.max_concurrency` (default 4,
  deliberately low — the real ceiling is the Bright Data plan's limit, which the code can't
  know).
- Retries on timeout, 429, and 5xx with exponential backoff (`base * 2**attempt`, base 1.0s,
  3 retries). On final failure a query resolves to `[]` so one bad query never aborts the
  file.
- **TLS via `truststore`.** Corporate HTTPS inspection re-signs traffic with a CA that strict
  OpenSSL 3.x rejects. `truststore.SSLContext` verifies against the OS certificate store
  instead of certifi. **Reuse this for any new HTTP code in this repo** — without it, requests
  fail with cert errors on machines behind such a proxy.
- **Bright Data's error shape is a trap.** Upstream errors come back as an outer **HTTP 200
  with an empty body**; the real status is in the `x-brd-status-code` header, details in
  `x-brd-err-code` / `x-brd-err-msg`. `_check_brd_error` inspects these. A credential or zone
  rejection (`client_10000`, or proxy status `407`) raises `SerpAuthError`, which aborts the
  entire run rather than retrying 200 queries into the same failure. Everything else is logged
  and yields no URLs.
- Progress logs via structlog: `serp_run_start`, `serp_progress` per query, `serp_run_done`.
  structlog is unconfigured, so these print with timestamps to the console.

### `serp_exporter.py` — rows to Excel

openpyxl. Eight fixed columns plus `URL 1..N`. Tree depth is conveyed twice on the Internal
Part Number and Description cells: four leading spaces per level *and* openpyxl's alignment
`indent`. Header row is bold and frozen.

---

## 7. The data model

### Two families of types, on purpose

**Plain frozen dataclasses** in `models/geometry.py` and `models/records.py`. These are passed
between stages thousands of times per page, and Pydantic validation on that hot path would be
pure overhead. They use `slots=True` for memory and attribute-access speed.

**Pydantic models** in `models/bom.py`, constructed only at the export boundary — where
validation actually earns its cost, because that's the contract with the outside world.

The Pydantic base is strict:

```python
model_config = ConfigDict(
    extra="forbid",              # unknown fields are an error, not silently kept
    frozen=True,                 # immutable after construction
    str_strip_whitespace=True,
    validate_assignment=True,
)
```

### The output contract

[`models/bom.py`](../src/bom_parser/models/bom.py) is **the public interface of the parser**.
The enrichment stage and any downstream consumer read this JSON. Changing a field name or
type is a breaking change; treat it accordingly.

```
BomDocument
├── metadata: ParseMetadata
│   ├── source_file, parser_version, extracted_at, page_count
│   ├── discovered_internal_pattern
│   ├── new_supplier_candidates: list[str]
│   ├── hard_rejected_candidates: list[HardRejectedCandidate]
│   └── warnings: list[ParseWarning]
└── parts: list[PartNode]          ← the explosion tree
    ├── internal_author_part, description, quantity, total_quantity
    ├── uom, commodity, parent_internal_part, description_truncated
    ├── suppliers: list[Supplier]
    └── children: list[PartNode]   ← recursive
```

`Part` and `Occurrence` are the *internal* flat representation used during assembly. They are
not serialized — `build_part_tree` converts them into the `PartNode` forest that is.

Two closed vocabularies, both `Literal` types, both worth knowing:

```python
RejectionReason = "date_shaped" | "quantity_shaped" | "below_min_confidence" | "empty_token"

WarningCode = "non_adjacent_supplier_columns" | "low_confidence_internal_pattern"
            | "vertically_stacked_supplier_suspected" | "combined_supplier_cell_suspected"
            | "ocr_fallback_used"
```

Adding a value means updating the `Literal` — pyright will then find every place that needs
to handle it.

### The `total_quantity` gotcha

`total_quantity` is an **occurrence sum**, not an exploded quantity. Parent quantities are not
propagated. A bolt used once under each of four brackets has `total_quantity == 4`, no matter
how many brackets the machine uses. Any consumer needing a purchasing rollup must multiply
through the parent chain itself. This is documented in the model's docstring; it surprises
people regularly.

---

## 8. The configuration system

Three YAML files in [`config/`](../config/), each with a loader that validates shape and
raises a clear error on malformed input.

| File | Loader | Consumed by |
| --- | --- | --- |
| `header_synonyms.yaml` | `layout_detector.load_header_synonyms` | Stage 2 |
| `supplier_aliases.yaml` | `supplier_normalizer.load_supplier_aliases` | Stage 6 |
| `heuristic_weights.yaml` | `heuristic_scorer.load_heuristic_weights` | Stage 5 |

All three are loaded once in `parse_bom()` and passed down. No stage reads a file itself,
which keeps them testable.

**Constants versus config.** `utils/consts.py` holds every threshold in the project. A value
belongs in YAML instead when an *operator* should be able to change it without a code review —
currently the header synonyms, the supplier aliases, and the scoring weights. Everything else
(tolerances, gutter widths, regex shapes) stays in `consts.py` where it can carry an
explanatory comment and be type-checked.

Note that `heuristic_weights.yaml` duplicates the defaults in `models/scoring.py`
`HeuristicWeights`. The two must stay in sync or the property tests break. If you change one,
change the other.

---

## 9. Code conventions

Follow what's there — the codebase is consistent, and consistency is most of its readability.

- **`from __future__ import annotations`** at the top of every module.
- **Keyword-only arguments** for anything beyond the primary input: `def f(x, *, config, weights)`.
  Call sites read as documentation.
- **Frozen dataclasses** for internal types. Mutation is done by constructing a new instance
  (see `ParentTracker.push`).
- **Pure functions** wherever the work allows. `score_part_number`, `discover_internal_pattern`,
  `group_into_physical_lines`, and `build_part_tree` are all pure, and all are directly
  unit-testable because of it.
- **Every magic number lives in `consts.py`** with a comment explaining what it is and, where
  relevant, what broke to produce that value.
- **Docstrings carry the *why*.** Most modules open with an explanation of the stage's problem
  and approach. Keep this up; it's why the project is learnable at all.
- **Errors carry diagnostics.** `HeaderDetectionError` includes the page, the observed words,
  and the missing columns. When you raise something, give the operator enough to act.

Tooling, all configured in [`pyproject.toml`](../pyproject.toml):

```powershell
ruff check src tests     # lint: E, F, I, N, W, UP
black src tests          # format, line length 100
pyright                  # strict mode over src and tests
```

Strict pyright is not decorative — it's why the pipeline's typed data flow is trustworthy.
Expect to add explicit annotations, and prefer fixing a type over `# type: ignore`. Where the
existing code does suppress, it names the rule and gives a reason:
`# pyright: ignore[reportUnusedFunction]  (registered via Typer decorator)`.

There's a pre-commit config dependency declared; run `pre-commit install` if you want the
hooks active locally.

---

## 10. Running the tests

```powershell
pytest                      # everything
pytest tests/unit           # fast, no PDFs needed
pytest tests/unit/test_heuristic_scorer.py -v
pytest --cov=bom_parser     # with coverage
```

`pyproject.toml` sets `testpaths = ["tests"]` and `addopts = "-v --tb=short"`.

### Test layout

**`tests/unit/`** — pure-function tests, no PDFs required. Covers the scorer, internal-pattern
discovery, row assembler, supplier normalizer, and all three SERP modules. Several use
**hypothesis** for property-based testing: instead of fixed examples, hypothesis generates
many inputs and checks invariants hold (e.g. confidence always lands in `[0, 1]`). When one
fails it shrinks to a minimal reproducing case and remembers it in `.hypothesis/`.

**`tests/integration/`** — end-to-end runs over real PDFs.
- `test_pipeline.py` — structural assertions: parts are emitted, confidences are in range,
  known internal IDs match the learned pattern, known supplier parts don't.
- `test_golden_snapshots.py` — full-output comparison against `tests/fixtures/expected_*.json`.
- `test_bug_fixes.py` — regression tests, one per historical bug.

### Integration tests need PDFs you have to supply

[`tests/conftest.py`](../tests/conftest.py) expects sample BoMs at:

```
Resources/BoMs/UA000456AF Bill of Materials.pdf
Resources/BoMs/UA000457AD Bill of Materials.pdf
```

`Resources/BoMs/` is git-ignored, so **a fresh clone has no PDFs and every integration test
errors** with `FileNotFoundError`. That's expected, not a broken checkout. Copy the sample
BoMs into that folder and they run. Until you do, `pytest tests/unit` is your signal.

### Golden snapshots

`test_golden_snapshots.py` parses each sample PDF and compares the *entire* deserialized
output against `tests/fixtures/expected_*.json` (with `extracted_at` replaced by a placeholder
so the comparison is stable across runs).

This is the project's strongest safety net: any unintended change to any part of the pipeline
shows up as a diff. When a change is *deliberate*:

```powershell
python scripts/regenerate_goldens.py
```

Then **read the diff before committing it**. The whole value of the mechanism is that someone
looks at what changed. A regenerated golden nobody inspected is worse than no golden at all.

> **Current state:** the checked-in goldens are stale. With sample PDFs in place the suite
> reports 142 passing and 2 golden-snapshot failures. The differences are all improvements the
> parser has made since the fixtures were generated — word-splitting fixed
> (`Wilkerso n Filters` → `Wilkerson Filters`) and a supplier part number no longer bleeding
> into a description. Regenerating is almost certainly the right call, but confirm the diff
> matches that description before you do.

---

## 11. Debugging playbook

### The parse fails outright

`HeaderDetectionError` is the only error the pipeline raises by design. Its message names the
page, the words it saw, and the missing columns. Nearly always the fix is a new entry in
`header_synonyms.yaml`.

### The output is wrong but the parse succeeded

Work down the pipeline in order — a problem at stage 2 will look like a problem at stage 7.

**1. Check the layout.**

```powershell
bom-parser inspect "path\to\file.pdf"
```

Prints page 0's column bands and the learned pattern with its match rate. If the bands look
wrong (overlapping, absurdly wide, a missing column), the problem is stage 2 and nothing
downstream will be right.

**2. Check the learned pattern.** Same command. Match rate below 80% means stage 4 failed, so
stage 3 can't find record starts, so records are being missed entirely.

**3. Look at the raw words.** When you need to see what pdfplumber actually produced:

```python
from bom_parser.services.ingestion import ingest

doc = ingest("Resources/BoMs/UA000456AF Bill of Materials.pdf")
page = doc.pages[0]
for w in page.words[:40]:
    print(f"{w.bbox.x0:7.1f} {w.bbox.top:7.1f}  {w.text!r}")
```

**4. Look at the assembled records.** The layer between raw words and final output:

```python
from bom_parser.services.ingestion import ingest
from bom_parser.services.layout_detector import detect_page_layout, load_header_synonyms
from bom_parser.services.internal_pattern import discover_internal_pattern
from bom_parser.services.row_assembler import ParentTracker, assemble_records
from bom_parser.models.records import InProgressRecord

syn = load_header_synonyms("config/header_synonyms.yaml")
doc = ingest("Resources/BoMs/UA000456AF Bill of Materials.pdf")
page = doc.pages[0]
layout, warnings = detect_page_layout(page, syn)
# Approximation: the real pipeline pools only Part-Identifier-band tokens across
# every page. Good enough to inspect one page; expect a slightly different pattern.
discovery = discover_internal_pattern([w.text for w in page.words])

records, _, _ = assemble_records(
    page, layout, discovery.pattern,
    parents=ParentTracker(), in_progress=InProgressRecord(),
)
for r in records:
    print(r.internal_part, r.depth, "|", r.description[:50], "|", len(r.suppliers), "suppliers")
```

**5. Check a specific token's score.**

```python
import regex
from bom_parser.services.heuristic_scorer import score_part_number, load_heuristic_weights

print(score_part_number(
    "596-00379",
    internal_pattern=regex.compile(r"^[A-Z]{2}\d{6}$"),
    weights=load_heuristic_weights("config/heuristic_weights.yaml"),
))
```

### Enrichment problems

Always start with `--dry-run` — it exercises flattening and query construction with no
network at all, which isolates parser-side problems from API problems.

The response headers are where the truth lives. If you need to see them raw, post a single
request to `https://api.brightdata.com/request` and print `dict(response.headers)`; check
`x-brd-status-code` before believing the HTTP status.

Common causes:
- `client_10000` — a zone password used where the account API token is required.
- `client_10020` — account suspended, i.e. billing. Nothing to fix in the code.
- Certificate errors — something bypassed `truststore`. All HTTP in this repo must use it.

---

## 12. Recipes: how to make common changes

### Support a new BoM template

Start with `bom-parser inspect`. Nine times out of ten it's unfamiliar header labels, and the
fix is appending them to `config/header_synonyms.yaml` — no code. If the layout is
structurally different (columns in a different order, multi-line headers), stage 2 is where
the work is.

### Add a new canonical column

Say the BoM has a `Revision` column you want to capture.

1. **`models/geometry.py`** — add `"revision"` to the `CanonicalColumn` Literal. The YAML
   loader validates against this automatically. Only add it to `REQUIRED_CANONICAL_COLUMNS` if
   a page without it should be a hard failure.
2. **`config/header_synonyms.yaml`** — add a `revision:` key with its label variants.
3. **`services/row_assembler.py`** — extract the value from the record's lines using the new
   band, and add it to `RawRecord` in `models/records.py`.
4. **`models/bom.py`** — add the field to `Part` and `PartNode`.
5. **`services/exporter.py`** — carry it through `_build_part`.
6. **`services/tree_builder.py`** — carry it into the `PartNode` construction.
7. Regenerate goldens, review the diff, and add a test.

Let pyright drive steps 3–6: add the field to the model first and it will flag every
construction site that now needs it.

### Tune the scorer

Change `config/heuristic_weights.yaml`, mirror the change in `HeuristicWeights` defaults in
`models/scoring.py`, then run the golden snapshots to see the blast radius. Sweep against the
fixtures rather than guessing — the comment on `min_confidence` says exactly this.

### Add a CLI subcommand

Add a function to `cli.py` decorated with `@app.command("name")`. Keep it thin: argument
parsing, calling into a service, and printing. Business logic belongs in `services/`. Use
`typer.Option` / `typer.Argument` with `exists=True` where a path must already exist — Typer
validates before your code runs.

### Add a warning type

Add the code to the `WarningCode` Literal in `models/bom.py`, then emit
`ParseWarning(code=..., detail=..., page=...)` from the relevant stage. Warnings propagate up
to `build_bom_document`, which deduplicates them to one per code. Document it in the user
guide's warning table.

### Support scanned PDFs

Not implemented. The `[ocr]` extra declares `pdf2image` and `pytesseract`, and
`ocr_fallback_used` fires when a page has fewer than 20 words, but no OCR runs. The natural
shape is a fallback inside `services/ingestion.py`: on a sparse page, rasterise, OCR, and
synthesise `Word` objects with bounding boxes from the OCR engine's output. Everything
downstream already works on `Word`s and needs no change — which is the payoff of the geometry
based design.

---

## 13. Known issues and technical debt

Ordered roughly by how likely you are to trip over them.

**Golden fixtures are stale.** Two integration tests fail against current output. The diffs
look like genuine improvements. See [section 10](#10-running-the-tests).

**Integration tests need manually-staged PDFs.** `Resources/BoMs/` is git-ignored, so a fresh
clone can't run them. There's no fixture-generation path and no sanitised sample committed.

**Footer detection is template-specific.** `FOOTER_LINE_MARKERS` in `consts.py` hard-codes
`"BOMRPT"`, `"Explosion/Implosion"`, and `"Alternate BOM Code"` — strings from one company's
report generator. This is the one place the "nothing is hard-coded to a format" principle is
broken. A different BoM template's footer will be folded into the last record on each page.
Generalising this (detecting repeated text at consistent y-positions across pages) is a
well-scoped improvement.

**`bom-parser --version` doesn't work.** It's declared on the Typer root callback, but the app
lacks `invoke_without_command=True`, so running it with no subcommand errors with "Missing
command" instead. One-line fix in `cli.py`; adding `no_args_is_help=True` at the same time
would make a bare invocation print help rather than an error.

**`docs/FUTURE_BOM_FORMATS.md` is referenced but doesn't exist.** Cited in
`services/ingestion.py` and `utils/consts.py` as the home of the planned OCR approach. Either
write it or drop the references.

**No OCR despite the warning name.** `ocr_fallback_used` means "this page looks scanned and we
did nothing about it."

**`prototyping.py` imports `requests`, which isn't a declared dependency.** It's a git-ignored
scratch file for poking the Bright Data API by hand, and it happens to work because `requests`
is present transitively. Not part of the application.

**`heuristic_weights.yaml` duplicates `HeuristicWeights` defaults.** Two sources of truth that
must be manually kept in sync; the property tests assume they match.

**`total_quantity` semantics surprise people.** See [section 7](#the-total_quantity-gotcha).

**Enrichment has no result caching.** Re-running `enrich` on the same JSON re-sends every
query and re-spends the credits. Deduplication happens within a run, not across runs. A
persistent cache keyed on the query string would be a cheap, high-value addition.

---

## 14. Where to look first

If you're about to make your first change, read in this order:

1. [`src/bom_parser/pipeline.py`](../src/bom_parser/pipeline.py) — 120 lines, the whole shape
   of the system.
2. [`src/bom_parser/models/bom.py`](../src/bom_parser/models/bom.py) — the output contract, and
   therefore what everything is working toward.
3. [`src/bom_parser/utils/consts.py`](../src/bom_parser/utils/consts.py) — every tunable, with
   the reasoning behind it.
4. Whichever stage in `services/` your change touches. Each module's docstring explains its
   problem before its solution.

Then run `bom-parser inspect` on a sample PDF and read the output next to the PDF itself. Ten
minutes of that teaches you more about the domain than any amount of reading code.
