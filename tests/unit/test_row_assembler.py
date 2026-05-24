"""Unit tests for row_assembler's ParentTracker.

The tracker is a pure data structure — easy to cover exhaustively
without setting up a full PDF.
"""

from __future__ import annotations

from bom_parser.services.row_assembler import ParentTracker


def test_empty_tracker_has_no_parent() -> None:
    t = ParentTracker()
    assert t.parent_of(1) is None
    assert t.parent_of(5) is None


def test_push_and_lookup() -> None:
    t = ParentTracker().push(1, "UA000456").push(2, "LB000300")
    assert t.parent_of(3) == "LB000300"
    assert t.parent_of(2) == "UA000456"
    assert t.parent_of(1) is None


def test_push_at_same_depth_replaces() -> None:
    """A new push at depth N pops any previous entry at depth >= N."""
    t = (
        ParentTracker()
        .push(1, "UA000456")
        .push(2, "LB000300")
        .push(3, "LB000200")
        .push(2, "LB000301")  # new depth-2 — replaces LB000300 and LB000200
    )
    assert t.parent_of(3) == "LB000301"
    assert t.parent_of(2) == "UA000456"


def test_push_at_shallower_depth_pops_deeper() -> None:
    t = (
        ParentTracker()
        .push(1, "UA000456")
        .push(2, "LB000300")
        .push(3, "LB000200")
        .push(1, "UA000999")  # shallower — pops everything below
    )
    assert t.parent_of(2) == "UA000999"
    assert t.parent_of(3) == "UA000999"
    assert t.parent_of(1) is None


def test_immutability() -> None:
    """ParentTracker.push returns a new instance; the original is unchanged."""
    original = ParentTracker().push(1, "ROOT")
    extended = original.push(2, "CHILD")
    assert original.parent_of(2) == "ROOT"
    assert extended.parent_of(2) == "ROOT"
    assert extended.parent_of(3) == "CHILD"
    # No mutation
    assert len(original.stack) == 1
    assert len(extended.stack) == 2
