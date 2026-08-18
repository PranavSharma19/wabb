from __future__ import annotations

from collections.abc import Iterable

from data.mock_profiles import MOCK_PROFILES
from models.candidate import Candidate
from ranking.normalization import normalize_text


class MockXClient:
    cache_namespace = "mock"

    # X's /2/users/search matches on name and username only; matching bios or
    # locations here would make the mock easier than the real endpoint.
    DEFAULT_MATCH_FIELDS = ("name", "username")

    def __init__(self, match_fields: Iterable[str] | None = None) -> None:
        self.match_fields = tuple(match_fields or self.DEFAULT_MATCH_FIELDS)

    def search_users(self, query: str, max_results: int = 10) -> list[Candidate]:
        limit = min(max(int(max_results), 1), 10)
        query_tokens = set(normalize_text(query).split())

        scored: list[tuple[int, str, dict[str, object]]] = []
        for profile in MOCK_PROFILES:
            searchable = set(
                normalize_text(
                    " ".join(str(profile.get(field, "")) for field in self.match_fields)
                ).split()
            )
            overlap = len(query_tokens & searchable)
            # The real endpoint returns nothing for a query it cannot match.
            if overlap:
                scored.append((-overlap, str(profile["id"]), profile))

        scored.sort(key=lambda item: item[:2])
        return [Candidate.from_dict(profile) for _, _, profile in scored[:limit]]

    def lookup_username(self, username: str) -> Candidate | None:
        handle = str(username or "").strip().lstrip("@").casefold()
        profile = next(
            (
                profile
                for profile in MOCK_PROFILES
                if str(profile.get("username", "")).casefold() == handle
            ),
            None,
        )
        return Candidate.from_dict(profile) if profile is not None else None
