from __future__ import annotations

import re
import unicodedata


_PHRASE_ALIASES = {
    "u of t": "university of toronto",
    "u oft": "university of toronto",
    "uoft": "university of toronto",
    "univ of toronto": "university of toronto",
}
_COMPANY_SUFFIXES = {"co", "company", "corp", "corporation", "inc", "incorporated", "llc", "ltd", "limited"}


def normalize_text(value: str) -> str:
    """Case-fold text, remove accents/punctuation, and collapse whitespace."""

    ascii_text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9]+", " ", ascii_text.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    # Expand aliases inside longer bios, not only when the entire value is an alias.
    for alias, canonical in sorted(_PHRASE_ALIASES.items(), key=lambda item: -len(item[0])):
        normalized = re.sub(rf"\b{re.escape(alias)}\b", canonical, normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_name(value: str) -> str:
    return normalize_text(value)


def _tokens(value: str) -> list[str]:
    return normalize_text(value).split()


def acronym(value: str) -> str:
    words = [word for word in _tokens(value) if word not in _COMPANY_SUFFIXES and word not in {"of", "the", "and"}]
    return "".join(word[0] for word in words if word)


def phrase_matches(needle: str, haystack: str) -> bool:
    wanted = normalize_text(needle)
    target = normalize_text(haystack)
    if not wanted or not target:
        return False
    return f" {wanted} " in f" {target} "


def company_matches(company: str, text: str) -> bool:
    if phrase_matches(company, text):
        return True

    company_words = [word for word in _tokens(company) if word not in _COMPANY_SUFFIXES]
    text_words = set(_tokens(text))
    if company_words and all(word in text_words for word in company_words):
        return True

    company_acronym = acronym(company)
    # Acronym matching is useful for IBM/UofT-style names, but avoid noisy 1-char matches.
    return len(company_acronym) >= 2 and company_acronym in text_words


def location_matches(expected: str, profile_location: str, bio: str = "") -> bool:
    if not expected:
        return False
    return phrase_matches(expected, profile_location) or phrase_matches(expected, bio)


def school_matches(school: str, text: str) -> bool:
    return company_matches(school, text)
