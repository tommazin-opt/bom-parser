"""Models for the SERP-enrichment stage (``bom-parser enrich``).

This stage is a *consumer* of the parser's JSON output ([models/bom.py]):
it walks the serialized ``PartNode`` tree, builds Google search queries for
every supplier part, runs them through Bright Data's SERP API, and writes a
flat-but-indented spreadsheet preserving the BoM hierarchy.

Two models live here:

* ``EnrichmentRow`` — one spreadsheet row. Every tree node yields at least one
  row (so the hierarchy stays intact); nodes with eligible suppliers yield one
  row per generated query.
* ``SerpConfig`` — runtime configuration for the Bright Data client. Credentials
  are passed in by the CLI from environment variables; nothing secret is
  defaulted here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bom_parser.utils.consts import (
    BRIGHTDATA_DEFAULT_ENDPOINT,
    DEFAULT_SERP_BACKOFF_BASE_S,
    DEFAULT_SERP_CONCURRENCY,
    DEFAULT_SERP_MAX_RETRIES,
    DEFAULT_SERP_TIMEOUT_S,
    DEFAULT_SERP_TOP_N,
)


class EnrichmentRow(BaseModel):
    """One row of the enrichment spreadsheet.

    A node with no eligible supplier (an assembly, a label group, or a node
    whose only supplier is ``is_internal_author_part``) still produces a single
    row with the supplier/query fields left ``None`` — that keeps the parent
    -child tree visible in the sheet. A node with N eligible supplier queries
    produces N rows, all sharing the node's hierarchy fields.

    ``query`` is the exact string sent to the API (or ``None`` for a
    supplier-less row); the resolved URLs are looked up by ``query`` from the
    SERP results map at export time, so they are deliberately *not* stored here.
    """

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    level: int = Field(ge=0)
    parent_part: str | None = None
    internal_part: str = ""
    description: str = ""
    name_raw: str | None = None
    name_normalized: str | None = None
    part_number: str | None = None
    query: str | None = None


class SerpConfig(BaseModel):
    """Runtime configuration for the Bright Data SERP client.

    ``api_token`` / ``zone`` carry no defaults on purpose: the CLI reads them
    from the ``BRIGHTDATA_API_TOKEN`` / ``BRIGHTDATA_SERP_ZONE`` environment
    variables and fails loudly when they are missing (outside ``--dry-run``).
    """

    model_config = ConfigDict(frozen=True)

    api_token: str
    zone: str
    endpoint: str = BRIGHTDATA_DEFAULT_ENDPOINT
    max_concurrency: int = Field(default=DEFAULT_SERP_CONCURRENCY, ge=1)
    max_retries: int = Field(default=DEFAULT_SERP_MAX_RETRIES, ge=0)
    timeout_s: float = Field(default=DEFAULT_SERP_TIMEOUT_S, gt=0)
    backoff_base_s: float = Field(default=DEFAULT_SERP_BACKOFF_BASE_S, gt=0)
    top_n: int = Field(default=DEFAULT_SERP_TOP_N, ge=1)
