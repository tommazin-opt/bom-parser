"""Filesystem helpers for finding BoM PDFs.

Used by the CLI batch mode and by any code that needs to iterate every
BoM in a directory without hardcoding filenames. Non-recursive by
default; pass ``recursive=True`` to descend into subdirectories.
"""

from __future__ import annotations

from pathlib import Path

from bom_parser.utils.consts import PDF_GLOB_PATTERN, PDF_GLOB_PATTERN_RECURSIVE


def discover_bom_pdfs(
    directory: str | Path,
    *,
    recursive: bool = False,
) -> tuple[Path, ...]:
    """Return every PDF in ``directory``, alphabetically sorted.

    Raises:
        NotADirectoryError: ``directory`` does not exist or is not a directory.
    """
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")
    pattern = PDF_GLOB_PATTERN_RECURSIVE if recursive else PDF_GLOB_PATTERN
    return tuple(sorted(p for p in root.glob(pattern) if p.is_file()))
