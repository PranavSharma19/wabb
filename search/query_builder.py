from __future__ import annotations

import re
import unicodedata

from models.criteria import RecipientCriteria


MAX_QUERY_LENGTH = 50

# X's /2/users/search only matches on name and username, so extra clues such as
# role or location are dead weight in the query. They stay useful for ranking.
FALLBACK_FIELDS = ("company", "school", "role")


def _truncate_at_word_boundary(value: str, limit: int) -> str:
    """Cut to `limit` characters without splitting a word."""

    if len(value) <= limit:
        return value
    head = value[: limit + 1]
    boundary = head.rfind(" ")
    if boundary <= 0:
        # A single word longer than the limit still has to be cut somewhere.
        return value[:limit]
    return head[:boundary].rstrip()


def sanitize_query(value: str) -> str:
    """Conform to X's documented `[A-Za-z0-9_' ]{1,50}` query pattern."""

    ascii_text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9_' ]+", " ", ascii_text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return _truncate_at_word_boundary(cleaned, MAX_QUERY_LENGTH)


def build_search_query(criteria: RecipientCriteria) -> str:
    """Build a name-primary query for X's `/2/users/search` endpoint."""

    candidates = [criteria.name]
    candidates.extend(getattr(criteria, field) for field in FALLBACK_FIELDS)
    candidates.extend(criteria.extra_clues)
    for value in candidates:
        query = sanitize_query(value)
        if query:
            return query
    raise ValueError("At least one searchable criterion is required.")


def normalized_query_key(query: str) -> str:
    return re.sub(r"\s+", " ", sanitize_query(query).casefold()).strip()
