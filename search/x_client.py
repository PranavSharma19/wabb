from __future__ import annotations

from typing import Any

import requests

from models.candidate import Candidate


class XApiError(RuntimeError):
    pass


class XClient:
    """Thin adapter over X API v2 so raw API JSON stays out of the application."""

    BASE_URL = "https://api.x.com/2/users/search"
    USER_FIELDS = (
        "id,name,username,description,location,profile_image_url,verified,"
        "is_identity_verified,receives_your_dm"
    )
    cache_namespace = "x-api-v2"

    def __init__(self, access_token: str, timeout_seconds: float = 15.0):
        if not access_token:
            raise ValueError(
                "X_ACCESS_TOKEN is required when MOCK_X=false. It must be a user-context access token."
            )
        self.access_token = access_token
        self.timeout_seconds = timeout_seconds

    def search_users(self, query: str, max_results: int = 10) -> list[Candidate]:
        limit = min(max(int(max_results), 1), 10)
        try:
            response = requests.get(
                self.BASE_URL,
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={
                    "query": query,
                    "max_results": limit,
                    "user.fields": self.USER_FIELDS,
                },
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise XApiError(f"Could not reach the X API: {exc}") from exc

        if not response.ok:
            detail = _error_detail(response)
            raise XApiError(f"X API request failed ({response.status_code}): {detail}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise XApiError("X API returned a non-JSON response.") from exc

        data = payload.get("data", [])
        if not isinstance(data, list):
            raise XApiError("X API response did not contain a valid user list.")
        return [_candidate_from_x(user) for user in data[:limit]]


def _candidate_from_x(user: dict[str, Any]) -> Candidate:
    receives_dm = user.get("receives_your_dm")
    return Candidate(
        id=str(user.get("id", "")),
        name=str(user.get("name", "")),
        username=str(user.get("username", "")),
        bio=str(user.get("description", "") or ""),
        location=str(user.get("location", "") or ""),
        profile_image_url=str(user.get("profile_image_url", "") or ""),
        verified=bool(user.get("verified", False) or user.get("is_identity_verified", False)),
        can_dm=receives_dm if isinstance(receives_dm, bool) else None,
    )


def _error_detail(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300] or response.reason
    if isinstance(payload, dict):
        if payload.get("detail"):
            return str(payload["detail"])
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                return str(first.get("detail") or first.get("message") or first)
    return str(payload)[:300]
