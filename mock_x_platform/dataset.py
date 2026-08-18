from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Iterator

from config import load_settings
from data.mock_profiles import MOCK_PROFILES

from .store import MockXStore


FIRST_NAMES = (
    "Alex", "Amir", "Ana", "Avery", "Ben", "Chloe", "Daniel", "David", "Elena",
    "Emily", "Emma", "Ethan", "Fatima", "Grace", "Hannah", "Isabella", "Jack",
    "James", "Jason", "Jessica", "Jordan", "Joseph", "Julia", "Kevin", "Laura",
    "Liam", "Lucas", "Maya", "Michael", "Nadia", "Nathan", "Noah", "Olivia",
    "Omar", "Priya", "Rachel", "Ryan", "Sam", "Sara", "Sophia", "Taylor",
)
LAST_NAMES = (
    "Adams", "Ali", "Anderson", "Baker", "Brown", "Campbell", "Carter", "Chen",
    "Clark", "Davis", "Evans", "Garcia", "Green", "Hall", "Harris", "Jackson",
    "Johnson", "Jones", "Khan", "Kim", "Lee", "Lewis", "Martin", "Martinez",
    "Miller", "Mitchell", "Moore", "Nguyen", "Patel", "Robinson", "Rodriguez",
    "Scott", "Singh", "Smith", "Taylor", "Thomas", "Thompson", "Walker", "White",
    "Williams", "Wilson", "Wong", "Young",
)
COMPANIES = (
    "Acme", "Apex Systems", "Bluebird", "Cedar Labs", "Cloudworks", "Evergreen",
    "Globex", "Harbour Tech", "Initech", "Maple Health", "Northstar Labs", "Nova",
    "Orbit", "Pioneer", "Quantum Works", "Redwood", "Summit Group", "Vertex",
    "Wayfinder", "XYZ",
)
ROLES = (
    "Account Executive", "Analyst", "Consultant", "Designer", "Engineer", "Founder",
    "Marketing Manager", "Operations Lead", "Product Manager", "Recruiter",
    "Researcher", "Sales Director", "Software Engineer", "Talent Partner",
)
LOCATIONS = (
    "Austin, TX", "Boston, MA", "Calgary, Alberta", "Chicago, IL", "London, UK",
    "Los Angeles, CA", "Montreal, Quebec", "New York, NY", "Ottawa, Ontario",
    "San Francisco, CA", "Seattle, WA", "Toronto, Ontario", "Vancouver, BC",
    "Waterloo, Ontario",
)
SCHOOLS = (
    "McGill University", "Queen's University", "University of British Columbia",
    "University of Toronto", "University of Waterloo", "Western University",
    "York University",
)

GENERATOR_VERSION = 3

# Follower counts are heavy-tailed: most accounts have a few hundred, a handful
# have millions, and nothing about them is normally distributed. Log-uniform over
# 10^0.5 to 10^5.5 (roughly 3 to 320,000) reproduces the property Unknown B turns
# on -- that a member of technical staff is systematically outranked by a
# personality with the same name -- without pretending to be a measured census.
FOLLOWER_LOG_RANGE = (0.5, 5.5)

# The hand-written fixtures get a fixed, unremarkable count so they neither lead
# nor trail under follower_weighted ordering.
FIXTURE_FOLLOWER_COUNT = 1_000

TIER_NAMES = ("clean", "partial", "decorated", "handle_name")

# Ratios are estimates, not measurements -- we have no ground-truth census of X
# profiles. They are chosen so the dataset stresses the pipeline without hiding
# regressions:
#   clean 0.40      still the plurality, so a drop caused by something other than
#                   dirt stays visible in the tier that has no dirt.
#   partial 0.30    empty bio / missing location is the most common real gap and
#                   it is the tier that exercises the scoring ceiling (a profile
#                   with no bio cannot earn company/role/school points at all).
#   decorated 0.20  emoji, pronouns and " | hiring @ X" suffixes are routine on
#                   professional accounts; the real name is still present, so
#                   this isolates name-noise from missing-data.
#   handle_name 0.10 smallest because a genuinely unfindable display name is the
#                   rarest case, but 10% keeps the per-tier sample large enough
#                   (~100 cases per 1,000) to state a retrieval ceiling to the
#                   nearest percent.
# Override with `--tier-mix` / the `tier_mix` argument when you want a harsher or
# gentler corpus; the mix used is recorded in dataset_metadata.
DEFAULT_TIER_MIX: dict[str, float] = {
    "clean": 0.40,
    "partial": 0.30,
    "decorated": 0.20,
    "handle_name": 0.10,
}

# DM eligibility is frequently absent from the X user object, so "unknown" has to
# be a common outcome rather than a rounding error; the ranker treats only an
# explicit True as evidence.
CAN_DM_WEIGHTS: tuple[tuple[bool | None, float], ...] = (
    (True, 0.45),
    (None, 0.35),
    (False, 0.20),
)

NON_LITERAL_LOCATIONS = (
    "the internet", "everywhere", "remote", "planet earth", "/dev/null",
)
THIN_BIOS = (
    "",
    "she/her",
    "he/him",
    "opinions my own",
    "Building useful things.",
    "dm open",
)
DECORATIONS = (
    "{emoji} {name}",
    "{name} {pronoun}",
    "{name} | Hiring @ {company}",
    "{emoji} {name} {emoji} | {role} @ {company}",
    "{name} — building things at {company}",
    "{name} {emoji} {pronoun}",
)
EMOJI = ("\N{SPARKLES}", "\N{ROCKET}", "\N{FIRE}", "\N{SEEDLING}", "\N{GLOWING STAR}")
PRONOUNS = ("(she/her)", "(he/him)", "(they/them)")
HANDLE_WORDS = ("builds", "codes", "writes", "ships", "designs", "hires")
# Short forms that share no token with the formal first name, so a name-primary
# query cannot reach them.
NICKNAMES = {
    "Alexander": "Xander", "Alex": "Al", "Benjamin": "Benji", "Ben": "Benny",
    "Daniel": "Danny", "David": "Dave", "Elena": "Ellie", "Emily": "Em",
    "Isabella": "Bella", "Jack": "Jax", "James": "Jim", "Jessica": "Jess",
    "Jordan": "Jords", "Joseph": "Joe", "Julia": "Jules", "Kevin": "Kev",
    "Michael": "Mike", "Nathan": "Nate", "Olivia": "Liv", "Rachel": "Rach",
    "Samuel": "Sammy", "Sophia": "Soph", "Thomas": "Tom", "William": "Will",
}


def normalize_tier_mix(tier_mix: dict[str, float] | None) -> dict[str, float]:
    mix = dict(tier_mix or DEFAULT_TIER_MIX)
    unknown = set(mix) - set(TIER_NAMES)
    if unknown:
        raise ValueError(f"Unknown dirt tiers: {', '.join(sorted(unknown))}")
    if any(value < 0 for value in mix.values()):
        raise ValueError("Tier ratios must not be negative.")
    total = sum(mix.values())
    if not total:
        raise ValueError("Tier ratios must not all be zero.")
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Tier ratios must sum to 1.0 (got {total}).")
    return {tier: float(mix.get(tier, 0.0)) for tier in TIER_NAMES}


def _tier_sequence(
    total: int, tier_mix: dict[str, float], randomizer: random.Random
) -> list[str]:
    """Assign exact per-tier quotas, then shuffle, so counts are reproducible.

    Independent per-profile draws would leave the tier denominators jittering
    between runs, which makes small per-tier recall differences unreadable.
    """

    mix = normalize_tier_mix(tier_mix)
    counts = {tier: int(total * ratio) for tier, ratio in mix.items()}
    # Hand any rounding remainder to the largest tiers, largest ratio first.
    order = sorted(TIER_NAMES, key=lambda tier: (-mix[tier], tier))
    for position in range(total - sum(counts.values())):
        counts[order[position % len(order)]] += 1
    sequence = [tier for tier in TIER_NAMES for _ in range(counts[tier])]
    randomizer.shuffle(sequence)
    return sequence


def _render_profile(
    tier: str,
    truth: dict[str, str],
    serial: int,
    randomizer: random.Random,
) -> dict[str, object]:
    """Turn one true person into the profile a dirty X account would show."""

    first, last = truth["first"], truth["last"]
    company, role, school = truth["company"], truth["role"], truth["school"]
    clean_bio = f"{role} at {company}. {school} alum. Building useful things."
    name = f"{first} {last}"
    username = f"{first}_{last}_{serial}".casefold()
    description = clean_bio
    location = truth["location"]

    if tier == "partial":
        # The bio is always degraded; the location is empty half the time and
        # non-literal the other half. A quarter of the tier ends up with both
        # empty, which is the shape that caps the achievable ranking score.
        bio_shape, location_shape = randomizer.choice(
            (("thin", "empty"), ("thin", "non_literal"), ("empty", "empty"), ("empty", "non_literal"))
        )
        description = randomizer.choice(THIN_BIOS) if bio_shape == "thin" else ""
        location = (
            randomizer.choice(NON_LITERAL_LOCATIONS) if location_shape == "non_literal" else ""
        )
    elif tier == "decorated":
        # Real name kept intact, wrapped in the noise X users actually add.
        name = randomizer.choice(DECORATIONS).format(
            name=name,
            emoji=randomizer.choice(EMOJI),
            pronoun=randomizer.choice(PRONOUNS),
            company=company,
            role=role,
        )
    elif tier == "handle_name":
        style = randomizer.choice(("initial_last", "word_handle", "first_only", "nickname"))
        nickname = NICKNAMES.get(first)
        if style == "nickname" and not nickname:
            style = "initial_last"
        if style == "initial_last":
            handle = f"{first[0]}{last}".casefold()
            name, username = handle, f"{handle}_{serial}"
        elif style == "word_handle":
            handle = f"{first[0].casefold()}_{randomizer.choice(HANDLE_WORDS)}"
            name, username = handle, f"{handle}_{serial}"
        elif style == "first_only":
            name, username = first, f"{first}_{serial}".casefold()
        else:
            name, username = f"{nickname} {last}", f"{nickname}_{last}_{serial}".casefold()
        # A handle-only account does not spell its owner's real name in the bio.
        description = f"{role} at {company}. {school} alum."

    return {
        "id": str(2_000_000 + serial),
        "name": name,
        "username": username,
        "description": description,
        "location": location,
        "profile_image_url": f"https://example.com/generated/{serial}.jpg",
        "verified": serial % 37 == 0,
        "receives_your_dm": _weighted_choice(CAN_DM_WEIGHTS, randomizer),
        "tier": tier,
        "truth": truth,
    }


def _weighted_choice(
    options: tuple[tuple[bool | None, float], ...], randomizer: random.Random
) -> bool | None:
    threshold = randomizer.random() * sum(weight for _, weight in options)
    cumulative = 0.0
    for value, weight in options:
        cumulative += weight
        if threshold < cumulative:
            return value
    return options[-1][0]


def generate_profiles(
    count: int = 100_000,
    *,
    seed: int = 20260814,
    tier_mix: dict[str, float] | None = None,
) -> Iterator[dict[str, object]]:
    if count < len(MOCK_PROFILES):
        raise ValueError(f"count must be at least {len(MOCK_PROFILES)}")
    # The hand-written fixtures are complete profiles, so they are the clean tier.
    yield from (
        dict(profile, tier="clean", follower_count=FIXTURE_FOLLOWER_COUNT)
        for profile in MOCK_PROFILES
    )
    # Two streams on purpose: identities come from `randomizer`, dirt from
    # `dirt`. Because tier rendering never touches the identity stream, the same
    # seed produces the same people under any tier mix, so a clean run and a
    # dirty run are a paired comparison rather than two different populations.
    randomizer = random.Random(seed)
    dirt = random.Random(seed + 1)
    # A third stream on purpose. Drawing follower counts from `dirt` would shift
    # every later dirt draw and silently change the corpus that rounds 1-6 were
    # measured against.
    popularity = random.Random(seed + 2)
    generated = count - len(MOCK_PROFILES)
    tiers = _tier_sequence(generated, tier_mix, dirt)
    for index in range(generated):
        first = FIRST_NAMES[(index * 17 + randomizer.randrange(len(FIRST_NAMES))) % len(FIRST_NAMES)]
        last = LAST_NAMES[(index * 23 + randomizer.randrange(len(LAST_NAMES))) % len(LAST_NAMES)]
        company = COMPANIES[(index * 7 + randomizer.randrange(len(COMPANIES))) % len(COMPANIES)]
        role = ROLES[(index * 11 + randomizer.randrange(len(ROLES))) % len(ROLES)]
        location = LOCATIONS[(index * 5 + randomizer.randrange(len(LOCATIONS))) % len(LOCATIONS)]
        school = SCHOOLS[(index * 3 + randomizer.randrange(len(SCHOOLS))) % len(SCHOOLS)]
        # The truth travels with the profile: ground truth is the person, not the
        # account, so it stays identical no matter which tier renders it.
        truth = {
            "first": first,
            "last": last,
            "name": f"{first} {last}",
            "company": company,
            "role": role,
            "location": location,
            "school": school,
        }
        profile = _render_profile(tiers[index], truth, index + 1, dirt)
        profile["follower_count"] = int(10 ** popularity.uniform(*FOLLOWER_LOG_RANGE))
        yield profile


def build_dataset(
    path: str | Path,
    *,
    count: int = 100_000,
    seed: int = 20260814,
    tier_mix: dict[str, float] | None = None,
) -> int:
    mix = normalize_tier_mix(tier_mix)
    profiles = list(generate_profiles(count, seed=seed, tier_mix=mix))
    store = MockXStore(path)
    store.replace_profiles(profiles)
    store.replace_evaluation_cases(_evaluation_cases(profiles))
    store.replace_dataset_metadata(
        {
            "generator_version": GENERATOR_VERSION,
            "profile_count": count,
            "seed": seed,
            "tier_mix": json.dumps(mix, sort_keys=True),
        }
    )
    return store.profile_count()


def _evaluation_cases(profiles: list[dict[str, object]]) -> list[dict[str, object]]:
    generated = profiles[len(MOCK_PROFILES) :]
    if not generated:
        return []
    target_count = min(1_000, len(generated))
    step = max(1, len(generated) // target_count)
    selected = generated[::step][:target_count]
    cases: list[dict[str, object]] = []
    for case_id, profile in enumerate(selected, start=1):
        # Criteria describe what the sender knows about the person. That does not
        # shrink when the account hides it, so the case is built from the truth
        # record and is identical whichever tier rendered the profile.
        truth = dict(profile["truth"])  # type: ignore[arg-type]
        criteria = {
            "name": truth["name"],
            "company": truth["company"],
            "role": truth["role"],
            "location": truth["location"],
            "school": truth["school"],
            "extra_clues": [],
        }
        cases.append(
            {
                "id": case_id,
                "description": (
                    f"{truth['name']}, {truth['role']} at {truth['company']} "
                    f"in {truth['location']}, went to {truth['school']}"
                ),
                "criteria_json": json.dumps(criteria, ensure_ascii=False, sort_keys=True),
                "expected_profile_id": profile["id"],
            }
        )
    return cases


def main() -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Generate the synthetic Mock X profile dataset")
    parser.add_argument("--database", default=str(settings.mock_x_database_path))
    parser.add_argument("--count", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=20260814)
    parser.add_argument(
        "--tier-mix",
        default="",
        help=(
            'JSON ratios per dirt tier, e.g. \'{"clean": 0.4, "partial": 0.3, '
            '"decorated": 0.2, "handle_name": 0.1}\'. Must sum to 1.0.'
        ),
    )
    args = parser.parse_args()
    tier_mix = json.loads(args.tier_mix) if args.tier_mix else None
    total = build_dataset(
        args.database, count=args.count, seed=args.seed, tier_mix=tier_mix
    )
    size_mb = Path(args.database).stat().st_size / (1024 * 1024)
    store = MockXStore(args.database)
    evaluations = store.evaluation_case_count()
    tiers = ", ".join(f"{tier}={count:,}" for tier, count in store.tier_counts().items())
    print(
        f"Generated {total:,} profiles and {evaluations:,} evaluation cases "
        f"at {args.database} ({size_mb:.1f} MB)."
    )
    print(f"Dirt tiers: {tiers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
