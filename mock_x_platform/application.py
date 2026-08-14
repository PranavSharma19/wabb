from __future__ import annotations

import re
from typing import Any

from data.mock_profiles import MOCK_PROFILES
from ranking.normalization import normalize_text

from .store import MockXStore


OWNER_ID = "9000000"
QUERY_PATTERN = re.compile(r"^[A-Za-z0-9_' ]{1,50}$")
FAILURE_OPERATIONS = {"search", "send_dm", "list_dm"}


class MockXHttpError(RuntimeError):
    def __init__(
        self,
        status: int,
        detail: str,
        title: str = "Mock X request failed",
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.title = title

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "detail": self.detail,
            "status": self.status,
            "type": "https://mock.x.local/problems/request-failed",
        }


class MockXApplication:
    """Transport-independent X-like behavior used by the local HTTP server."""

    def __init__(self, store: MockXStore, *, owner_id: str = OWNER_ID):
        self.store = store
        self.owner_id = owner_id

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "service": "mock-x-platform", "owner_id": self.owner_id}

    def search_users(self, query: str, max_results: int = 10) -> dict[str, Any]:
        result, _ = self.search_users_with_diagnostics(query, max_results)
        return result

    def search_users_with_diagnostics(
        self, query: str, max_results: int = 10
    ) -> tuple[dict[str, Any], list[str]]:
        """Run the real search path and expose preliminary IDs for offline evaluation."""

        self._raise_injected_failure("search")
        if not QUERY_PATTERN.fullmatch(query or ""):
            raise MockXHttpError(
                400,
                "query must match [A-Za-z0-9_' ] and contain 1 to 50 characters",
                "Invalid query",
            )
        limit = min(max(int(max_results), 1), 10)
        query_tokens = set(normalize_text(query).split())

        def relevance(profile: dict[str, object]) -> tuple[int, str]:
            searchable_tokens = set(
                normalize_text(
                    " ".join(
                        str(profile.get(field, ""))
                        for field in ("name", "username", "description", "location")
                    )
                ).split()
            )
            overlap = sum(token in searchable_tokens for token in query_tokens)
            return (-overlap, str(profile["id"]))

        source = (
            self.store.search_profiles(query, limit=250)
            if self.store.profile_count()
            else MOCK_PROFILES
        )
        profiles = sorted(source, key=relevance)[:limit]
        result = {
            "data": profiles,
            "meta": {"result_count": len(profiles), "mock": True},
        }
        return result, [str(profile["id"]) for profile in source]

    def send_message(self, participant_id: str, text: str) -> dict[str, Any]:
        self._raise_injected_failure("send_dm")
        profile = self._profile(participant_id)
        cleaned = str(text or "").strip()
        if not cleaned:
            raise MockXHttpError(400, "Message text is required.", "Invalid message")
        if len(cleaned) > 10_000:
            raise MockXHttpError(400, "Message text exceeds 10000 characters.", "Invalid message")
        if profile.get("receives_your_dm") is not True:
            raise MockXHttpError(
                403,
                "This user cannot receive a DM from the mock owner account.",
                "DM not permitted",
            )
        message = self.store.save_message(
            sender_id=self.owner_id,
            recipient_id=str(participant_id),
            text=cleaned,
        )
        return {"data": message.to_dict()}

    def list_messages(self, participant_id: str) -> dict[str, Any]:
        self._raise_injected_failure("list_dm")
        self._profile(participant_id)
        messages = self.store.list_messages(
            owner_id=self.owner_id,
            participant_id=str(participant_id),
        )
        return {
            "data": [message.to_dict() for message in messages],
            "meta": {"result_count": len(messages), "mock": True},
        }

    def all_messages(self) -> dict[str, Any]:
        messages = self.store.all_messages()
        return {"data": [message.to_dict() for message in messages]}

    def inject_failure(
        self,
        operation: str,
        *,
        status: int,
        detail: str,
        count: int = 1,
    ) -> dict[str, Any]:
        if operation not in FAILURE_OPERATIONS:
            raise MockXHttpError(
                400,
                f"operation must be one of: {', '.join(sorted(FAILURE_OPERATIONS))}",
                "Invalid failure rule",
            )
        if not 400 <= int(status) <= 599:
            raise MockXHttpError(400, "status must be between 400 and 599", "Invalid failure rule")
        self.store.set_failure(
            operation,
            status=int(status),
            detail=str(detail or "Injected failure"),
            count=int(count),
        )
        return {"ok": True, "operation": operation, "count": int(count)}

    def reset(self) -> dict[str, Any]:
        self.store.reset()
        return {"ok": True}

    def _profile(self, participant_id: str) -> dict[str, Any]:
        profile = self.store.get_profile(participant_id)
        if profile is None:
            profile = next(
                (
                    profile
                    for profile in MOCK_PROFILES
                    if str(profile["id"]) == str(participant_id)
                ),
                None,
            )
        if profile is None:
            raise MockXHttpError(404, f"User {participant_id} was not found.", "User not found")
        return profile

    def _raise_injected_failure(self, operation: str) -> None:
        failure = self.store.consume_failure(operation)
        if failure is not None:
            raise MockXHttpError(
                int(failure["status"]),
                str(failure["detail"]),
                "Injected mock failure",
            )
