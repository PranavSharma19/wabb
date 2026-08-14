from models.candidate import Candidate
from models.criteria import RecipientCriteria
from ranking.ranker import rank_candidates, score_candidate


def test_scoring_applies_all_documented_weights() -> None:
    criteria = RecipientCriteria(
        name="John Doe", company="XYZ", role="Recruiter", location="Toronto"
    )
    candidate = Candidate(
        id="1",
        name="john doe",
        username="johndoe",
        bio="Recruiter at XYZ",
        location="Toronto, ON",
        can_dm=True,
    )

    result = score_candidate(candidate, criteria)

    assert result.score == 100
    assert result.match_reasons == [
        "+40 Exact name",
        "+30 Company found in profile",
        "+15 Role found in bio",
        "+10 Location match",
        "+5 Can receive DM",
    ]
    assert candidate.score == 0  # Ranking does not mutate cached/source candidates.


def test_candidates_sort_by_score_then_stable_identity() -> None:
    criteria = RecipientCriteria(name="John Doe", location="Toronto")
    candidates = [
        Candidate(id="2", name="John Doe", username="zulu", location="Toronto"),
        Candidate(id="3", name="Someone Else", username="other"),
        Candidate(id="1", name="John Doe", username="alpha", location="Toronto"),
    ]

    ranked = rank_candidates(candidates, criteria)

    assert [item.username for item in ranked] == ["alpha", "zulu", "other"]
