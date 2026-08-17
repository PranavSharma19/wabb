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
