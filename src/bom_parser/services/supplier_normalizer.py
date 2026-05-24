"""Stage 6 — cluster raw supplier strings to canonical names.

Goal (plan §Stage 6): given the raw text the BoM author typed for the
supplier (``"North Coast Com"``, ``"North Coast"``, ``"NCC"``, all three
in the same document), emit a single canonical name (``"North Coast
Components"``) for every variant.

Pipeline order:

    1. **Pre-normalise** the raw string for lookup-key generation:
       ``unidecode``, strip trailing punctuation, collapse internal
       whitespace, lowercase. (The original casing is still kept around
       for acronym detection.)
    2. **Alias-table lookup.** ``config/supplier_aliases.yaml`` maps
       canonical names → lists of known aliases. We pre-normalise every
       alias and the canonical itself, then exact-match the incoming
       pre-normalised key.
    3. **Acronym detection.** A short, all-uppercase, all-alpha raw token
       (``NCC``) matches any canonical whose space-separated word
       initials equal it (``North Coast Components`` → ``NCC``).
    4. **Fuzzy fallback.** ``rapidfuzz.process.extractOne`` against
       both (a) the canonical names and (b) every raw name already seen
       in this document, with the threshold from
       ``DEFAULT_SUPPLIER_FUZZY_MIN_WRATIO`` (default 88).
    5. **New canonical.** If none of the above clusters the name, the
       pre-normalised raw becomes its own canonical, and the original
       raw text is recorded for the operator to triage (surfaces in
       ``ParseMetadata.new_supplier_candidates`` in the output JSON).

The normaliser is stateful by design: ``canonical_names`` and the cache
of already-seen raw → canonical mappings grow during a single document
pass so later supplier rows benefit from clusters established earlier.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml
from rapidfuzz import fuzz, process
from unidecode import unidecode

from bom_parser.utils.consts import (
    ACRONYM_MAX_LENGTH,
    ACRONYM_MIN_LENGTH,
    DEFAULT_SUPPLIER_FUZZY_MIN_WRATIO,
    SUPPLIER_TRAILING_PUNCT,
)

_WHITESPACE = re.compile(r"\s+")


def load_supplier_aliases(path: str | Path) -> dict[str, list[str]]:
    """Load the canonical → aliases mapping from YAML.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        ValueError: YAML has the wrong shape.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"supplier aliases config not found: {source}")
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"supplier aliases YAML must be a mapping at the top level: {source}"
        )

    parsed: dict[str, list[str]] = {}
    for canonical, aliases in cast(dict[Any, Any], raw).items():
        if not isinstance(aliases, list):
            raise ValueError(
                f"aliases for {canonical!r} must be a list of strings in {source}"
            )
        parsed[str(canonical)] = [str(a) for a in cast(list[Any], aliases)]
    return parsed


def pre_normalize(name: str) -> str:
    """Aggressive normalisation used as the alias-table lookup key.

    ``unidecode`` strips non-ASCII (Unicode dashes etc.), lowercase
    erases case differences, collapsed whitespace catches double-spaces,
    and trailing punctuation removal handles the ``Mcmaster-`` /
    ``McMaster-Carr`` style truncations.
    """
    s = unidecode(name).strip().lower()
    s = _WHITESPACE.sub(" ", s)
    s = s.rstrip(SUPPLIER_TRAILING_PUNCT).strip()
    return s


class SupplierNormalizer:
    """Stateful clusterer for one document pass.

    Build one instance per ``BomDocument`` and call :py:meth:`normalize`
    for every raw supplier name encountered. The instance accumulates
    cluster state across calls so naming variants that share *no*
    canonical seed in the YAML can still cluster together within the
    same document.
    """

    def __init__(
        self,
        alias_yaml: dict[str, list[str]],
        *,
        min_wratio: int = DEFAULT_SUPPLIER_FUZZY_MIN_WRATIO,
    ) -> None:
        self._min_wratio = min_wratio
        self._canonical_names: list[str] = []
        # Maps pre-normalised lookup key → canonical name.
        self._key_to_canonical: dict[str, str] = {}
        # Maps original raw string → canonical (memoisation).
        self._cache: dict[str, str] = {}
        # Canonicals that did NOT come from the alias YAML — i.e. new
        # supplier candidates the operator may want to review.
        self._new_candidates: list[str] = []
        # Pre-normalised forms of every canonical, for fuzzy lookup.
        self._canonical_keys: list[str] = []

        for canonical, aliases in alias_yaml.items():
            self._register_canonical(canonical, is_new=False)
            self._key_to_canonical[pre_normalize(canonical)] = canonical
            for alias in aliases:
                self._key_to_canonical[pre_normalize(alias)] = canonical

    @property
    def new_supplier_candidates(self) -> tuple[str, ...]:
        """Canonicals minted during this pass (not present in the alias YAML)."""
        return tuple(self._new_candidates)

    def normalize(self, raw_name: str) -> str:
        """Return the canonical name for ``raw_name``.

        Empty / whitespace-only input is returned unchanged (the caller
        is expected to skip such rows before reaching this stage).
        """
        if raw_name in self._cache:
            return self._cache[raw_name]
        if not raw_name or not raw_name.strip():
            self._cache[raw_name] = raw_name
            return raw_name

        key = pre_normalize(raw_name)

        canonical = self._key_to_canonical.get(key)
        if canonical is None:
            canonical = self._acronym_match(raw_name)
        if canonical is None:
            canonical = self._fuzzy_match(key)
        if canonical is None:
            canonical = self._mint_new_canonical(raw_name)

        # Memoise on both the raw and the pre-normalised key so
        # subsequent lookups via either form are fast.
        self._cache[raw_name] = canonical
        self._key_to_canonical.setdefault(key, canonical)
        return canonical

    # ---- internal helpers --------------------------------------------------

    def _register_canonical(self, canonical: str, *, is_new: bool) -> None:
        if canonical in self._canonical_names:
            return
        self._canonical_names.append(canonical)
        self._canonical_keys.append(pre_normalize(canonical))
        if is_new:
            self._new_candidates.append(canonical)

    def _acronym_match(self, raw_name: str) -> str | None:
        """Match a short uppercase token against canonical word-initials."""
        token = raw_name.strip()
        if not (ACRONYM_MIN_LENGTH <= len(token) <= ACRONYM_MAX_LENGTH):
            return None
        if not (token.isalpha() and token.isupper()):
            return None
        upper = token.upper()
        for canonical in self._canonical_names:
            initials = "".join(
                w[0] for w in canonical.split() if w and w[0].isalpha()
            ).upper()
            if initials == upper:
                return canonical
        return None

    def _fuzzy_match(self, key: str) -> str | None:
        if not self._canonical_keys:
            return None
        result = process.extractOne(
            key,
            self._canonical_keys,
            scorer=fuzz.WRatio,
        )
        _matched_key, score, idx = result
        if score < self._min_wratio:
            return None
        return self._canonical_names[idx]

    def _mint_new_canonical(self, raw_name: str) -> str:
        """Promote ``raw_name`` to its own canonical and remember it."""
        # Title-case unless the original was clearly an acronym — the
        # raw form is shown to the operator in the new_supplier_candidates
        # report, so prefer the most readable representation.
        canonical = raw_name.strip()
        if canonical.isupper() and len(canonical) <= ACRONYM_MAX_LENGTH:
            pass  # keep ACRONYM casing
        else:
            canonical = _smart_title(canonical)
        self._register_canonical(canonical, is_new=True)
        return canonical


def _smart_title(name: str) -> str:
    """Title-case while preserving short tokens already in title-case.

    ``str.title()`` mangles patterns like ``McMaster-Carr`` into
    ``Mcmaster-Carr``; this helper title-cases word-by-word but leaves
    words that already contain at least one lowercase letter after the
    first character alone (heuristic: they were probably typed
    intentionally).
    """
    pieces: list[str] = []
    for word in name.split():
        if not word:
            continue
        if len(word) > 1 and any(c.islower() for c in word[1:]) and any(
            c.isupper() for c in word
        ):
            # Word has mixed case (e.g. McMaster) — preserve as typed.
            pieces.append(word)
        else:
            pieces.append(word[:1].upper() + word[1:].lower())
    return " ".join(pieces)
