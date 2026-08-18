from __future__ import annotations

import pytest

from mock_x_platform.store import MATCH_SCOPES, MockXStore, RESULT_ORDERS


def _profile(identifier: str, name: str, username: str, **overrides) -> dict[str, object]:
    return {
        "id": identifier,
        "name": name,
        "username": username,
        "description": overrides.get("description", ""),
        "location": overrides.get("location", ""),
        "profile_image_url": "",
        "verified": False,
        "receives_your_dm": overrides.get("receives_your_dm", True),
        "tier": overrides.get("tier", "clean"),
    }


@pytest.fixture
def store(tmp_path) -> MockXStore:
    store = MockXStore(tmp_path / "search.sqlite3")
    profiles = [
        # Twelve people match both query tokens, so the old code stopped here.
        _profile(str(1000 + index), "Liam Jackson", f"liam_jackson_{index}")
        for index in range(12)
    ]
    profiles.append(
        # The person we actually want: display name is one of the two tokens.
        _profile("2001", "Liam", "liam_685", description="Consultant at Globex.")
    )
    profiles.append(
        _profile("2002", "Ada Lovelace", "ada_l", description="Recruiter in Toronto.",
                 location="Toronto, Ontario")
    )
    store.replace_profiles(profiles)
    return store


def test_partial_token_match_survives_a_crowd_of_full_matches(store: MockXStore) -> None:
    pool = [profile["id"] for profile in store.search_profiles("Liam Jackson", limit=250)]

    # The regression: twelve AND matches used to suppress the OR pass entirely,
    # so the single-token match never entered the pool at any limit.
    assert "2001" in pool
    # Full matches still lead; AND is a preference, not a gate.
    assert pool.index("2001") >= 12


def test_pool_is_truncated_but_still_prefers_full_matches(store: MockXStore) -> None:
    pool = [profile["id"] for profile in store.search_profiles("Liam Jackson", limit=5)]

    assert len(pool) == 5
    assert all(identifier.startswith("10") for identifier in pool)


def test_search_matches_only_the_fields_x_matches(store: MockXStore) -> None:
    # Bio and location are no longer indexed: X's /2/users/search does not
    # match them, and indexing them skewed bm25 toward sparse profiles.
    assert store.search_profiles("Toronto") == []
    assert store.search_profiles("Recruiter") == []
    assert [profile["id"] for profile in store.search_profiles("Ada")] == ["2002"]


def test_pool_order_is_stable_across_identical_queries(store: MockXStore) -> None:
    first = [profile["id"] for profile in store.search_profiles("Liam Jackson")]
    second = [profile["id"] for profile in store.search_profiles("Liam Jackson")]

    assert first == second


def test_search_index_is_rebuilt_when_it_still_covers_bio_and_location(tmp_path) -> None:
    import sqlite3

    database = tmp_path / "legacy-index.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE profiles (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL, location TEXT NOT NULL,
            profile_image_url TEXT NOT NULL, verified INTEGER NOT NULL,
            receives_your_dm INTEGER
        );
        CREATE VIRTUAL TABLE profiles_fts USING fts5(
            id UNINDEXED, name, username, description, location,
            tokenize = 'unicode61 remove_diacritics 2'
        );
        INSERT INTO profiles VALUES
            ('1', 'Ada Lovelace', 'ada_l', 'Recruiter in Toronto.', 'Toronto', '', 0, 1);
        INSERT INTO profiles_fts VALUES
            ('1', 'Ada Lovelace', 'ada_l', 'Recruiter in Toronto.', 'Toronto');
        """
    )
    connection.commit()
    connection.close()

    store = MockXStore(database)

    assert [profile["id"] for profile in store.search_profiles("Ada")] == ["1"]
    assert store.search_profiles("Toronto") == []


def _profiles() -> list[dict[str, object]]:
    return [
        {
            "id": "1",
            "name": "Joe Bart",
            "username": "joebart1",
            "description": "Member of technical staff at Meta.",
            "location": "San Francisco, CA",
            "profile_image_url": "",
            "verified": False,
            "receives_your_dm": True,
            "tier": "clean",
            "follower_count": 12,
        },
        {
            "id": "2",
            "name": "Joe Bart",
            "username": "joebart2",
            "description": "Broadcaster.",
            "location": "London, UK",
            "profile_image_url": "",
            "verified": False,
            "receives_your_dm": True,
            "tier": "clean",
            "follower_count": 900_000,
        },
    ]


def test_the_default_scope_cannot_see_the_bio(tmp_path) -> None:
    store = MockXStore(tmp_path / "x.sqlite3")
    store.replace_profiles(_profiles())

    # Round 4's narrowed index, unchanged: X's /2/users/search matches name and
    # username, so a bio term must not retrieve anybody by default.
    assert store.search_profiles("Meta") == []


def test_the_bio_scope_can_see_the_bio(tmp_path) -> None:
    store = MockXStore(tmp_path / "x.sqlite3", match_scope="name_username_bio")
    store.replace_profiles(_profiles())

    assert [row["id"] for row in store.search_profiles("Meta")] == ["1"]


def test_follower_weighted_order_puts_the_personality_first(tmp_path) -> None:
    # Paired baseline: show the flip, not just one side of it, so a regression
    # to plain id order (e.g. `p.id DESC`) would be visible here too.
    default = MockXStore(tmp_path / "default.sqlite3")
    default.replace_profiles(_profiles())
    assert [row["id"] for row in default.search_profiles("Joe Bart")] == ["1", "2"]

    store = MockXStore(tmp_path / "x.sqlite3", result_order="follower_weighted")
    store.replace_profiles(_profiles())

    # Unknown B in its strong form: if X sorts by popularity, the member of
    # technical staff is systematically behind the broadcaster of the same name.
    assert [row["id"] for row in store.search_profiles("Joe Bart")] == ["2", "1"]


def test_an_unknown_flag_value_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError):
        MockXStore(tmp_path / "x.sqlite3", match_scope="everything")
    with pytest.raises(ValueError):
        MockXStore(tmp_path / "x2.sqlite3", result_order="everything")
    assert "name_username" in MATCH_SCOPES and "bm25" in RESULT_ORDERS


def test_follower_weighted_refuses_a_corpus_with_no_follower_signal(tmp_path) -> None:
    # An older corpus gets the column at DEFAULT 0, so the ordering silently
    # collapses to id order. Failing loudly beats a run that measures nothing.
    database = tmp_path / "flat.sqlite3"
    store = MockXStore(database)
    store.replace_profiles([{**_profiles()[0], "follower_count": 0}])

    with pytest.raises(ValueError, match="follower counts"):
        MockXStore(database, result_order="follower_weighted")


def test_follower_weighted_construction_does_not_raise_on_an_empty_database(tmp_path) -> None:
    # A brand-new store has no profiles yet, so the flatness check must not
    # mistake "nothing written" for "no follower signal."
    MockXStore(tmp_path / "empty.sqlite3", result_order="follower_weighted")
