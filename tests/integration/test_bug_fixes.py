"""Regression tests for the six UA000456 extraction/tree bug fixes.

Each test asserts *invariants* (no date, no footer, correct commodity, no
phantom vendor, vendor/child counts, child order) rather than byte-exact
strings: the effectivity date is overprinted on the description baseline, so
some characters it covered are unrecoverable from the text layer and exact
strings would be perpetually red. See the plan / module history for root causes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import regex

from bom_parser.models.bom import BomDocument, PartNode
from bom_parser.models.ingestion import IngestedDocument
from bom_parser.models.internal_pattern import InternalPatternDiscovery
from bom_parser.models.records import RawRecord
from bom_parser.pipeline import parse_bom
from bom_parser.services.exporter import build_bom_document
from bom_parser.services.heuristic_scorer import load_heuristic_weights
from bom_parser.services.tree_builder import iter_nodes
from bom_parser.utils.consts import HEURISTIC_WEIGHTS_FILENAME

_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
_FOOTER_MARKERS = ("BOMRPT", "Explosion/Implosion", "Opti Temp Inc", "Alternate BOM Code")


@pytest.fixture(scope="session")
def doc_456(pdf_456: Path, config_dir: Path) -> BomDocument:
    return parse_bom(pdf_456, config_dir=config_dir)


@pytest.fixture(scope="session")
def nodes_by_id(doc_456: BomDocument) -> dict[str, list[PartNode]]:
    index: dict[str, list[PartNode]] = {}
    for node in iter_nodes(doc_456.parts):
        index.setdefault(node.internal_author_part, []).append(node)
    return index


def _first(nodes_by_id: dict[str, list[PartNode]], pid: str) -> PartNode:
    assert pid in nodes_by_id, f"part {pid} missing from parsed output"
    return nodes_by_id[pid][0]


# ---- BUG 1: description corruption (date overprint, X, flag/commodity leak) --


@pytest.mark.parametrize(
    "pid", ["LB000300", "SA000683", "EL000491", "EL000515", "EL000601", "PL000130"]
)
def test_bug1_no_effectivity_date_in_description(
    nodes_by_id: dict[str, list[PartNode]], pid: str
) -> None:
    if pid not in nodes_by_id:
        pytest.skip(f"{pid} not present in this BoM")
    desc = _first(nodes_by_id, pid).description
    assert _DATE.search(desc) is None, f"{pid} description still carries a date: {desc!r}"


def test_bug1_dimensional_x_preserved(nodes_by_id: dict[str, list[PartNode]]) -> None:
    # The "X" separator must survive (it was being stripped as a flag token).
    assert '1.35" X 2.75"' in _first(nodes_by_id, "LB000300").description


def test_bug1_no_flag_block_in_description(
    nodes_by_id: dict[str, list[PartNode]]
) -> None:
    # The data-row flag block "U EA 0 N 0 AA A" must not leak into description.
    assert "0 N 0" not in _first(nodes_by_id, "LB000300").description


# ---- ISSUE A: stray trailing UOM token on label descriptions ----------------


@pytest.mark.parametrize(
    "pid", ["LB000300", "LB000307", "LB000317", "LB000325", "LB000355"]
)
def test_issueA_no_trailing_uom_on_labels(
    nodes_by_id: dict[str, list[PartNode]], pid: str
) -> None:
    desc = _first(nodes_by_id, pid).description
    assert desc.split()[-1] != "EA", f"{pid} description still ends in stray UOM: {desc!r}"


@pytest.mark.parametrize("pid,tail", [("EL000491", "EL000514"), ("FI000234", "Operating")])
def test_issueA_legitimate_trailing_words_untouched(
    nodes_by_id: dict[str, list[PartNode]], pid: str, tail: str
) -> None:
    # The strip is UOM-equality-gated, so non-label rows keep their real tails.
    assert _first(nodes_by_id, pid).description.split()[-1] == tail


# ---- BUG 2: page-footer leak ------------------------------------------------


@pytest.mark.parametrize(
    "pid",
    ["LB000201", "EL001149", "FT000445", "M006359", "VL000135",
     "EL000515", "EL000984", "HD000472", "PC000128"],
)
def test_bug2_footer_stripped(
    nodes_by_id: dict[str, list[PartNode]], pid: str
) -> None:
    if pid not in nodes_by_id:
        pytest.skip(f"{pid} not present in this BoM")
    desc = _first(nodes_by_id, pid).description
    leaked = [m for m in _FOOTER_MARKERS if m in desc]
    assert not leaked, f"{pid} description still carries footer text {leaked}: {desc!r}"


# ---- BUG 3: note line misparsed as vendor + commodity dropped ---------------


@pytest.mark.parametrize(
    "pid,commodity",
    [("VL000172", "FITT"), ("EL000227", "OBS"), ("EL000271", "ELECT"),
     ("HD000552", "HDWARE")],
)
def test_bug3_commodity_retained(
    nodes_by_id: dict[str, list[PartNode]], pid: str, commodity: str
) -> None:
    assert _first(nodes_by_id, pid).commodity == commodity


def test_bug3_no_phantom_vendors(doc_456: BomDocument) -> None:
    phantom_fragments = (
        "Panel Mount Coupling",
        "SEE PART NOTES FOR",
        "OBSOLETE",
        "Maintains UL",
    )
    all_names = [s.name_raw for n in iter_nodes(doc_456.parts) for s in n.suppliers]
    offenders = [
        frag for frag in phantom_fragments if any(frag in name for name in all_names)
    ]
    assert not offenders, f"phantom vendor names emitted: {offenders}"


def test_bug3_real_vendors_retained(nodes_by_id: dict[str, list[PartNode]]) -> None:
    # The genuine vendors on the BUG-3 parts must survive the reclassification.
    vl = {s.part_number for s in _first(nodes_by_id, "VL000172").suppliers}
    assert {"50785K273", "X0032MRAWF"} <= vl
    hd = {s.part_number for s in _first(nodes_by_id, "HD000552").suppliers}
    assert "CWHK" in hd


# ---- BUG 4: dropped vendor on FT000823 (trailing parenthetical) -------------


def test_bug4_ft000823_keeps_both_vendors(
    nodes_by_id: dict[str, list[PartNode]]
) -> None:
    parts = {s.part_number for s in _first(nodes_by_id, "FT000823").suppliers}
    assert {"129HB-6-4", "53525K17"} <= parts, (
        f"FT000823 should keep Legend + McMaster-Carr, got {parts}"
    )


@pytest.mark.parametrize(
    "pid,expected_part", [("HD000462", "1010-S (36)"), ("HD000464", "1010-S (24)")]
)
def test_bug4_length_parenthetical_preserved(
    nodes_by_id: dict[str, list[PartNode]], pid: str, expected_part: str
) -> None:
    # Only *packaging* parentheticals are stripped (ISSUE B): the length suffix
    # "(36)"/"(24)" on the 80/20 extrusion part number must be preserved, and
    # the McMaster vendor on the row still parses.
    if pid not in nodes_by_id:
        pytest.skip(f"{pid} not present in this BoM")
    parts = {s.part_number for s in _first(nodes_by_id, pid).suppliers}
    assert expected_part in parts, f"{pid} should keep {expected_part!r}, got {parts}"


def test_bug4_packaging_parenthetical_stripped(
    nodes_by_id: dict[str, list[PartNode]]
) -> None:
    # The "(PACK OF 5)" packaging note is stripped from the FT000823 MPN.
    mcmaster = [
        s
        for s in _first(nodes_by_id, "FT000823").suppliers
        if s.name_normalized == "McMaster-Carr"
    ]
    assert mcmaster and mcmaster[0].part_number == "53525K17"


# ---- BUG 5: duplicated subassembly children --------------------------------


def test_bug5_sa000749_each_instance_has_four_children(
    nodes_by_id: dict[str, list[PartNode]]
) -> None:
    instances = nodes_by_id["SA000749"]
    assert len(instances) == 2, "SA000749 should appear twice (re-exploded)"
    expected = {"EL000807", "EL000831", "EL001321", "EL001322"}
    for node in instances:
        kids = {c.internal_author_part for c in node.children}
        assert kids == expected, f"SA000749 instance has wrong children: {kids}"
        assert len(node.children) == 4


# ---- BUG 6: child order preserves document order ----------------------------


def test_bug6_sa000980_children_in_document_order(
    nodes_by_id: dict[str, list[PartNode]]
) -> None:
    sa980 = _first(nodes_by_id, "SA000980")
    order = [c.internal_author_part for c in sa980.children]
    # EL000556 / EL001322 were previously hoisted to the front; they belong in
    # their page-14/15 positions.
    assert order[0] not in {"EL000556", "EL001322"}
    # Page-14 EL001321 precedes EL001322; the page-15 SA000749 sub-block follows.
    assert order.index("EL001321") < order.index("EL001322") < order.index("SA000749")


# ---- ISSUE C: total_quantity is an occurrence-sum, not effective quantity ---


@pytest.mark.parametrize(
    "pid,expected", [("EL000491", 2.0), ("HD000104", 8.0), ("LB000203", 20.0),
                     ("LB000208", 7.0)]
)
def test_issueC_total_quantity_is_occurrence_sum(
    nodes_by_id: dict[str, list[PartNode]], pid: str, expected: float
) -> None:
    # The rolled-up number is the plain sum of each occurrence's line quantity.
    assert _first(nodes_by_id, pid).total_quantity == expected


def test_issueC_total_quantity_does_not_multiply_through_parent_qty(
    config_dir: Path,
) -> None:
    """The discriminating test: a child under a parent with quantity > 1.

    Occurrence-sum yields the child's own line quantity (3); an *effective*
    (parent-multiplied) rollup would yield 3 x 2 = 6. Asserting 3 fails if the
    other interpretation were ever implemented. (The reference BoMs can't tell
    the two apart — every real parent there is qty 1 — hence this synthetic case.)
    """
    parent = RawRecord(
        internal_part="PA000001", description="parent assembly", quantity=2.0,
        uom="EA", commodity="ASSY", depth=1, parent_internal_part=None,
        suppliers=(), page_index=0, line_y=0.0,
    )
    child = RawRecord(
        internal_part="CH000001", description="child widget", quantity=3.0,
        uom="EA", commodity="PART", depth=2, parent_internal_part="PA000001",
        suppliers=(), page_index=0, line_y=10.0,
    )
    ingestion = IngestedDocument(
        source_path=Path("synthetic.pdf"), page_count=1, pages=(), warnings=()
    )
    discovery = InternalPatternDiscovery(
        pattern=regex.compile(r"^[A-Z]{2}\d+$"),
        pattern_source=r"^[A-Z]{2}\d+$",
        accepted_shapes=(), match_rate=1.0, warnings=(),
    )
    weights = load_heuristic_weights(config_dir / HEURISTIC_WEIGHTS_FILENAME)

    doc = build_bom_document(
        (parent, child),
        ingestion=ingestion,
        discovery=discovery,
        layout_warnings=(),
        weights=weights,
        supplier_aliases={},
    )
    child_node = next(
        n for n in iter_nodes(doc.parts) if n.internal_author_part == "CH000001"
    )
    assert child_node.total_quantity == 3.0  # occurrence-sum
    assert child_node.total_quantity != 6.0  # would be effective (parent-multiplied)


# ---- ISSUE D: truncation quality flag ---------------------------------------


@pytest.mark.parametrize("pid", ["LB000300", "LB000308", "LB000311"])
def test_issueD_truncated_descriptions_flagged(
    nodes_by_id: dict[str, list[PartNode]], pid: str
) -> None:
    assert _first(nodes_by_id, pid).description_truncated is True


@pytest.mark.parametrize("pid", ["EL000491", "EL000515", "FI000234"])
def test_issueD_complete_descriptions_not_flagged(
    nodes_by_id: dict[str, list[PartNode]], pid: str
) -> None:
    # Complete words whose body sits left of the date column must not be flagged.
    assert _first(nodes_by_id, pid).description_truncated is False
