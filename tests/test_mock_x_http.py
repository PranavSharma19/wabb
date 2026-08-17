from __future__ import annotations

import threading

import pytest

from mock_x_platform.client import MockXPlatformClient, MockXPlatformError
from mock_x_platform.server import create_server


@pytest.fixture
def platform_client(tmp_path):
    server = create_server("127.0.0.1", 0, database_path=tmp_path / "mock-x.sqlite3")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    client = MockXPlatformClient(f"http://{host}:{port}", timeout_seconds=2)
    try:
        yield client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_search_health_and_dm_round_trip(platform_client) -> None:
    assert platform_client.health()["status"] == "ok"

    candidates = platform_client.search_users("John Doe XYZ Toronto", max_results=50)
    assert len(candidates) == 10
    assert candidates[0].id == "1000001"
    assert candidates[0].can_dm is True

    sent = platform_client.send_message("1000001", "Hello from the device")
    messages = platform_client.list_messages("1000001")
    assert messages == [sent]
    assert messages[0].text == "Hello from the device"


def test_http_surfaces_dm_policy_errors(platform_client) -> None:
    with pytest.raises(MockXPlatformError, match="403"):
        platform_client.send_message("1000002", "This should fail")


def test_http_failure_injection_is_one_shot(platform_client) -> None:
    platform_client.inject_failure(
        "search", status=429, detail="Synthetic rate limit", count=1
    )

    with pytest.raises(MockXPlatformError, match="429"):
        platform_client.search_users("John Doe")

    assert platform_client.search_users("John Doe")


def test_http_reset_clears_persisted_messages(platform_client) -> None:
    platform_client.send_message("1000001", "Temporary")
    platform_client.reset()
    assert platform_client.list_messages("1000001") == []


def test_http_handle_lookup_round_trip(platform_client) -> None:
    candidate = platform_client.lookup_username("@johndoe_xyz")

    assert candidate is not None
    assert candidate.id == "1000001"


def test_http_handle_lookup_returns_none_for_a_missing_account(platform_client) -> None:
    assert platform_client.lookup_username("nobodyhome") is None
