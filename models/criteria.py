from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class RecipientCriteria:
    """Structured clues describing the person the user wants to find."""

    name: str = ""
    company: str = ""
    role: str = ""
    location: str = ""
    school: str = ""
    extra_clues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecipientCriteria":
        fields = ("name", "company", "role", "location", "school")
        values = {key: str(data.get(key, "") or "").strip() for key in fields}
        values["extra_clues"] = [
            str(item).strip() for item in data.get("extra_clues", []) if str(item).strip()
        ]
        return cls(**values)
