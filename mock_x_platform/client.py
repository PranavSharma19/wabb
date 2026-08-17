from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from models.candidate import Candidate
from models.direct_message import DirectMessage


class MockXPlatformError(RuntimeError):
    pass


class MockXPlatformClient:
    """Search and DM adapter for the standalone local Mock X service."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8765",
        *,
        timeout_seconds: float = 5.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self.cache_namespace = f"mock-x-platform:{self.base_url.casefold()}"

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def search_users(
        self, query: str, max_results: int = 100, next_token: str | None = None
    ) -> list[Candidate]:
        candidates, _ = self.search_users_page(
            query, max_results=max_results, next_token=next_token
        )
        return candidates

    def search_users_page(
        self,
        query: str,
        *,
        max_results: int = 100,
        next_token: str | None = None,
    ) -> tuple[list[Candidate], str | None]:
        """One page, plus the cursor for the next one when there is one."""

        limit = min(max(int(max_results), 1), 1000)
        params: dict[str, Any] = {"query": query, "max_results": limit}
        if next_token:
            params["next_token"] = next_token
        payload = self._request("GET", "/2/users/search", params=params)
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise MockXPlatformError("Mock X search returned an invalid user list.")
        meta = payload.get("meta") or {}
        cursor = meta.get("next_token") if isinstance(meta, dict) else None
        return (
            [Candidate.from_dict(user) for user in data[:limit]],
            str(cursor) if cursor else None,
        )

    def lookup_username(self, username: str) -> Candidate | None:
        """One profile for one handle, or None when no such account exists."""

        payload = self._request(
            "GET",
            f"/2/users/by/username/{quote(str(username or '').lstrip('@'), safe='')}",
            allow_missing=True,
        )
        data = payload.get("data")
        return Candidate.from_dict(data) if isinstance(data, dict) else None

    def send_message(self, recipient_id: str, text: str) -> DirectMessage:
        payload = self._request(
            "POST",
            f"/2/dm_conversations/with/{quote(str(recipient_id), safe='')}/messages",
            json={"text": text},
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MockXPlatformError("Mock X send returned an invalid message.")
        return DirectMessage.from_dict(data)

    def list_messages(self, participant_id: str) -> list[DirectMessage]:
        payload = self._request(
            "GET",
            f"/2/dm_conversations/with/{quote(str(participant_id), safe='')}/dm_events",
        )
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise MockXPlatformError("Mock X conversation returned an invalid message list.")
        return [DirectMessage.from_dict(message) for message in data]

    def inject_failure(
        self,
        operation: str,
        *,
        status: int = 500,
        detail: str = "Injected failure",
        count: int = 1,
    ) -> None:
        self._request(
            "POST",
            "/__mock__/failures",
            json={
                "operation": operation,
                "status": status,
                "detail": detail,
                "count": count,
            },
        )

    def reset(self) -> None:
        self._request("POST", "/__mock__/reset", json={})

    def _request(
        self,
        method: str,
        path: str,
        *,
        allow_missing: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout_seconds,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise MockXPlatformError(
                f"Could not reach the Mock X platform at {self.base_url}: {exc}"
            ) from exc
        # A 404 on a lookup is data, not a failure: it means no such account.
        # Every other non-OK status is still an error, including the 400 that a
        # malformed handle earns.
        if allow_missing and response.status_code == 404:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise MockXPlatformError(
                f"Mock X returned a non-JSON response ({response.status_code})."
            ) from exc
        if not response.ok:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise MockXPlatformError(
                f"Mock X request failed ({response.status_code}): {detail or payload}"
            )
        if not isinstance(payload, dict):
            raise MockXPlatformError("Mock X returned an invalid JSON payload.")
        return payload
