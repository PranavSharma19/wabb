from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DirectMessage:
    """Provider-neutral representation of a sent or retrieved direct message."""

    id: str
    conversation_id: str
    sender_id: str
    recipient_id: str
    text: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DirectMessage":
        return cls(
            id=str(data.get("id", "")),
            conversation_id=str(data.get("conversation_id", "")),
            sender_id=str(data.get("sender_id", "")),
            recipient_id=str(data.get("recipient_id", "")),
            text=str(data.get("text", "")),
            created_at=str(data.get("created_at", "")),
        )
