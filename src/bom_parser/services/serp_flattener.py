"""Flatten a parsed BoM JSON tree into enrichment rows + their search queries.

Consumes the serialized ``PartNode`` forest (the ``parts`` array of a
``bom-parser parse`` JSON, see [models/bom.py]) and produces a flat list of
``EnrichmentRow`` in document order — the same depth-first walk the CLI's
``summary`` command uses — while tracking each node's hierarchy ``level`` and
its parent's internal part number.

Query construction (per the SERP task spec):

* ``Q1 = "{name_raw} {part_number} {description}"``
* ``Q2 = "{name_normalized} {part_number} {description}"``
* If ``name_raw == name_normalized`` only ``Q1`` is emitted (the duplicate is
  skipped to save API credits).
* Suppliers flagged ``is_internal_author_part`` are skipped entirely — those
  "part numbers" are the BoM author's own internal ids misattributed to a
  supplier, so a web search on them is wasted spend.

A node with no eligible supplier still yields one supplier-less row, so the
assembly hierarchy remains visible in the exported spreadsheet.
"""

from __future__ import annotations

from typing import Any

from bom_parser.models.serp import EnrichmentRow


def _supplier_queries(supplier: dict[str, Any], description: str) -> list[tuple[str, str]]:
    """Return ``(query_label, query)`` pairs for one supplier.

    ``query_label`` distinguishes the raw-name and normalized-name variants for
    callers that care; both share the row's supplier fields. Empty list when the
    supplier is an internal-part misattribution.
    """
    if supplier.get("is_internal_author_part"):
        return []

    name_raw = (supplier.get("name_raw") or "").strip()
    name_normalized = (supplier.get("name_normalized") or "").strip()
    part_number = (supplier.get("part_number") or "").strip()

    queries: list[tuple[str, str]] = []
    if name_raw:
        queries.append(("raw", f"{name_raw} {part_number} {description}".strip()))
    # Only add the normalized variant when it differs from the raw name.
    if name_normalized and name_normalized != name_raw:
        queries.append(
            ("normalized", f"{name_normalized} {part_number} {description}".strip())
        )
    return queries


def _rows_for_node(
    node: dict[str, Any], *, level: int, parent_part: str | None
) -> list[EnrichmentRow]:
    """Build the row(s) for a single node (excluding its children)."""
    internal_part = node.get("internal_author_part", "") or ""
    description = node.get("description", "") or ""

    rows: list[EnrichmentRow] = []
    for supplier in node.get("suppliers", []):
        queries = _supplier_queries(supplier, description)
        if not queries:  # internal-part misattribution → no searches
            continue
        name_raw = (supplier.get("name_raw") or "").strip() or None
        name_normalized = (supplier.get("name_normalized") or "").strip() or None
        part_number = (supplier.get("part_number") or "").strip() or None
        for _label, query in queries:
            rows.append(
                EnrichmentRow(
                    level=level,
                    parent_part=parent_part,
                    internal_part=internal_part,
                    description=description,
                    name_raw=name_raw,
                    name_normalized=name_normalized,
                    part_number=part_number,
                    query=query,
                )
            )

    # No eligible supplier rows → keep the node in the tree with a bare row.
    if not rows:
        rows.append(
            EnrichmentRow(
                level=level,
                parent_part=parent_part,
                internal_part=internal_part,
                description=description,
            )
        )
    return rows


def flatten(parts: list[dict[str, Any]]) -> list[EnrichmentRow]:
    """Depth-first flatten the ``parts`` forest into ordered enrichment rows."""
    rows: list[EnrichmentRow] = []

    def walk(node: dict[str, Any], *, level: int, parent_part: str | None) -> None:
        rows.extend(_rows_for_node(node, level=level, parent_part=parent_part))
        node_id = node.get("internal_author_part")
        for child in node.get("children", []):
            walk(child, level=level + 1, parent_part=node_id)

    for root in parts:
        walk(root, level=0, parent_part=None)
    return rows


def unique_queries(rows: list[EnrichmentRow]) -> list[str]:
    """Deduplicated, order-preserving list of non-empty queries to actually send.

    Sub-parts recur under many parents (e.g. ``LB000200``), so the same query
    string appears in many rows; sending each once is the credit-saving cache
    key the SERP client reuses.
    """
    seen: dict[str, None] = {}
    for row in rows:
        if row.query:
            seen.setdefault(row.query, None)
    return list(seen)
