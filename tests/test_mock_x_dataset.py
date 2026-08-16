from __future__ import annotations

import json

import pytest

from data.mock_profiles import MOCK_PROFILES
from mock_x_platform.application import MockXApplication
from mock_x_platform.dataset import (
    DEFAULT_TIER_MIX,
    TIER_NAMES,
    build_dataset,
    generate_profiles,
    normalize_tier_mix,
)
from mock_x_platform.store import MockXStore
from ranking.normalization import normalize_text


def _generated(profiles: list[dict[str, object]]) -> list[dict[str, object]]:
    return profiles[len(MOCK_PROFILES) :]


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
    # generator_version moved to 2 and tier_mix joined the metadata when dirt
    # tiers were added: the profile shape changed, so reports from older
    # datasets are not comparable to these and must be distinguishable.
    assert store.dataset_metadata() == {
        "generator_version": "2",
        "profile_count": "1000",
        "seed": "42",
        "tier_mix": json.dumps(DEFAULT_TIER_MIX, sort_keys=True),
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


def test_dirt_tiers_hit_their_configured_quotas_exactly() -> None:
    generated = _generated(list(generate_profiles(1_012, seed=42)))
    counts = {tier: 0 for tier in TIER_NAMES}
    for profile in generated:
        counts[str(profile["tier"])] += 1

    assert len(generated) == 1_000
    assert counts == {"clean": 400, "partial": 300, "decorated": 200, "handle_name": 100}


def test_tier_mix_is_configurable_and_validated() -> None:
    generated = _generated(
        list(generate_profiles(112, seed=42, tier_mix={"partial": 0.5, "handle_name": 0.5}))
    )
    counts = {tier: 0 for tier in TIER_NAMES}
    for profile in generated:
        counts[str(profile["tier"])] += 1

    assert counts == {"clean": 0, "partial": 50, "decorated": 0, "handle_name": 50}

    with pytest.raises(ValueError, match="sum to 1.0"):
        normalize_tier_mix({"clean": 0.9, "partial": 0.4})
    with pytest.raises(ValueError, match="Unknown dirt tiers"):
        normalize_tier_mix({"clean": 0.5, "pristine": 0.5})


def test_ground_truth_is_the_person_not_the_rendered_profile(tmp_path) -> None:
    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=1_000, seed=42)
    store = MockXStore(database)
    cases = store.evaluation_cases()
    labels = store.profile_labels(str(case["expected_profile_id"]) for case in cases)

    seen_tiers = set()
    for case in cases:
        criteria = json.loads(case["criteria_json"])
        label = labels[str(case["expected_profile_id"])]
        seen_tiers.add(label["tier"])
        # Every case carries the full real identity regardless of what the
        # account displays, so recall is comparable across tiers.
        assert criteria["name"] and criteria["company"] and criteria["role"]
        assert criteria["school"] and criteria["location"]

    assert seen_tiers == set(TIER_NAMES)


def test_each_tier_degrades_the_profile_the_way_it_claims() -> None:
    generated = _generated(list(generate_profiles(1_012, seed=42)))
    by_tier: dict[str, list[dict[str, object]]] = {tier: [] for tier in TIER_NAMES}
    for profile in generated:
        by_tier[str(profile["tier"])].append(profile)

    for profile in by_tier["clean"]:
        truth = profile["truth"]
        assert profile["name"] == truth["name"]
        assert truth["company"] in str(profile["description"])
        assert profile["location"]

    for profile in by_tier["partial"]:
        truth = profile["truth"]
        assert profile["name"] == truth["name"]
        assert truth["company"] not in str(profile["description"])
        assert len(str(profile["description"])) < 40

    for profile in by_tier["decorated"]:
        truth = profile["truth"]
        # Real name still present, just wrapped in noise.
        assert normalize_text(str(truth["name"])) in normalize_text(str(profile["name"]))
        assert profile["name"] != truth["name"]

    for profile in by_tier["handle_name"]:
        truth = profile["truth"]
        assert normalize_text(str(truth["name"])) not in normalize_text(str(profile["name"]))


def test_can_dm_is_frequently_unknown() -> None:
    generated = _generated(list(generate_profiles(1_012, seed=42)))
    values = [profile["receives_your_dm"] for profile in generated]

    assert values.count(None) > 250
    assert values.count(True) > 250
    assert values.count(False) > 100


def test_store_round_trips_tiers_without_exposing_them_to_the_api(tmp_path) -> None:
    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=1_000, seed=42)
    store = MockXStore(database)

    counts = store.tier_counts()
    assert set(counts) == set(TIER_NAMES)
    assert sum(counts.values()) == 1_000

    unknown_dm = [
        profile
        for profile in store.search_profiles("Ana", limit=250)
        if profile["receives_your_dm"] is None
    ]
    assert unknown_dm, "unknown DM eligibility should survive the round trip as None"
    # Tier is ground truth for the harness, not something the API may leak.
    assert all("tier" not in profile for profile in store.search_profiles("Ana", limit=10))
    assert "tier" not in (store.get_profile("2000001") or {})


def test_old_databases_are_migrated_instead_of_dropped(tmp_path) -> None:
    import sqlite3

    database = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            location TEXT NOT NULL,
            profile_image_url TEXT NOT NULL,
            verified INTEGER NOT NULL,
            receives_your_dm INTEGER NOT NULL
        );
        INSERT INTO profiles VALUES ('1', 'Old Row', 'old_row', 'bio', 'Toronto', '', 0, 1);
        """
    )
    connection.commit()
    connection.close()

    store = MockXStore(database)
    profile = store.get_profile("1")

    assert profile is not None and profile["name"] == "Old Row"
    assert store.tier_counts() == {"clean": 1}
    store.replace_profiles([{"id": "2", "name": "New", "username": "new", "receives_your_dm": None}])
    assert store.get_profile("2")["receives_your_dm"] is None
