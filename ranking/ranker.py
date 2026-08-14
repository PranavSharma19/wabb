from __future__ import annotations

from difflib import SequenceMatcher

from models.candidate import Candidate
from models.criteria import RecipientCriteria
from ranking.normalization import (
    company_matches,
    location_matches,
    normalize_name,
    phrase_matches,
    school_matches,
)


def score_candidate(candidate: Candidate, criteria: RecipientCriteria) -> Candidate:
    """Return a scored copy using deterministic, explainable rules."""

    scored = Candidate.from_dict(candidate.to_dict())
    score = 0
    reasons: list[str] = []

    expected_name = normalize_name(criteria.name)
    actual_name = normalize_name(candidate.name)
    if expected_name and actual_name == expected_name:
        score += 40
        reasons.append("+40 Exact name")
    elif expected_name and actual_name and SequenceMatcher(None, expected_name, actual_name).ratio() >= 0.86:
        score += 20
        reasons.append("+20 Similar name")

    searchable_profile = " ".join((candidate.name, candidate.username, candidate.bio))
    if criteria.company and company_matches(criteria.company, searchable_profile):
        score += 30
        reasons.append("+30 Company found in profile")
    if criteria.role and phrase_matches(criteria.role, candidate.bio):
        score += 15
        reasons.append("+15 Role found in bio")
    if criteria.location and location_matches(criteria.location, candidate.location, candidate.bio):
        score += 10
        reasons.append("+10 Location match")
    if criteria.school and school_matches(criteria.school, candidate.bio):
        score += 15
        reasons.append("+15 School found in bio")
    if candidate.can_dm is True:
        score += 5
        reasons.append("+5 Can receive DM")

    scored.score = score
    scored.match_reasons = reasons
    return scored


def rank_candidates(candidates: list[Candidate], criteria: RecipientCriteria) -> list[Candidate]:
    scored = [score_candidate(candidate, criteria) for candidate in candidates]
    return sorted(
        scored,
        key=lambda item: (-item.score, normalize_name(item.name), item.username.casefold(), item.id),
    )
