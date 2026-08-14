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


def generate_profiles(count: int = 100_000, *, seed: int = 20260814) -> Iterator[dict[str, object]]:
    if count < len(MOCK_PROFILES):
        raise ValueError(f"count must be at least {len(MOCK_PROFILES)}")
    yield from (dict(profile) for profile in MOCK_PROFILES)
    randomizer = random.Random(seed)
    for index in range(count - len(MOCK_PROFILES)):
        first = FIRST_NAMES[(index * 17 + randomizer.randrange(len(FIRST_NAMES))) % len(FIRST_NAMES)]
        last = LAST_NAMES[(index * 23 + randomizer.randrange(len(LAST_NAMES))) % len(LAST_NAMES)]
        company = COMPANIES[(index * 7 + randomizer.randrange(len(COMPANIES))) % len(COMPANIES)]
        role = ROLES[(index * 11 + randomizer.randrange(len(ROLES))) % len(ROLES)]
        location = LOCATIONS[(index * 5 + randomizer.randrange(len(LOCATIONS))) % len(LOCATIONS)]
        school = SCHOOLS[(index * 3 + randomizer.randrange(len(SCHOOLS))) % len(SCHOOLS)]
        serial = index + 1
        yield {
            "id": str(2_000_000 + serial),
            "name": f"{first} {last}",
            "username": f"{first}_{last}_{serial}".casefold(),
            "description": f"{role} at {company}. {school} alum. Building useful things.",
            "location": location,
            "profile_image_url": f"https://example.com/generated/{serial}.jpg",
            "verified": serial % 37 == 0,
            "receives_your_dm": serial % 4 != 0,
        }


def build_dataset(path: str | Path, *, count: int = 100_000, seed: int = 20260814) -> int:
    profiles = list(generate_profiles(count, seed=seed))
    store = MockXStore(path)
    store.replace_profiles(profiles)
    store.replace_evaluation_cases(_evaluation_cases(profiles))
    store.replace_dataset_metadata(
        {"generator_version": 1, "profile_count": count, "seed": seed}
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
        bio_parts = str(profile["description"]).split(". ")
        role, _, company = bio_parts[0].partition(" at ")
        school = bio_parts[1].removesuffix(" alum") if len(bio_parts) > 1 else ""
        criteria = {
            "name": profile["name"],
            "company": company,
            "role": role,
            "location": profile["location"],
            "school": school,
            "extra_clues": [],
        }
        cases.append(
            {
                "id": case_id,
                "description": (
                    f"{profile['name']}, {role} at {company} in {profile['location']}, "
                    f"went to {school}"
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
    args = parser.parse_args()
    total = build_dataset(args.database, count=args.count, seed=args.seed)
    size_mb = Path(args.database).stat().st_size / (1024 * 1024)
    evaluations = MockXStore(args.database).evaluation_case_count()
    print(
        f"Generated {total:,} profiles and {evaluations:,} evaluation cases "
        f"at {args.database} ({size_mb:.1f} MB)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
