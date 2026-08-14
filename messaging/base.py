from __future__ import annotations

from typing import Protocol

from models.direct_message import DirectMessage


class DirectMessageClient(Protocol):
    def send_message(self, recipient_id: str, text: str) -> DirectMessage: ...
    def list_messages(self, participant_id: str) -> list[DirectMessage]: ...
