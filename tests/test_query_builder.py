import re

import pytest

from models.criteria import RecipientCriteria
from search.query_builder import build_search_query, sanitize_query


def test_query_sanitization_follows_x_character_and_length_constraints() -> None:
    query = sanitize_query("Jöhn) Doe OR @evil.com — recruiter!!! " + "x" * 100)

    assert len(query) <= 50
    assert re.fullmatch(r"[A-Za-z0-9_' ]{1,50}", query)
    assert "@" not in query and ")" not in query


def test_query_building_deduplicates_and_preserves_priority() -> None:
    criteria = RecipientCriteria(name="John Doe", company="XYZ", extra_clues=["xyz", "U of T"])
    assert build_search_query(criteria) == "John Doe XYZ U of T"


def test_empty_query_is_rejected_before_any_api_call() -> None:
    with pytest.raises(ValueError, match="searchable criterion"):
        build_search_query(RecipientCriteria())
