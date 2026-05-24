"""Golden-file snapshot tests.

The fixtures under ``tests/fixtures/`` are the recorded expected output
for each reference BoM. Diffs against them require explicit re-blessing
by re-running ``python scripts/regenerate_goldens.py``.

``extracted_at`` is the only volatile field and is replaced with a
placeholder before comparison.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bom_parser.models.bom import BomDocument
from bom_parser.pipeline import parse_bom

GOLDEN_PLACEHOLDER = "GOLDEN_EXTRACTED_AT"
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Strip volatile metadata fields so two runs can be compared."""
    data["metadata"]["extracted_at"] = GOLDEN_PLACEHOLDER
    return data


def _parsed_to_dict(doc: BomDocument) -> dict[str, Any]:
    return _normalize(json.loads(doc.model_dump_json()))


@pytest.mark.parametrize(
    "pdf_fixture,golden_name",
    [
        ("pdf_456", "expected_456.json"),
        ("pdf_457", "expected_457.json"),
    ],
    ids=["UA000456AF", "UA000457AD"],
)
def test_matches_golden_snapshot(
    pdf_fixture: str,
    golden_name: str,
    request: pytest.FixtureRequest,
    config_dir: Path,
) -> None:
    pdf_path: Path = request.getfixturevalue(pdf_fixture)
    golden_path = FIXTURES_DIR / golden_name
    assert golden_path.is_file(), (
        f"Golden file {golden_path} missing — regenerate via "
        f"`python scripts/regenerate_goldens.py`"
    )

    expected = json.loads(golden_path.read_text(encoding="utf-8"))
    actual = _parsed_to_dict(parse_bom(pdf_path, config_dir=config_dir))

    assert actual == expected, (
        f"Output for {pdf_path.name} diverged from golden "
        f"{golden_name}. If the change is intentional, re-run "
        f"`python scripts/regenerate_goldens.py` and commit the "
        f"updated fixture."
    )
