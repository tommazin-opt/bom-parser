"""Unit tests for the SERP flattener.

Covers hierarchy-level/parent tracking, the two-query construction rule
(including raw==normalized dedup), the ``is_internal_author_part`` skip, that
supplier-less nodes still yield a tree row, and cross-node query dedup.
"""

from __future__ import annotations

from typing import Any

from bom_parser.services.serp_flattener import flatten, unique_queries


def _tree() -> list[dict[str, Any]]:
    """A 4-deep tree mirroring out/456.json's UA->LA->LB000300->LB000200 chain,
    plus a node whose only supplier is an internal-part misattribution, plus a
    supplier whose raw/normalized names differ."""
    return [
        {
            "internal_author_part": "UA000456",
            "description": "Optunity System",
            "suppliers": [],
            "children": [
                {
                    "internal_author_part": "LA000456",
                    "description": "Label Package",
                    "suppliers": [],
                    "children": [
                        {
                            "internal_author_part": "LB000300",
                            "description": "DANGER Label",
                            "suppliers": [],
                            "children": [
                                {
                                    "internal_author_part": "LB000200",
                                    "description": "Template",
                                    "suppliers": [
                                        {
                                            "name_raw": "North Coast Com",
                                            "name_normalized": "North Coast Components",
                                            "part_number": "596-00379",
                                            "is_internal_author_part": False,
                                        }
                                    ],
                                    "children": [],
                                }
                            ],
                        },
                        {
                            "internal_author_part": "M006359",
                            "description": "Base Plate",
                            "suppliers": [
                                {
                                    "name_raw": "Trimet",
                                    "name_normalized": "Trimet",
                                    "part_number": "M006359",
                                    "is_internal_author_part": True,
                                }
                            ],
                            "children": [],
                        },
                        {
                            "internal_author_part": "EL000515",
                            "description": "Coupler",
                            "suppliers": [
                                {
                                    "name_raw": "Cat 6",
                                    "name_normalized": "Cat 6",
                                    "part_number": "102050",
                                    "is_internal_author_part": False,
                                }
                            ],
                            "children": [],
                        },
                    ],
                }
            ],
        }
    ]


def test_levels_and_parents() -> None:
    rows = flatten(_tree())
    by_part = {r.internal_part: r for r in rows}
    assert by_part["UA000456"].level == 0
    assert by_part["UA000456"].parent_part is None
    assert by_part["LA000456"].level == 1
    assert by_part["LA000456"].parent_part == "UA000456"
    assert by_part["LB000300"].level == 2
    assert by_part["LB000200"].level == 3
    assert by_part["LB000200"].parent_part == "LB000300"


def test_two_queries_when_names_differ() -> None:
    rows = [r for r in flatten(_tree()) if r.internal_part == "LB000200"]
    queries = {r.query for r in rows}
    assert queries == {
        "North Coast Com 596-00379 Template",
        "North Coast Components 596-00379 Template",
    }


def test_single_query_when_names_identical() -> None:
    rows = [r for r in flatten(_tree()) if r.internal_part == "EL000515"]
    assert len(rows) == 1
    assert rows[0].query == "Cat 6 102050 Coupler"


def test_internal_author_part_supplier_skipped() -> None:
    rows = [r for r in flatten(_tree()) if r.internal_part == "M006359"]
    # The only supplier is is_internal_author_part=True, so no query rows — but
    # the node still appears as a bare tree row.
    assert len(rows) == 1
    assert rows[0].query is None
    assert rows[0].name_raw is None


def test_supplierless_nodes_present() -> None:
    rows = flatten(_tree())
    parts_with_bare_rows = {
        r.internal_part for r in rows if r.query is None
    }
    assert {"UA000456", "LA000456", "LB000300", "M006359"} <= parts_with_bare_rows


def test_unique_queries_dedup_and_excludes_empty() -> None:
    rows = flatten(_tree())
    queries = unique_queries(rows)
    # No empty/None queries, and no duplicates.
    assert "" not in queries
    assert len(queries) == len(set(queries))
    assert "Cat 6 102050 Coupler" in queries
