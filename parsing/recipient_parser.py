from __future__ import annotations

import re

from models.criteria import RecipientCriteria


_ROLE_WORDS = (
    "recruiter",
    "talent acquisition",
    "software engineer",
    "engineer",
    "developer",
    "designer",
    "product manager",
    "manager",
    "founder",
    "ceo",
    "cto",
    "director",
    "professor",
    "researcher",
    "journalist",
)

_SCHOOL_ALIASES = {
    "u of t": "University of Toronto",
    "uoft": "University of Toronto",
    "university of toronto": "University of Toronto",
}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,.;:-")


def _canonical_school(value: str) -> str:
    cleaned = _clean(value)
    return _SCHOOL_ALIASES.get(cleaned.casefold().replace(".", ""), cleaned)


def parse_recipient_description(text: str) -> RecipientCriteria:
    """Parse common outreach descriptions behind an AI-replaceable boundary.

    This intentionally conservative parser handles the prototype's common forms.
    Unknown clauses are retained as extra clues rather than silently discarded.
    """

    original = _clean(text)
    if not original:
        return RecipientCriteria()

    result = RecipientCriteria()
    working = original

    # A leading comma-delimited phrase is normally the person's name.
    if "," in working:
        leading, working = working.split(",", 1)
        if not re.search(r"\b(?:lives?|located|went|studied|works?)\b", leading, re.I):
            result.name = _clean(leading)
        working = _clean(working)

    school_match = re.search(
        r"\b(?:went to|studied at|attended|alumn(?:us|a) of)\s+(.+?)(?=\s+(?:and|but)\s+|$)",
        working,
        re.I,
    )
    if school_match:
        result.school = _canonical_school(school_match.group(1))
        working = _clean(working[: school_match.start()] + " " + working[school_match.end() :])
        working = _clean(re.sub(r"\b(?:and|but)\s*$", "", working, flags=re.I))

    location_match = re.search(
        r"\b(?:lives? in|located in|based in)\s+(.+?)(?=\s+(?:and|but|who)\s+|$)",
        working,
        re.I,
    )
    if not location_match:
        location_match = re.search(r"\bin\s+([^,]+?)(?=\s+(?:and|but|who)\s+|$)", working, re.I)
    if location_match:
        result.location = _clean(location_match.group(1))
        working = _clean(working[: location_match.start()] + " " + working[location_match.end() :])

    # "role at company" or simply "at company".
    at_match = re.search(r"(?:^|\s)(.*?)\s+at\s+(.+?)(?=\s+(?:and|but|who)\s+|$)", working, re.I)
    if at_match:
        before = _clean(at_match.group(1))
        result.company = _clean(at_match.group(2))
        role = next((item for item in _ROLE_WORDS if item in before.casefold()), "")
        if role:
            result.role = role.title() if len(role) > 3 else role.upper()
            possible_name = _clean(re.sub(re.escape(role), "", before, flags=re.I))
        else:
            possible_name = before
        if not result.name and possible_name and not re.match(r"^(?:he|she|they)\b", possible_name, re.I):
            result.name = possible_name
        working = _clean(working[: at_match.start()] + " " + working[at_match.end() :])
    else:
        role = next((item for item in _ROLE_WORDS if re.search(rf"\b{re.escape(item)}\b", working, re.I)), "")
        if role:
            result.role = role.title() if len(role) > 3 else role.upper()
            working = _clean(re.sub(rf"\b{re.escape(role)}\b", "", working, flags=re.I))

    if not result.name:
        possible_name = re.split(r"\b(?:who|works?|lives?|located|based|went|studied)\b", working, 1, flags=re.I)[0]
        possible_name = _clean(re.sub(r"^(?:find|looking for)\s+", "", possible_name, flags=re.I))
        if possible_name and not re.match(r"^(?:he|she|they)\b", possible_name, re.I):
            result.name = possible_name

    # Preserve meaningful unmatched text so an AI parser can use it later.
    residue = _clean(working)
    residue = _clean(re.sub(r"\b(?:he|she|they|and|who|also)\b", " ", residue, flags=re.I))
    if residue and residue.casefold() not in {result.name.casefold(), result.role.casefold()}:
        result.extra_clues.append(residue)

    return result


def refine_criteria(existing_criteria: RecipientCriteria, new_information: str) -> RecipientCriteria:
    """Add newly parsed information without deleting existing non-empty clues."""

    new = parse_recipient_description(new_information)
    merged = RecipientCriteria.from_dict(existing_criteria.to_dict())
    for field_name in ("name", "company", "role", "location", "school"):
        value = getattr(new, field_name)
        if value:
            setattr(merged, field_name, value)

    seen = {item.casefold() for item in merged.extra_clues}
    for clue in new.extra_clues:
        if clue.casefold() not in seen:
            merged.extra_clues.append(clue)
            seen.add(clue.casefold())
    return merged


_REFINEMENT_FIELDS = {
    "name": "name",
    "company": "company",
    "employer": "company",
    "role": "role",
    "job": "role",
    "title": "role",
    "location": "location",
    "city": "location",
    "school": "school",
    "university": "school",
    "college": "school",
}


def apply_spoken_refinement(
    existing_criteria: RecipientCriteria,
    spoken_information: str,
) -> RecipientCriteria:
    """Apply a targeted spoken correction, falling back to additive parsing."""

    text = _clean(spoken_information)
    updated = RecipientCriteria.from_dict(existing_criteria.to_dict())
    if not text:
        return updated

    field_words = "|".join(sorted(_REFINEMENT_FIELDS, key=len, reverse=True))
    clear_match = re.fullmatch(
        rf"(?:please\s+)?(?:remove|clear|delete)\s+(?:the\s+)?(?P<field>{field_words})",
        text,
        re.I,
    )
    if clear_match:
        setattr(updated, _REFINEMENT_FIELDS[clear_match.group("field").casefold()], "")
        return updated

    update_match = re.fullmatch(
        rf"(?:please\s+)?(?:(?:change|set|update)\s+)?(?:the\s+)?"
        rf"(?P<field>{field_words})\s+(?:is|to|as)\s+(?P<value>.+)",
        text,
        re.I,
    )
    if update_match:
        attribute = _REFINEMENT_FIELDS[update_match.group("field").casefold()]
        value = _clean(update_match.group("value"))
        if attribute == "school":
            value = _canonical_school(value)
        setattr(updated, attribute, value)
        return updated

    return refine_criteria(updated, text)
