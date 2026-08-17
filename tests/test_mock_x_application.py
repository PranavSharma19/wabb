from __future__ import annotations

import pytest

from mock_x_platform.application import OWNER_ID, MockXApplication, MockXHttpError
from mock_x_platform.store import MockXStore


@pytest.fixture
def app(tmp_path) -> MockXApplication:
    return MockXApplication(MockXStore(tmp_path / "mock-x.sqlite3"))


def test_search_is_x_shaped_relevant_and_capped(app: MockXApplication) -> None:
    result = app.search_users("John Doe XYZ Toronto", max_results=100)

    assert result["meta"] == {"result_count": 10, "mock": True}
    assert len(result["data"]) == 10
    assert result["data"][0]["id"] == "1000001"
    assert "description" in result["data"][0]


def test_search_rejects_an_invalid_query(app: MockXApplication) -> None:
    with pytest.raises(MockXHttpError) as error:
        app.search_users("John @ Doe")

    assert error.value.status == 400


def test_dm_is_persisted_for_eligible_profile(app: MockXApplication) -> None:
    sent = app.send_message("1000001", "Hello John")
    conversation = app.list_messages("1000001")

    assert sent["data"]["sender_id"] == OWNER_ID
    assert sent["data"]["recipient_id"] == "1000001"
    assert conversation["data"] == [sent["data"]]


def test_dm_is_refused_for_ineligible_profile(app: MockXApplication) -> None:
    with pytest.raises(MockXHttpError) as error:
        app.send_message("1000002", "Hello")

    assert error.value.status == 403
    assert app.all_messages()["data"] == []


def test_injected_failure_is_consumed_then_operation_recovers(
    app: MockXApplication,
) -> None:
    app.inject_failure("search", status=429, detail="Try later", count=1)

    with pytest.raises(MockXHttpError) as error:
        app.search_users("John Doe")

    assert error.value.status == 429
    assert app.search_users("John Doe")["meta"]["result_count"] == 10


def test_reset_clears_messages_and_failures(app: MockXApplication) -> None:
    app.send_message("1000001", "Before reset")
    app.inject_failure("search", status=503, detail="Offline")

    assert app.reset() == {"ok": True}
    assert app.all_messages()["data"] == []
    assert app.search_users("John Doe")["data"]


def test_handle_lookup_resolves_one_profile(app: MockXApplication) -> None:
    result = app.lookup_user_by_username("johndoe_xyz")

    assert result["data"]["id"] == "1000001"
    assert result["data"]["username"] == "johndoe_xyz"


def test_handle_lookup_is_case_insensitive_and_ignores_a_leading_at(
    app: MockXApplication,
) -> None:
    assert app.lookup_user_by_username("@JohnDoe_XYZ")["data"]["id"] == "1000001"


def test_handle_lookup_rejects_something_that_is_not_a_handle(
    app: MockXApplication,
) -> None:
    with pytest.raises(MockXHttpError) as error:
        app.lookup_user_by_username("this is far too long to be one")

    assert error.value.status == 400


def test_handle_lookup_reports_a_missing_account_as_missing(
    app: MockXApplication,
) -> None:
    # 404 rather than an empty list: the user asked for one specific account, so
    # "there is no such account" is an answer the device has to be able to show.
    with pytest.raises(MockXHttpError) as error:
        app.lookup_user_by_username("nobodyhome")

    assert error.value.status == 404
