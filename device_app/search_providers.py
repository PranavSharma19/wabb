from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from models.candidate import Candidate
from models.criteria import RecipientCriteria

from .search_contract import ProfileCandidate, SearchProvider, SearchResult


def criteria_query(criteria: RecipientCriteria) -> str:
    parts = (
        criteria.name,
        criteria.company,
        criteria.role,
        criteria.location,
        criteria.school,
        *criteria.extra_clues,
    )
    return " ".join(part.strip() for part in parts if part.strip())


class ExistingSearchAdapter:
    """Adapts the repo's existing finder without exposing it to UI/state code."""

    def __init__(
        self,
        finder: Callable[[RecipientCriteria], list[Candidate]],
    ) -> None:
        self._finder = finder

    def search(self, criteria: RecipientCriteria) -> SearchResult:
        domain_candidates = self._finder(
            RecipientCriteria.from_dict(criteria.to_dict())
        )[:10]
        return SearchResult(
            query=criteria_query(criteria),
            candidates=tuple(_from_domain(item) for item in domain_candidates),
        )


class MockSearchProvider:
    """Fast deterministic provider for developing the entire device offline."""

    def __init__(self, candidates: Iterable[ProfileCandidate] | None = None) -> None:
        self._candidates = tuple(candidates or MOCK_CANDIDATES)

    def search(self, criteria: RecipientCriteria) -> SearchResult:
        query = criteria_query(criteria)
        terms = _terms(query)
        ranked = sorted(
            self._candidates,
            key=lambda candidate: (
                -_mock_score(candidate, terms),
                candidate.name.casefold(),
                candidate.username.casefold(),
            ),
        )[:10]
        candidates = tuple(
            ProfileCandidate(
                **{
                    **candidate.to_dict(),
                    "score": _mock_score(candidate, terms),
                    "match_reasons": _mock_reasons(candidate, terms),
                }
            )
            for candidate in ranked
        )
        return SearchResult(query=query, candidates=candidates)


def _from_domain(candidate: Candidate) -> ProfileCandidate:
    return ProfileCandidate(
        id=candidate.id,
        name=candidate.name,
        username=candidate.username.lstrip("@"),
        bio=candidate.bio,
        location=candidate.location,
        profile_image_url=candidate.profile_image_url,
        profile_url=f"https://x.com/{candidate.username.lstrip('@')}",
        verified=candidate.verified,
        can_dm=candidate.can_dm,
        score=float(candidate.score),
        match_reasons=tuple(candidate.match_reasons),
    )


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _searchable(candidate: ProfileCandidate) -> set[str]:
    return _terms(
        " ".join(
            (
                candidate.name,
                candidate.username,
                candidate.company,
                candidate.bio,
                candidate.location,
            )
        )
    )


def _mock_score(candidate: ProfileCandidate, terms: set[str]) -> float:
    if not terms:
        return candidate.score
    matched = terms & _searchable(candidate)
    return round(100.0 * len(matched) / len(terms), 1)


def _mock_reasons(candidate: ProfileCandidate, terms: set[str]) -> tuple[str, ...]:
    matched = sorted(terms & _searchable(candidate))
    if not matched:
        return ("Mock candidate",)
    return (f"Matched: {', '.join(matched[:5])}",)


MOCK_CANDIDATES: tuple[ProfileCandidate, ...] = (
    ProfileCandidate(
        id="1000001",
        name="John Doe",
        username="johndoe_xyz",
        company="XYZ",
        bio="Technical recruiter at XYZ. University of Toronto alumnus.",
        location="Toronto, Canada",
        profile_image_url="mock://avatar/john-doe-xyz",
        profile_url="https://x.com/johndoe_xyz",
        verified=True,
        can_dm=True,
        score=98,
    ),
    ProfileCandidate(
        id="1000002",
        name="John Doe",
        username="johnbuilds",
        company="ABC Labs",
        bio="Product engineer building developer tools and open-source software.",
        location="Toronto, Canada",
        profile_image_url="mock://avatar/john-builds",
        profile_url="https://x.com/johnbuilds",
        can_dm=False,
        score=92,
    ),
    ProfileCandidate(
        id="1000003",
        name="John A. Doe",
        username="jdoe_recruits",
        company="Northstar Talent",
        bio="Recruiter for product and engineering teams. McGill graduate.",
        location="Montreal, Canada",
        profile_image_url="mock://avatar/jdoe-recruits",
        profile_url="https://x.com/jdoe_recruits",
        can_dm=True,
        score=88,
    ),
    ProfileCandidate(
        id="1000004",
        name="Maya Chen",
        username="mayachen",
        company="Solis",
        bio="VP Product at Solis. Previously fintech and marketplace products.",
        location="Vancouver, Canada",
        profile_image_url="mock://avatar/maya-chen",
        profile_url="https://x.com/mayachen",
        verified=True,
        can_dm=True,
        score=86,
    ),
    ProfileCandidate(
        id="1000005",
        name="Jordan Lee",
        username="jordanlee_ai",
        company="Vector North",
        bio="Machine learning researcher and University of Waterloo alumnus.",
        location="Waterloo, Canada",
        profile_image_url="mock://avatar/jordan-lee",
        profile_url="https://x.com/jordanlee_ai",
        can_dm=False,
        score=82,
    ),
    ProfileCandidate(
        id="1000006",
        name="Priya Shah",
        username="priyashah_design",
        company="Fieldnote",
        bio="Design lead creating calm tools for collaborative work.",
        location="New York, USA",
        profile_image_url="mock://avatar/priya-shah",
        profile_url="https://x.com/priyashah_design",
        can_dm=True,
        score=80,
    ),
    ProfileCandidate(
        id="1000007",
        name="Alex Morgan",
        username="alexmorgan_ops",
        company="Cedar Systems",
        bio="Operations leader scaling early-stage climate technology companies.",
        location="Boston, USA",
        profile_image_url="mock://avatar/alex-morgan",
        profile_url="https://x.com/alexmorgan_ops",
        can_dm=True,
        score=78,
    ),
    ProfileCandidate(
        id="1000008",
        name="Sofia Martinez",
        username="sofia_grows",
        company="Canvas House",
        bio="Growth marketer. Community, content, and B2B product launches.",
        location="Austin, USA",
        profile_image_url="mock://avatar/sofia-martinez",
        profile_url="https://x.com/sofia_grows",
        can_dm=False,
        score=76,
    ),
    ProfileCandidate(
        id="1000009",
        name="Daniel Kim",
        username="danielkim_data",
        company="Maple Bank",
        bio="Data platform manager in financial services. UBC alumnus.",
        location="Toronto, Canada",
        profile_image_url="mock://avatar/daniel-kim",
        profile_url="https://x.com/danielkim_data",
        can_dm=True,
        score=74,
    ),
    ProfileCandidate(
        id="1000010",
        name="Taylor Brooks",
        username="taylorbrooks",
        company="Independent",
        bio="Founder, advisor, and occasional angel investor in SaaS.",
        location="San Francisco, USA",
        profile_image_url="mock://avatar/taylor-brooks",
        profile_url="https://x.com/taylorbrooks",
        verified=True,
        can_dm=True,
        score=72,
    ),
)


__all__ = [
    "ExistingSearchAdapter",
    "MockSearchProvider",
    "SearchProvider",
    "criteria_query",
]
