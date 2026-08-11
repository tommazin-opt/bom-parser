# bom-parser

Turn engineering **Bill of Materials PDFs** into structured data — and then into a
supplier-research spreadsheet.

The parser reads a BoM PDF, works out its table layout from the page geometry, rebuilds
the parent/child assembly tree, extracts every supplier and manufacturer part number it
can find, and writes clean JSON. A second stage takes that JSON, googles every supplier
part through the Bright Data SERP API, and writes an Excel workbook with the top result
URLs next to each part.

```
BoM PDF  ──▶  bom-parser parse  ──▶  structured .json  ──▶  bom-parser enrich  ──▶  .xlsx
                                       (assembly tree,                            (+ search
                                        suppliers, MPNs)                           result URLs)
```

**Nothing about the parser is hard-coded to one BoM format.** Column headers are
recognised from a synonym list in [`config/header_synonyms.yaml`](config/header_synonyms.yaml),
column positions are measured from the document's own word distribution, and the BoM
author's internal part-numbering scheme is *learned at runtime* from the document. Adding
support for a new BoM template is normally a YAML edit, not a code change.

---

## Documentation

| Document | Read this if… |
| --- | --- |
| **[docs/HANDOVER.md](docs/HANDOVER.md)** | You are *taking ownership* of this project and aren't a software engineer. Current status, the inherited backlog, and a step-by-step method for directing changes through Claude Code safely. |
| **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** | You want to *run* the tool. No programming knowledge assumed — installation, every command, how to read the output, and how to fix the common problems yourself by editing config files. |
| **[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)** | You want to *change* the tool. Architecture, all seven pipeline stages, the data model, testing, debugging, and step-by-step recipes for the usual modifications. Written for someone early in their software career. |

---

## Quick start

Requires Python 3.10 or newer.

```powershell
# From the project root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Then:

```powershell
# One PDF -> JSON
bom-parser parse "path\to\UA000456AF Bill of Materials.pdf" -o out\456.json

# Read it back in human form, one line per part
bom-parser summary out\456.json

# JSON -> Excel with Google result URLs (needs Bright Data credentials)
bom-parser enrich out\456.json -o out\ --dry-run   # preview, spends nothing
bom-parser enrich out\456.json -o out\             # the real run
```

On macOS/Linux the only difference is `source .venv/bin/activate` and forward slashes.

---

## Commands

| Command | Purpose |
| --- | --- |
| `bom-parser parse <pdf> -o <out.json>` | Parse one PDF into structured JSON. |
| `bom-parser batch <dir> -o <out_dir>` | Parse every PDF in a directory. Add `--recursive` to descend into subfolders. |
| `bom-parser inspect <pdf>` | Diagnostic. Prints the detected column bands and the learned internal-part pattern without writing anything. Use this first when onboarding a new BoM template. |
| `bom-parser summary <out.json>` | Prints one plain-text line per part so you can scroll it side by side with the PDF. |
| `bom-parser enrich <json-or-dir> -o <dir>` | Google every supplier part via Bright Data and write a tree-shaped `.xlsx`. `--dry-run` previews the queries without spending API credits. |

Every command accepts `--config <dir>` (default `config/`). Run from the project root
unless you pass it explicitly.

All commands are also reachable as `python -m bom_parser <command>` — useful when the
virtual environment's `Scripts/` directory isn't on your `PATH`.

---

## Configuration

Three YAML files in [`config/`](config/) control the parser's behaviour. They are the
intended tuning surface — you can fix most extraction problems here without touching Python.

| File | Controls |
| --- | --- |
| [`header_synonyms.yaml`](config/header_synonyms.yaml) | Which header labels map to which canonical column. Teach the parser a new BoM format by adding the unfamiliar label here. |
| [`supplier_aliases.yaml`](config/supplier_aliases.yaml) | Canonical supplier names and their known variants, so `North Coast Com`, `North Coast`, and `NCC` all normalise to `North Coast Components`. |
| [`heuristic_weights.yaml`](config/heuristic_weights.yaml) | How aggressively the parser decides a token is a real supplier part number rather than stray text. |

The SERP stage additionally needs two environment variables:

| Variable | Value |
| --- | --- |
| `BRIGHTDATA_API_TOKEN` | Your Bright Data **account** API token (Settings → API tokens). Not a zone password. |
| `BRIGHTDATA_SERP_ZONE` | The SERP zone name, e.g. `bom_serp_api`. |

---

## Project layout

```
bom-parser/
├── config/                  Tunable YAML — see above
├── docs/                    User guide and developer guide
├── scripts/
│   └── regenerate_goldens.py   Refresh test snapshots after intentional output changes
├── src/bom_parser/
│   ├── cli.py               Typer CLI — thin wrapper over the pipeline
│   ├── pipeline.py          Orchestrator: the one public entry point, parse_bom()
│   ├── models/              Typed data structures (Pydantic at the boundary, dataclasses inside)
│   ├── services/            One module per pipeline stage
│   └── utils/consts.py      Every threshold, tolerance, and magic string in the project
├── tests/
│   ├── unit/                Fast, no PDFs required
│   ├── integration/         End-to-end; needs sample PDFs in Resources/BoMs/
│   └── fixtures/            Golden JSON snapshots
└── .vscode/                 Interpreter setting + run/debug configurations
```

---

## Development

```powershell
pytest                    # run the test suite
pytest tests/unit         # fast subset — no sample PDFs needed
ruff check src tests      # lint
black src tests           # format
pyright                   # strict type checking
```

Integration tests parse real PDFs and expect them at `Resources/BoMs/`. That folder is
git-ignored, so a fresh clone runs the unit tests only until you drop sample BoMs in.
See the [developer guide](docs/DEVELOPER_GUIDE.md#running-the-tests) for details.

---

## How it works, in one paragraph

Words are pulled off each PDF page with their bounding boxes (never plain text extraction —
overlapping fields corrupt it). Header labels are matched against the synonym list to
identify columns, and each column's true horizontal extent is measured from gaps in the
body text. The internal part-number scheme is inferred by abstracting every Part Identifier
token to a letter/digit shape and synthesising a regex from the common shapes. Physical
lines are then classified — part identifier, description, or supplier row — and grouped into
records, with a depth-marker stack (`1`, `.2`, `..3`) rebuilding the assembly hierarchy.
Supplier part candidates are scored by a weighted heuristic, supplier names are clustered to
canonical forms via alias table plus fuzzy matching, and the flat records are exploded into
a parent/child tree and serialised. The full stage-by-stage account is in the
[developer guide](docs/DEVELOPER_GUIDE.md#the-pipeline-stage-by-stage).

---

## License and status

Version 0.1.0 — internal tooling, no license declared. The JSON schema in
[`src/bom_parser/models/bom.py`](src/bom_parser/models/bom.py) is the parser's public
contract; the enrichment stage and any downstream consumer depend on it, so treat changes
there as breaking.
