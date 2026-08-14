from storage.cache import JsonSearchCache


def test_cache_round_trip_and_exact_key_behavior(tmp_path) -> None:
    cache = JsonSearchCache(tmp_path / "nested" / "cache.json")
    profiles = [{"id": "1", "name": "John Doe"}]

    assert cache.get("x:john doe") is None
    cache.set("x:john doe", profiles)

    assert cache.get("x:john doe") == profiles
    assert cache.get("x:john  doe") is None


def test_corrupt_cache_is_treated_as_empty(tmp_path) -> None:
    path = tmp_path / "cache.json"
    path.write_text("not json", encoding="utf-8")
    cache = JsonSearchCache(path)

    assert cache.get("anything") is None
