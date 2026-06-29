"""Flat-to-tree conversion — assemble the BoM explosion hierarchy.

The exporter groups raw rows into a flat list of :class:`Part` objects whose
parent/child relationships live inside each part's ``occurrences``. This module
turns that flat list into the nested :class:`PartNode` forest that
:class:`~bom_parser.models.bom.BomDocument` serializes.

The conversion is a full *explosion*: a part consumed under several parents is
emitted once per parent (mirroring how BoM explosion reports read), carrying
that occurrence's quantity. The build is O(n) in the number of occurrences for
adjacency construction, then expands the forest with constant-time hash-map
lookups and a cycle guard so a malformed (cyclic) BoM can't recurse forever.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence

from bom_parser.models.bom import Occurrence, Part, PartNode


def build_part_tree(parts: Sequence[Part]) -> list[PartNode]:
    """Convert flat grouped ``parts`` into a forest of ``PartNode`` roots.

    Roots are the occurrences with no parent, plus any occurrence whose parent
    id wasn't captured as a part (orphans) — so no occurrence is ever dropped.
    Root and child ordering follows the exporter's original emission order,
    keeping the serialized output deterministic.

    Each occurrence carries its *owning* ``Part`` through the build: the same
    internal id can belong to several description-grouped parts (e.g. an
    "alternate BOM code" variant of the same id with its own suppliers), so the
    node's attributes must come from the part that actually emitted the
    occurrence, never from a first-wins id lookup.

    Linkage is keyed by ``occurrence_id`` / ``parent_occurrence_id`` (the source
    record's document-order instance), *not* the part-number string. This keeps
    a re-exploded subassembly's two copies distinct — each collects only its own
    children — and emits children in document order.
    """
    # 1. Index every occurrence by its document-order instance id, and group
    #    occurrences under their parent *instance*. Occurrences with no parent
    #    instance (or a dangling one) are roots.
    Pair = tuple[Occurrence, Part]
    occ_by_id: dict[int, Pair] = {}
    for part in parts:
        for occ in part.occurrences:
            occ_by_id[occ.occurrence_id] = (occ, part)

    children_of: dict[int, list[int]] = {}
    roots: list[int] = []
    for part in parts:
        for occ in part.occurrences:
            parent_id = occ.parent_occurrence_id
            if parent_id is None or parent_id not in occ_by_id:
                roots.append(occ.occurrence_id)
            else:
                children_of.setdefault(parent_id, []).append(occ.occurrence_id)

    # 2. Expand the forest depth-first. Children are visited in document order
    #    (ascending occurrence_id). The ancestor set guards against cycles.
    def build(occ_id: int, ancestors: frozenset[int]) -> PartNode:
        occ, part = occ_by_id[occ_id]
        children: list[PartNode] = []
        if occ_id not in ancestors:
            next_ancestors = ancestors | {occ_id}
            for child_id in sorted(children_of.get(occ_id, ())):
                children.append(build(child_id, next_ancestors))
        return PartNode(
            internal_author_part=occ.internal_author_part,
            description=part.description,
            quantity=occ.quantity,
            total_quantity=part.total_quantity,
            uom=part.uom,
            commodity=part.commodity,
            parent_internal_part=occ.parent_internal_part,
            description_truncated=part.description_truncated,
            suppliers=part.suppliers,
            children=children,
        )

    return [build(occ_id, frozenset()) for occ_id in sorted(roots)]


def iter_nodes(nodes: Iterable[PartNode]) -> Iterator[PartNode]:
    """Yield every node in ``nodes`` and their descendants (pre-order DFS)."""
    for node in nodes:
        yield node
        yield from iter_nodes(node.children)
