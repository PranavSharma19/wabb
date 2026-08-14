from pathlib import Path

from config import Settings
from models.candidate import Candidate
from models.criteria import RecipientCriteria
from mock_x_platform.client import MockXPlatformClient
from search.service import find_users
from storage.cache import JsonSearchCache


class CountingClient:
    cache_namespace = "test"

    def __init__(self) -> None:
        self.calls = 0
        self.requested_limits: list[int] = []

    def search_users(self, query: str, max_results: int = 10) -> list[Candidate]:
        self.calls += 1
        self.requested_limits.append(max_results)
        return [Candidate(id="1", name="John Doe", username="john")]


def test_find_users_caps_requests_and_reuses_cache(tmp_path, caplog) -> None:
    client = CountingClient()
    cache = JsonSearchCache(tmp_path / "cache.json")
    settings = Settings(True, "", Path(tmp_path / "unused.json"))
    criteria = RecipientCriteria(name="John Doe")

    with caplog.at_level("INFO"):
        first = find_users(criteria, client=client, cache=cache, settings=settings)
        second = find_users(criteria, client=client, cache=cache, settings=settings)

    assert client.calls == 1
    assert client.requested_limits == [10]
    assert first == second
    assert "[CACHE] Reusing previous search." in caplog.text


def test_settings_select_the_standalone_mock_platform(monkeypatch, tmp_path) -> None:
    settings = Settings(
        True,
        "",
        Path(tmp_path / "cache.json"),
        mock_x_base_url="http://127.0.0.1:9876",
    )
    expected = [Candidate(id="remote", name="John Doe", username="remote_john")]

    monkeypatch.setattr(MockXPlatformClient, "search_users", lambda self, query, max_results: expected)
    results = find_users(RecipientCriteria(name="John Doe"), settings=settings)

    assert results[0].id == "remote"
