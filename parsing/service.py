from __future__ import annotations

from typing import Protocol

from models.criteria import RecipientCriteria
from parsing.recipient_parser import apply_spoken_refinement, parse_recipient_description


class RecipientParser(Protocol):
    def parse(self, text: str) -> RecipientCriteria: ...
    def refine(self, existing: RecipientCriteria, text: str) -> RecipientCriteria: ...


class RuleBasedRecipientParser:
    """Current deterministic parser behind a cloud-replaceable interface."""

    def parse(self, text: str) -> RecipientCriteria:
        return parse_recipient_description(text)

    def refine(self, existing: RecipientCriteria, text: str) -> RecipientCriteria:
        return apply_spoken_refinement(existing, text)
