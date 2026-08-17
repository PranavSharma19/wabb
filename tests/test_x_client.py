from typing import Any

import requests

from search.x_client import XClient


class FakeResponse:
    ok = True
    status_code = 200

    def json(self) -> dict[str, Any]:
        return {
            "data": [
                {
                    "id": str(index),
                    "name": f"Person {index}",
                    "username": f"person{index}",
                    "description": "Recruiter",
                    "location": "Toronto",
                    "verified": index == 0,
                    "receives_your_dm": index % 2 == 0,
                }
                for index in range(12)
            ]
        }


def test_x_client_sets_explicit_limit_fields_and_maps_response(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)
    client = XClient("user-context-token")

    results = client.search_users("John Doe", max_results=500)

    assert captured["url"] == "https://api.x.com/2/users/search"
    assert captured["params"]["max_results"] == 10
    assert "receives_your_dm" in captured["params"]["user.fields"]
    assert captured["headers"] == {"Authorization": "Bearer user-context-token"}
    assert len(results) == 10
    assert results[0].verified is True
    assert results[0].can_dm is True


def test_lookup_username_returns_none_when_the_account_does_not_exist(monkeypatch) -> None:
    class Response:
        status_code = 404
        ok = False
        reason = "Not Found"
        text = ""

        def json(self):
            return {"errors": [{"detail": "Not Found Error"}]}

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    assert XClient("token").lookup_username("nobodyhome") is None


def test_lookup_username_returns_none_when_x_reports_errors_without_data(monkeypatch) -> None:
    # X's other way of saying "no such account": 200, an errors array, no data.
    class Response:
        status_code = 200
        ok = True
        reason = "OK"
        text = ""

        def json(self):
            return {"errors": [{"title": "Not Found Error"}]}

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    assert XClient("token").lookup_username("nobodyhome") is None
