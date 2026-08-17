# Recipient Refinement Loop — Design

Date: 2026-08-17
Status: approved for planning
Supersedes: nothing. Builds on rounds 1–6 (name-primary query, dirt tiers, narrowed FTS index, acceptance gate).

## 1. Problem

The device asks the user to describe a person, searches X, and shows ten ranked
candidates. When the right person is not among them, the user presses Refine and
says more: a city, a school, what the person looks like. The loop is supposed to
converge on the right account.

It cannot. Not "works badly" — cannot, by construction.

`search/service.py:32` builds the cache key from `build_search_query(criteria)`,
and round 1 deliberately made that **name-only**. So:

1. "Joe Bart, member of technical staff at Meta" → query `"Joe Bart"` → ten of the
   many Joe Barts on X.
2. Refine: "he's in San Francisco" → `refine_criteria` sets `location` → the query
   is still `"Joe Bart"` → **cache hit** → the same ten rows → `rank_candidates`
   reorders them.

Refinement re-ranks; it never re-retrieves. If the target was not in the first ten,
no number of refinement turns can reach them. The user can talk forever.

`search/x_client.py:34` makes this structural rather than incidental:

```python
limit = min(max(int(max_results), 1), 10)
```

The real client is hard-capped at ten results and has no `next_token` handling at
all. There is no depth to reach for even if the caller asked.

The sharp part: the cache-reuse test round 1 asked for — "refining criteria without
changing the name reuses the cache" — is the same mechanism that caps refinement at
ten candidates. It was specified as a cost optimisation and is simultaneously the
ceiling on the feature. Both readings are correct. This design resolves the tension
explicitly instead of quietly deleting one side.

## 2. What the X API actually permits

Checked against `docs.x.com` on 2026-08-17. Recorded here because two prior
assumptions in the project document (§32, §33) are now out of date.

**Access model.** The Free/Basic/Pro subscription tiers are retired. X is
pay-per-use: credits purchased up front, deducted per request. `GET /2/users/search`
carries **no stated access restriction** and needs an OAuth 2.0 user token with
`tweet.read` and `users.read`. Enterprise is only required above 3M post reads per
month, which this project will not approach.

An earlier concern in discussion — that user search might be Enterprise-gated the
way LinkedIn's is — was wrong under the current model. It is recorded here only so
nobody re-derives it.

**Endpoint shape.**

| Property | Value |
| --- | --- |
| Path | `GET /2/users/search` |
| `query` pattern | `^[A-Za-z0-9_' ]{1,50}$` |
| `max_results` | min 1, **default 100**, max 1000 |
| Pagination | `next_token`, from `meta.next_token` |
| Auth | OAuth2 user token (`tweet.read`, `users.read`) |

The documented `query` pattern is byte-identical to what `sanitize_query` already
enforces. Round 1's constraint was correct and stays.

**Cost.** Read operations are charged per resource returned. A User is **$0.010**.

| Call | Profiles | Cost |
| --- | --- | --- |
| `max_results=10` | 10 | $0.10 |
| `max_results` omitted (default 100) | 100 | **$1.00** |
| `max_results=1000` | 1000 | **$10.00** |

Omitting `max_results` costs a dollar a search. The current client's hard cap of ten
is, accidentally, the only thing standing between this project and a large bill.
Any change that relaxes it must replace it with a deliberate budget.

**Deduplication.** Resources are deduplicated within 24-hour UTC windows — the same
profile requested twice in a day is billed once. This is the single most important
fact for this design. The billable unit is **distinct profiles seen**, not requests.
Fetching 10 profiles ten times over costs $0.10 total; fetching 10 new profiles ten
times costs $1.00. Lazy incremental depth is therefore not merely a nice
optimisation, it is the economically correct architecture, and cost scales with how
much of the corpus we actually look at.

**Rough monthly envelope.** A user sending 20 messages a month, averaging two
refinement pulls each at 10 new profiles per pull, sees 20 × 30 = 600 distinct
profiles ≈ **$6.00/month** in search, plus DM sends at $0.010–0.015 each. Viable for
a subscription device, but refinement depth is the dominant cost line, which is why
this design instruments it rather than assuming it.

## 3. The two things we do not know

Both are cheap to settle once credentials exist and expensive to guess wrong.

**Unknown A — what does `query` match?** The docs do not say whether it matches
display name, username, bio, or all three. Round 4 narrowed the mock's FTS index to
name and username on the assumption it is the first two. If X also matches bio, then
`"Joe Bart Meta"` simply works, refinement becomes structured query composition, and
most of Task 4 is unnecessary.

**Unknown B — how are results ordered?** Our mock orders same-name profiles by
bm25-then-id, effectively arbitrary. Real X almost certainly applies its own
relevance, plausibly follower-weighted. This is not cosmetic: if X sorts by
popularity, page three of the Joe Barts means *less famous*, and the Joe Bart the
user wants — a member of technical staff, not a personality — is systematically
deep. Under our mock he is uniformly scattered. Every convergence number bends on
this.

**How this design handles them.** We do not guess. The mock gains a flag for each,
and results are reported under both settings:

- Unknown A is the **primary axis**, because it changes the architecture. All
  headline metrics are reported twice, once per match scope.
- Unknown B is a **sensitivity check** on the headline number only, because it
  changes magnitudes rather than structure.

If a conclusion holds under both settings of an unknown, it is robust to our
ignorance. If it holds under only one, then the conclusion depends on a fact we do
not have — and saying so is the finding.

## 4. Success criteria

Approved in brainstorming: a refinement turn succeeds when the expected profile is
**on the visible screen** — present in the displayed ten — and no turn pushes it
further down.

This matches the device's actual job. §6 of the project document is explicit that
the human confirms identity and the AI only ranks; requiring rank 1 would measure
something the product does not promise.

Primary metrics:

- **turns-to-visible** — refinement turns needed before the target appears.
- **convergence rate** — fraction of cases that ever reach visible within budget.
- **monotonicity violations** — turns that moved the target *down*. On a device
  where the user cannot see why, a refinement that demotes the target is worse than
  one that does nothing; it teaches the user the feature is broken.
- **profiles purchased per convergence** — the billable unit from §2.
- **structurally unconvergeable fraction** — cases no refinement can ever fix.

## 5. Tasks

### Task 0 — Live probe (blocked; specify now, run when credentials exist)

Not runnable today: there is no developer app, no OAuth, no credits. Specified here
because it invalidates or confirms Task 4 before Task 4 is expensive to change.

Create a developer app, obtain a user-context token, and issue two searches at
`max_results=10`, against a name whose holder has a distinctive, non-name term in
their bio:

1. Search the bare name. Record the ten results and their order.
2. Search `name + bio-term`. If the bio-term holder rises or the result set changes,
   **`query` matches bio** and Unknown A is settled in favour of composition.
3. Record follower counts across both result sets to test whether ordering
   correlates with popularity (Unknown B).

Cost is at most $0.20 and less in practice, since profiles appearing in both result
sets are deduplicated within the 24-hour window. Output is a recorded finding, not
code. Whoever runs it updates §3 of this document with the answer.

**Explicitly:** Tasks 1–5 proceed against the mock without waiting for this. The
mock's flags exist so the probe's answer selects a setting rather than triggering a
rewrite.

### Task 1 — Handle shortcut

The user who already knows the handle should never enter the search loop.

New `parsing/handle_detector.py`:

```python
def detect_handle(text: str) -> str | None: ...
```

Run **before** `parse_recipient_description`. On a hit, skip search entirely: a new
`lookup_username()` on the client protocol resolves one profile in one call
($0.010), and the device goes straight to a single-candidate confirm screen — 1/1,
arrows inert, Refine falling back to the normal description flow.

**The hard part is the false positive.** X handles are `[A-Za-z0-9_]{1,15}`, so
"message at **Meta**" parses as a valid handle and would resolve to a real account,
showing the user a confidently wrong card. Syntax alone cannot separate a handle
from the preposition "at". Detection therefore fires only when the handle is
*dominant*:

- a literal `@` appears in the transcript, **or**
- an explicit "handle" / "username" keyword appears, **or**
- the utterance reduces to a bare single token after stripping lead-in verbs
  ("message", "DM", "find", "send a message to").

"Joe Bart at Meta" has a name before the "at" and must not fire. Ambiguity resolves
toward the search loop, which is recoverable, rather than toward a wrong profile,
which is not.

ASR forms to cover, since the transcript never contains a clean `@`:
`at jay bart`, `j b a r t` (spelled out), `jbart underscore dev`, `at johndoe`,
`his handle is jbart`.

New state: `AppState.HANDLE_LOOKUP` between `RECIPIENT_TRANSCRIBING` and
`SELECT_PROFILE`. Failure to resolve falls through to normal search silently — the
user should never see "handle not found", only the ordinary candidate list.

### Task 2 — Pagination and cost model in the mock

`MockXStore.search_profiles(query, limit=250)` currently returns a pool and the top
ten. It gains X's actual shape:

- `next_token` cursors on the mock HTTP layer and `MockXClient`, encoding
  (query, offset) opaquely.
- `max_results` honouring X's real bounds (1–1000, default 100), so a caller that
  forgets the parameter reproduces the $1.00 mistake in the harness rather than in
  production.
- A **billing ledger**: distinct profile ids served per 24-hour window, so the
  harness reports dollars, not requests. This is the metric §4 cares about and it
  cannot be reconstructed from request counts.
- `match_scope` flag (Unknown A): `name_username` | `name_username_bio`, changing
  which columns the FTS index covers.
- `result_order` flag (Unknown B): `bm25` | `follower_weighted`. Requires a
  follower-count field on generated profiles, drawn from a heavy-tailed distribution
  with the shape reasoning written down in a comment — the same discipline the tier
  mix follows.

Existing tests that pin round 4's narrowed index must keep passing under the
`name_username` default. Any that need updating get a stated reason in the diff.

### Task 3 — Refinement harness

The measurement, built against the **unfixed** code so the no-op is proven rather
than asserted. This is the largest task and the most valuable output of the phase.

New case shape in `mock_x_platform/`:

```
RefinementCase:
    expected_profile_id     # ground truth, stable across turns
    initial_criteria        # deliberately underspecified
    turns: [RefinementTurn] # ordered
```

Turns are **labeled by type**, because the types behave categorically differently
and blending them would hide the finding:

| Type | Meaning | Can it re-retrieve? |
| --- | --- | --- |
| `on_profile` | clue appears in the profile's bio or location | discriminate only |
| `off_profile` | clue is true of the person but absent from their X profile | **never** |
| `narrowing_name` | fuller or corrected name | yes — changes the query |
| `handle` | the user produces the handle | yes — exits the loop |

`off_profile` is first-class, not an edge case. It is the Joe Bart scenario exactly:
information the user has that the platform does not hold. Everyone's intuition says
"add more detail and it'll find them"; for this turn type that is provably false,
and the harness exists to quantify how much of real refinement lands here.

Turn content is generated from the gold profile's `truth` record, not by parsing
bios — the same choice `_evaluation_cases` already makes, so parser quality never
contaminates retrieval measurement.

Reported per turn index, split by match scope, split by turn type:
fraction visible, mean rank, monotonicity violations, profiles purchased,
unconvergeable fraction.

Mix ratios configurable with documented reasoning, deterministic under a fixed seed,
paired corpora via the two-RNG-stream pattern already used in `dataset.py`.

**Task 3 ships its report before Task 4 begins.** If the numbers contradict the
premise of Task 4 — for instance if `on_profile` clues already converge without any
extra retrieval — say so plainly and stop, the way round 3 did.

### Task 4 — Depth on demand

Conditional on Task 3's report supporting it.

- Cache key becomes `namespace:query:depth`. Refinement increments depth instead of
  hitting the same key forever, resolving the §1 tension: the cache still prevents
  re-buying profiles already seen (matching X's own 24h dedup), but no longer
  prevents seeing new ones.
- `XClient.search_users` gains `next_token` support and its hard cap of ten is
  replaced by an explicit per-session budget rather than removed.
- **Budget cap**: a documented default for distinct profiles per recipient session,
  chosen against §2's monthly envelope with the arithmetic written in a comment.
  Configurable. The device stops and offers the handle shortcut when exhausted.
- Name-variant expansion (Joe ↔ Joseph) is a second billable search, gated behind a
  saturated pool rather than run speculatively.
- **`off_profile` clues buy no extra retrieval.** Spending money on a clue that
  provably cannot change who is retrieved is wrong, and Task 3 will have proven it
  is wrong rather than us asserting it.

### Task 5 — Re-run and report

Before/after on everything that moves, under both match scopes, with the ordering
model as a sensitivity check on the headline. Plus one product output that falls
directly out of the unconvergeable fraction: a device state that says *"I can't find
them — do you know their handle?"*, which is the moment Task 1 stops being a
convenience and becomes the recovery path.

## 6. Out of scope

Deliberately excluded, each for a stated reason:

- **Ranking weights.** Finding 1 (partial-tier profiles capped at 45) stays open.
- **Tier mix ratios.** Changing them would move every baseline mid-phase.
- **The regex parser.** `parsing/recipient_parser.py` will not survive "the guy with
  the beard who used to be at Stripe", but replacing it adds latency and
  nondeterminism, and the harness feeds structured criteria directly so it does not
  need the replacement. Next phase.
- **Appearance / profile-photo matching.** Vision is a *discriminate*-only signal —
  it can raise the bearded Joe Bart within the ten we hold, never conjure the
  eleventh. Built today it would be measured against synthetic profile pictures we
  generated, inside a mock we wrote, over a fixed pool. Three layers of fiction.
  It becomes worth building once Task 4 makes the pool dynamic.
- **Existing acceptance threshold values.** New refinement-specific checks may be
  added; the round-6 values are not re-baselined here.

## 7. Risks

| Risk | Mitigation |
| --- | --- |
| Unknown A resolves to "matches bio", making Task 4 largely unnecessary | Task 0 costs $0.20 and is specified now; Task 3 reports under both scopes, so the work is not wasted either way |
| Convergence numbers are an artifact of the mock's arbitrary ordering | Ordering flag; headline reported under both models |
| Depth-on-demand ships an unbounded bill | Budget cap is part of Task 4's definition, not a follow-up; the mock's ledger makes the bill visible in tests |
| Handle detector fires on "at Meta" | Dominance rule; ambiguity resolves toward the recoverable path |
| Task 3 grows past one phase | It is the phase's deliverable; Tasks 4–5 are explicitly conditional on its report |

## 8. Testing

- Handle detector: table-driven over ASR forms, including the negatives
  ("Joe Bart at Meta", "message at Google").
- Pagination: cursor stability, no duplicate ids across pages, ledger arithmetic,
  `max_results` bounds matching X's documented 1/100/1000.
- Both mock flags: existing round-4 tests must pass unchanged under the
  `name_username` default.
- Refinement harness: determinism under fixed seed; a deliberately unconvergeable
  case must be reported as unconvergeable rather than silently failing.
- Acceptance gate: any new refinement check must be observed failing under an
  injected regression before it is trusted — the round-6 rule.
