"""Typer CLI — thin wrapper around ``bom_parser.pipeline.parse_bom``.

Subcommands:

    bom-parser parse <pdf>   -o out.json [--config config/]
    bom-parser batch  <dir>  -o out_dir/ [--config config/]
    bom-parser inspect <pdf> [--config config/]   # debug

The ``inspect`` command runs the pipeline up through layout detection
and internal-pattern discovery, then prints what was discovered without
emitting JSON. Useful when onboarding a new BoM template.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from bom_parser import __version__
from bom_parser.pipeline import parse_bom
from bom_parser.services.ingestion import ingest
from bom_parser.services.internal_pattern import discover_internal_pattern
from bom_parser.services.layout_detector import (
    detect_page_layout,
    load_header_synonyms,
)
from bom_parser.utils.consts import CONFIG_DIR_NAME, HEADER_SYNONYMS_FILENAME
from bom_parser.utils.discovery import discover_bom_pdfs

app = typer.Typer(
    name="bom-parser",
    help="Deterministic, programmatic PDF Bill of Materials parser.",
    add_completion=False,
)
console = Console()


@app.callback()
def _root(  # pyright: ignore[reportUnusedFunction]  (registered via Typer decorator)
    version: bool = typer.Option(
        False, "--version", help="Print the version and exit."
    ),
) -> None:
    if version:
        console.print(f"bom-parser {__version__}")
        raise typer.Exit()


@app.command("parse")
def parse_cmd(
    pdf: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    output: Path = typer.Option(
        ..., "-o", "--output", help="Path to write the JSON output."
    ),
    config: Path = typer.Option(
        Path(CONFIG_DIR_NAME),
        "--config",
        help="Directory holding header_synonyms.yaml / heuristic_weights.yaml / supplier_aliases.yaml.",
    ),
) -> None:
    """Parse one BoM PDF and write its structured JSON to OUTPUT."""
    document = parse_bom(pdf, config_dir=config)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    console.print(
        f"[green]Wrote {len(document.parts)} parts to {output}[/green]"
    )
    if document.metadata.warnings:
        console.print(
            f"[yellow]{len(document.metadata.warnings)} warning(s):[/yellow]"
        )
        for warning in document.metadata.warnings:
            console.print(f"  - {warning.code}: {warning.detail}")


@app.command("batch")
def batch_cmd(
    directory: Path = typer.Argument(
        ..., exists=True, file_okay=False, readable=True
    ),
    output_dir: Path = typer.Option(
        ..., "-o", "--output", help="Directory to write JSON files into."
    ),
    config: Path = typer.Option(
        Path(CONFIG_DIR_NAME),
        "--config",
        help="Directory holding the parser config files.",
    ),
    recursive: bool = typer.Option(
        False, "--recursive", help="Descend into subdirectories looking for PDFs."
    ),
) -> None:
    """Parse every PDF under DIRECTORY into OUTPUT/<name>.json."""
    pdfs = discover_bom_pdfs(directory, recursive=recursive)
    if not pdfs:
        console.print(f"[red]No PDFs found under {directory}[/red]")
        raise typer.Exit(code=1)
    output_dir.mkdir(parents=True, exist_ok=True)
    for pdf in pdfs:
        document = parse_bom(pdf, config_dir=config)
        target = output_dir / f"{pdf.stem}.json"
        target.write_text(document.model_dump_json(indent=2), encoding="utf-8")
        console.print(
            f"  {pdf.name} -> {target.name}  "
            f"({len(document.parts)} parts, "
            f"{len(document.metadata.warnings)} warning(s))"
        )


@app.command("inspect")
def inspect_cmd(
    pdf: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    config: Path = typer.Option(
        Path(CONFIG_DIR_NAME),
        "--config",
        help="Directory holding the parser config files.",
    ),
) -> None:
    """Print discovered layout + internal pattern without emitting JSON."""
    synonyms = load_header_synonyms(config / HEADER_SYNONYMS_FILENAME)
    document_geom = ingest(pdf)
    console.print(
        f"[bold]{pdf.name}[/bold]: {document_geom.page_count} pages, "
        f"{document_geom.total_words} words"
    )

    first_layout, warnings = detect_page_layout(
        document_geom.pages[0],
        synonyms,
        config_path=config / HEADER_SYNONYMS_FILENAME,
    )
    table = Table(title="Page 0 column bands")
    table.add_column("Canonical column")
    table.add_column("x_min", justify="right")
    table.add_column("x_max", justify="right")
    table.add_column("width", justify="right")
    for col in first_layout.column_order:
        span = first_layout.columns[col]
        table.add_row(col, f"{span.x_min:.1f}", f"{span.x_max:.1f}", f"{span.width:.1f}")
    console.print(table)

    # Pool tokens for pattern discovery.
    column_tokens: list[str] = []
    for page in document_geom.pages:
        layout, _ = detect_page_layout(page, synonyms)
        band = layout.columns.get("part_identifier")
        if band is None:
            continue
        for word in page.words:
            cx = (word.bbox.x0 + word.bbox.x1) / 2.0
            if band.contains(cx) and word.bbox.top >= layout.body_y_top:
                column_tokens.append(word.text)
    discovery = discover_internal_pattern(column_tokens)
    console.print(
        f"\n[bold]Discovered internal pattern:[/bold] {discovery.pattern_source}"
    )
    console.print(f"  accepted shapes: {discovery.accepted_shapes}")
    console.print(f"  match rate:      {discovery.match_rate:.1%}")
    if warnings:
        console.print("\n[yellow]Layout warnings on page 0:[/yellow]")
        for w in warnings:
            console.print(f"  - {w.code}: {w.detail}")


@app.command("summary")
def summary_cmd(
    json_path: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True,
        help="Path to a parsed BoM JSON (output of `bom-parser parse`).",
    ),
    desc_width: int = typer.Option(
        80,
        "--desc-width",
        help="Truncate descriptions longer than this many characters.",
    ),
) -> None:
    """Print one line per internal-part occurrence for manual PDF cross-check.

    Format: ``<internal_id> [(parent=<parent_id>)]  |  <description>  |  <suppliers>``
    where ``<suppliers>`` is ``Name1 PartNum1; Name2 PartNum2; ...`` or ``(none)``.

    Each occurrence in the source BoM produces one line, so the output
    can be scrolled side-by-side with the PDF.
    """
    data: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))
    for part in data.get("parts", []):
        desc = part.get("description", "")
        if len(desc) > desc_width:
            desc = desc[: desc_width - 3] + "..."
        suppliers = part.get("suppliers", [])
        if suppliers:
            suppliers_str = "; ".join(
                f"{s['name_normalized']} {s['part_number']}" for s in suppliers
            )
        else:
            suppliers_str = "(none)"
        for occ in part.get("occurrences", []):
            parent = occ.get("parent_internal_part")
            parent_str = f" (parent={parent})" if parent else ""
            # plain print so output is pipe-friendly (no Rich ANSI codes)
            print(
                f"{occ['internal_author_part']}{parent_str}  |  {desc}  |  {suppliers_str}"
            )


if __name__ == "__main__":
    app()
