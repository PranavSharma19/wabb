from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from data.mock_profiles import MOCK_PROFILES
from ranking.normalization import normalize_text

from .store import MockXStore


OWNER_ID = "9000000"
QUERY_PATTERN = re.compile(r"^[A-Za-z0-9_' ]{1,50}$")
# X's real limit is 15 characters, and `parse_handle` enforces it on the way in,
# where a spoken handle is the thing being validated. This check is deliberately
# looser: it guards against malformed input reaching the store, and the mock's
# own generated corpus contains usernames like `isabella_rodriguez_4507` that
# exceed 15. Re-asserting X's rule here would make the API refuse to resolve
# profiles that exist in the fixture, which is the mock being pedantic about a
# rule its own dataset breaks. See "What this plan deliberately does not do".
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,50}$")
FAILURE_OPERATIONS = {"search", "lookup", "send_dm", "list_dm"}


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


# X's real bounds. The default of 100 is deliberate: a caller that omits
# max_results should reproduce the $1.00-per-search mistake here, in a harness
# that reports it, rather than in production.
DEFAULT_MAX_RESULTS = 100
MAX_MAX_RESULTS = 1000


def encode_cursor(query: str, offset: int) -> str:
    """An opaque (query, offset) cursor, so callers cannot page by arithmetic."""

    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
    raw = f"{digest}:{int(offset)}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str, query: str) -> int:
    """Read an offset back, refusing a cursor minted for a different query."""

    padded = str(token) + "=" * (-len(str(token)) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        digest, _, raw_offset = decoded.partition(":")
        offset = int(raw_offset)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MockXHttpError(
            400, "next_token is not a valid pagination token", "Invalid next_token"
        ) from exc
    if offset < 0 or digest != hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]:
        raise MockXHttpError(
            400, "next_token does not belong to this query", "Invalid next_token"
        )
    return offset


class MockXApplication:
    """Transport-independent X-like behavior used by the local HTTP server."""

    def __init__(self, store: MockXStore, *, owner_id: str = OWNER_ID):
        self.store = store
        self.owner_id = owner_id

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "service": "mock-x-platform", "owner_id": self.owner_id}

    def search_users(
        self,
        query: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        next_token: str | None = None,
    ) -> dict[str, Any]:
        result, _ = self.search_users_with_diagnostics(query, max_results, next_token)
        return result

    def search_users_with_diagnostics(
        self,
        query: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        next_token: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Run the real search path and expose preliminary IDs for offline evaluation.

        The IDs come back in relevance order, not pool-insertion order, since
        pagination now slices the same relevance-sorted list the page itself is
        drawn from. The one caller only tests these IDs for set membership, so
        the ordering is not load-bearing there -- but a caller that started
        relying on position would be relying on relevance, not the store.
        """

        self._raise_injected_failure("search")
        if not QUERY_PATTERN.fullmatch(query or ""):
            raise MockXHttpError(
                400,
                "query must match [A-Za-z0-9_' ] and contain 1 to 50 characters",
                "Invalid query",
            )
        limit = min(max(int(max_results), 1), MAX_MAX_RESULTS)
        offset = decode_cursor(next_token, query) if next_token else 0
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

        # The pool has to reach at least as deep as the page being served. The
        # floor of 250 keeps the first page byte-identical to round 4's.
        pool_limit = min(1000, max(250, offset + limit))
        source = (
            self.store.search_profiles(query, limit=pool_limit)
            if self.store.profile_count()
            else MOCK_PROFILES
        )
        ordered = sorted(source, key=relevance)
        profiles = ordered[offset : offset + limit]
        # Billed here rather than in the store: the store hands back a candidate
        # pool, and X charges for the resources the API returns.
        self.store.ledger.record_search()
        self.store.ledger.record_profiles(str(profile["id"]) for profile in profiles)
        meta: dict[str, Any] = {"result_count": len(profiles), "mock": True}
        if offset + limit < len(ordered):
            meta["next_token"] = encode_cursor(query, offset + limit)
        result = {"data": profiles, "meta": meta}
        return result, [str(profile["id"]) for profile in ordered]

    def lookup_user_by_username(self, username: str) -> dict[str, Any]:
        """Resolve one profile by handle -- X's /2/users/by/username/:username.

        A lookup, not a search: one profile in and one profile out, so it bills
        one User where a ten-result search bills ten.
        """

        self._raise_injected_failure("lookup")
        handle = str(username or "").strip().lstrip("@")
        if not USERNAME_PATTERN.fullmatch(handle):
            raise MockXHttpError(
                400,
                # 50, not X's 15, and the message has to say so: the bound
                # named here is the bound USERNAME_PATTERN enforces. See the
                # comment on USERNAME_PATTERN for why the mock is looser than X
                # -- its own generated corpus contains handles X would reject,
                # and a message promising 15 would send a reader hunting for a
                # bug in a handle this endpoint resolves perfectly well.
                "username must be 1 to 50 characters of letters, digits or underscore",
                "Invalid username",
            )
        profile = self.store.get_profile_by_username(handle)
        if profile is None:
            profile = next(
                (
                    dict(item)
                    for item in MOCK_PROFILES
                    if str(item["username"]).casefold() == handle.casefold()
                ),
                None,
            )
        if profile is None:
            raise MockXHttpError(404, f"User @{handle} was not found.", "User not found")
        self.store.ledger.record_lookup()
        self.store.ledger.record_profiles([str(profile["id"])])
        return {"data": profile}

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
        self.store.ledger.record_dm_send()
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
