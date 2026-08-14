from main import confirm_criteria
from models.criteria import RecipientCriteria


def answers(*values: str):
    responses = iter(values)
    return lambda _prompt: next(responses)


def test_criteria_confirmation_can_edit_a_field_before_search() -> None:
    parsed = RecipientCriteria(name="John Doe", company="XYZ", location="Toronto")

    confirmed = confirm_criteria(parsed, input_fn=answers("e", "2", "ABC", ""))

    assert confirmed is not None
    assert confirmed.company == "ABC"
    assert confirmed.name == "John Doe"
    assert parsed.company == "XYZ"  # Cancelling/editing never mutates the parsed source.


def test_criteria_confirmation_can_clear_and_replace_extra_clues() -> None:
    parsed = RecipientCriteria(name="John Doe", extra_clues=["old clue"])

    confirmed = confirm_criteria(
        parsed,
        input_fn=answers("e", "6", "U of T, fintech", "e", "1", "-", ""),
    )

    assert confirmed is not None
    assert confirmed.name == ""
    assert confirmed.extra_clues == ["U of T", "fintech"]


def test_cancelling_confirmation_returns_none_and_preserves_input() -> None:
    parsed = RecipientCriteria(name="John Doe", company="XYZ")

    assert confirm_criteria(parsed, input_fn=answers("c")) is None
    assert parsed == RecipientCriteria(name="John Doe", company="XYZ")
