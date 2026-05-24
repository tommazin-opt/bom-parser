"""Regenerate the golden snapshot files under ``tests/fixtures``.

Run this script whenever a parser change is *deliberately* expected to
shift the output, then commit the updated goldens alongside the change
so reviewers see the diff. ``extracted_at`` is replaced with a
placeholder string so the snapshot stays byte-stable across runs.

Invocation (from project root):

    python scripts/regenerate_goldens.py
"""

from __future__ import annotations

import json
from pathlib import Path

from bom_parser.pipeline import parse_bom
from bom_parser.utils.consts import (
    BOMS_DIR_NAME,
    CONFIG_DIR_NAME,
    RESOURCES_DIR_NAME,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = PROJECT_ROOT / "tests" / "fixtures"
GOLDEN_PLACEHOLDER = "GOLDEN_EXTRACTED_AT"

PDFS_TO_GOLDEN: dict[str, str] = {
    "UA000456AF Bill of Materials.pdf": "expected_456.json",
    "UA000457AD Bill of Materials.pdf": "expected_457.json",
}


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    boms_dir = PROJECT_ROOT / RESOURCES_DIR_NAME / BOMS_DIR_NAME
    config_dir = PROJECT_ROOT / CONFIG_DIR_NAME

    for pdf_name, golden_name in PDFS_TO_GOLDEN.items():
        doc = parse_bom(boms_dir / pdf_name, config_dir=config_dir)
        data = json.loads(doc.model_dump_json())
        data["metadata"]["extracted_at"] = GOLDEN_PLACEHOLDER
        (GOLDEN_DIR / golden_name).write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        n_parts = len(data["parts"])
        n_pairs = sum(len(p["suppliers"]) for p in data["parts"])
        print(f"wrote {golden_name}: {n_parts} parts, {n_pairs} supplier-part pairs")


if __name__ == "__main__":
    main()
