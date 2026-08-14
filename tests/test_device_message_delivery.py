from __future__ import annotations

import pytest

from device_app.message_delivery import LocalMockDirectMessageClient
from mock_x_platform.application import MockXHttpError


def test_local_mock_delivery_persists_eligible_message(tmp_path) -> None:
    client = LocalMockDirectMessageClient(tmp_path / "device-messages.sqlite3")

    sent = client.send_message("1000001", "Hello from the device")
    conversation = client.list_messages("1000001")

    assert sent.recipient_id == "1000001"
    assert sent.text == "Hello from the device"
    assert conversation == [sent]


def test_local_mock_delivery_preserves_dm_eligibility_rules(tmp_path) -> None:
    client = LocalMockDirectMessageClient(tmp_path / "device-messages.sqlite3")

    with pytest.raises(MockXHttpError, match="cannot receive a DM"):
        client.send_message("1000002", "This should fail")
    assert client.list_messages("1000002") == []
