from __future__ import annotations

from data.mock_profiles import MOCK_PROFILES
from models.candidate import Candidate
from ranking.normalization import normalize_text


class MockXClient:
    cache_namespace = "mock"

    def search_users(self, query: str, max_results: int = 10) -> list[Candidate]:
        limit = min(max(int(max_results), 1), 10)
        query_tokens = set(normalize_text(query).split())

        def relevance(profile: dict[str, object]) -> tuple[int, str]:
            searchable = normalize_text(
                " ".join(
                    str(profile.get(field, ""))
                    for field in ("name", "username", "description", "location")
                )
            )
            overlap = sum(1 for token in query_tokens if token in searchable.split())
            return (-overlap, str(profile["id"]))

        profiles = sorted(MOCK_PROFILES, key=relevance)[:limit]
        return [Candidate.from_dict(profile) for profile in profiles]
