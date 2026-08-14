from __future__ import annotations

from pathlib import Path

from mock_x_platform.application import MockXApplication
from mock_x_platform.store import MockXStore
from models.direct_message import DirectMessage


class LocalMockDirectMessageClient:
    """Persistent, transport-free adapter around the existing Mock X DM service."""

    def __init__(self, database_path: str | Path) -> None:
        self._application = MockXApplication(MockXStore(database_path))

    def send_message(self, recipient_id: str, text: str) -> DirectMessage:
        payload = self._application.send_message(recipient_id, text)
        return DirectMessage.from_dict(payload["data"])

    def list_messages(self, participant_id: str) -> list[DirectMessage]:
        payload = self._application.list_messages(participant_id)
        return [DirectMessage.from_dict(item) for item in payload["data"]]
