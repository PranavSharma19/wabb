from __future__ import annotations

import re
import unicodedata

from models.criteria import RecipientCriteria


MAX_QUERY_LENGTH = 50


def sanitize_query(value: str) -> str:
    """Conform to X's documented `[A-Za-z0-9_' ]{1,50}` query pattern."""

    ascii_text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9_' ]+", " ", ascii_text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:MAX_QUERY_LENGTH].rstrip()


def build_search_query(criteria: RecipientCriteria) -> str:
    values = [criteria.name, criteria.company, criteria.role, criteria.location, criteria.school]
    values.extend(criteria.extra_clues)
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = sanitize_query(value)
        if clean and clean.casefold() not in seen:
            unique.append(clean)
            seen.add(clean.casefold())

    query = sanitize_query(" ".join(unique))
    if not query:
        raise ValueError("At least one searchable criterion is required.")
    return query


def normalized_query_key(query: str) -> str:
    return re.sub(r"\s+", " ", sanitize_query(query).casefold()).strip()
