# User Guide

**For everyone who needs to run this tool. No programming experience assumed.**

This guide walks you through installing the tool, converting BoM PDFs into data, producing
the supplier-research spreadsheet, and fixing the problems that come up — most of which you
can solve yourself by editing a text file.

---

## Table of contents

1. [What this tool does](#1-what-this-tool-does)
2. [Installing it (once)](#2-installing-it-once)
3. [Opening a terminal in the right place](#3-opening-a-terminal-in-the-right-place)
4. [Step 1 — PDF to data](#4-step-1--pdf-to-data)
5. [Step 2 — checking the result](#5-step-2--checking-the-result)
6. [Step 3 — the supplier spreadsheet](#6-step-3--the-supplier-spreadsheet)
7. [Understanding the JSON output](#7-understanding-the-json-output)
8. [Understanding the Excel output](#8-understanding-the-excel-output)
9. [Warnings and what they mean](#9-warnings-and-what-they-mean)
10. [Fixing problems yourself](#10-fixing-problems-yourself)
11. [Running from Visual Studio Code](#11-running-from-visual-studio-code)
12. [Command reference](#12-command-reference)
13. [Troubleshooting](#13-troubleshooting)
14. [Glossary](#14-glossary)

---

## 1. What this tool does

A **Bill of Materials** (BoM) is the parts list for a manufactured product. A BoM PDF prints
it as a table: an internal part number, a description, a quantity, and — for bought-in parts —
the manufacturer's name and *their* part number. Parts nest inside other parts, so the list
is really a tree: a machine contains a sub-assembly, which contains a bracket, which contains
bolts.

PDFs are designed for printing, not for reading by software. There is no table in the file —
just thousands of pieces of text at particular positions on the page. This tool reconstructs
the table from those positions.

It runs in two stages, and you can stop after the first if that's all you need.

**Stage 1 — `parse`.** PDF in, JSON file out. JSON is a structured text format that other
programs can read. It contains every part, its description, its quantity, its position in the
assembly tree, and every supplier/part-number pair found for it.

**Stage 2 — `enrich`.** JSON in, Excel spreadsheet out. For every supplier part number, the
tool runs a Google search and records the top ten result links. The spreadsheet keeps the
assembly tree visible through indentation, so you can see which part belongs to which
assembly while you research prices and availability.

> **Stage 2 costs money.** Each search is a paid API request through a service called Bright
> Data. A typical BoM generates 200+ searches. There is a `--dry-run` mode that shows you
> exactly how many searches would run, without spending anything. Use it first, every time.

---

## 2. Installing it (once)

You need **Python 3.10 or newer**. To check whether you have it, open a terminal (see the
next section) and type:

```powershell
python --version
```

If you see `Python 3.10.x` or higher, you're set. If you see an error or an older version,
download Python from [python.org/downloads](https://www.python.org/downloads/). During
installation on Windows, **tick the box that says "Add Python to PATH"** — this matters, and
it's easy to miss.

Now install the tool. Run these three commands one at a time from the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Here's what each one does:

1. **`python -m venv .venv`** creates a *virtual environment* — a private copy of Python
   living in a `.venv` folder inside the project. This keeps the tool's requirements
   separate from everything else on your computer, so installing it can't break other
   software. You only do this once.
2. **`.\.venv\Scripts\Activate.ps1`** switches your terminal over to that private copy. Your
   prompt will change to show `(.venv)` at the start. **You do this every time you open a new
   terminal.** If you forget, commands will fail with "not recognized".
3. **`pip install -e ".[dev]"`** downloads the tool's dependencies and installs the
   `bom-parser` command. Takes a minute or two the first time.

Check it worked:

```powershell
bom-parser --help
```

You should see a list of commands: `parse`, `batch`, `inspect`, `summary`, `enrich`.

<details>
<summary><b>If PowerShell blocks the activation script</b></summary>

Windows sometimes refuses to run scripts. If `Activate.ps1` gives you an error about
"execution of scripts is disabled", run this once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Then try activating again. This allows locally-created scripts to run, which is the standard
developer setting.
</details>

<details>
<summary><b>Skipping activation entirely</b></summary>

If activation is a nuisance, you can call the tool through its full path instead. Anywhere
this guide says `bom-parser`, you can substitute:

```powershell
.\.venv\Scripts\python.exe -m bom_parser
```

So `bom-parser parse ...` becomes `.\.venv\Scripts\python.exe -m bom_parser parse ...`.
Longer to type, but it always works and never depends on activation.
</details>

---

## 3. Opening a terminal in the right place

A **terminal** is a window where you type commands. Everything in this guide happens in one.

The tool needs to run **from the project folder** (the one containing `config`, `src`, and
this `docs` folder). It looks for its settings in `config/` relative to wherever you are, so
starting in the wrong place produces confusing "file not found" errors.

**Easiest way:** open the project folder in Visual Studio Code and press `` Ctrl+` `` (the
backtick key, above Tab). A terminal opens at the bottom, already in the right place, and VS
Code usually activates the virtual environment for you.

**Alternative:** open the project folder in File Explorer, click the address bar, type
`powershell`, and press Enter.

To confirm you're in the right place, type `ls` (or `dir`). You should see `config`, `src`,
`tests`, `docs`, and `pyproject.toml` listed.

---

## 4. Step 1 — PDF to data

### A single PDF

```powershell
bom-parser parse "C:\path\to\UA000456AF Bill of Materials.pdf" -o out\456.json
```

Breaking that down:

- `parse` — the command
- `"C:\path\to\...pdf"` — the input PDF. **Wrap the path in double quotes** whenever it
  contains spaces, which BoM filenames usually do.
- `-o out\456.json` — where to write the result. `-o` is short for "output". The folder is
  created automatically if it doesn't exist.

A successful run prints:

```
Wrote 146 parts to out\456.json
1 warning(s):
  - non_adjacent_supplier_columns: mfg_name and mfg_part are 2 columns apart ...
```

Warnings are normal and do not mean the run failed. See
[section 9](#9-warnings-and-what-they-mean).

### Several PDFs at once

```powershell
bom-parser batch "C:\path\to\pdf-folder" -o out\
```

Every PDF in the folder becomes `out\<same-name>.json`. Add `--recursive` to include PDFs in
sub-folders too. Each file's part count and warning count is printed as it completes.

---

## 5. Step 2 — checking the result

The JSON file is readable but dense. The `summary` command prints it as one line per part,
designed to be scrolled alongside the original PDF:

```powershell
bom-parser summary out\456.json
```

```
UA000456  |  Optunity System 2,4,5 Optunity  |  (none)
LA000456 (parent=UA000456)  |  Label Package for UA000456  |  (none)
LB000300 (parent=LA000456)  |  DANGER w/Yellow Triangle Label ...  |  North Coast Components 596-00379
```

Each line is: **internal part number**, then its **parent** in brackets, then the
**description**, then every **supplier and part number** found — or `(none)` for assemblies
and parts with no purchased source.

To search it, or save it for review:

```powershell
bom-parser summary out\456.json > review.txt
```

That writes the output to `review.txt` instead of the screen. Open it in Notepad or Excel.

**What to look for:** descriptions that are cut off mid-word, supplier names attached to the
wrong part, or parts you can see in the PDF that are missing here. If you find any, go to
[section 10](#10-fixing-problems-yourself).

---

## 6. Step 3 — the supplier spreadsheet

### First: the dry run

**Always do this before a real run.** It shows the workload and cost without spending
anything:

```powershell
bom-parser enrich out\456.json -o out\ --dry-run
```

```
  456.json: 301 rows, 208 unique queries (dry-run)
      e.g. North Coast Com 596-00379 Pre-Printed/Blank Template "DANGER" ...
```

- **rows** — how many lines the spreadsheet will have (one per supplier per part, plus one
  for every part that has no supplier, so the tree stays visible).
- **unique queries** — how many searches will actually be sent, and therefore what you pay
  for. It's lower than the row count because the same part appears under many parents and
  identical searches are sent only once.

Check the sample query looks sensible before continuing.

### Setting up credentials

The real run needs a Bright Data account. Set two environment variables — one-time setup that
persists across reboots:

```powershell
setx BRIGHTDATA_API_TOKEN "your-token-here"
setx BRIGHTDATA_SERP_ZONE "bom_serp_api"
```

**Close and reopen your terminal afterwards** — `setx` only affects new sessions. If you're
using VS Code, restart VS Code entirely.

> The token must be your **account API token**, found under Settings → API tokens in the
> Bright Data control panel. A *zone password* looks similar but will be rejected.

### The real run

```powershell
bom-parser enrich out\456.json -o out\
```

You'll see live progress as searches complete — a line per query showing how many are done
and how many links came back. When it finishes you get `out\456.xlsx`.

Useful options:

| Option | What it does |
| --- | --- |
| `--dry-run` | Preview only. Sends nothing, spends nothing. |
| `--concurrency 8` | How many searches run at the same time. Default 4. Higher is faster, but exceeding your Bright Data plan's limit causes rate-limit errors and slows things down. Raise it only once you know your plan's ceiling. |
| `--top-n 5` | How many result links to record per search. Default 10. Doesn't change the number of searches, only how many columns of links you get. |
| `--token` / `--zone` | Supply credentials directly instead of via environment variables. |

You can point `enrich` at a whole folder instead of a single file — `bom-parser enrich out\ -o out\` —
but note that processes *every* JSON in it, so the cost is the sum of all of them.

---

## 7. Understanding the JSON output

You don't need to read the JSON to use the tool, but knowing its shape helps when checking
results. Opening it in a browser or a code editor gives you collapsible sections.

The file has two top-level parts: `metadata` and `parts`.

### `metadata` — how the parse went

```json
{
  "source_file": "UA000456AF Bill of Materials.pdf",
  "parser_version": "0.1.0",
  "extracted_at": "2026-08-09T10:14:22Z",
  "page_count": 12,
  "discovered_internal_pattern": "[A-Z]{2}\\d{6}|[A-Z]\\d{6}",
  "new_supplier_candidates": ["Acme Widgets"],
  "hard_rejected_candidates": [...],
  "warnings": [...]
}
```

The two fields worth your attention:

- **`new_supplier_candidates`** — supplier names the tool had never seen and couldn't match
  to a known company. Each one is a suggestion that you might want to add to the alias file
  (see [section 10](#10-fixing-problems-yourself)). It's not an error — a genuinely new
  supplier appears here the first time too.
- **`hard_rejected_candidates`** — tokens that looked like they might be part numbers but
  were rejected as dates or quantities. Listed so you can confirm nothing real was thrown
  away.

### `parts` — the assembly tree

Each entry is one part, with its sub-parts nested inside `children`:

```json
{
  "internal_author_part": "LB000300",
  "description": "DANGER w/Yellow Triangle Label, 1.35\" X 2.75\", VINYL",
  "quantity": 1.0,
  "total_quantity": 4.0,
  "uom": "EA",
  "commodity": "LABEL",
  "parent_internal_part": "LA000456",
  "description_truncated": false,
  "suppliers": [
    {
      "name_raw": "North Coast Com",
      "name_normalized": "North Coast Components",
      "part_number": "596-00379",
      "confidence_score": 0.6,
      "is_internal_author_part": false
    }
  ],
  "children": []
}
```

Field by field:

| Field | Meaning |
| --- | --- |
| `internal_author_part` | Your company's own part number for this item. |
| `description` | The part description as printed. |
| `quantity` | How many are used **at this position in the tree**. |
| `total_quantity` | The sum across every place this part appears. **It is not multiplied through the parent chain** — if a bolt is used once under each of four brackets, this is 4, regardless of how many brackets the machine needs. For a purchasing total you must multiply down the tree yourself. |
| `uom` | Unit of measure — each, feet, pounds. |
| `commodity` | The part's category code from the BoM. |
| `parent_internal_part` | Which assembly this sits under. Empty for top-level items. |
| `description_truncated` | `true` means the description is known to be incomplete — the PDF printed something over the top of it and part of the text was lost. Check these against the PDF. |
| `suppliers` | Every supplier/part-number pair found. Often empty for assemblies. |
| `children` | The parts used inside this one. |

Inside each supplier:

| Field | Meaning |
| --- | --- |
| `name_raw` | Exactly what the PDF said, e.g. `North Coast Com`. |
| `name_normalized` | The tidied-up canonical name, e.g. `North Coast Components`. |
| `part_number` | The manufacturer's part number. |
| `confidence_score` | 0.0–1.0. How sure the tool is this is a real part number and not stray text. Anything below 0.35 was discarded before reaching the file. |
| `is_internal_author_part` | `true` would mean this "supplier part number" actually looks like one of your own internal numbers — a sign of a mis-read. These are skipped during enrichment because searching for them wastes money. |

**A part that appears under several parents appears several times in this file**, once per
parent, each with its own `quantity`. That's deliberate — it mirrors how a BoM explosion
report reads.

---

## 8. Understanding the Excel output

`out\456.xlsx` has one row per search performed, with the header row frozen so it stays
visible as you scroll.

| Column | Contents |
| --- | --- |
| Hierarchy Level | Depth in the tree. 0 is a top-level unit, 1 its direct children, and so on. |
| Parent Part Number | The assembly this part sits under. |
| Internal Part Number | Your part number, indented to show tree depth. |
| Description | The description, indented to match. |
| Supplier Name Raw | The name as printed in the PDF. |
| Supplier Name Normalized | The cleaned-up canonical name. |
| Supplier Part Number | The manufacturer's part number. |
| Search Query Attempted | The exact text sent to Google. |
| URL 1 … URL 10 | The top result links, best first. Blank if the search returned fewer. |

A few things to expect:

- **Parts with no supplier still get a row**, with the supplier columns empty. This is so the
  assembly structure remains readable — you can see the bracket that holds the bolts, even
  though the bracket itself isn't purchased.
- **Some parts get two rows**, one searching the raw supplier name and one the normalised
  name. Two different phrasings often surface different vendors. When both names are
  identical only one search runs.
- **Empty URL columns** mean the search returned nothing useful. Obscure industrial part
  numbers genuinely have no web presence.

---

## 9. Warnings and what they mean

Warnings appear after a parse and in the JSON's `metadata.warnings`. **None of them stops the
parse.** They flag things worth a human glance.

| Warning | What it means | What to do |
| --- | --- | --- |
| `non_adjacent_supplier_columns` | The supplier-name and supplier-part-number columns aren't side by side, which is unusual. | Open the PDF and confirm the two columns were matched correctly. If the values in the output look right, ignore it. |
| `low_confidence_internal_pattern` | The tool couldn't confidently learn your internal part-number format and fell back to a generic guess. | Run `bom-parser inspect` on the PDF and check the reported match rate. If parts are missing from the output, this is the likely cause — flag it to a developer. |
| `vertically_stacked_supplier_suspected` | A supplier's name and part number appear to be on different lines rather than side by side. | Spot-check those parts in the output against the PDF. |
| `combined_supplier_cell_suspected` | A supplier name and part number look like they're crammed into one cell. | As above — spot-check. |
| `ocr_fallback_used` | A page had almost no machine-readable text. It's probably a scan — a picture of a page rather than a real document. | This version cannot read scanned pages. That page's parts will be missing. You need a text-based PDF. |

Only one warning of each type is reported per document, even if the condition occurs on
several pages.

---

## 10. Fixing problems yourself

Most extraction problems are fixable by editing one of three files in the `config` folder.
They're plain text — open them in Notepad, VS Code, or any editor. **Do not use Word**, which
adds invisible formatting that breaks them.

These files use YAML, where indentation is meaningful. Copy the shape of the existing entries
exactly: two spaces before each `-`, and quotes around any name containing punctuation.

### Problem: "Failed to detect a valid BoM header on page N"

The parse stops completely with a message listing the words it found in the header area and
which columns it couldn't identify. This means the BoM uses a column heading the tool doesn't
recognise.

**Fix:** open [`config/header_synonyms.yaml`](../config/header_synonyms.yaml) and add the
unfamiliar heading under the right category. If a BoM labels its supplier column
`Mfr` instead of the recognised spellings:

```yaml
mfg_name:
  - "Mfg Name"
  - "Manufacturer"
  - "Vendor"
  - "Supplier"
  - "Mfr"          # <-- added
```

Save and re-run. Matching ignores capitalisation and extra spaces, so `MFR` and `Mfr` are
both covered by one entry.

The four categories that *must* be found are `part_identifier`, `description`, `mfg_name`,
and `mfg_part`. The error message tells you which one is missing.

### Problem: the same supplier appears under several different names

You see `North Coast Com`, `North Coast`, and `NCC` treated as three companies. The tool
already merges obvious variants automatically, but it deliberately won't merge names that
merely look similar, in case they're genuinely different companies.

**Fix:** open [`config/supplier_aliases.yaml`](../config/supplier_aliases.yaml). Put the
correct full name on the left and every variant beneath it:

```yaml
"North Coast Components":
  - "North Coast Com"
  - "North Coast"
  - "NCC"
```

Check `metadata.new_supplier_candidates` in your JSON output — it's a ready-made list of
names the tool didn't recognise, which is exactly the list worth triaging here.

### Problem: real part numbers are missing from the output

The tool scores every candidate and discards anything below 0.35 confidence. Short or
unusually-formatted part numbers sometimes fall below the line.

**Fix (careful — this one has side effects):** open
[`config/heuristic_weights.yaml`](../config/heuristic_weights.yaml) and lower
`min_confidence` slightly, say from `0.35` to `0.30`. Re-run and check the result.

Lowering the threshold lets more real parts through **and** more junk. Change it in small
steps, and always check the output afterwards with `bom-parser summary`. If you find yourself
wanting to go below about 0.25, the real problem is elsewhere — raise it with a developer.

### Problem: something else

Run the diagnostic command:

```powershell
bom-parser inspect "path\to\file.pdf"
```

It prints the column boundaries it detected on the first page and the internal part-number
pattern it learned, including a **match rate** — the percentage of part identifiers fitting
the learned pattern. Above 80% is healthy. Much lower means the tool is misreading the
Part Identifier column, and that's worth handing to a developer along with the output of this
command.

---

## 11. Running from Visual Studio Code

If you'd rather click than type, the project ships with ready-made run configurations.

Press `Ctrl+Shift+D` to open the Run and Debug panel, choose a configuration from the
dropdown at the top, and press `F5`:

| Configuration | What it does |
| --- | --- |
| **BoM: parse a PDF** | Asks for a PDF and an output path, then parses. |
| **BoM: inspect a PDF (layout debug)** | Runs the diagnostic. |
| **BoM: summary of a parsed JSON** | Prints the line-per-part review. |
| **BoM: enrich out/ (dry run, no API credits)** | Previews the search workload. |
| **Python: current file** | Runs whatever file is open — for standalone scripts. |

The prompts come pre-filled with sensible defaults, so it's usually just Enter, Enter.

The plain ▶ play button in the editor's top-right corner is different: it runs the open file
with no arguments. That works for standalone scripts but not for the main tool, which needs
to be told what to do. Use the Run and Debug panel for that.

If a run fails with "No module named bom_parser", VS Code is using the wrong Python. Press
`Ctrl+Shift+P`, type "Python: Select Interpreter", and choose the one inside `.venv`.

---

## 12. Command reference

Every command takes `--help` for its full option list, e.g. `bom-parser enrich --help`.

### `parse` — one PDF to JSON

```
bom-parser parse <pdf> -o <output.json> [--config <dir>]
```

### `batch` — a folder of PDFs to JSON

```
bom-parser batch <directory> -o <output-dir> [--recursive] [--config <dir>]
```

### `inspect` — diagnose a PDF's layout

```
bom-parser inspect <pdf> [--config <dir>]
```

Writes nothing. Prints the detected columns and learned part-number pattern.

### `summary` — human-readable review of a parse

```
bom-parser summary <parsed.json> [--desc-width 80]
```

`--desc-width` sets where long descriptions are truncated for display; it doesn't alter the
data.

### `enrich` — JSON to supplier spreadsheet

```
bom-parser enrich <json-or-directory> -o <output-dir>
                  [--dry-run] [--concurrency 4] [--top-n 10]
                  [--token <token>] [--zone <zone>] 
```

---

## 13. Troubleshooting

**`bom-parser: The term 'bom-parser' is not recognized`**
The virtual environment isn't active. Run `.\.venv\Scripts\Activate.ps1` — you should see
`(.venv)` appear in your prompt. If it's already active, the install step didn't finish;
re-run `pip install -e ".[dev]"`.

**`FileNotFoundError: header synonyms config not found: config\header_synonyms.yaml`**
You're running from the wrong folder. `cd` to the project root — the folder containing
`config` and `src` — and try again. Or pass `--config C:\full\path\to\config`.

**`Failed to detect a valid BoM header on page N`**
An unrecognised column heading. See [section 10](#10-fixing-problems-yourself); the error
message lists the words it found and what's missing.

**`Missing Bright Data credentials`**
`BRIGHTDATA_API_TOKEN` or `BRIGHTDATA_SERP_ZONE` isn't set. Set them with `setx` (see
[section 6](#6-step-3--the-supplier-spreadsheet)) and **open a new terminal** — `setx` doesn't
affect the window you typed it in.

**`Bright Data authentication failed`**
Read the message underneath, which quotes Bright Data directly:
- *"Account is suspended"* — a billing problem. Log in to
  `brightdata.com/cp/setting/billing`. Nothing is wrong with the tool or your token.
- *"client_10000"* — the token is being rejected. The usual cause is using a zone password
  instead of the account API token from Settings → API tokens.

**The parse produces far fewer parts than the PDF shows**
Run `bom-parser inspect` on the file and check the match rate. Below 80% means the internal
part-number pattern wasn't learned properly and records are being missed. Hand the inspect
output to a developer.

**Descriptions are cut off mid-sentence**
Look for `"description_truncated": true` on those parts. The source PDF prints an effectivity
date over the description text, and where they overlap the text is genuinely lost from the
file. It cannot be recovered from the PDF — read those descriptions off the printed page.

**Excel shows `###` instead of text**
The column is too narrow. Double-click the boundary between column headers to auto-fit. The
data is fine.

---

## 14. Glossary

**BoM (Bill of Materials)** — the structured parts list for a manufactured product.

**Canonical name** — the single agreed-upon spelling of a supplier, which all its variants
are mapped to.

**CLI (Command-Line Interface)** — software you drive by typing commands rather than
clicking.

**Commodity** — a category code grouping similar parts, e.g. `HDWARE`, `ELECT`.

**Confidence score** — 0.0 to 1.0, the tool's certainty that a token is a real part number.

**Enrichment** — adding information from outside the document; here, web search results.

**Explosion** — a full expansion of the assembly tree, listing every part once per place it's
used.

**Internal part number** — your own company's identifier for a part, e.g. `LB000300`.

**JSON** — a structured text format that both people and programs can read.

**MPN (Manufacturer Part Number)** — the supplier's own identifier for a part, e.g.
`596-00379`.

**SERP (Search Engine Results Page)** — the page of links a search engine returns. The
enrichment stage collects these programmatically.

**Terminal** — the window where you type commands. Also called a command prompt, console,
or shell.

**UoM (Unit of Measure)** — how a quantity is counted: each, feet, pounds.

**Virtual environment** — a private, self-contained Python installation for one project, so
its requirements can't conflict with other software.

**YAML** — the plain-text format the config files use. Indentation carries meaning, so keep
the existing shape when editing.
