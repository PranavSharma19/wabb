from __future__ import annotations

from typing import Protocol

from models.candidate import Candidate


class UserSearchClient(Protocol):
    cache_namespace: str

    def search_users(self, query: str, max_results: int = 10) -> list[Candidate]: ...

    def lookup_username(self, username: str) -> Candidate | None: ...
