from models.criteria import RecipientCriteria
from parsing.recipient_parser import (
    apply_spoken_refinement,
    parse_recipient_description,
    refine_criteria,
)


def test_parses_core_example() -> None:
    criteria = parse_recipient_description("John Doe, recruiter at XYZ in Toronto")

    assert criteria.name == "John Doe"
    assert criteria.company == "XYZ"
    assert criteria.role == "Recruiter"
    assert criteria.location == "Toronto"


def test_refinement_preserves_existing_context_and_adds_new_fields() -> None:
    existing = RecipientCriteria(name="John Doe", company="XYZ")

    refined = refine_criteria(existing, "He lives in Toronto and went to U of T")

    assert refined.name == "John Doe"
    assert refined.company == "XYZ"
    assert refined.location == "Toronto"
    assert refined.school == "University of Toronto"


def test_spoken_refinement_updates_and_clears_targeted_fields() -> None:
    existing = RecipientCriteria(
        name="John Doe", company="XYZ", location="Toronto", school="McGill"
    )

    changed = apply_spoken_refinement(existing, "change company to ABC")
    relocated = apply_spoken_refinement(changed, "location is Montreal")
    cleared = apply_spoken_refinement(relocated, "remove the school")

    assert cleared.name == "John Doe"
    assert cleared.company == "ABC"
    assert cleared.location == "Montreal"
    assert cleared.school == ""
    assert existing.company == "XYZ"


def test_spoken_refinement_falls_back_to_additive_parser() -> None:
    existing = RecipientCriteria(name="John Doe", company="XYZ")

    refined = apply_spoken_refinement(existing, "also went to U of T")

    assert refined.school == "University of Toronto"
