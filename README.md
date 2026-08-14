# X User Finder Prototype

A Windows-friendly Python CLI that accepts a typed or locally transcribed person description, turns it into structured criteria, searches X (or bundled mock profiles), applies a deterministic ranking, and supports adding clues without restarting.

The repository now also includes an 800×480 Pygame prototype of the future handheld device. It connects real offline speech-to-text to the recipient parser and mock/live user finder without coupling its UI to Whisper, X JSON, keyboard events, or future GPIO pins.

## Handheld device prototype

Install the device dependencies (this includes the offline voice stack):

```powershell
python -m pip install -r requirements-device.txt
python -m voice.download_model
```

Launch the fixed-resolution interface:

```powershell
python -m device_app
```

The Windows version opens an exact 800×480 window. Set `DEVICE_FULLSCREEN=true` for the Raspberry Pi display. The first milestone implements:

```text
HOME
  → RECIPIENT_RECORDING
  → TRANSCRIBING
  → CRITERIA_REVIEW
  → SEARCHING
  → PROFILE_RESULTS
  → mocked MESSAGE_RECORDING handoff
```

Controls:

| Keyboard | Device action | Current behavior |
|---|---|---|
| Hold/release `SPACE` | Send down/up | Record voice; confirm criteria; select profile |
| `R` | Refine | Cancel recording or add a spoken recipient correction |
| `LEFT` / `RIGHT` | Previous/next | Browse candidate profiles |
| `UP` / `DOWN` | Scroll | Scroll transcript or profile details |
| `ESC` | Back | Cancel or return to the previous safe screen |

The UI uses semantic actions rather than raw keys, so a later GPIO adapter can emit the same actions without changing the state machine. Recording, Whisper inference, and user search run in worker threads; the 30 FPS display loop continues animating while they complete. Cancelled operations are identified by operation ID and their late results are ignored.

At recipient review, `R` opens a spoken-refinement screen. Supported deterministic corrections include:

```text
change company to ABC
location is Montreal
remove the school
also went to U of T
```

Targeted commands update or clear a single field. Other descriptions fall back to the additive parser. The new criteria must be reviewed and confirmed before another search.

`MOCK_X=true` remains the default, so the device UI uses real local voice while avoiding paid X requests. Selecting a result preserves the complete `Candidate` and opens a mocked message-recording handoff; message generation and sending remain outside this milestone.

## Quick start (free mock mode)

Python 3.10 or newer is recommended.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python main.py
```

This launches the original terminal interface. Use `python -m device_app` for the handheld UI.

`MOCK_X=true` is the default, even if no `.env` exists. No network call or X credential is used in this mode. The mock source contains 12 deliberately similar profiles and returns at most 10 per search.

Try:

```text
John Doe, recruiter at XYZ in Toronto
```

After the ranked results, choose `R` and add:

```text
He lives in Toronto and went to U of T
```

The existing name/company/role context is preserved, the school is canonicalized to `University of Toronto`, and the candidates are searched and ranked again.

## Standalone Mock X platform

The repository includes a local X-shaped HTTP service for testing the application as two separate systems. Unlike the embedded mock client, this service persists fake DMs in SQLite and can deliberately return errors such as rate limits or service failures.

In the first terminal, start the platform:

```powershell
python -m mock_x_platform
```

Then set these values in `.env`:

```dotenv
MOCK_X=true
MOCK_X_BASE_URL=http://127.0.0.1:8765
```

In a second terminal, run either client:

```powershell
python main.py
# or
python -m device_app
```

The existing `find_users(criteria) -> list[Candidate]` boundary does not change. An empty `MOCK_X_BASE_URL` selects the original in-process mock profiles; a local URL selects the standalone platform; `MOCK_X=false` selects live X.

Implemented local routes:

| Route | Purpose |
|---|---|
| `GET /health` | Service readiness |
| `GET /2/users/search` | X-shaped user search, capped at 10 |
| `POST /2/dm_conversations/with/{id}/messages` | Send a fake one-to-one DM |
| `GET /2/dm_conversations/with/{id}/dm_events` | Read that fake conversation |
| `GET /__mock__/messages` | Inspect all stored fake messages |
| `POST /__mock__/failures` | Make an operation fail a chosen number of times |
| `POST /__mock__/reset` | Clear messages and failure rules |

For example, make the next search return a synthetic rate limit:

```powershell
$body = @{ operation = "search"; status = 429; detail = "Try later"; count = 1 } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/__mock__/failures -ContentType application/json -Body $body
```

Fake messages are stored at `.cache/mock_x_platform.sqlite3` by default. `MOCK_X_HOST`, `MOCK_X_PORT`, and `MOCK_X_DATABASE_PATH` configure the server. It binds to loopback by default and is a development fixture, not a production or internet-facing service. The DM API and provider-neutral message model are ready for the next message-flow milestone; the current Pygame handoff intentionally does not send yet.

### Generate the 100,000-profile dataset

The server falls back to the 12 hand-authored profiles until a generated dataset exists. Build the full deterministic dataset with:

```powershell
python -m mock_x_platform.dataset
```

Restart `python -m mock_x_platform` afterward. The generator uses a fixed seed and includes the original ambiguous John Doe profiles plus broad synthetic variation across names, companies, roles, schools, locations, verification, and DM eligibility. It also stores 1,000 deterministic ground-truth cases pairing natural-language descriptions and structured criteria with the expected profile ID. Re-running the command replaces the generated profiles and evaluation cases while preserving fake messages.

Profiles live in SQLite alongside fake DM data and are retrieved through an FTS5 index. Each search first retrieves at most 250 plausible profiles, applies deterministic mock relevance, and returns at most 10 for the application's detailed criteria ranker. This avoids scanning and sorting all 100,000 profiles per request.

For a smaller local dataset or another repeatable variation:

```powershell
python -m mock_x_platform.dataset --count 10000 --seed 42
```

The generated database remains under `.cache/` and is intentionally excluded from Git. Source control contains the generator, fixed seed, schema, and compact fixtures rather than a large generated binary.

### Intensive search evaluation

Run the complete 1,000-case robustness and HTTP load evaluation separately from the fast unit suite:

```powershell
python -m mock_x_platform.evaluation
```

The command performs 6,000 searches covering clean criteria, formatting changes, missing school/role/location clues, and deterministic name typos. It also checks repeatability and exercises the real HTTP endpoint with 1, 4, and 8 concurrent clients. The measured full run takes roughly 35–40 minutes on the current Windows development machine; failures are still written to the reports before the command returns a non-zero exit code.

For a quick development sample:

```powershell
python -m mock_x_platform.evaluation --limit 25 --http-requests 10 --report-only
```

Timestamped JSON summaries and row-level CSV details are written to `.cache/evaluation/`. Reports include the database SHA-256 fingerprint and generator metadata, top-1/top-3/top-10 accuracy, mean reciprocal rank, retrieval recall, ranking success, latency percentiles, HTTP throughput/errors, acceptance checks, and concrete failure examples. Use `--skip-http` to isolate in-process search quality or `--report-only` when collecting a baseline that should not return an error status after a failed threshold.

## Offline voice input

Voice support is optional and does not add weight to typed-only installations. It uses [python-sounddevice](https://python-sounddevice.readthedocs.io/) for microphone capture and [faster-whisper](https://github.com/SYSTRAN/faster-whisper) with CPU INT8 inference by default.

Install the voice dependencies:

```powershell
python -m pip install -r requirements-voice.txt
```

Download the `base.en` model once while connected to the internet:

```powershell
python -m voice.download_model
```

This explicit setup step stores the model under `.models/faster-whisper/base.en`. Normal transcription loads only that local directory with `local_files_only=True`; it never calls a cloud transcription service and does not silently download a missing model.

Optionally inspect microphone device numbers:

```powershell
python -m voice.devices
```

Set `VOICE_INPUT_DEVICE` in `.env` if the Windows default microphone is not the one you want. Then run the normal application:

```powershell
python main.py
```

At either a new-description or refinement prompt, enter:

```text
/voice
```

Press Enter when ready and speak while the terminal displays a live remaining-seconds counter. Press Enter again to stop early, or let recording stop automatically at the configured maximum. As soon as capture ends, the terminal prints `[VOICE] Recording complete. Transcribing locally...`, followed by the transcript when inference finishes. You can accept it, edit it, record again, or cancel.

After typed or spoken text is parsed, the program shows the structured criteria before searching:

```text
Search criteria:
Name: John Doe
Company: XYZ
Role: Recruiter
Location: Toronto
School: (not specified)

[Enter] Search  [E] Edit criteria  [C] Cancel:
```

Choose `E` to update or clear any field, including extra clues. No mock or paid X request happens until you confirm with Enter. Cancelling a refinement preserves the previously active criteria. These confirmation steps protect X usage from both transcription and parsing mistakes.

The temporary mono PCM WAV is deleted immediately after transcription. One `OfflineWhisperTranscriber` is reused for the CLI session, so the model is not reloaded for every refinement.

Voice settings in `.env`:

| Setting | Default | Purpose |
|---|---|---|
| `VOICE_MODEL` | `base.en` | Model downloaded by the setup command |
| `VOICE_MODEL_PATH` | `.models/faster-whisper/base.en` | Fully local runtime model directory |
| `VOICE_RECORD_SECONDS` | `8` | Maximum recording length, constrained to 1–30 seconds |
| `VOICE_SAMPLE_RATE` | `16000` | Mono WAV sample rate |
| `VOICE_INPUT_DEVICE` | empty | Default device, numeric index, or device name |
| `VOICE_LANGUAGE` | `en` | Transcription language |
| `VOICE_COMPUTE_DEVICE` | `cpu` | CTranslate2 compute device |
| `VOICE_COMPUTE_TYPE` | `int8` | CPU-friendly quantization |
| `VOICE_CPU_THREADS` | `0` | Let CTranslate2 choose, or set an explicit thread count |
| `VOICE_NUM_WORKERS` | `1` | Parallel model workers |

`small.en` can be selected for higher accuracy at the cost of more download size, memory, and latency. Change both `VOICE_MODEL` and `VOICE_MODEL_PATH`, then run the download command again.

The public boundary is deliberately backend-neutral:

```python
from voice import transcribe_audio

text = transcribe_audio("recording.wav")
```

On the future Raspberry Pi, `OfflineWhisperTranscriber` can be replaced with a `whisper.cpp` adapter without changing microphone, parser, search, or UI-facing code.

## Live X API setup

This implementation follows X's current [User Search documentation](https://docs.x.com/x-api/users/search/introduction) and calls:

```text
GET https://api.x.com/2/users/search
```

It always sends `max_results=10`; X's [endpoint reference](https://docs.x.com/x-api/users/search-users) documents a much larger default, so the application never relies on that default. It requests:

```text
id,name,username,description,location,profile_image_url,
verified,is_identity_verified,receives_your_dm
```

X's [authentication documentation](https://docs.x.com/fundamentals/authentication/oauth-2-0/application-only) specifically lists user search as requiring user-context authentication. An app-only bearer token is therefore not sufficient.

To use live mode:

1. Create/choose a Project and App in the [X Developer Console](https://developer.x.com/).
2. Enable OAuth 2.0 for the app, configure an exact callback URL, and request a user-context authorization grant using Authorization Code with PKCE. Request at least the `users.read` scope. This grant includes a browser approval step on X. OAuth 1.0a user context is also supported by X, but this client expects the resulting OAuth 2.0 bearer access token.
3. Put the resulting **user access token** in `.env` and disable mock mode:

   ```dotenv
   MOCK_X=false
   X_ACCESS_TOKEN=your_user_context_access_token
   ```

4. Run `python main.py`.

The prototype consumes an already-authorized access token; it does not store passwords, automate the browser grant, or persist refresh tokens. If the token expires, complete/refresh the OAuth grant with your chosen OAuth client and update `X_ACCESS_TOKEN`. `X_CLIENT_ID` and `X_CLIENT_SECRET` are included in `.env.example` as convenient placeholders for that external OAuth setup but are not read by the search runtime.

X access tiers, pricing, and endpoint availability can change. Confirm that User Search is enabled for your developer account before switching off mock mode.

## Ranking

Ranking is deterministic and lives entirely in `ranking/`, so it can later be replaced by semantic ranking without changing API or UI code.

| Match | Points |
|---|---:|
| Exact normalized name | 40 |
| Similar normalized name | 20 |
| Company in name/handle/bio | 30 |
| Role in bio | 15 |
| Location in location/bio | 10 |
| School in bio | 15 |
| Can receive a DM | 5 |

Every awarded rule is returned in `Candidate.match_reasons` and printed by the CLI. Results sort by descending score, then by normalized name, username, and ID for stable ties. Verification is displayed but intentionally does not affect the score.

Normalization handles capitalization, accents, punctuation, and whitespace. Company suffixes and acronyms are considered where practical, and the prototype recognizes `University of Toronto`, `U of T`, and `UofT` as equivalent.

`receives_your_dm` can be absent from an X response. The model represents that as `can_dm=None`/`Unknown` rather than guessing.

## API cost protection and cache

- Both the service and the concrete clients cap each search at 10 candidates.
- No pagination request is made.
- Search queries are constrained to X's documented 50-character user-search pattern.
- Raw, unranked candidates are cached in `.cache/x_user_search.json` under a provider-specific normalized query key.
- Repeating the same query prints `[CACHE] Reusing previous search.` and performs no API request.
- A new query prints `[X API] Performing new user search...` (or `[MOCK X] ...`).
- Ranking runs after cache retrieval, so criteria can be reranked without exposing cached API JSON to callers.

Delete `.cache/x_user_search.json` manually when you intentionally want a fresh result for a previously searched query.

## Using the service from future device code

```python
from models.criteria import RecipientCriteria
from search import find_users

criteria = RecipientCriteria(
    name="John Doe",
    company="XYZ",
    role="Recruiter",
    location="Toronto",
)
results = find_users(criteria)  # list[Candidate], never raw X JSON
```

Each `Candidate` contains `id`, `name`, `username`, `bio`, `location`, `profile_image_url`, `verified`, `can_dm`, `score`, and `match_reasons`.

The parser is independently callable:

```python
from parsing import parse_recipient_description, refine_criteria

criteria = parse_recipient_description("John Doe at XYZ")
criteria = refine_criteria(criteria, "He lives in Toronto and went to U of T")
```

The current parser is deliberately small and conservative. It handles common `name, role at company in location`, `lives in`, and `went to` forms; unmatched text is kept in `extra_clues`. An AI parser can later replace `parse_recipient_description()` without coupling it to X search.

## Project structure

```text
main.py                       CLI only
config.py                     environment settings
models/                       criteria, candidate, and direct-message domain models
parsing/recipient_parser.py   parsing/refinement boundary
search/                       query builder, X/mock clients, service
ranking/                      normalization and deterministic scoring
storage/cache.py              local JSON cache
data/mock_profiles.py         12 fake profiles
mock_x_platform/              standalone X-like search and DM test service
messaging/                    provider-neutral DM client boundary
voice/                        microphone and offline transcription boundary
device_app/                   Pygame UI, semantic input, state machine, workers
tests/                        offline unit tests
```

## Tests

All tests use local objects or mock profiles and never call X:

```powershell
python -m pytest -q
```

Coverage includes name normalization, company and school aliases, location matching, scoring, deterministic sorting, criteria refinement and confirmation editing, caching, query sanitization, the service's 10-result/cached-request behavior, countdown/early-stop WAV capture, local-only model loading, transcript assembly, model reuse, and temporary-audio cleanup. Device tests cover hold/release transitions, confirmation-before-search, spoken corrections, cancellation, stale events, retry, profile navigation/selection, keyboard-action mapping, and headless rendering of every state at exactly 800×480. Tests inject fake audio/model/search backends and never access a microphone, download a model, or call X.
#   w a b b  
 #   w a b b  
 