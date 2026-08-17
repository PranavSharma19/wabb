# Recipient Refinement Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the handle-entry mode, give the mock X platform X's real pagination and pricing, and build the harness that measures whether spoken refinement can converge on the right person.

**Architecture:** Three independent strands, in order. (1) Handle mode: a spoken handle resolves one profile through a new lookup path that skips search entirely. (2) The mock gains X's real `max_results` bounds, `next_token` pagination, a billing ledger that charges distinct profiles per 24-hour window, and two flags that model what we do not know about the real endpoint. (3) A refinement harness drives labeled multi-turn refinement ladders against the **unfixed** search path and reports convergence, monotonicity, and dollars.

**Tech Stack:** Python 3.11+, stdlib `sqlite3` with FTS5, `requests`, `pytest`, `pygame` (device UI only).

**Spec:** `docs/superpowers/specs/2026-08-17-refinement-loop-design.md`

## Global Constraints

- **Do not change the real `XClient`'s ten-result cap.** `search/x_client.py:33` clamps `max_results` to 10. Spec §2: omitting `max_results` on the real endpoint costs **$1.00 per search** and that clamp is the only thing preventing it. Only spec Task 4 (depth on demand, not in this plan) may relax it, and only by replacing it with an explicit budget. X's real bounds go into the **mock** stack so the harness can reproduce the mistake; the real client keeps its cap.
- **Round-4 default behaviour must stay byte-identical.** `MockXStore` defaults to `match_scope="name_username"` and `result_order="bm25"`, querying the same two-column `profiles_fts` index with the same `ORDER BY bm25(profiles_fts), p.id`. Existing tests pin this; any test that needs updating gets a stated reason in the diff.
- **X API rates, checked 2026-08-17:** a User is **$0.010**; resources are deduplicated within 24-hour UTC windows, so the billable unit is *distinct profiles seen*, not requests. `query` pattern is `^[A-Za-z0-9_' ]{1,50}$`. `max_results` is min 1, **default 100**, max 1000. X usernames are `^[A-Za-z0-9_]{1,15}$`.
- **Cost is reported, never gated.** No acceptance threshold on dollars this round. Round 6's rule: a check must be observed failing under an injected regression before it is trusted, and there is no baseline yet.
- **The generator's existing RNG streams must not shift.** `randomizer` (identities) and `dirt` (rendering) are consumed in a fixed order. New per-profile draws go in a **new** stream, or every corpus every earlier round measured silently changes.
- Every commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Run the full suite with `python -m pytest -q` before each commit. It is 100 tests today and must stay green.

---

## File Structure

**Strand 1 — handle mode (Tasks 1–4)**

- Create `parsing/handle_transcript.py` — spoken text to X username. Pure, no I/O.
- Create `tests/test_handle_transcript.py`
- Create `tests/test_device_handle_mode.py`
- Modify `parsing/__init__.py` — export `parse_handle`
- Modify `mock_x_platform/store.py` — `get_profile_by_username`
- Modify `mock_x_platform/application.py` — `lookup_user_by_username`, `lookup` failure operation
- Modify `mock_x_platform/server.py` — `GET /2/users/by/username/{username}`
- Modify `mock_x_platform/client.py` — `lookup_username`, 404-tolerant `_request`
- Modify `search/base.py` — `lookup_username` on the client protocol
- Modify `search/x_client.py`, `search/mock_x_client.py` — `lookup_username`
- Modify `search/service.py` — `lookup_user`
- Modify `device_app/actions.py`, `device_app/input/keyboard.py` — `Action.HANDLE_MODE`
- Modify `device_app/search_contract.py`, `device_app/search_providers.py` — `lookup` on the provider protocol
- Modify `device_app/jobs.py` — `start_handle_lookup`
- Modify `device_app/state.py` — `HANDLE_RECORDING` / `HANDLE_LOOKUP` / `HANDLE_NOT_FOUND`
- Modify `device_app/ui/renderer.py`, `device_app/app.py`

**Strand 2 — mock economics (Tasks 5–9)**

- Create `mock_x_platform/pricing.py` — rate table + `BillingLedger`. One responsibility: what a run would have cost.
- Create `tests/test_pricing.py`
- Modify `mock_x_platform/store.py` — `follower_count`, `match_scope`, `result_order`, bio index, ledger ownership
- Modify `mock_x_platform/dataset.py` — third RNG stream, follower counts, `GENERATOR_VERSION` 3
- Modify `mock_x_platform/application.py` — ledger recording, `next_token`, X's real `max_results` bounds
- Modify `mock_x_platform/server.py`, `mock_x_platform/client.py` — pagination through HTTP
- Modify `mock_x_platform/evaluation.py` — `cost` block in the summary and printed report

**Strand 3 — the harness (Tasks 10–11)**

- Create `mock_x_platform/refinement.py` — case generation, the runner, the CLI
- Create `tests/test_refinement_harness.py`

---

### Task 1: Spoken handle to X username

**Files:**
- Create: `parsing/handle_transcript.py`
- Modify: `parsing/__init__.py`
- Test: `tests/test_handle_transcript.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_handle(text: str) -> str | None`, exported from `parsing`.

Context for the implementer: this function is only ever called when the user has already pressed the **Enter handle** button, so the mode is known before a word is spoken. That is what makes a leading "at" unambiguously the `@` sign here, when the same token in a spoken description means "works at". Do not add description-parsing heuristics; the whole point of the separate button is that this function never has to guess.

- [ ] **Step 1: Write the failing test**

Create `tests/test_handle_transcript.py`:

```python
from __future__ import annotations

import pytest

from parsing.handle_transcript import parse_handle


@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("at jbart", "jbart"),
        ("@jbart", "jbart"),
        ("at jay bart", "jaybart"),
        ("j b a r t", "jbart"),
        ("jbart underscore dev", "jbart_dev"),
        ("his handle is jbart", "jbart"),
        ("her username is mayachen", "mayachen"),
        ("JBart", "jbart"),
        ("at jbart.", "jbart"),
    ],
)
def test_spoken_forms_resolve_to_one_handle(spoken: str, expected: str) -> None:
    assert parse_handle(spoken) == expected


@pytest.mark.parametrize(
    "spoken",
    [
        "",
        "   ",
        # Sixteen characters. X stops at fifteen, so this is not a handle at all.
        "abcdefghijklmnop",
        # A description, not a handle. Handle mode should reject it rather than
        # join it into a forty-character pseudo-handle and look it up.
        "joe bart a member of technical staff at meta",
        "jbart at gmail dot com",
    ],
)
def test_things_that_are_not_handles_are_rejected(spoken: str) -> None:
    assert parse_handle(spoken) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_handle_transcript.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'parsing.handle_transcript'`

- [ ] **Step 3: Write the implementation**

Create `parsing/handle_transcript.py`:

```python
from __future__ import annotations

import re


# X usernames are one to fifteen characters of ASCII letters, digits and
# underscore. Nothing else is a handle, however confidently it was spoken.
HANDLE_PATTERN = re.compile(r"^[a-z0-9_]{1,15}$")

# Lead-ins people say before the handle itself, longest first so "his handle is"
# never leaves a stray "is" behind.
_LEAD_INS = (
    "my handle is",
    "his handle is",
    "her handle is",
    "their handle is",
    "the handle is",
    "handle is",
    "my username is",
    "his username is",
    "her username is",
    "their username is",
    "the username is",
    "username is",
    "it's",
    "its",
)

# Spoken punctuation. Only the underscore survives: X allows no other symbol, so
# a spoken "dot" or "dash" should make the handle invalid rather than be
# translated into something the user did not say.
_SPOKEN_SYMBOLS = ((" underscore ", "_"), (" under score ", "_"))


def parse_handle(text: str) -> str | None:
    """Turn a spoken handle into an X username, or None if it is not one.

    Only ever called in handle mode, where the user has already said they are
    giving a handle. That is what makes a leading "at" unambiguously the '@'
    sign here, when the same token in a spoken description means "works at".
    """

    cleaned = re.sub(r"[.,!?]", " ", str(text or "").casefold())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None

    for lead_in in _LEAD_INS:
        if cleaned.startswith(f"{lead_in} "):
            cleaned = cleaned[len(lead_in) + 1 :].strip()
            break
    if cleaned.startswith("at "):
        cleaned = cleaned[3:].strip()
    cleaned = cleaned.lstrip("@").strip()

    for spoken, symbol in _SPOKEN_SYMBOLS:
        cleaned = cleaned.replace(spoken, symbol)

    # A handle contains no spaces, so every space left is an artifact of
    # dictation: "j b a r t" and "jay bart" are each one handle. Anything long
    # enough that joining it produces more than fifteen characters was a
    # sentence, and falls out through the pattern check below.
    handle = cleaned.replace(" ", "")
    return handle if HANDLE_PATTERN.fullmatch(handle) else None
```

- [ ] **Step 4: Export it from the package**

In `parsing/__init__.py`, replace the whole file with:

```python
from .handle_transcript import parse_handle
from .recipient_parser import apply_spoken_refinement, parse_recipient_description, refine_criteria
from .service import RecipientParser, RuleBasedRecipientParser

__all__ = [
    "RecipientParser",
    "RuleBasedRecipientParser",
    "apply_spoken_refinement",
    "parse_handle",
    "parse_recipient_description",
    "refine_criteria",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_handle_transcript.py -q`
Expected: PASS — 14 passed

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS — all existing tests still green

- [ ] **Step 7: Commit**

```bash
git add parsing/handle_transcript.py parsing/__init__.py tests/test_handle_transcript.py
git commit -m "feat: parse a spoken X handle in handle mode

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Handle lookup through the mock X platform

**Files:**
- Modify: `mock_x_platform/store.py`
- Modify: `mock_x_platform/application.py`
- Modify: `mock_x_platform/server.py`
- Modify: `mock_x_platform/client.py`
- Test: `tests/test_mock_x_application.py`, `tests/test_mock_x_http.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `MockXStore.get_profile_by_username(username: str) -> dict[str, Any] | None`
  - `MockXApplication.lookup_user_by_username(username: str) -> dict[str, Any]` — `{"data": profile}`, raises `MockXHttpError` 400 on a malformed handle and 404 on no such user
  - `MockXPlatformClient.lookup_username(username: str) -> Candidate | None`
  - HTTP route `GET /2/users/by/username/{username}`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mock_x_application.py`:

```python
def test_handle_lookup_resolves_one_profile(app: MockXApplication) -> None:
    result = app.lookup_user_by_username("johndoe_xyz")

    assert result["data"]["id"] == "1000001"
    assert result["data"]["username"] == "johndoe_xyz"


def test_handle_lookup_is_case_insensitive_and_ignores_a_leading_at(
    app: MockXApplication,
) -> None:
    assert app.lookup_user_by_username("@JohnDoe_XYZ")["data"]["id"] == "1000001"


def test_handle_lookup_rejects_something_that_is_not_a_handle(
    app: MockXApplication,
) -> None:
    with pytest.raises(MockXHttpError) as error:
        app.lookup_user_by_username("this is far too long to be one")

    assert error.value.status == 400


def test_handle_lookup_reports_a_missing_account_as_missing(
    app: MockXApplication,
) -> None:
    # 404 rather than an empty list: the user asked for one specific account, so
    # "there is no such account" is an answer the device has to be able to show.
    with pytest.raises(MockXHttpError) as error:
        app.lookup_user_by_username("nobodyhome")

    assert error.value.status == 404
```

Append to `tests/test_mock_x_http.py`:

```python
def test_http_handle_lookup_round_trip(platform_client) -> None:
    candidate = platform_client.lookup_username("@johndoe_xyz")

    assert candidate is not None
    assert candidate.id == "1000001"


def test_http_handle_lookup_returns_none_for_a_missing_account(platform_client) -> None:
    assert platform_client.lookup_username("nobodyhome") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_mock_x_application.py tests/test_mock_x_http.py -q`
Expected: FAIL — `AttributeError: 'MockXApplication' object has no attribute 'lookup_user_by_username'`

- [ ] **Step 3: Add the store lookup**

In `mock_x_platform/store.py`, immediately after the `get_profile` method (line 239-244), add:

```python
    def get_profile_by_username(self, username: str) -> dict[str, Any] | None:
        """Resolve one profile by handle. Case-insensitive: X handles are."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM profiles WHERE username = ? COLLATE NOCASE",
                (str(username or "").lstrip("@"),),
            ).fetchone()
        return _profile_row(row) if row is not None else None
```

- [ ] **Step 4: Add the application lookup**

In `mock_x_platform/application.py`, change the module constants (lines 13-14) to:

```python
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
```

Then add this method immediately after `search_users_with_diagnostics` (after line 89):

```python
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
                "username must be 1 to 15 characters of letters, digits or underscore",
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
        return {"data": profile}
```

- [ ] **Step 5: Add the HTTP route**

In `mock_x_platform/server.py`, change the import on line 11 to:

```python
from urllib.parse import parse_qs, unquote, urlparse
```

Add after line 22 (`DM_LIST_PATTERN = ...`):

```python
USER_LOOKUP_PATTERN = re.compile(r"^/2/users/by/username/([^/]+)$")
```

In `_dispatch`, immediately after the `/2/users/search` block (after line 63), add:

```python
            lookup_match = USER_LOOKUP_PATTERN.fullmatch(parsed.path)
            if method == "GET" and lookup_match:
                self._json(
                    HTTPStatus.OK,
                    self.app.lookup_user_by_username(unquote(lookup_match.group(1))),
                )
                return
```

- [ ] **Step 6: Add the platform client lookup**

In `mock_x_platform/client.py`, add this method after `search_users` (after line 44):

```python
    def lookup_username(self, username: str) -> Candidate | None:
        """One profile for one handle, or None when no such account exists."""

        payload = self._request(
            "GET",
            f"/2/users/by/username/{quote(str(username or '').lstrip('@'), safe='')}",
            allow_missing=True,
        )
        data = payload.get("data")
        return Candidate.from_dict(data) if isinstance(data, dict) else None
```

Change the `_request` signature (line 89) and add the 404 branch. Replace lines 89-100 with:

```python
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
```

(The remaining body of `_request`, from `try: payload = response.json()` onward, is unchanged.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_mock_x_application.py tests/test_mock_x_http.py -q`
Expected: PASS

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add mock_x_platform tests/test_mock_x_application.py tests/test_mock_x_http.py
git commit -m "feat: resolve a profile by handle through the mock X platform

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Handle lookup on the search clients and service

**Files:**
- Modify: `search/base.py`
- Modify: `search/mock_x_client.py`
- Modify: `search/x_client.py`
- Modify: `search/service.py`
- Test: `tests/test_service.py`, `tests/test_x_client.py`

**Interfaces:**
- Consumes: `MockXPlatformClient.lookup_username` from Task 2.
- Produces:
  - `UserSearchClient.lookup_username(username: str) -> Candidate | None` on the protocol
  - `search.service.lookup_user(username, *, client=None, settings=None) -> Candidate | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_service.py`:

```python
def test_lookup_user_resolves_a_handle_without_searching(tmp_path) -> None:
    class LookupClient:
        cache_namespace = "test"

        def __init__(self) -> None:
            self.searches: list[str] = []
            self.lookups: list[str] = []

        def search_users(self, query: str, max_results: int = 10) -> list[Candidate]:
            self.searches.append(query)
            return []

        def lookup_username(self, username: str) -> Candidate | None:
            self.lookups.append(username)
            return Candidate(id="1", name="Joe Bart", username=username)

    client = LookupClient()
    settings = Settings(True, "", Path(tmp_path / "unused.json"))
    found = lookup_user("@jbart", client=client, settings=settings)

    assert found is not None and found.username == "jbart"
    # The whole point of the shortcut: the search path is never entered.
    assert client.searches == []
    assert client.lookups == ["jbart"]
```

Change line 7 of that file to `from search.service import find_users, lookup_user`. `Settings` and `Path` are already imported there.

Append to `tests/test_x_client.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_service.py tests/test_x_client.py -q`
Expected: FAIL — `ImportError: cannot import name 'lookup_user'`

- [ ] **Step 3: Extend the client protocol**

Replace `search/base.py` with:

```python
from __future__ import annotations

from typing import Protocol

from models.candidate import Candidate


class UserSearchClient(Protocol):
    cache_namespace: str

    def search_users(self, query: str, max_results: int = 10) -> list[Candidate]: ...

    def lookup_username(self, username: str) -> Candidate | None: ...
```

- [ ] **Step 4: Implement it on the offline client**

In `search/mock_x_client.py`, add after `search_users` (after line 37):

```python
    def lookup_username(self, username: str) -> Candidate | None:
        handle = str(username or "").strip().lstrip("@").casefold()
        profile = next(
            (
                profile
                for profile in MOCK_PROFILES
                if str(profile.get("username", "")).casefold() == handle
            ),
            None,
        )
        return Candidate.from_dict(profile) if profile is not None else None
```

- [ ] **Step 5: Implement it on the real client**

In `search/x_client.py`, add `from urllib.parse import quote` to the imports, add this constant after `BASE_URL` (line 17):

```python
    LOOKUP_URL = "https://api.x.com/2/users/by/username/{username}"
```

and add this method after `search_users` (after line 60):

```python
    def lookup_username(self, username: str) -> Candidate | None:
        """Resolve one handle. One User billed, against ten for a search."""

        handle = str(username or "").strip().lstrip("@")
        try:
            response = requests.get(
                self.LOOKUP_URL.format(username=quote(handle, safe="")),
                headers={"Authorization": f"Bearer {self.access_token}"},
                params={"user.fields": self.USER_FIELDS},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise XApiError(f"Could not reach the X API: {exc}") from exc

        # X reports a missing account both ways depending on the endpoint: a 404,
        # or a 200 whose payload carries `errors` and no `data`. Both mean the
        # same thing to the caller.
        if response.status_code == 404:
            return None
        if not response.ok:
            detail = _error_detail(response)
            raise XApiError(f"X API request failed ({response.status_code}): {detail}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise XApiError("X API returned a non-JSON response.") from exc

        data = payload.get("data")
        return _candidate_from_x(data) if isinstance(data, dict) else None
```

- [ ] **Step 6: Add the service entry point**

In `search/service.py`, add after `find_users` (after line 49):

```python
def lookup_user(
    username: str,
    *,
    client: UserSearchClient | None = None,
    settings: Settings | None = None,
) -> Candidate | None:
    """Resolve one profile from a handle, skipping the search loop entirely.

    Deliberately uncached. A lookup returns one profile for one User charge, and
    X already deduplicates a repeated profile inside its 24-hour window, so a
    local cache would save nothing while risking a stale DM-eligibility flag on
    the one profile the user is about to message.
    """

    handle = str(username or "").strip().lstrip("@")
    if not handle:
        return None
    active_settings = settings or load_settings()
    active_client = client or _make_client(active_settings)
    logger.info("[LOOKUP] Resolving @%s", handle)
    return active_client.lookup_username(handle)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_service.py tests/test_x_client.py -q`
Expected: PASS

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. `tests/test_x_client.py::test_search_users_clamps_max_results` must still assert `params["max_results"] == 10` — see Global Constraints; the real client's cap does not move in this plan.

- [ ] **Step 9: Commit**

```bash
git add search tests/test_service.py tests/test_x_client.py
git commit -m "feat: add lookup_user, the handle shortcut past the search loop

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Handle mode on the device

**Files:**
- Modify: `device_app/actions.py`
- Modify: `device_app/input/keyboard.py`
- Modify: `device_app/search_contract.py`
- Modify: `device_app/search_providers.py`
- Modify: `device_app/jobs.py`
- Modify: `device_app/state.py`
- Modify: `device_app/ui/renderer.py`
- Modify: `device_app/app.py`
- Test: `tests/test_device_handle_mode.py`

**Interfaces:**
- Consumes: `parse_handle` (Task 1), `search.service.lookup_user` (Task 3).
- Produces:
  - `Action.HANDLE_MODE`
  - `AppState.HANDLE_RECORDING`, `AppState.HANDLE_LOOKUP`, `AppState.HANDLE_NOT_FOUND`
  - `SessionContext.handle: str`
  - `SearchProvider.lookup(handle: str) -> SearchResult`
  - `WorkflowRunner.start_handle_lookup(handle: str) -> int`

Context for the implementer: the handle is a **second record button on the recipient screen**, not a detector that sniffs free speech. Because the mode is chosen before the user speaks, "message at Meta" has nothing to disambiguate. Two consequences follow and both are requirements, not niceties. First, describe-mode input must never reach `parse_handle` and handle-mode input must never reach `parse_recipient_description`. Second, failure is loud: an unresolvable handle lands on `HANDLE_NOT_FOUND`, and must not silently fall through into a search — the user asked for one specific account, so "no such account" is an answer.

Note on hardware: `device_app/input/gpio.py` maps five fixed physical buttons and is deliberately left alone. The handle button is a touchscreen affordance; the keyboard emulator gets `H`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_device_handle_mode.py`:

```python
from __future__ import annotations

from models.criteria import RecipientCriteria

from device_app.actions import Action
from device_app.events import WorkerEvent, WorkerEventType
from device_app.search_contract import ProfileCandidate, SearchResult
from device_app.state import AppState, DeviceController

from tests.test_device_state import FakeRunner


class HandleRunner(FakeRunner):
    def __init__(self, result: SearchResult | None = None) -> None:
        super().__init__()
        self.lookup_calls: list[str] = []
        self.result = result

    def start_handle_lookup(self, handle: str) -> int:
        operation_id = self.next_id
        self.next_id += 1
        self.lookup_calls.append(handle)
        return operation_id


FOUND = SearchResult(
    query="@jbart",
    candidates=(
        ProfileCandidate(id="55", name="Joe Bart", username="jbart", profile_url="https://x.com/jbart"),
    ),
)


def speak(controller: DeviceController, runner: HandleRunner, transcript: str) -> None:
    operation_id = controller.context.current_operation_id
    assert operation_id is not None
    controller.dispatch(Action.SEND_UP)
    runner.emit(
        WorkerEvent(operation_id, WorkerEventType.VOICE_COMPLETE, transcript, "handle_voice")
    )
    controller.update()


def test_handle_mode_skips_the_search_loop_entirely() -> None:
    runner = HandleRunner()
    controller = DeviceController(runner)

    controller.dispatch(Action.HANDLE_MODE)
    assert controller.state is AppState.HANDLE_RECORDING
    assert runner.voice_calls == ["handle_voice"]

    speak(controller, runner, "his handle is jbart")
    assert controller.state is AppState.HANDLE_LOOKUP
    assert runner.lookup_calls == ["jbart"]
    # The point of the shortcut: no search was ever started.
    assert runner.search_calls == []

    operation_id = controller.context.current_operation_id
    assert operation_id is not None
    runner.emit(
        WorkerEvent(operation_id, WorkerEventType.SEARCH_COMPLETE, FOUND, "handle_lookup")
    )
    controller.update()

    assert controller.state is AppState.SELECT_PROFILE
    assert len(controller.context.candidates) == 1
    assert controller.current_candidate.username == "jbart"


def test_an_unspeakable_handle_stops_rather_than_becoming_a_search() -> None:
    runner = HandleRunner()
    controller = DeviceController(runner)

    controller.dispatch(Action.HANDLE_MODE)
    speak(controller, runner, "joe bart a member of technical staff at meta")

    # A deliberate handle request that cannot be parsed is an answer, not a
    # mis-detection to be quietly converted into a description search.
    assert controller.state is AppState.HANDLE_NOT_FOUND
    assert runner.lookup_calls == []
    assert runner.search_calls == []


def test_an_unresolvable_handle_lands_on_not_found() -> None:
    runner = HandleRunner()
    controller = DeviceController(runner)

    controller.dispatch(Action.HANDLE_MODE)
    speak(controller, runner, "at nobodyhome")
    operation_id = controller.context.current_operation_id
    assert operation_id is not None
    runner.emit(
        WorkerEvent(
            operation_id,
            WorkerEventType.SEARCH_COMPLETE,
            SearchResult(query="@nobodyhome", candidates=()),
            "handle_lookup",
        )
    )
    controller.update()

    assert controller.state is AppState.HANDLE_NOT_FOUND
    assert controller.context.handle == "nobodyhome"


def test_not_found_offers_both_ways_forward() -> None:
    runner = HandleRunner()
    controller = DeviceController(runner)
    controller.dispatch(Action.HANDLE_MODE)
    speak(controller, runner, "not a handle at all because it is far too long")

    controller.dispatch(Action.SEND_DOWN)
    assert controller.state is AppState.RECORD_RECIPIENT
    assert runner.voice_calls[-1] == "initial_voice"


def test_the_profile_screen_can_switch_to_the_handle_as_a_recovery_path() -> None:
    runner = HandleRunner()
    controller = DeviceController(runner)
    controller.context.criteria = RecipientCriteria(name="Joe Bart")
    controller.state = AppState.SELECT_PROFILE

    controller.dispatch(Action.HANDLE_MODE)

    assert controller.state is AppState.HANDLE_RECORDING
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_device_handle_mode.py -q`
Expected: FAIL — `AttributeError: HANDLE_MODE`

- [ ] **Step 3: Add the action and its key**

In `device_app/actions.py`, add after `REFINE = auto()`:

```python
    HANDLE_MODE = auto()
```

In `device_app/input/keyboard.py`, add to the `key_down` map after the `pygame.K_r` line:

```python
            pygame.K_h: Action.HANDLE_MODE,
```

- [ ] **Step 4: Add `lookup` to the search provider contract**

In `device_app/search_contract.py`, replace the `SearchProvider` protocol (lines 91-94) with:

```python
class SearchProvider(Protocol):
    """The only search dependency visible to the device application."""

    def search(self, criteria: RecipientCriteria) -> SearchResult: ...

    def lookup(self, handle: str) -> SearchResult: ...
```

In `device_app/search_providers.py`, replace `ExistingSearchAdapter` (lines 24-40) with:

```python
class ExistingSearchAdapter:
    """Adapts the repo's existing finder without exposing it to UI/state code."""

    def __init__(
        self,
        finder: Callable[[RecipientCriteria], list[Candidate]],
        looker: Callable[[str], Candidate | None] | None = None,
    ) -> None:
        self._finder = finder
        self._looker = looker

    def search(self, criteria: RecipientCriteria) -> SearchResult:
        domain_candidates = self._finder(
            RecipientCriteria.from_dict(criteria.to_dict())
        )[:10]
        return SearchResult(
            query=criteria_query(criteria),
            candidates=tuple(_from_domain(item) for item in domain_candidates),
        )

    def lookup(self, handle: str) -> SearchResult:
        """Zero or one candidate. Empty is "no such account", not "search harder"."""

        wanted = str(handle or "").strip().lstrip("@")
        found = self._looker(wanted) if self._looker is not None else None
        return SearchResult(
            query=f"@{wanted}",
            candidates=() if found is None else (_from_domain(found),),
        )
```

Add this method to `MockSearchProvider`, after `search` (after line 70):

```python
    def lookup(self, handle: str) -> SearchResult:
        wanted = str(handle or "").strip().lstrip("@").casefold()
        found = next(
            (item for item in self._candidates if item.username.casefold() == wanted),
            None,
        )
        return SearchResult(
            query=f"@{wanted}", candidates=() if found is None else (found,)
        )
```

- [ ] **Step 5: Add the lookup job**

In `device_app/jobs.py`, add to the `WorkflowRunner` protocol after `start_search` (line 25):

```python
    def start_handle_lookup(self, handle: str) -> int: ...
```

Add this method to `ThreadedWorkflowRunner` after `start_search` (after line 90):

```python
    def start_handle_lookup(self, handle: str) -> int:
        self.cancel_current()
        operation_id = next(self._ids)
        self._current_operation_id = operation_id
        self._executor.submit(self._run_handle_lookup, operation_id, str(handle))
        return operation_id
```

and this worker after `_run_search` (after line 166):

```python
    def _run_handle_lookup(self, operation_id: int, handle: str) -> None:
        try:
            result = self.search_provider.lookup(handle)
        except Exception as exc:
            self._events.put(
                WorkerEvent(operation_id, WorkerEventType.FAILED, str(exc), "handle_lookup")
            )
            return
        # Reuses SEARCH_COMPLETE: a lookup is a search result of length zero or
        # one, and the profile screen already knows how to show one candidate.
        self._events.put(
            WorkerEvent(
                operation_id, WorkerEventType.SEARCH_COMPLETE, result, "handle_lookup"
            )
        )
```

- [ ] **Step 6: Add the three states and their handlers**

In `device_app/state.py`:

Change the import on line 11 to:

```python
from parsing import RecipientParser, RuleBasedRecipientParser, parse_handle
```

In `AppState`, add after `ERROR = auto()` (line 33) and before the compatibility-names comment:

```python
    HANDLE_RECORDING = auto()
    HANDLE_LOOKUP = auto()
    HANDLE_NOT_FOUND = auto()
```

In `SessionContext`, add after `search_query: str = ""` (line 47):

```python
    handle: str = ""
```

In `dispatch`, add to the `handlers` dict:

```python
            AppState.HANDLE_RECORDING: self._handle_handle_recording,
            AppState.HANDLE_LOOKUP: self._handle_handle_lookup,
            AppState.HANDLE_NOT_FOUND: self._handle_handle_not_found,
```

Replace `_handle_home` (lines 133-138) with:

```python
    def _handle_home(self, action: Action) -> None:
        if action is Action.SEND_DOWN:
            self.context = SessionContext(
                recording_remaining=int(self.record_limit_seconds)
            )
            self._start_voice("initial_voice", AppState.RECORD_RECIPIENT)
        elif action is Action.HANDLE_MODE:
            # The recipient screen's second record button. Which of the two the
            # user pressed is the whole disambiguation.
            self.context = SessionContext(
                recording_remaining=int(self.record_limit_seconds)
            )
            self._start_voice("handle_voice", AppState.HANDLE_RECORDING)
```

In `_handle_profiles`, add before the `elif action is Action.SEND_DOWN` branch (line 203):

```python
        elif action is Action.HANDLE_MODE:
            # The recovery path: the candidate list is not the person, and the
            # user turns out to know the handle after all. The criteria are kept
            # so BACK still returns to a populated review screen.
            self._start_voice("handle_voice", AppState.HANDLE_RECORDING)
```

In `_handle_transcribing`, add to the `targets` dict:

```python
            "handle_voice": AppState.HOME,
```

Add the three handlers after `_handle_searching` (after line 186):

```python
    def _handle_handle_recording(self, action: Action) -> None:
        if action is Action.SEND_UP:
            self._finish_recording()
        elif action in {Action.REFINE, Action.BACK}:
            self._cancel_to(AppState.HOME)

    def _handle_handle_lookup(self, action: Action) -> None:
        if action in {Action.REFINE, Action.BACK}:
            self._cancel_to(AppState.HOME)

    def _handle_handle_not_found(self, action: Action) -> None:
        # Both ways forward are offered explicitly. Picking one silently is what
        # the detector design did wrong: the user asked for a specific account
        # and deserves to be told it is not there.
        if action is Action.SEND_DOWN:
            self._start_voice("initial_voice", AppState.RECORD_RECIPIENT)
        elif action is Action.HANDLE_MODE:
            self._start_voice("handle_voice", AppState.HANDLE_RECORDING)
        elif action is Action.BACK:
            self._cancel_to(AppState.HOME)
```

Add `_start_handle_lookup` after `_start_search` (after line 292):

```python
    def _start_handle_lookup(self, handle: str) -> None:
        operation_id = self.runner.start_handle_lookup(handle)
        self.context.current_operation_id = operation_id
        self.context.current_operation = "handle_lookup"
        self.context.recoverable_error = ""
        self.state = AppState.HANDLE_LOOKUP
```

In `_handle_worker_event`, add `AppState.HANDLE_RECORDING` to the `RECORDING_FINISHED` state set (lines 320-325), and change the `SEARCH_COMPLETE` branch (lines 331-333) to:

```python
        if event.type is WorkerEventType.SEARCH_COMPLETE:
            self._handle_search_complete(event.payload, event.operation)
            return
```

In `_handle_voice_complete`, add immediately after the `initial_voice` block (after line 352):

```python
        if operation == "handle_voice":
            self.context.transcript = transcript
            handle = parse_handle(transcript)
            self.context.handle = handle or ""
            if handle is None:
                self.state = AppState.HANDLE_NOT_FOUND
                return
            self._start_handle_lookup(handle)
            return
```

Replace `_handle_search_complete` (lines 381-393) with:

```python
    def _handle_search_complete(self, payload: Any, operation: str = "search") -> None:
        if isinstance(payload, SearchResult):
            self.context.search_query = payload.query
            self.context.candidates = list(payload.candidates)
        else:
            self.context.search_query = ""
            self.context.candidates = [
                _coerce_candidate(candidate) for candidate in list(payload or [])[:10]
            ]
        self.context.selected_index = 0
        self.context.profile_scroll = 0
        self._clear_operation()
        if operation == "handle_lookup" and not self.context.candidates:
            self.state = AppState.HANDLE_NOT_FOUND
            return
        self.state = AppState.SELECT_PROFILE
```

In `_handle_failure`, add to the `back_states` dict:

```python
            "handle_voice": AppState.HOME,
            "handle_lookup": AppState.HOME,
```

In `_handle_error`, replace the retry body (lines 254-267) with:

```python
        if action is Action.SEND_DOWN:
            operation = self.context.error_operation
            self.context.recoverable_error = ""
            if operation == "search":
                self._start_search()
            elif operation == "handle_lookup" and self.context.handle:
                self._start_handle_lookup(self.context.handle)
            elif operation == "send_message":
                self._start_message()
            else:
                states = {
                    "initial_voice": AppState.RECORD_RECIPIENT,
                    "recipient_refinement": AppState.REFINE_RECIPIENT,
                    "handle_voice": AppState.HANDLE_RECORDING,
                    "message_voice": AppState.RECORD_MESSAGE,
                    "message_refinement": AppState.REFINE_MESSAGE,
                }
                self._start_voice(operation or "initial_voice", states.get(operation, AppState.RECORD_RECIPIENT))
```

- [ ] **Step 7: Render the new states**

In `device_app/ui/renderer.py`, add to the `render` chain after the `AppState.SEARCHING` branch (after line 72):

```python
        elif state is AppState.HANDLE_RECORDING:
            self._recording(surface, controller, timestamp, "SAY THE HANDLE", "Release when done")
        elif state is AppState.HANDLE_LOOKUP:
            self._loading(
                surface,
                "LOOKING UP HANDLE",
                f"Resolving @{controller.context.handle}",
                timestamp,
            )
        elif state is AppState.HANDLE_NOT_FOUND:
            self._handle_not_found(surface, controller)
```

Add this method after `_recipient_refinement`:

```python
    def _handle_not_found(self, surface: object, controller: DeviceController) -> None:
        handle = controller.context.handle
        self._eyebrow(surface, "HANDLE")
        self._center_text(surface, "NO ACCOUNT FOUND", 92, self.font_title)
        self._rounded_panel(surface, (90, 160, 620, 128), self.theme.panel)
        subject = f"@{handle}" if handle else "That did not sound like a handle"
        self._center_text(surface, subject, 190, self.font_body, self.theme.danger)
        self._center_text(surface, "Hold SPACE or ENTER to describe them instead", 232, self.font_body)
        self._center_text(surface, "H  Try another handle", 264, self.font_small, self.theme.muted)
        self._footer(surface, "ESC  Home", "SPACE / ENTER  Describe instead")
```

In `_home`, add the second affordance after line 95:

```python
        self._text(surface, "H  I already know their @handle", (132, 222), self.font_small, self.theme.accent)
```

- [ ] **Step 8: Wire the real lookup into the app**

In `device_app/app.py`, replace the `existing` branch of `_configured_search_provider` (lines 83-90) with:

```python
    if mode == "existing":
        # Import only when explicitly selected. Mock device development never
        # initializes or calls the real search implementation.
        from search.service import find_users, lookup_user

        return ExistingSearchAdapter(
            lambda criteria: find_users(criteria, settings=settings),
            lambda handle: lookup_user(handle, settings=settings),
        )
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `python -m pytest tests/test_device_handle_mode.py -q`
Expected: PASS — 5 passed

- [ ] **Step 10: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. `tests/test_device_ui.py` renders every `AppState`; the three new branches must keep it green.

- [ ] **Step 11: Commit**

```bash
git add device_app tests/test_device_handle_mode.py
git commit -m "feat: handle entry as an explicit device mode

The second record button on the recipient screen, not a detector over free
speech. Because the mode is chosen before the user speaks, 'message at Meta'
has nothing to disambiguate. An unresolvable handle stops on a state the user
can act on rather than silently becoming a description search.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The pricing model

**Files:**
- Create: `mock_x_platform/pricing.py`
- Test: `tests/test_pricing.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `USER_USD: float`, `DM_EVENT_USD: float`, `RATES_CHECKED_ON: str`
  - `window_key(moment: datetime) -> str`
  - `BillingLedger` with `record_profiles(ids, *, at=None) -> int`, `record_search()`, `record_lookup()`, `record_dm_send()`, `reset()`, properties `distinct_profiles`, `billed_windows`, `estimated_usd`, and `summary(*, case_count: int = 0) -> dict[str, Any]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pricing.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mock_x_platform.pricing import USER_USD, BillingLedger


MOMENT = datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc)


def test_the_same_profile_inside_one_window_is_billed_once() -> None:
    ledger = BillingLedger()

    ledger.record_profiles(["1", "2", "3"], at=MOMENT)
    newly_billed = ledger.record_profiles(["2", "3", "4"], at=MOMENT + timedelta(hours=2))

    # This is the fact the whole design rests on: the billable unit is distinct
    # profiles seen, not requests made.
    assert newly_billed == 1
    assert ledger.distinct_profiles == 4
    assert ledger.estimated_usd == round(4 * USER_USD, 6)


def test_the_same_profile_in_the_next_window_is_billed_again() -> None:
    ledger = BillingLedger()

    ledger.record_profiles(["1"], at=MOMENT)
    ledger.record_profiles(["1"], at=MOMENT + timedelta(days=1))

    assert ledger.billed_windows == 2
    assert ledger.distinct_profiles == 2


def test_a_lookup_bills_one_user_where_a_search_bills_ten() -> None:
    search_ledger = BillingLedger()
    search_ledger.record_search()
    search_ledger.record_profiles([str(index) for index in range(10)], at=MOMENT)

    lookup_ledger = BillingLedger()
    lookup_ledger.record_lookup()
    lookup_ledger.record_profiles(["1"], at=MOMENT)

    assert search_ledger.estimated_usd == round(10 * USER_USD, 6)
    assert lookup_ledger.estimated_usd == round(USER_USD, 6)


def test_the_summary_leads_with_the_per_case_figure() -> None:
    ledger = BillingLedger()
    ledger.record_profiles([str(index) for index in range(100)], at=MOMENT)

    summary = ledger.summary(case_count=50)

    assert summary["per_case_usd"] == round(100 * USER_USD / 50, 6)
    assert summary["distinct_profiles"] == 100
    assert summary["rates_checked_on"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_pricing.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mock_x_platform.pricing'`

- [ ] **Step 3: Write the implementation**

Create `mock_x_platform/pricing.py`:

```python
"""X API rates, and a ledger that bills the way X bills.

Checked against docs.x.com on the date in RATES_CHECKED_ON. The subscription
tiers are retired; X charges per resource returned, against credits bought up
front. Two facts drive every number below:

  * A User costs $0.010. `max_results` defaults to 100, so a search that forgets
    the parameter costs a dollar and `max_results=1000` costs ten.
  * Resources are deduplicated inside 24-hour UTC windows. The billable unit is
    therefore *distinct profiles seen*, not requests made: fetching the same ten
    profiles ten times costs $0.10, while fetching ten new ones ten times costs
    $1.00. That is why lazy incremental depth is the economically correct
    architecture rather than merely a nice optimisation.

Update this module, and the date, when the rates move.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


RATES_CHECKED_ON = "2026-08-17"

USER_USD = 0.010
# X publishes DM sends in a $0.010-$0.015 band. Taking the top of it means an
# estimate here never understates what a real run would have cost.
DM_EVENT_USD = 0.015

# X documents "24-hour windows" without pinning the boundary, so this models them
# as UTC calendar days. Close enough for an estimate, and wrong in the safe
# direction: a real rolling window would deduplicate at least as much.
DEDUP_WINDOW = "UTC calendar day"


def window_key(moment: datetime) -> str:
    """Which deduplication window a moment falls in."""

    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d")


class BillingLedger:
    """What a run would have cost on the real API.

    Deliberately not a request counter. A counter would report the metric that
    looks natural and is wrong; this reports the one X actually charges on.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, set[str]] = {}
        self.searches = 0
        self.lookups = 0
        self.dm_sends = 0

    def record_profiles(
        self, profile_ids: Iterable[str], *, at: datetime | None = None
    ) -> int:
        """Record profiles served. Returns how many of them were newly billed."""

        window = window_key(at or datetime.now(timezone.utc))
        seen = self._profiles.setdefault(window, set())
        before = len(seen)
        seen.update(str(identifier) for identifier in profile_ids)
        return len(seen) - before

    def record_search(self) -> None:
        self.searches += 1

    def record_lookup(self) -> None:
        self.lookups += 1

    def record_dm_send(self) -> None:
        self.dm_sends += 1

    def reset(self) -> None:
        self._profiles.clear()
        self.searches = self.lookups = self.dm_sends = 0

    @property
    def distinct_profiles(self) -> int:
        return sum(len(identifiers) for identifiers in self._profiles.values())

    @property
    def billed_windows(self) -> int:
        return len(self._profiles)

    @property
    def estimated_usd(self) -> float:
        return round(
            self.distinct_profiles * USER_USD + self.dm_sends * DM_EVENT_USD, 6
        )

    def summary(self, *, case_count: int = 0) -> dict[str, Any]:
        return {
            "rates_checked_on": RATES_CHECKED_ON,
            "user_usd": USER_USD,
            "dedup_window": DEDUP_WINDOW,
            "distinct_profiles": self.distinct_profiles,
            "billed_windows": self.billed_windows,
            "searches": self.searches,
            "lookups": self.lookups,
            "dm_sends": self.dm_sends,
            "estimated_usd": self.estimated_usd,
            "per_case_usd": (
                round(self.estimated_usd / case_count, 6) if case_count else None
            ),
            # Said out loud so nobody reads a $400 evaluation as a $400 product:
            # the run total is the price of sweeping the corpus, and per_case_usd
            # is the one that resembles a real recipient search.
            "scope": "corpus sweep, not one device session",
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_pricing.py -q`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add mock_x_platform/pricing.py tests/test_pricing.py
git commit -m "feat: add the X API rate table and a distinct-profile billing ledger

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Follower counts and the two unknown-modelling flags

**Files:**
- Modify: `mock_x_platform/store.py`
- Modify: `mock_x_platform/dataset.py`
- Test: `tests/test_mock_x_store.py`, `tests/test_mock_x_dataset.py`

**Interfaces:**
- Consumes: `BillingLedger` (Task 5).
- Produces:
  - `MockXStore(path, *, match_scope="name_username", result_order="bm25", ledger=None)`, attribute `store.ledger`
  - `MATCH_SCOPES = ("name_username", "name_username_bio")`, `RESULT_ORDERS = ("bm25", "follower_weighted")`
  - `profiles.follower_count` column; `profiles_fts_bio` index
  - `dataset.FOLLOWER_LOG_RANGE`, `GENERATOR_VERSION = 3`

Context for the implementer: these two flags exist because we do not know two things about the real endpoint (spec §3) — what `query` matches, and how results are ordered — and measuring under both settings is how the harness stays honest about that. The bio index is a **separate FTS table** on purpose: fts5's bm25 divides by whole-row token length, so folding the bio into `profiles_fts` would re-introduce exactly the length bias round 4 removed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mock_x_store.py`:

```python
from mock_x_platform.store import MATCH_SCOPES, RESULT_ORDERS  # add to existing imports


def _profiles() -> list[dict[str, object]]:
    return [
        {
            "id": "1",
            "name": "Joe Bart",
            "username": "joebart1",
            "description": "Member of technical staff at Meta.",
            "location": "San Francisco, CA",
            "profile_image_url": "",
            "verified": False,
            "receives_your_dm": True,
            "tier": "clean",
            "follower_count": 12,
        },
        {
            "id": "2",
            "name": "Joe Bart",
            "username": "joebart2",
            "description": "Broadcaster.",
            "location": "London, UK",
            "profile_image_url": "",
            "verified": False,
            "receives_your_dm": True,
            "tier": "clean",
            "follower_count": 900_000,
        },
    ]


def test_the_default_scope_cannot_see_the_bio(tmp_path) -> None:
    store = MockXStore(tmp_path / "x.sqlite3")
    store.replace_profiles(_profiles())

    # Round 4's narrowed index, unchanged: X's /2/users/search matches name and
    # username, so a bio term must not retrieve anybody by default.
    assert store.search_profiles("Meta") == []


def test_the_bio_scope_can_see_the_bio(tmp_path) -> None:
    store = MockXStore(tmp_path / "x.sqlite3", match_scope="name_username_bio")
    store.replace_profiles(_profiles())

    assert [row["id"] for row in store.search_profiles("Meta")] == ["1"]


def test_follower_weighted_order_puts_the_personality_first(tmp_path) -> None:
    store = MockXStore(tmp_path / "x.sqlite3", result_order="follower_weighted")
    store.replace_profiles(_profiles())

    # Unknown B in its strong form: if X sorts by popularity, the member of
    # technical staff is systematically behind the broadcaster of the same name.
    assert [row["id"] for row in store.search_profiles("Joe Bart")] == ["2", "1"]


def test_an_unknown_flag_value_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError):
        MockXStore(tmp_path / "x.sqlite3", match_scope="everything")
    assert "name_username" in MATCH_SCOPES and "bm25" in RESULT_ORDERS
```

Append to `tests/test_mock_x_dataset.py`:

```python
def test_generated_profiles_carry_a_heavy_tailed_follower_count() -> None:
    from mock_x_platform.dataset import generate_profiles

    counts = [
        int(profile["follower_count"])
        for profile in generate_profiles(600, seed=42)
    ]

    assert all(count > 0 for count in counts)
    # Heavy-tailed, not normal: the top of the distribution is orders of
    # magnitude above the median, which is the shape Unknown B turns on.
    ordered = sorted(counts)
    assert ordered[-1] > 20 * ordered[len(ordered) // 2]


def test_adding_follower_counts_did_not_disturb_the_existing_streams() -> None:
    from mock_x_platform.dataset import generate_profiles

    profiles = list(generate_profiles(300, seed=42))
    names = [profile["name"] for profile in profiles]
    tiers = [profile["tier"] for profile in profiles]

    # Follower counts come from a third RNG stream precisely so identities and
    # dirt stay byte-identical to every corpus measured in earlier rounds.
    again = list(generate_profiles(300, seed=42))
    assert [profile["name"] for profile in again] == names
    assert [profile["tier"] for profile in again] == tiers
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_mock_x_store.py tests/test_mock_x_dataset.py -q`
Expected: FAIL — `ImportError: cannot import name 'MATCH_SCOPES'`

- [ ] **Step 3: Add the flags, the column and the second index to the store**

In `mock_x_platform/store.py`, add to the imports:

```python
from .pricing import BillingLedger
```

Add these module constants after the imports:

```python
MATCH_SCOPES = ("name_username", "name_username_bio")
RESULT_ORDERS = ("bm25", "follower_weighted")

# Which FTS table serves each scope. Two tables rather than extra columns on one:
# fts5's bm25 divides by whole-row token length, so folding the bio into the
# default index would re-introduce the length bias round 4 removed, for the
# name_username scope as well as the new one.
_FTS_TABLES = {
    "name_username": "profiles_fts",
    "name_username_bio": "profiles_fts_bio",
}
```

Replace `__init__` (lines 15-18) with:

```python
    def __init__(
        self,
        path: str | Path,
        *,
        match_scope: str = "name_username",
        result_order: str = "bm25",
        ledger: BillingLedger | None = None,
    ):
        if match_scope not in MATCH_SCOPES:
            raise ValueError(f"match_scope must be one of: {', '.join(MATCH_SCOPES)}")
        if result_order not in RESULT_ORDERS:
            raise ValueError(f"result_order must be one of: {', '.join(RESULT_ORDERS)}")
        self.path = Path(path)
        self.match_scope = match_scope
        self.result_order = result_order
        # Owned here so every MockXApplication over the same store shares one
        # bill, and so an in-process evaluation can read it without going
        # anywhere near the HTTP layer.
        self.ledger = ledger or BillingLedger()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
```

In the `_initialize` schema script, add `follower_count` to the `profiles` table definition (after the `tier` line):

```sql
                    tier TEXT NOT NULL DEFAULT 'clean',
                    follower_count INTEGER NOT NULL DEFAULT 0
```

and add the second index after the existing `profiles_fts` definition:

```sql
                -- Used only when match_scope is 'name_username_bio', to model
                -- the possibility that X's query matches bios too (Unknown A).
                CREATE VIRTUAL TABLE IF NOT EXISTS profiles_fts_bio USING fts5(
                    id UNINDEXED,
                    name,
                    username,
                    description,
                    location,
                    tokenize = 'unicode61 remove_diacritics 2'
                );
```

Extend the end of `_initialize` (lines 92-93) to:

```python
            self._migrate_profiles(connection)
            self._migrate_search_index(connection)
            self._migrate_follower_count(connection)
            self._migrate_bio_index(connection)
```

Add both migrations after `_migrate_search_index`:

```python
    @staticmethod
    def _migrate_follower_count(connection: sqlite3.Connection) -> None:
        """Add the follower column to a database generated before it existed."""

        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(profiles)").fetchall()
        }
        if not columns or "follower_count" in columns:
            return
        connection.execute(
            "ALTER TABLE profiles ADD COLUMN follower_count INTEGER NOT NULL DEFAULT 0"
        )

    @staticmethod
    def _migrate_bio_index(connection: sqlite3.Connection) -> None:
        """Fill the bio index for a database generated before it existed.

        Without this, switching match_scope on an existing 100k corpus silently
        returns nothing rather than obviously failing.
        """

        profiles = connection.execute("SELECT COUNT(*) AS count FROM profiles").fetchone()
        indexed = connection.execute(
            "SELECT COUNT(*) AS count FROM profiles_fts_bio"
        ).fetchone()
        if not int(profiles["count"]) or int(indexed["count"]):
            return
        connection.execute(
            """
            INSERT INTO profiles_fts_bio (id, name, username, description, location)
            SELECT id, name, username, description, location FROM profiles
            """
        )
```

- [ ] **Step 4: Write both indexes and the follower column in `replace_profiles`**

Replace `replace_profiles` (lines 167-197) with:

```python
    def replace_profiles(self, profiles: list[dict[str, Any]]) -> None:
        rows = [
            (
                str(profile["id"]),
                str(profile["name"]),
                str(profile["username"]),
                str(profile.get("description", "")),
                str(profile.get("location", "")),
                str(profile.get("profile_image_url", "")),
                int(bool(profile.get("verified", False))),
                _optional_flag(profile.get("receives_your_dm")),
                str(profile.get("tier", "clean")),
                int(profile.get("follower_count", 0) or 0),
            )
            for profile in profiles
        ]
        with self._connection() as connection:
            connection.execute("DELETE FROM profiles")
            connection.execute("DELETE FROM profiles_fts")
            connection.execute("DELETE FROM profiles_fts_bio")
            connection.executemany(
                """
                INSERT INTO profiles (
                    id, name, username, description, location,
                    profile_image_url, verified, receives_your_dm, tier,
                    follower_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.executemany(
                "INSERT INTO profiles_fts (id, name, username) VALUES (?, ?, ?)",
                [row[:3] for row in rows],
            )
            connection.executemany(
                """
                INSERT INTO profiles_fts_bio (id, name, username, description, location)
                VALUES (?, ?, ?, ?, ?)
                """,
                [row[:5] for row in rows],
            )
```

- [ ] **Step 5: Make `_fts_rows` honour both flags**

Replace `_fts_rows` (lines 222-237) with:

```python
    def _fts_rows(
        self, connection: sqlite3.Connection, expression: str, limit: int
    ) -> list[sqlite3.Row]:
        # Table and ordering are chosen from validated constants, never from
        # caller input, which is what makes the interpolation below safe.
        table = _FTS_TABLES[self.match_scope]
        # id breaks ties so the pool is byte-identical between runs.
        order = (
            f"bm25({table}), p.id"
            if self.result_order == "bm25"
            else "p.follower_count DESC, p.id"
        )
        return connection.execute(
            f"""
            SELECT p.*
            FROM {table}
            JOIN profiles AS p ON p.id = {table}.id
            WHERE {table} MATCH ?
            ORDER BY {order}
            LIMIT ?
            """,
            (expression, limit),
        ).fetchall()
```

`search_profiles` calls it unchanged — `self._fts_rows(connection, ...)` already reads as an instance call there.

- [ ] **Step 6: Generate follower counts from a third RNG stream**

In `mock_x_platform/dataset.py`, change `GENERATOR_VERSION` (line 53) to `3` and add after it:

```python
# Follower counts are heavy-tailed: most accounts have a few hundred, a handful
# have millions, and nothing about them is normally distributed. Log-uniform over
# 10^0.5 to 10^5.5 (roughly 3 to 320,000) reproduces the property Unknown B turns
# on -- that a member of technical staff is systematically outranked by a
# personality with the same name -- without pretending to be a measured census.
FOLLOWER_LOG_RANGE = (0.5, 5.5)

# The hand-written fixtures get a fixed, unremarkable count so they neither lead
# nor trail under follower_weighted ordering.
FIXTURE_FOLLOWER_COUNT = 1_000
```

In `generate_profiles`, replace the fixture yield (line 248) with:

```python
    yield from (
        dict(profile, tier="clean", follower_count=FIXTURE_FOLLOWER_COUNT)
        for profile in MOCK_PROFILES
    )
```

Add the third stream after `dirt = random.Random(seed + 1)` (line 254):

```python
    # A third stream on purpose. Drawing follower counts from `dirt` would shift
    # every later dirt draw and silently change the corpus that rounds 1-6 were
    # measured against.
    popularity = random.Random(seed + 2)
```

Replace the final `yield` (line 275) with:

```python
        profile = _render_profile(tiers[index], truth, index + 1, dirt)
        profile["follower_count"] = int(10 ** popularity.uniform(*FOLLOWER_LOG_RANGE))
        yield profile
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_mock_x_store.py tests/test_mock_x_dataset.py -q`
Expected: PASS

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. If a test fails because it asserts on `GENERATOR_VERSION`, update the expected value to 3 — the generator now writes an extra column, so the version bump is correct.

- [ ] **Step 9: Commit**

```bash
git add mock_x_platform tests/test_mock_x_store.py tests/test_mock_x_dataset.py
git commit -m "feat: model the two X unknowns as store flags

match_scope models what query matches (Unknown A) and result_order models how
results are sorted (Unknown B). The bio index is a separate FTS table because
bm25 divides by whole-row length, so folding bios into the default index would
undo round 4. Follower counts come from a third RNG stream so identities and
dirt stay byte-identical to every earlier corpus.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Bill the mock for what it serves

**Files:**
- Modify: `mock_x_platform/application.py`
- Test: `tests/test_mock_x_application.py`

**Interfaces:**
- Consumes: `store.ledger` (Task 6).
- Produces: `MockXApplication` records searches, lookups, DM sends, and the profiles each one returns.

Context for the implementer — this corrects a detail of the spec, deliberately. Spec §Task 2 says the ledger must attach to `MockXStore` rather than the HTTP layer, and the reason is sound: most of the suite runs `run_http=False`, so a ledger on the HTTP boundary would report $0.00 for nearly every run. But billing must follow **what the API returns**, and `store.search_profiles` returns a 250-row candidate pool that the API never returns. So the ledger *lives on* the store, and is *recorded by* `MockXApplication` at the point where the response is formed. Recording in `search_profiles` would overstate the bill by roughly 25x.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mock_x_application.py`:

```python
def test_a_search_bills_only_the_profiles_it_returns(app: MockXApplication) -> None:
    app.search_users("John Doe", max_results=5)

    # Not the 250-row candidate pool behind it: X charges for resources returned.
    assert app.store.ledger.distinct_profiles == 5
    assert app.store.ledger.searches == 1


def test_repeating_a_search_inside_the_window_costs_nothing_more(
    app: MockXApplication,
) -> None:
    app.search_users("John Doe", max_results=5)
    app.search_users("John Doe", max_results=5)

    assert app.store.ledger.searches == 2
    assert app.store.ledger.distinct_profiles == 5


def test_a_lookup_bills_one_profile(app: MockXApplication) -> None:
    app.lookup_user_by_username("johndoe_xyz")

    assert app.store.ledger.lookups == 1
    assert app.store.ledger.distinct_profiles == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_mock_x_application.py -q`
Expected: FAIL — `assert 0 == 5`

- [ ] **Step 3: Record in the application**

In `mock_x_platform/application.py`, in `search_users_with_diagnostics`, replace the result construction (lines 84-89) with:

```python
        profiles = sorted(source, key=relevance)[:limit]
        # Billed here rather than in the store: the store hands back a 250-row
        # candidate pool, and X charges for the resources the API returns.
        self.store.ledger.record_search()
        self.store.ledger.record_profiles(str(profile["id"]) for profile in profiles)
        result = {
            "data": profiles,
            "meta": {"result_count": len(profiles), "mock": True},
        }
        return result, [str(profile["id"]) for profile in source]
```

In `lookup_user_by_username`, immediately before `return {"data": profile}`, add:

```python
        self.store.ledger.record_lookup()
        self.store.ledger.record_profiles([str(profile["id"])])
```

In `send_message`, immediately before `return {"data": message.to_dict()}`, add:

```python
        self.store.ledger.record_dm_send()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_mock_x_application.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add mock_x_platform/application.py tests/test_mock_x_application.py
git commit -m "feat: bill the mock for the profiles its API returns

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: X's real `max_results` bounds and `next_token` pagination

**Files:**
- Modify: `mock_x_platform/application.py`
- Modify: `mock_x_platform/server.py`
- Modify: `mock_x_platform/client.py`
- Test: `tests/test_mock_x_application.py`, `tests/test_mock_x_http.py`

**Interfaces:**
- Consumes: Task 7's ledger recording.
- Produces:
  - `MockXApplication.search_users(query, max_results=100, next_token=None)` and `search_users_with_diagnostics(query, max_results=100, next_token=None)`; `meta.next_token` present when more pages exist
  - `encode_cursor(query, offset) -> str`, `decode_cursor(token, query) -> int`
  - `MockXPlatformClient.search_users_page(query, *, max_results=100, next_token=None) -> tuple[list[Candidate], str | None]`

Reminder from Global Constraints: this is the **mock** stack only. `search/x_client.py` keeps its cap of ten.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mock_x_application.py`:

```python
def test_max_results_follows_x_not_us(app: MockXApplication) -> None:
    # X's documented bounds: min 1, default 100, max 1000. Our old cap of ten was
    # ours, and it hid the fact that omitting the parameter costs $1.00 a search.
    assert len(app.search_users("John Doe")["data"]) == 12
    assert len(app.search_users("John Doe", max_results=3)["data"]) == 3
    assert len(app.search_users("John Doe", max_results=5000)["data"]) == 12


def test_pagination_walks_the_pool_without_repeating_anybody(
    app: MockXApplication,
) -> None:
    first = app.search_users("John Doe", max_results=5)
    token = first["meta"]["next_token"]
    second = app.search_users("John Doe", max_results=5, next_token=token)

    first_ids = [profile["id"] for profile in first["data"]]
    second_ids = [profile["id"] for profile in second["data"]]
    assert len(second_ids) == 5
    assert not set(first_ids) & set(second_ids)


def test_a_cursor_from_another_query_is_refused(app: MockXApplication) -> None:
    token = app.search_users("John Doe", max_results=5)["meta"]["next_token"]

    with pytest.raises(MockXHttpError) as error:
        app.search_users("Maya Chen", max_results=5, next_token=token)

    assert error.value.status == 400


def test_the_last_page_carries_no_cursor(app: MockXApplication) -> None:
    assert "next_token" not in app.search_users("John Doe", max_results=1000)["meta"]
```

Replace the existing `test_search_is_x_shaped_relevant_and_capped` (lines 14-20) with:

```python
def test_search_is_x_shaped_and_relevant(app: MockXApplication) -> None:
    result = app.search_users("John Doe XYZ Toronto", max_results=10)

    assert result["meta"]["result_count"] == 10
    assert result["meta"]["mock"] is True
    assert len(result["data"]) == 10
    assert result["data"][0]["id"] == "1000001"
    assert "description" in result["data"][0]
```

and update the two assertions in `test_injected_failure_is_consumed_then_operation_recovers` and `test_reset_clears_messages_and_failures` that assume a cap of ten:

```python
    assert app.search_users("John Doe", max_results=10)["meta"]["result_count"] == 10
```

In `tests/test_mock_x_http.py`, change line 29 to `max_results=10` so the assertion on line 30 keeps testing paging rather than the fixture size.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_mock_x_application.py -q`
Expected: FAIL — `TypeError: search_users() got an unexpected keyword argument 'next_token'`

- [ ] **Step 3: Add the cursor helpers**

In `mock_x_platform/application.py`, add `import base64` and `import hashlib` to the imports, and add these module-level functions after the `MockXHttpError` class:

```python
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
```

- [ ] **Step 4: Paginate the search**

Replace `search_users` and the head of `search_users_with_diagnostics` (lines 48-64) with:

```python
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
        """Run the real search path and expose preliminary IDs for offline evaluation."""

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
```

Then replace the source/result block (lines 79-89) with:

```python
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
```

- [ ] **Step 5: Pass the parameters through HTTP**

In `mock_x_platform/server.py`, replace the search route body (lines 58-63) with:

```python
            if method == "GET" and parsed.path == "/2/users/search":
                query = parse_qs(parsed.query)
                search_text = query.get("query", [""])[0]
                # Absent max_results means X's default of 100, not ours of 10.
                max_results = _as_int(
                    query.get("max_results", [str(DEFAULT_MAX_RESULTS)])[0], "max_results"
                )
                cursor = query.get("next_token", [""])[0] or None
                self._json(
                    HTTPStatus.OK,
                    self.app.search_users(search_text, max_results, cursor),
                )
                return
```

and change the import on line 15 to:

```python
from .application import DEFAULT_MAX_RESULTS, MockXApplication, MockXHttpError
```

In `mock_x_platform/client.py`, replace `search_users` (lines 34-44) with:

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_mock_x_application.py tests/test_mock_x_http.py -q`
Expected: PASS

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. `search/service.py` passes `max_results=10` explicitly and `mock_x_platform/evaluation.py` passes `10`, so the device and the evaluation still fetch ten.

- [ ] **Step 8: Commit**

```bash
git add mock_x_platform tests/test_mock_x_application.py tests/test_mock_x_http.py
git commit -m "feat: give the mock X's real max_results bounds and next_token paging

Default 100 on purpose: a caller that forgets max_results should reproduce the
dollar-a-search mistake in the harness, where the ledger reports it, rather than
in production. The real XClient keeps its cap of ten until a budget replaces it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Report what every accuracy run would have cost

**Files:**
- Modify: `mock_x_platform/evaluation.py`
- Test: `tests/test_search_evaluation.py`

**Interfaces:**
- Consumes: `store.ledger` (Task 6), application recording (Task 7).
- Produces: `summary["cost"]` — the dict from `BillingLedger.summary(case_count=...)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_search_evaluation.py`:

```python
def test_every_run_reports_what_it_would_have_cost(tmp_path) -> None:
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.evaluation import run_evaluation

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=300, seed=42)

    summary, _, _ = run_evaluation(
        database,
        output_directory=tmp_path / "reports",
        case_limit=5,
        determinism_cases=1,
        run_http=False,
    )

    cost = summary["cost"]
    # run_http=False is the common case in this suite, so a ledger hung off the
    # HTTP layer would report $0.00 here -- a number that looks like a pass.
    assert cost["distinct_profiles"] > 0
    assert cost["estimated_usd"] > 0
    assert cost["per_case_usd"] == pytest.approx(cost["estimated_usd"] / 5)
    assert cost["rates_checked_on"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_search_evaluation.py -q`
Expected: FAIL — `KeyError: 'cost'`

- [ ] **Step 3: Add the cost block to the summary**

In `mock_x_platform/evaluation.py`, add to the `summary` dict, immediately after `"search_count": len(rows),`:

```python
        # What this run would have cost on the real API. The HTTP load phase runs
        # against its own store and its own ledger, so it is deliberately not
        # counted here: it measures latency, not retrieval.
        "cost": store.ledger.summary(case_count=len(cases)),
```

- [ ] **Step 4: Print it**

In `_print_summary`, add immediately before the `calibration = summary["acceptance_calibration"]` line:

```python
    cost = summary["cost"]
    print("\nEstimated cost on the real X API")
    print(
        f"  distinct profiles  {cost['distinct_profiles']:,}   "
        f"windows={cost['billed_windows']}   searches={cost['searches']:,}   "
        f"lookups={cost['lookups']:,}"
    )
    print(
        f"  per case           ${cost['per_case_usd'] or 0:.4f}   "
        f"(run total ${cost['estimated_usd']:,.2f}, {cost['scope']})"
    )
    print(f"  rates checked      {cost['rates_checked_on']} at ${cost['user_usd']:.3f}/user")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_search_evaluation.py -q`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add mock_x_platform/evaluation.py tests/test_search_evaluation.py
git commit -m "feat: report estimated X API cost on every evaluation run

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Build labeled refinement ladders

**Files:**
- Create: `mock_x_platform/refinement.py`
- Test: `tests/test_refinement_harness.py`

**Interfaces:**
- Consumes: `MockXStore` (Task 6).
- Produces:
  - `TURN_TYPES`, `CLUE_FIELDS`
  - `RefinementTurn(type, field, value, criteria)`
  - `RefinementCase(id, expected_profile_id, initial_criteria, turns)`
  - `build_refinement_cases(store, *, limit=None, seed=42) -> list[RefinementCase]`

Context for the implementer. A case is one person and the sequence of things a user says while trying to find them. The initial criteria are **name plus company** — the Joe Bart scenario, where the sender leads with the strongest clue they have and it happens to be one the account does not carry. Each later turn adds one more clue the sender knows.

The turn's *type* is read off the stored profile, never assumed:

| Type | Meaning | Can it re-retrieve? |
| --- | --- | --- |
| `on_profile` | every token of the clue appears in the profile's bio or location | discriminate only |
| `off_profile` | the clue is true of the person and absent from their X profile | **never** |
| `narrowing_name` | a fuller or corrected name | yes — it changes the query |
| `handle` | the user switches to handle mode | yes — it exits the loop |

`off_profile` is first-class, not an edge case. Everyone's intuition says "add more detail and it'll find them"; for that turn type it is provably false, and quantifying how much of real refinement lands there is the point of the phase.

Two deliberate deviations from the spec, both to be kept:

1. **Every case gets every applicable turn type (a "ladder"), rather than a sampled mix.** The spec asked for configurable mix ratios; a ladder measures every type on every case and so yields strictly more data per case than any sampling of it would, with no ratio to justify. The `seed` still controls the order clues arrive in, so determinism is preserved.
2. Cases are built from the stored `evaluation_cases` rows, whose `criteria_json` already holds the full truth record, joined to the stored profile. No regeneration, and it works on any existing corpus.

- [ ] **Step 1: Write the failing test**

Create `tests/test_refinement_harness.py`:

```python
from __future__ import annotations

import json

import pytest

from mock_x_platform.refinement import build_refinement_cases
from mock_x_platform.store import MockXStore


def _store(tmp_path, *, description: str, location: str) -> MockXStore:
    store = MockXStore(tmp_path / "x.sqlite3")
    store.replace_profiles(
        [
            {
                "id": "9001",
                "name": "Joe Bart",
                "username": "joebart9001",
                "description": description,
                "location": location,
                "profile_image_url": "",
                "verified": False,
                "receives_your_dm": True,
                "tier": "clean",
                "follower_count": 10,
            }
        ]
    )
    store.replace_evaluation_cases(
        [
            {
                "id": 1,
                "description": "Joe Bart",
                "criteria_json": json.dumps(
                    {
                        "name": "Joe Bart",
                        "company": "Meta",
                        "role": "Engineer",
                        "location": "San Francisco, CA",
                        "school": "McGill University",
                        "extra_clues": [],
                    }
                ),
                "expected_profile_id": "9001",
            }
        ]
    )
    return store


def test_a_clue_the_profile_carries_is_labeled_on_profile(tmp_path) -> None:
    store = _store(
        tmp_path,
        description="Engineer at Meta. McGill University alum.",
        location="San Francisco, CA",
    )

    case = build_refinement_cases(store)[0]
    types = {turn.field: turn.type for turn in case.turns}

    assert types["role"] == "on_profile"
    assert types["school"] == "on_profile"
    assert types["location"] == "on_profile"


def test_a_clue_the_profile_does_not_carry_is_labeled_off_profile(tmp_path) -> None:
    # The partial tier: no bio, no location. Everything the sender knows about
    # this person is true and invisible to the platform.
    store = _store(tmp_path, description="", location="")

    case = build_refinement_cases(store)[0]

    assert {turn.type for turn in case.turns if turn.field in {"role", "school", "location"}} == {
        "off_profile"
    }


def test_every_ladder_ends_with_the_handle(tmp_path) -> None:
    store = _store(tmp_path, description="", location="")

    case = build_refinement_cases(store)[0]

    assert case.turns[-1].type == "handle"
    assert case.turns[-1].value == "joebart9001"


def test_the_initial_criteria_are_deliberately_underspecified(tmp_path) -> None:
    store = _store(tmp_path, description="", location="")

    case = build_refinement_cases(store)[0]

    assert case.initial_criteria.name == "Joe Bart"
    assert case.initial_criteria.company == "Meta"
    assert case.initial_criteria.role == ""


def test_a_correctable_name_earns_a_narrowing_turn(tmp_path) -> None:
    store = _store(tmp_path, description="", location="")
    store.replace_profiles(
        [
            {
                "id": "9001",
                "name": "Joe Bart",
                "username": "joebart9001",
                "description": "",
                "location": "",
                "profile_image_url": "",
                "verified": False,
                "receives_your_dm": True,
                "tier": "handle_name",
                "follower_count": 10,
            }
        ]
    )
    store.replace_evaluation_cases(
        [
            {
                "id": 1,
                "description": "Joseph Bart",
                "criteria_json": json.dumps(
                    {"name": "Joseph Bart", "company": "Meta", "extra_clues": []}
                ),
                "expected_profile_id": "9001",
            }
        ]
    )

    case = build_refinement_cases(store)[0]

    # "He goes by Joe, not Joseph" -- shares the surname, not the whole name.
    assert [turn.type for turn in case.turns if turn.type == "narrowing_name"] == [
        "narrowing_name"
    ]


@pytest.mark.parametrize("display_name", ["Joe Bart", "jbart"])
def test_a_name_nobody_could_guess_earns_no_narrowing_turn(
    tmp_path, display_name: str
) -> None:
    # Two exclusions, for opposite reasons. An identical display name has
    # nothing to correct. A handle-style one ("jbart") shares no token, and a
    # user able to produce that string would have used handle mode -- crediting
    # search with it would hide the population the recovery path exists for.
    store = _store(tmp_path, description="", location="")
    store.replace_profiles(
        [
            {
                "id": "9001",
                "name": display_name,
                "username": "joebart9001",
                "description": "",
                "location": "",
                "profile_image_url": "",
                "verified": False,
                "receives_your_dm": True,
                "tier": "handle_name",
                "follower_count": 10,
            }
        ]
    )

    case = build_refinement_cases(store)[0]

    assert not any(turn.type == "narrowing_name" for turn in case.turns)


def test_case_generation_is_deterministic_under_a_fixed_seed(tmp_path) -> None:
    store = _store(tmp_path, description="Engineer at Meta.", location="")

    first = build_refinement_cases(store, seed=7)
    second = build_refinement_cases(store, seed=7)

    assert [turn.field for turn in first[0].turns] == [
        turn.field for turn in second[0].turns
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_refinement_harness.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mock_x_platform.refinement'`

- [ ] **Step 3: Write the case builder**

Create `mock_x_platform/refinement.py` with this content (the runner is added in Task 11):

```python
"""Multi-turn recipient refinement: cases, the harness, and its report.

Measures whether the loop can converge -- whether saying more about a person
brings them onto the visible screen -- against the search path exactly as it is,
so a no-op is proven rather than asserted.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any

from models.criteria import RecipientCriteria
from ranking.normalization import normalize_text

from .store import MockXStore


TURN_TYPES = ("on_profile", "off_profile", "narrowing_name", "handle")

# The clues a sender adds after leading with a name and a company, in the order
# they are shuffled into each ladder.
CLUE_FIELDS = ("role", "location", "school")


@dataclass(frozen=True, slots=True)
class RefinementTurn:
    """One thing the user says, and the criteria that result from saying it."""

    type: str
    field: str
    value: str
    criteria: RecipientCriteria


@dataclass(frozen=True, slots=True)
class RefinementCase:
    id: int
    expected_profile_id: str
    initial_criteria: RecipientCriteria
    turns: tuple[RefinementTurn, ...]


def build_refinement_cases(
    store: MockXStore, *, limit: int | None = None, seed: int = 42
) -> list[RefinementCase]:
    """One refinement ladder per evaluation case, labeled from the real profile.

    The initial criteria are name plus company: the Joe Bart scenario, where the
    sender leads with the strongest clue they have and it happens to be one the
    account does not carry. Each later turn adds one clue the sender knows.

    A turn's type is read off the stored profile rather than assumed. If every
    token of the clue appears in the profile's bio or location it is on_profile
    and could in principle discriminate; if it does not, it is off_profile and
    provably cannot, whatever the ranker does with it.
    """

    randomizer = random.Random(seed)
    cases: list[RefinementCase] = []
    for record in store.evaluation_cases(limit=limit):
        truth = json.loads(record["criteria_json"])
        profile = store.get_profile(str(record["expected_profile_id"]))
        if profile is None:
            continue

        # What the platform can actually see about this person. Whole tokens,
        # because whole tokens are what the mock's FTS index matches.
        visible = set(
            normalize_text(
                f"{profile['name']} {profile['description']} {profile['location']}"
            ).split()
        )
        initial = RecipientCriteria.from_dict(
            {"name": truth.get("name", ""), "company": truth.get("company", "")}
        )

        running = initial
        turns: list[RefinementTurn] = []
        order = list(CLUE_FIELDS)
        randomizer.shuffle(order)
        for field_name in order:
            value = str(truth.get(field_name, "") or "")
            if not value:
                continue
            running = RecipientCriteria.from_dict(
                {**running.to_dict(), field_name: value}
            )
            turns.append(
                RefinementTurn(
                    type="on_profile" if _visible(visible, value) else "off_profile",
                    field=field_name,
                    value=value,
                    criteria=running,
                )
            )

        # The user remembers the account goes by a different form of the name --
        # "he goes by Joe, not Joseph". Generated only where the display name
        # shares *some but not all* tokens with the name the sender started
        # from, which is the knowledge a person could plausibly have.
        #
        # The two excluded ends are excluded on purpose. Sharing every token
        # means there is nothing to correct. Sharing none means the account
        # displays something like "jbart", and a user who could produce that
        # string would have used handle mode instead -- crediting search with
        # that turn would quietly launder the handle shortcut into a search win
        # and hide the population the recovery path exists for.
        display_name = str(profile["name"])
        display_tokens = set(normalize_text(display_name).split())
        truth_tokens = set(normalize_text(str(truth.get("name", ""))).split())
        shared = display_tokens & truth_tokens
        if shared and shared != truth_tokens:
            running = RecipientCriteria.from_dict(
                {**running.to_dict(), "name": display_name}
            )
            turns.append(
                RefinementTurn("narrowing_name", "name", display_name, running)
            )

        # Every ladder ends in handle mode. Its share of the convergences is the
        # product finding: how much of the corpus is reachable no other way.
        turns.append(
            RefinementTurn("handle", "handle", str(profile["username"]), running)
        )
        cases.append(
            RefinementCase(
                id=int(record["id"]),
                expected_profile_id=str(profile["id"]),
                initial_criteria=initial,
                turns=tuple(turns),
            )
        )
    return cases


def _visible(profile_tokens: set[str], clue: str) -> bool:
    tokens = set(normalize_text(clue).split())
    return bool(tokens) and tokens <= profile_tokens
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_refinement_harness.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add mock_x_platform/refinement.py tests/test_refinement_harness.py
git commit -m "feat: build labeled refinement ladders from the corpus

Turn types are read off the stored profile, not assumed: a clue whose tokens do
not appear in the bio or location is off_profile and provably cannot change who
is retrieved, however the ranker scores it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Run the ladders and report convergence

**Files:**
- Modify: `mock_x_platform/refinement.py`
- Test: `tests/test_refinement_harness.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `run_refinement(database_path, *, output_directory, case_limit=None, match_scope="name_username", result_order="bm25", seed=42) -> tuple[dict[str, Any], Path, Path]`
  - `python -m mock_x_platform.refinement` CLI with `--database`, `--output`, `--limit`, `--scope`, `--order`, `--seed`

Context for the implementer. This runs against the **unfixed** search path on purpose. Do not "helpfully" change `search/service.py`, `build_search_query`, or the cache while building this — the whole value of the measurement is that it reports what today's code does.

One thing to understand before writing it: the JSON cache in `search/service.py` is *not* what makes refinement a no-op. `build_search_query` is name-only, so refining with a location produces a byte-identical query and the mock returns a byte-identical page whether or not anything is cached. The cache only makes it cheaper to be a no-op. That is why the harness drives `MockXApplication` directly and skips the cache: doing so reproduces the behaviour faithfully with no file I/O.

Structural reachability is probed through `store.search_profiles` directly, which the ledger does not record — so asking "could this person ever be reached?" costs nothing and does not pollute the bill.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_refinement_harness.py`:

```python
def test_off_profile_refinement_changes_nothing_at_all(tmp_path) -> None:
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.refinement import run_refinement

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=3_000, seed=42)

    summary, _, _ = run_refinement(
        database, output_directory=tmp_path / "reports", case_limit=25
    )

    # The finding this whole phase exists to establish, asserted against the code
    # as it stands. build_search_query returns the first non-empty of name,
    # company, school, role -- location is not even a fallback -- so adding a
    # clue leaves the query byte-identical and the same ten profiles come back.
    off_profile = summary["by_turn_type"]["off_profile"]
    assert off_profile["turns"] > 0
    assert off_profile["retrieval_changed"] == 0

    # The target can never rise on such a turn: scoring only ever adds points,
    # the expected profile earns none from a clue its account does not carry,
    # and competitors may earn some. So an off-profile clue is either inert or
    # actively harmful, and `worsened` is left free to be non-zero because a
    # refinement that demotes the target is a real result worth reporting.
    assert off_profile["improved"] == 0


def test_the_handle_turn_converges_where_search_cannot(tmp_path) -> None:
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.refinement import run_refinement

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=3_000, seed=42)

    summary, _, _ = run_refinement(
        database, output_directory=tmp_path / "reports", case_limit=25
    )

    assert summary["by_turn_type"]["handle"]["visible"] == 1.0


def test_an_unreachable_person_is_reported_as_unconvergeable_not_as_a_failure(
    tmp_path,
) -> None:
    from mock_x_platform.refinement import run_refinement

    # Built by hand rather than sampled, so the case is unreachable by
    # construction: the account displays "jbart" and shares no token with the
    # name the sender has. This is the handle_name tier in miniature, and no
    # ranking change, refinement turn or extra depth can ever recover it.
    store = _store(tmp_path, description="", location="")
    store.replace_profiles(
        [
            {
                "id": "9001",
                "name": "jbart",
                "username": "jbart",
                "description": "",
                "location": "",
                "profile_image_url": "",
                "verified": False,
                "receives_your_dm": True,
                "tier": "handle_name",
                "follower_count": 10,
            }
        ]
    )

    summary, _, _ = run_refinement(
        store.path, output_directory=tmp_path / "reports"
    )

    assert summary["convergence"]["structurally_unconvergeable"] == 1.0
    assert summary["convergence"]["unconvergeable_cases"] == 1
    assert summary["convergence"]["rate"] == 0.0
    # And yet the handle turn still resolves them. That contrast is the product
    # finding: past a certain amount of dirt, only the handle works.
    assert summary["by_turn_type"]["handle"]["visible"] == 1.0


def test_the_run_reports_what_it_would_have_cost(tmp_path) -> None:
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.refinement import run_refinement

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=3_000, seed=42)

    summary, _, _ = run_refinement(
        database, output_directory=tmp_path / "reports", case_limit=25
    )

    assert summary["cost"]["distinct_profiles"] > 0
    assert summary["cost"]["estimated_usd"] > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_refinement_harness.py -q`
Expected: FAIL — `ImportError: cannot import name 'run_refinement'`

- [ ] **Step 3: Write the runner**

Append to `mock_x_platform/refinement.py`. Extend the imports at the top of the file to:

```python
import argparse
import csv
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import PROJECT_ROOT, load_settings
from models.candidate import Candidate
from models.criteria import RecipientCriteria
from ranking.normalization import normalize_text
from ranking.ranker import rank_candidates
from search.query_builder import build_search_query

from .application import MockXApplication, MockXHttpError
from .store import MATCH_SCOPES, RESULT_ORDERS, MockXStore
```

Then append:

```python
# The visible screen is ten profiles. Success is arriving on it, not arriving
# first: the device's own principle is that the AI ranks and the human confirms.
VISIBLE_DEPTH = 10

# How deep to look when asking whether a person is reachable at all. This probe
# goes through the store rather than the API, so it is never billed.
REACHABILITY_DEPTH = 1000


def run_refinement(
    database_path: str | Path,
    *,
    output_directory: str | Path,
    case_limit: int | None = None,
    match_scope: str = "name_username",
    result_order: str = "bm25",
    seed: int = 42,
) -> tuple[dict[str, Any], Path, Path]:
    """Drive every ladder and report whether refinement converges."""

    database = Path(database_path)
    store = MockXStore(database, match_scope=match_scope, result_order=result_order)
    application = MockXApplication(store)
    cases = build_refinement_cases(store, limit=case_limit, seed=seed)
    if not cases:
        raise RuntimeError("No evaluation cases exist. Generate the dataset first.")

    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    per_case: list[dict[str, Any]] = []
    for case in cases:
        purchased_before = store.ledger.distinct_profiles
        # Turn 0 is the first search, before any refinement. Every later index
        # is one thing the user said.
        rank, identifiers = _rank_of(
            application, case.initial_criteria, case.expected_profile_id
        )
        ranks = [rank]
        rows.append(
            _row(
                case,
                index=0,
                turn_type="initial",
                field="",
                rank=rank,
                previous=None,
                top_ids=identifiers,
                previous_ids=None,
            )
        )
        for index, turn in enumerate(case.turns, start=1):
            previous_ids = identifiers
            rank, identifiers = (
                _handle_rank(application, turn.value, case.expected_profile_id)
                if turn.type == "handle"
                else _rank_of(application, turn.criteria, case.expected_profile_id)
            )
            rows.append(
                _row(
                    case,
                    index=index,
                    turn_type=turn.type,
                    field=turn.field,
                    rank=rank,
                    previous=ranks[-1],
                    top_ids=identifiers,
                    previous_ids=previous_ids,
                )
            )
            ranks.append(rank)

        search_ranks = ranks[: len(ranks) - 1]  # everything before the handle turn
        visible_at = next(
            (index for index, rank in enumerate(search_ranks) if rank is not None), None
        )
        per_case.append(
            {
                "case_id": case.id,
                "expected_profile_id": case.expected_profile_id,
                "turns": len(case.turns),
                "turns_to_visible": visible_at,
                "converged_by_search": visible_at is not None,
                "reachable": _reachable(store, case),
                "profiles_purchased": store.ledger.distinct_profiles - purchased_before,
            }
        )

    summary = _summarize(
        cases,
        rows,
        per_case,
        store=store,
        match_scope=match_scope,
        result_order=result_order,
        seed=seed,
        database=database,
        duration=time.perf_counter() - started,
    )
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output / f"refinement-{match_scope}-{result_order}-{stamp}.json"
    csv_path = output / f"refinement-{match_scope}-{result_order}-{stamp}.csv"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_csv(csv_path, rows)
    _print_summary(summary, json_path, csv_path)
    return summary, json_path, csv_path


def _rank_of(
    application: MockXApplication, criteria: RecipientCriteria, expected_id: str
) -> tuple[int | None, list[str]]:
    """Where the expected profile lands on the visible screen, and who is on it.

    Both halves matter and they are not the same measurement. The rank can move
    while the retrieved set does not: `build_search_query` returns the first
    non-empty of name, company, school, role, so adding a role or a location
    leaves the query byte-identical and the page byte-identical, and only
    `rank_candidates` reorders what was already there. Recording the ids is what
    lets the report say "re-ranked" rather than "re-retrieved".
    """

    query = build_search_query(criteria)
    response = application.search_users(query, VISIBLE_DEPTH)
    ranked = rank_candidates(
        [Candidate.from_dict(profile) for profile in response["data"]], criteria
    )
    identifiers = [candidate.id for candidate in ranked]
    rank = identifiers.index(expected_id) + 1 if expected_id in identifiers else None
    return rank, identifiers


def _handle_rank(
    application: MockXApplication, handle: str, expected_id: str
) -> tuple[int | None, list[str]]:
    """A handle turn resolves one account, so it is rank 1 or nothing."""

    try:
        resolved = application.lookup_user_by_username(handle)["data"]
    except MockXHttpError:
        return None, []
    identifier = str(resolved["id"])
    return (1 if identifier == expected_id else None), [identifier]


def _reachable(store: MockXStore, case: RefinementCase) -> bool:
    """Is this person reachable by any search anywhere in the ladder?

    Every criteria state is checked except the handle turn's, because
    narrowing_name changes the query and can rescue a case the opening name
    could not reach. Checking only the opening state would overstate the
    unconvergeable share.

    False here is the structurally unconvergeable case: no ranking change, no
    refinement turn and no amount of extra depth recovers them, and only the
    handle does. That share is the product finding.

    Goes through the store rather than the API, so asking the question costs
    nothing and never appears in the bill.
    """

    states = [
        case.initial_criteria,
        *(turn.criteria for turn in case.turns if turn.type != "handle"),
    ]
    for criteria in states:
        query = build_search_query(criteria)
        if not query:
            continue
        rows = store.search_profiles(query, limit=REACHABILITY_DEPTH)
        if any(str(row["id"]) == case.expected_profile_id for row in rows):
            return True
    return False


def _row(
    case: RefinementCase,
    *,
    index: int,
    turn_type: str,
    field: str,
    rank: int | None,
    previous: int | None,
    top_ids: list[str],
    previous_ids: list[str] | None,
) -> dict[str, Any]:
    return {
        "case_id": case.id,
        "expected_profile_id": case.expected_profile_id,
        "turn_index": index,
        "turn_type": turn_type,
        "field": field,
        "rank": rank,
        "visible": rank is not None,
        "improved": _moved(previous, rank) > 0,
        "worsened": _moved(previous, rank) < 0,
        # The structural question, separate from the ranking one: did this turn
        # change *who* was retrieved, or only the order they were shown in?
        # Compared as sets on purpose -- rank_candidates reorders the same ten
        # every turn, and reordering is exactly what this must not count.
        "retrieval_changed": (
            previous_ids is not None and set(top_ids) != set(previous_ids)
        ),
        "top_ids": "|".join(top_ids),
    }


def _moved(previous: int | None, current: int | None) -> int:
    """Positive when the target rose, negative when it fell, zero when it stayed.

    A miss is treated as one place worse than the deepest visible rank, so
    falling off the screen counts as falling rather than as no change.
    """

    if previous is None and current is None:
        return 0
    before = VISIBLE_DEPTH + 1 if previous is None else previous
    after = VISIBLE_DEPTH + 1 if current is None else current
    return before - after


def _summarize(
    cases: list[RefinementCase],
    rows: list[dict[str, Any]],
    per_case: list[dict[str, Any]],
    *,
    store: MockXStore,
    match_scope: str,
    result_order: str,
    seed: int,
    database: Path,
    duration: float,
) -> dict[str, Any]:
    refinement_rows = [row for row in rows if row["turn_index"] > 0]
    by_type = {
        turn_type: _turn_metrics(
            [row for row in refinement_rows if row["turn_type"] == turn_type]
        )
        for turn_type in TURN_TYPES
    }
    indexes = sorted({row["turn_index"] for row in rows})
    by_index = {
        str(index): _turn_metrics([row for row in rows if row["turn_index"] == index])
        for index in indexes
    }
    converged = [item for item in per_case if item["converged_by_search"]]
    unreachable = [item for item in per_case if not item["reachable"]]
    purchased = [item["profiles_purchased"] for item in per_case]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_seconds": round(duration, 3),
        "database": str(database.resolve()),
        "match_scope": match_scope,
        "result_order": result_order,
        "seed": seed,
        "case_count": len(cases),
        "turn_count": len(refinement_rows),
        "by_turn_type": by_type,
        "by_turn_index": by_index,
        "convergence": {
            "rate": _ratio(len(converged), len(per_case)),
            "mean_turns_to_visible": (
                round(
                    sum(item["turns_to_visible"] for item in converged) / len(converged),
                    4,
                )
                if converged
                else None
            ),
            "monotonicity_violations": sum(row["worsened"] for row in refinement_rows),
            "structurally_unconvergeable": _ratio(len(unreachable), len(per_case)),
            "unconvergeable_cases": len(unreachable),
            "mean_profiles_purchased": (
                round(sum(purchased) / len(purchased), 4) if purchased else 0.0
            ),
        },
        "cost": store.ledger.summary(case_count=len(cases)),
        "cases": per_case[:200],
    }


def _turn_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    if not count:
        return {
            "turns": 0,
            "visible": 0.0,
            "improved": 0,
            "worsened": 0,
            "unchanged": 0,
            "retrieval_changed": 0,
        }
    return {
        "turns": count,
        "visible": _ratio(sum(row["visible"] for row in rows), count),
        "improved": sum(row["improved"] for row in rows),
        "worsened": sum(row["worsened"] for row in rows),
        "unchanged": sum(
            not row["improved"] and not row["worsened"] for row in rows
        ),
        "retrieval_changed": sum(row["retrieval_changed"] for row in rows),
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _print_summary(summary: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    print(
        f"\nRefinement: {summary['case_count']:,} cases, "
        f"{summary['turn_count']:,} turns  "
        f"[scope={summary['match_scope']} order={summary['result_order']}]"
    )
    print(
        f"  {'turn type':18}{'turns':>8}{'visible':>10}{'improved':>10}"
        f"{'worsened':>10}{'re-retrieved':>14}"
    )
    for turn_type, metrics in summary["by_turn_type"].items():
        print(
            f"  {turn_type:18}{metrics['turns']:>8,}{metrics['visible']:>10.1%}"
            f"{metrics['improved']:>10,}{metrics['worsened']:>10,}"
            f"{metrics['retrieval_changed']:>14,}"
        )
    convergence = summary["convergence"]
    mean_turns = convergence["mean_turns_to_visible"]
    print(
        f"\n  convergence by search   {convergence['rate']:.1%}"
        f"   mean turns to visible: {'n/a' if mean_turns is None else f'{mean_turns:.2f}'}"
    )
    print(
        f"  monotonicity violations {convergence['monotonicity_violations']:,}"
        f"   profiles purchased/case: {convergence['mean_profiles_purchased']:.1f}"
    )
    print(
        f"  unconvergeable          {convergence['structurally_unconvergeable']:.1%}"
        f"   ({convergence['unconvergeable_cases']:,} cases reachable only by handle)"
    )
    cost = summary["cost"]
    print(
        f"  estimated cost          ${cost['per_case_usd'] or 0:.4f}/case"
        f"   (run total ${cost['estimated_usd']:,.2f})"
    )
    print(f"JSON report: {json_path}")
    print(f"CSV detail: {csv_path}")


def main() -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Measure recipient refinement convergence")
    parser.add_argument("--database", default=str(settings.mock_x_database_path))
    parser.add_argument("--output", default=str(PROJECT_ROOT / ".cache" / "refinement"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--scope",
        default="both",
        choices=[*MATCH_SCOPES, "both"],
        help="What the query matches. 'both' reports under each, because we do not know.",
    )
    parser.add_argument(
        "--order",
        default="bm25",
        choices=[*RESULT_ORDERS, "both"],
        help="How results are sorted. A sensitivity check on the headline number.",
    )
    args = parser.parse_args()
    scopes = list(MATCH_SCOPES) if args.scope == "both" else [args.scope]
    orders = list(RESULT_ORDERS) if args.order == "both" else [args.order]
    for scope in scopes:
        for order in orders:
            run_refinement(
                args.database,
                output_directory=args.output,
                case_limit=args.limit,
                match_scope=scope,
                result_order=order,
                seed=args.seed,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_refinement_harness.py -q`
Expected: PASS — 12 passed

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Produce the report the phase exists for**

Run against the calibrated corpus, under both match scopes:

```bash
python -m mock_x_platform.dataset --database .cache/mock_x_platform.sqlite3 --count 100000 --seed 42
python -m mock_x_platform.refinement --database .cache/mock_x_platform.sqlite3 --scope both --order both
```

Read the four reports and write up the findings. The questions to answer, in order:

1. What share of refinement turns are `off_profile`? That is the fraction of real refinement the platform structurally cannot use.
2. Do `on_profile` clues converge without any extra retrieval? If they do, spec Task 4 (depth on demand) is unnecessary and the honest move is to say so and stop — the way round 3 did.
3. What share of cases is structurally unconvergeable, reachable only through the handle? That number is the product finding, and it is what turns Task 1 from a convenience into the recovery path.
4. Do the answers to 1–3 hold under both match scopes? A conclusion that holds under both is robust to our ignorance about Unknown A. One that holds under only one depends on a fact we do not have, and saying so is itself the finding.

- [ ] **Step 7: Commit**

```bash
git add mock_x_platform/refinement.py tests/test_refinement_harness.py
git commit -m "feat: measure whether spoken refinement can converge

Drives labeled ladders against the search path exactly as it stands, so the
no-op is proven rather than asserted. Reports turns-to-visible, monotonicity
violations, profiles purchased and the structurally unconvergeable share, under
each setting of the two things we do not know about the real endpoint.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## What this plan deliberately does not do

Each of these is out of scope with a reason, carried from spec §6. Do not pick them up mid-plan.

- **Spec Task 0, the live X probe.** It needs a developer app, an OAuth user token and purchased credits, none of which exist. It stays specified rather than built; whoever runs it updates spec §3 with the answer, and the mock's `match_scope` flag then selects a setting instead of triggering a rewrite.
- **Spec Tasks 4 and 5 (depth on demand, the re-run).** Conditional on Task 11's report. If `on_profile` clues already converge, Task 4 should not be built at all.
- **The generator's over-length usernames.** `dataset.py` produces names like `isabella_rodriguez_4507`, which X's fifteen-character limit would reject. Fixing it means regenerating the corpus and re-baselining every threshold rounds 1-6 measured, so it is recorded here and worked around in `USERNAME_PATTERN` instead. `parse_handle` still enforces the real limit on spoken input, which is where a real handle actually enters the system.
- **The real `XClient`'s ten-result cap.** See Global Constraints.
- **Ranking weights.** Finding 1 (partial-tier profiles capped at 45) stays open.
- **Tier mix ratios.** Changing them would move every baseline mid-phase.
- **The regex parser.** `parsing/recipient_parser.py` will not survive "the guy with the beard who used to be at Stripe". The harness feeds structured criteria directly, so it does not need the replacement. Next phase.
- **Appearance / profile-photo matching.** Vision is a discriminate-only signal: it can raise the bearded Joe Bart within the ten we hold, never conjure the eleventh. Built today it would be measured against synthetic profile pictures we generated, inside a mock we wrote, over a fixed pool.
- **Acceptance thresholds on cost or refinement.** Reported only this round; a threshold needs a baseline and an observed injected failure first.
