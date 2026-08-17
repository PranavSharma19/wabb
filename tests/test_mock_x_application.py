from __future__ import annotations

import pytest

from mock_x_platform.application import OWNER_ID, MockXApplication, MockXHttpError
from mock_x_platform.store import MockXStore


@pytest.fixture
def app(tmp_path) -> MockXApplication:
    return MockXApplication(MockXStore(tmp_path / "mock-x.sqlite3"))


def test_search_is_x_shaped_and_relevant(app: MockXApplication) -> None:
    result = app.search_users("John Doe XYZ Toronto", max_results=10)

    assert result["meta"]["result_count"] == 10
    assert result["meta"]["mock"] is True
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
    assert app.search_users("John Doe", max_results=10)["meta"]["result_count"] == 10


def test_reset_clears_messages_and_failures(app: MockXApplication) -> None:
    app.send_message("1000001", "Before reset")
    app.inject_failure("search", status=503, detail="Offline")

    assert app.reset() == {"ok": True}
    assert app.all_messages()["data"] == []
    assert app.search_users("John Doe", max_results=10)["meta"]["result_count"] == 10


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


def test_a_search_bills_only_the_profiles_it_returns(app: MockXApplication) -> None:
    app.search_users("John Doe", max_results=5)

    # The fixture uses a mock store, which falls back to the twelve hand-written
    # MOCK_PROFILES fixtures. This test verifies the page size (5) is billed, not
    # the full fixture set.
    assert app.store.ledger.distinct_profiles == 5
    assert app.store.ledger.searches == 1


def test_repeating_a_search_inside_the_window_costs_nothing_more(
    app: MockXApplication,
) -> None:
    app.search_users("John Doe", max_results=5)
    app.search_users("John Doe", max_results=5)

    assert app.store.ledger.searches == 2
    assert app.store.ledger.distinct_profiles == 5


def test_a_lookup_bills_one_profile(app: MockXApplication) -> None:
    app.lookup_user_by_username("johndoe_xyz")

    assert app.store.ledger.lookups == 1
    assert app.store.ledger.distinct_profiles == 1


def test_a_search_bills_the_page_not_the_pool_behind_it(tmp_path) -> None:
    # The other billing tests run against an empty store, which falls back to the
    # twelve hand-written fixtures -- so none of them actually exercises the
    # 250-row candidate pool. This one does. Recording the pool instead of the
    # page would inflate every cost figure in the project about twenty-five fold,
    # and would do it silently.
    from mock_x_platform.dataset import build_dataset

    database = tmp_path / "corpus.sqlite3"
    build_dataset(database, count=3_000, seed=42)
    store = MockXStore(database)
    app = MockXApplication(store)

    pooled = len(store.search_profiles("Sam Lee"))
    store.ledger.reset()

    returned = app.search_users("Sam Lee", max_results=5)["data"]

    assert len(returned) == 5
    assert pooled > 20, "fixture too small to distinguish page from pool"
    assert store.ledger.distinct_profiles == 5


def test_max_results_follows_x_not_us(app: MockXApplication) -> None:
    # X's documented bounds: min 1, default 100, max 1000. Our old cap of ten was
    # ours, and it hid the fact that omitting the parameter costs $1.00 a search.
    assert len(app.search_users("John Doe")["data"]) == 12
    assert len(app.search_users("John Doe", max_results=3)["data"]) == 3
    assert len(app.search_users("John Doe", max_results=5000)["data"]) == 12


def test_pagination_walks_the_pool_without_repeating_anybody(
    app: MockXApplication,
) -> None:
    first = app.search_users("John Doe", max_results=5)
    token = first["meta"]["next_token"]
    second = app.search_users("John Doe", max_results=5, next_token=token)

    first_ids = [profile["id"] for profile in first["data"]]
    second_ids = [profile["id"] for profile in second["data"]]
    assert len(second_ids) == 5
    assert not set(first_ids) & set(second_ids)


def test_a_cursor_from_another_query_is_refused(app: MockXApplication) -> None:
    token = app.search_users("John Doe", max_results=5)["meta"]["next_token"]

    with pytest.raises(MockXHttpError) as error:
        app.search_users("Maya Chen", max_results=5, next_token=token)

    assert error.value.status == 400


def test_the_last_page_carries_no_cursor(app: MockXApplication) -> None:
    assert "next_token" not in app.search_users("John Doe", max_results=1000)["meta"]
