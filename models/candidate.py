from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Candidate:
    """UI-safe representation of an X profile and its match score."""

    id: str
    name: str
    username: str
    bio: str = ""
    location: str = ""
    profile_image_url: str = ""
    verified: bool = False
    can_dm: bool | None = None
    score: int = 0
    match_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Candidate":
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            username=str(data.get("username", "")),
            bio=str(data.get("bio", data.get("description", "")) or ""),
            location=str(data.get("location", "") or ""),
            profile_image_url=str(data.get("profile_image_url", "") or ""),
            verified=bool(data.get("verified", False)),
            can_dm=data.get("can_dm", data.get("receives_your_dm")),
            score=int(data.get("score", 0)),
            match_reasons=list(data.get("match_reasons", [])),
        )
