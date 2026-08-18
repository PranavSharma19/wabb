from __future__ import annotations

import pytest

from mock_x_platform.application import (
    DEFAULT_MAX_RESULTS,
    MAX_MAX_RESULTS,
    OWNER_ID,
    MockXApplication,
    MockXHttpError,
)
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


def _crowd(store: MockXStore, count: int) -> None:
    """Put `count` identically-named profiles in the store.

    Every max_results assertion needs a corpus deeper than the bound it is
    testing, or the corpus -- not the bound -- decides the answer. The twelve
    hand-written fixtures are shallower than every bound in play, so against
    them a default of 12, 10, 100 or 1000 all return the same twelve rows.
    """

    store.replace_profiles(
        [
            {
                "id": str(2_000_000 + index),
                "name": "John Doe",
                "username": f"johndoe_{index:05d}",
                "description": "",
                "location": "",
                "profile_image_url": "",
                "verified": False,
                "receives_your_dm": True,
                "tier": "clean",
                "follower_count": 1,
            }
            for index in range(count)
        ]
    )


def test_max_results_defaults_to_x_s_hundred_not_to_our_old_ten(
    app: MockXApplication,
) -> None:
    # X's documented bounds: min 1, default 100, max 1000. Our old cap of ten was
    # ours, and it hid the fact that omitting the parameter costs $1.00 a search.
    # 1,100 candidates is deeper than every bound below, so each assertion is
    # decided by the bound and not by how many profiles happen to exist.
    _crowd(app.store, 1_100)

    assert len(app.search_users("John Doe")["data"]) == DEFAULT_MAX_RESULTS == 100
    # Pinned as an exact count, not `<= 100`: a default of 10 or of 1000 fails here.
    assert app.search_users("John Doe")["meta"]["result_count"] == 100


def test_max_results_is_clamped_to_x_s_floor_and_ceiling(app: MockXApplication) -> None:
    _crowd(app.store, 1_100)

    assert len(app.search_users("John Doe", max_results=3)["data"]) == 3
    # Below the floor: X's minimum is 1, so 0 is clamped up rather than
    # returning an empty page or raising.
    assert len(app.search_users("John Doe", max_results=0)["data"]) == 1
    # Above the ceiling: 1,100 people match, so a served page of exactly 1,000
    # is the ceiling doing the work. This asserts the observable contract
    # ("one page never exceeds MAX_MAX_RESULTS") rather than the specific line
    # that enforces it, because the candidate pool is independently capped at
    # 1,000 -- raising MAX_MAX_RESULTS alone would not show up anywhere else.
    ceiling_page = app.search_users("John Doe", max_results=5_000)["data"]
    assert len(ceiling_page) == MAX_MAX_RESULTS == 1_000


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


def test_the_username_rejection_message_names_the_bound_it_enforces(
    app: MockXApplication,
) -> None:
    # The endpoint deliberately allows 50 characters where X allows 15, because
    # the generated corpus contains handles longer than X's rule. A message that
    # says "1 to 15" therefore describes a rule this endpoint does not apply,
    # and sends the reader looking for a bug in a handle that is perfectly legal.
    long_handle = "isabella_rodriguez_4507"
    assert len(long_handle) > 15
    app.store.replace_profiles(
        [
            {
                "id": "2000000",
                "name": "Isabella Rodriguez",
                "username": long_handle,
                "description": "",
                "location": "",
                "profile_image_url": "",
                "verified": False,
                "receives_your_dm": True,
                "tier": "clean",
                "follower_count": 1,
            }
        ]
    )
    assert app.lookup_user_by_username(long_handle)["data"]["id"] == "2000000"

    with pytest.raises(MockXHttpError) as error:
        app.lookup_user_by_username("a" * 51)

    assert error.value.status == 400
    assert "1 to 50" in error.value.detail
    assert "15" not in error.value.detail


def test_a_saturated_pool_never_reports_end_of_results(app: MockXApplication) -> None:
    # 1,100 people match, but the candidate pool bottoms out at 250 when the
    # page asks for 250 or fewer. Reading "no next_token" as "no more results"
    # is then a lie told by the pool floor, not by the corpus: the caller is
    # told it has seen everybody when it has seen fewer than a quarter of them.
    _crowd(app.store, 1_100)

    page = app.search_users("John Doe", max_results=250)

    assert len(page["data"]) == 250
    assert "next_token" in page["meta"]


def test_the_page_after_a_saturated_pool_returns_the_rows_it_hid(
    app: MockXApplication,
) -> None:
    _crowd(app.store, 1_100)

    first = app.search_users("John Doe", max_results=250)
    second = app.search_users(
        "John Doe", max_results=250, next_token=first["meta"]["next_token"]
    )

    first_ids = [profile["id"] for profile in first["data"]]
    second_ids = [profile["id"] for profile in second["data"]]
    assert len(second_ids) == 250
    assert not set(first_ids) & set(second_ids)


def test_an_exhausted_pool_still_stops(app: MockXApplication) -> None:
    # The other half of the contract. X will hand out a next_token whose page
    # comes back empty, so an over-eager token is acceptable -- but a token on
    # a page that already ran out is a paging loop, and the caller has no way
    # to tell the difference from the outside.
    _crowd(app.store, 300)

    last = app.search_users("John Doe", max_results=1_000)

    assert len(last["data"]) == 300
    assert "next_token" not in last["meta"]
