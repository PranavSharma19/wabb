from __future__ import annotations

from mock_x_platform.application import MockXApplication
from mock_x_platform.dataset import build_dataset, generate_profiles
from mock_x_platform.store import MockXStore


def test_generation_is_deterministic_and_unique() -> None:
    first = list(generate_profiles(100, seed=42))
    second = list(generate_profiles(100, seed=42))

    assert first == second
    assert len({profile["id"] for profile in first}) == 100
    assert len({profile["username"] for profile in first}) == 100


def test_generated_database_is_counted_and_searchable(tmp_path) -> None:
    database = tmp_path / "profiles.sqlite3"
    assert build_dataset(database, count=1_000, seed=42) == 1_000

    store = MockXStore(database)
    results = MockXApplication(store).search_users("John Doe XYZ Toronto", 10)

    assert store.profile_count() == 1_000
    assert store.evaluation_case_count() == 988
    assert store.evaluation_cases(limit=1)[0]["expected_profile_id"] == "2000001"
    assert store.dataset_metadata() == {
        "generator_version": "1",
        "profile_count": "1000",
        "seed": "42",
    }
    assert len(results["data"]) == 10
    assert results["data"][0]["id"] == "1000001"


def test_profile_lookup_uses_generated_database(tmp_path) -> None:
    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=100, seed=9)
    store = MockXStore(database)

    profile = store.get_profile("2000001")

    assert profile is not None
    assert profile["username"].endswith("_1")
    assert isinstance(profile["verified"], bool)
