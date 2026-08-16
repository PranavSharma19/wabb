from __future__ import annotations

import pytest

from mock_x_platform.store import MockXStore


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
