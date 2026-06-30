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

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from bom_parser import __version__
from bom_parser.models.serp import SerpConfig
from bom_parser.pipeline import parse_bom
from bom_parser.services.ingestion import ingest
from bom_parser.services.internal_pattern import discover_internal_pattern
from bom_parser.services.layout_detector import (
    detect_page_layout,
    load_header_synonyms,
)
from bom_parser.services.serp_client import SerpAuthError, run_searches
from bom_parser.services.serp_exporter import write_workbook
from bom_parser.services.serp_flattener import flatten, unique_queries
from bom_parser.services.tree_builder import iter_nodes
from bom_parser.utils.consts import (
    BRIGHTDATA_TOKEN_ENV,
    BRIGHTDATA_ZONE_ENV,
    CONFIG_DIR_NAME,
    DEFAULT_SERP_CONCURRENCY,
    DEFAULT_SERP_TOP_N,
    HEADER_SYNONYMS_FILENAME,
    JSON_GLOB_PATTERN,
)
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
    node_count = sum(1 for _ in iter_nodes(document.parts))
    console.print(
        f"[green]Wrote {node_count} parts to {output}[/green]"
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
        node_count = sum(1 for _ in iter_nodes(document.parts))
        console.print(
            f"  {pdf.name} -> {target.name}  "
            f"({node_count} parts, "
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
    """Print one line per tree node for manual PDF cross-check.

    Format: ``<internal_id> [(parent=<parent_id>)]  |  <description>  |  <suppliers>``
    where ``<suppliers>`` is ``Name1 PartNum1; Name2 PartNum2; ...`` or ``(none)``.

    Each node in the explosion tree produces one line (a part consumed under
    several parents appears once per parent), so the output can be scrolled
    side-by-side with the PDF.
    """
    data: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))

    def walk(node: dict[str, Any]) -> None:
        desc = node.get("description", "")
        if len(desc) > desc_width:
            desc = desc[: desc_width - 3] + "..."
        suppliers = node.get("suppliers", [])
        if suppliers:
            suppliers_str = "; ".join(
                f"{s['name_normalized']} {s['part_number']}" for s in suppliers
            )
        else:
            suppliers_str = "(none)"
        parent = node.get("parent_internal_part")
        parent_str = f" (parent={parent})" if parent else ""
        # plain print so output is pipe-friendly (no Rich ANSI codes)
        print(
            f"{node['internal_author_part']}{parent_str}  |  {desc}  |  {suppliers_str}"
        )
        for child in node.get("children", []):
            walk(child)

    for root in data.get("parts", []):
        walk(root)


@app.command("enrich")
def enrich_cmd(
    source: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="A parsed BoM JSON file or a directory of them (e.g. ./out).",
    ),
    output_dir: Path = typer.Option(
        ..., "-o", "--output", help="Directory to write the .xlsx file(s) into."
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        help=f"Bright Data API token (overrides ${BRIGHTDATA_TOKEN_ENV}).",
    ),
    zone: str | None = typer.Option(
        None,
        "--zone",
        help=f"Bright Data SERP zone (overrides ${BRIGHTDATA_ZONE_ENV}).",
    ),
    concurrency: int = typer.Option(
        DEFAULT_SERP_CONCURRENCY,
        "--concurrency",
        min=1,
        help=(
            "Max concurrent API requests. Keep at or below your Bright Data "
            "plan's concurrent-request limit to avoid 429s."
        ),
    ),
    top_n: int = typer.Option(
        DEFAULT_SERP_TOP_N, "--top-n", min=1, help="Organic URLs to capture per query."
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Build queries and report counts without calling the API (no credits spent).",
    ),
) -> None:
    """Search each BoM JSON's supplier parts and write a tree-shaped .xlsx.

    For every supplier part the parser found, two Google queries (raw and
    normalized supplier name) are sent to Bright Data's SERP API and the top-N
    organic URLs are recorded. The output spreadsheet preserves the BoM
    parent-child tree via indentation.
    """
    json_files = (
        sorted(source.glob(JSON_GLOB_PATTERN))
        if source.is_dir()
        else [source]
    )
    if not json_files:
        console.print(f"[red]No .json files found under {source}[/red]")
        raise typer.Exit(code=1)

    # Resolve credentials once (CLI flag wins over env). Only required for a
    # real run — a --dry-run never touches the API.
    cfg: SerpConfig | None = None
    if not dry_run:
        api_token = token or os.environ.get(BRIGHTDATA_TOKEN_ENV)
        serp_zone = zone or os.environ.get(BRIGHTDATA_ZONE_ENV)
        if not api_token or not serp_zone:
            console.print(
                f"[red]Missing Bright Data credentials.[/red] Set "
                f"${BRIGHTDATA_TOKEN_ENV} and ${BRIGHTDATA_ZONE_ENV} (or pass "
                f"--token/--zone), or use --dry-run to preview queries."
            )
            raise typer.Exit(code=1)
        cfg = SerpConfig(
            api_token=api_token,
            zone=serp_zone,
            max_concurrency=concurrency,
            top_n=top_n,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    for json_path in json_files:
        data: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))
        rows = flatten(data.get("parts", []))
        queries = unique_queries(rows)

        if dry_run or cfg is None:
            sample = "; ".join(queries[:3])
            console.print(
                f"  {json_path.name}: {len(rows)} rows, "
                f"{len(queries)} unique queries [dim](dry-run)[/dim]"
                + (f"\n      e.g. {sample}" if sample else "")
            )
            continue

        try:
            query_results = asyncio.run(run_searches(queries, cfg))
        except SerpAuthError as exc:
            console.print(
                f"[red]Bright Data authentication failed:[/red] {exc}\n"
                f"Check that ${BRIGHTDATA_TOKEN_ENV} is your account API token "
                f"(Settings -> API tokens, not the zone password) and that "
                f"${BRIGHTDATA_ZONE_ENV} matches the zone name exactly."
            )
            raise typer.Exit(code=1) from exc
        target = output_dir / f"{json_path.stem}.xlsx"
        write_workbook(rows, query_results, target, top_n=top_n)
        console.print(
            f"  {json_path.name} -> {target.name}  "
            f"({len(rows)} rows, {len(queries)} unique queries)"
        )


if __name__ == "__main__":
    app()
