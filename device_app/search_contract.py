from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from models.criteria import RecipientCriteria


@dataclass(frozen=True, slots=True)
class ProfileCandidate:
    """Provider-neutral profile data that is safe for the device UI."""

    id: str
    name: str
    username: str
    company: str = ""
    bio: str = ""
    location: str = ""
    profile_image_url: str = ""
    profile_url: str = ""
    verified: bool = False
    can_dm: bool | None = None
    score: float = 0.0
    match_reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Candidate id is required.")
        if not self.name.strip():
            raise ValueError("Candidate name is required.")
        if not self.username.strip():
            raise ValueError("Candidate username is required.")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["match_reasons"] = list(self.match_reasons)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProfileCandidate":
        username = str(data.get("username", "") or "").strip().lstrip("@")
        return cls(
            id=str(data.get("id", "") or "").strip(),
            name=str(data.get("name", "") or "").strip(),
            username=username,
            company=str(data.get("company", "") or "").strip(),
            bio=str(data.get("bio", data.get("description", "")) or "").strip(),
            location=str(data.get("location", "") or "").strip(),
            profile_image_url=str(data.get("profile_image_url", "") or "").strip(),
            profile_url=str(data.get("profile_url", "") or "").strip(),
            verified=bool(data.get("verified", False)),
            can_dm=data.get("can_dm", data.get("receives_your_dm")),
            score=float(data.get("score", 0.0) or 0.0),
            match_reasons=tuple(str(item) for item in data.get("match_reasons", [])),
        )


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One complete, versioned search response consumed by the device."""

    query: str
    candidates: tuple[ProfileCandidate, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("Unsupported search result schema version.")
        if len(self.candidates) > 10:
            raise ValueError("Search providers must return at most 10 candidates.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "query": self.query,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchResult":
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            query=str(data.get("query", "") or ""),
            candidates=tuple(
                ProfileCandidate.from_dict(item)
                for item in data.get("candidates", [])
            ),
        )


class SearchProvider(Protocol):
    """The only search dependency visible to the device application."""

    def search(self, criteria: RecipientCriteria) -> SearchResult: ...

    def lookup(self, handle: str) -> SearchResult: ...
