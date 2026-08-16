import re
from pathlib import Path

import pytest

from config import Settings
from models.candidate import Candidate
from models.criteria import RecipientCriteria
from ranking.normalization import normalize_text
from search.mock_x_client import MockXClient
from search.query_builder import MAX_QUERY_LENGTH, build_search_query, sanitize_query
from search.service import find_users
from storage.cache import JsonSearchCache


def test_query_sanitization_follows_x_character_and_length_constraints() -> None:
    query = sanitize_query("Jöhn) Doe OR @evil.com — recruiter!!! " + "x" * 100)

    assert len(query) <= 50
    assert re.fullmatch(r"[A-Za-z0-9_' ]{1,50}", query)
    assert "@" not in query and ")" not in query


def test_query_uses_the_name_alone_because_x_matches_name_and_username() -> None:
    criteria = RecipientCriteria(
        name="John Doe",
        company="XYZ",
        role="Recruiter",
        location="Toronto",
        school="University of Toronto",
        extra_clues=["xyz", "U of T"],
    )
    assert build_search_query(criteria) == "John Doe"


def test_truncation_stops_at_a_word_boundary() -> None:
    criteria = RecipientCriteria(name="Alexandra Bartholomew Fitzgerald Montgomery Wellington")

    query = build_search_query(criteria)

    assert query == "Alexandra Bartholomew Fitzgerald Montgomery"
    assert len(query) <= MAX_QUERY_LENGTH
    assert not query.endswith(" ")


def test_a_single_oversized_word_is_still_cut_to_the_length_limit() -> None:
    query = sanitize_query("x" * 80)

    assert query == "x" * MAX_QUERY_LENGTH


@pytest.mark.parametrize(
    ("criteria", "expected"),
    [
        (RecipientCriteria(company="Cloud Works", role="Engineer", location="Toronto"), "Cloud Works"),
        (RecipientCriteria(school="University of Toronto", role="Engineer"), "University of Toronto"),
        (RecipientCriteria(role="Engineer", location="Toronto"), "Engineer"),
        (RecipientCriteria(extra_clues=["speaks at PyCon"]), "speaks at PyCon"),
    ],
)
def test_empty_name_falls_back_to_company_then_school_then_role(
    criteria: RecipientCriteria, expected: str
) -> None:
    assert build_search_query(criteria) == expected


def test_empty_query_is_rejected_before_any_api_call() -> None:
    with pytest.raises(ValueError, match="searchable criterion"):
        build_search_query(RecipientCriteria())


def test_mock_search_returns_nothing_when_no_name_or_username_matches() -> None:
    assert MockXClient().search_users("Zebediah Quaxlethorpe") == []


def test_mock_search_ignores_bio_and_location_matches() -> None:
    # "Toronto" and "recruiter" appear only in locations and bios, never in a
    # name or username -- X's endpoint would not match on them.
    assert MockXClient().search_users("Toronto") == []
    assert MockXClient().search_users("recruiter") == []

    matched = MockXClient().search_users("John Doe")
    assert matched
    assert all(
        {"john", "doe"} & set(normalize_text(f"{candidate.name} {candidate.username}").split())
        for candidate in matched
    )


def test_refining_criteria_without_changing_the_name_reuses_the_cache(tmp_path, caplog) -> None:
    class CountingClient:
        cache_namespace = "test"

        def __init__(self) -> None:
            self.queries: list[str] = []

        def search_users(self, query: str, max_results: int = 10) -> list[Candidate]:
            self.queries.append(query)
            return [Candidate(id="1", name="John Doe", username="john")]

    client = CountingClient()
    cache = JsonSearchCache(tmp_path / "cache.json")
    settings = Settings(True, "", Path(tmp_path / "unused.json"))

    with caplog.at_level("INFO"):
        first = find_users(RecipientCriteria(name="John Doe"), client=client, cache=cache, settings=settings)
        refined = find_users(
            RecipientCriteria(name="John Doe", company="XYZ", role="Recruiter", location="Toronto"),
            client=client,
            cache=cache,
            settings=settings,
        )

    assert client.queries == ["John Doe"]
    assert first == refined
    assert "[CACHE] Reusing previous search." in caplog.text
