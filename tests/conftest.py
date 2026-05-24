"""Shared pytest fixtures for the BoM parser test suite.

``PROJECT_ROOT`` and ``CONFIG_DIR`` reach back up from this file so the
tests don't depend on the pytest invocation's cwd.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bom_parser.utils.consts import (
    BOMS_DIR_NAME,
    CONFIG_DIR_NAME,
    RESOURCES_DIR_NAME,
)

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
CONFIG_DIR: Path = PROJECT_ROOT / CONFIG_DIR_NAME
BOMS_DIR: Path = PROJECT_ROOT / RESOURCES_DIR_NAME / BOMS_DIR_NAME


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def config_dir() -> Path:
    return CONFIG_DIR


@pytest.fixture(scope="session")
def boms_dir() -> Path:
    return BOMS_DIR


@pytest.fixture(scope="session")
def pdf_456(boms_dir: Path) -> Path:
    return boms_dir / "UA000456AF Bill of Materials.pdf"


@pytest.fixture(scope="session")
def pdf_457(boms_dir: Path) -> Path:
    return boms_dir / "UA000457AD Bill of Materials.pdf"
