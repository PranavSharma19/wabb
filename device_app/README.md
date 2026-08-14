# Device application prototype

This package owns the Raspberry Pi-facing UI and workflow. It does not depend on
the implementation details of X search. The default runtime uses ten local mock
profiles and never calls `search/`, the mock X server, or the generated dataset.

## Run

Install the device and offline voice dependencies, download the configured local
speech model once, and launch the 800x480 interface:

```powershell
python -m pip install -r requirements-device.txt
python -m voice.download_model
python -m device_app
```

Controls:

| Keyboard | Device action |
|---|---|
| Hold/release `SPACE` or `ENTER` | Send/record/confirm |
| `R` | Refine recipient or message |
| `LEFT` / `RIGHT` | Previous/next profile |
| `UP` / `DOWN` | Scroll details |
| `ESC` | Back/cancel |

The canonical flow is:

```text
HOME -> RECORD_RECIPIENT -> SEARCHING -> SELECT_PROFILE
     -> RECORD_MESSAGE -> REVIEW_MESSAGE -> SENDING -> SUCCESS
```

Transcription and recipient-review states sit between those major steps.
Recipient refinement from `SELECT_PROFILE` preserves the existing criteria,
adds the new spoken clue, and reruns search. Message refinement returns to
`REVIEW_MESSAGE`. Confirmation persists a fake DM through the local Mock X
service. There is no real-message provider in the device runtime.

By default, mock DMs are stored in `.cache/mock_x_platform.sqlite3`. If
`MOCK_X_BASE_URL` is configured, delivery uses that running Mock X HTTP service
instead. In either mode, DM eligibility is enforced and a failed send returns to
a retryable error screen without losing the draft.

To exercise the existing repo search through the adapter later:

```powershell
$env:DEVICE_SEARCH_PROVIDER = "existing"
python -m device_app
```

Mock mode is the default even if `MOCK_X` has another value. This explicit
switch prevents device development from accidentally invoking the search being
tested elsewhere.

## Raspberry Pi buttons

The GPIO adapter uses BCM numbering, active-low buttons, internal pull-ups, and
40 ms software debounce. Connect one terminal of each momentary button to its
GPIO pin and the other terminal to ground:

| Action | BCM GPIO | Physical header pin |
|---|---:|---:|
| Send | 17 | 11 |
| Refine | 27 | 13 |
| Previous | 22 | 15 |
| Next | 23 | 16 |
| Back | 24 | 18 |

On Raspberry Pi OS, install an `RPi.GPIO`-compatible backend and enable the
adapter:

```powershell
python -m pip install rpi-lgpio
$env:DEVICE_GPIO = "true"
python -m device_app
```

Keyboard input remains enabled alongside GPIO for bench testing. The Send pin
emits separate press/release actions for hold-to-record; other buttons emit one
action per debounced press. GPIO cleanup runs whenever the application exits.

## Search provider contract (schema version 1)

Device state and UI code depend only on this protocol:

```python
class SearchProvider(Protocol):
    def search(self, criteria: RecipientCriteria) -> SearchResult: ...
```

`SearchResult` is serializable to this exact shape:

```json
{
  "schema_version": 1,
  "query": "John Doe XYZ recruiter Toronto",
  "candidates": [
    {
      "id": "required-provider-id",
      "name": "John Doe",
      "username": "johndoe",
      "company": "XYZ",
      "bio": "Technical recruiter at XYZ",
      "location": "Toronto, Canada",
      "profile_image_url": "https://...",
      "profile_url": "https://x.com/johndoe",
      "verified": true,
      "can_dm": true,
      "score": 91.5,
      "match_reasons": ["Matched name and company"]
    }
  ]
}
```

Rules:

- `schema_version` must be `1`.
- `id`, `name`, and `username` are required non-empty strings.
- `username` must not include a leading `@`.
- `candidates` must contain no more than 10 profiles and must already be in
  best-first display order.
- `company`, `bio`, `location`, image/profile URLs, and match reasons may be
  empty. `can_dm` may be `true`, `false`, or `null`. `score` is numeric.

The current real-search code does not need to change to use the device. Pass its
existing callable to `ExistingSearchAdapter`; the callable signature is:

```python
def find_users(criteria: RecipientCriteria) -> list[models.candidate.Candidate]: ...
```

The adapter caps the list at ten, converts domain candidates to schema v1, and
adds the canonical `https://x.com/{username}` profile URL. Because the existing
domain model has no explicit `company`, its bio is displayed when company is
unavailable.

## Tests

```powershell
python -m pytest -q tests/test_device_state.py tests/test_device_jobs.py `
  tests/test_device_ui.py tests/test_device_search_contract.py
```

The tests simulate voice completions, provider responses, keyboard/GPIO hardware
actions, all screens, recipient refinement/re-search, message refinement, local
mock delivery, delivery failure/retry, and success. They do not access a
microphone, real X, or the generated search dataset.

`tests/test_device_integration.py` also starts a private loopback Mock X server
and temporary generated profile database, then exercises the complete device
path through the existing search adapter, HTTP search, ranking, selection,
message generation, HTTP mock DM persistence, and `SUCCESS`. It runs in the
normal suite and never reads or modifies `.cache/mock_x_platform.sqlite3`.
