from ranking.normalization import company_matches, location_matches, normalize_name, school_matches


def test_name_normalization_ignores_case_accents_punctuation_and_whitespace() -> None:
    assert normalize_name("  JÓHN D.  DOE!! ") == "john d doe"


def test_company_matching_handles_suffixes_and_acronyms() -> None:
    assert company_matches("Acme, Inc.", "Recruiter at ACME")
    assert company_matches("International Business Machines", "Engineer at IBM")


def test_school_aliases_are_related() -> None:
    assert school_matches("University of Toronto", "Proud UofT graduate")
    assert school_matches("U of T", "University of Toronto alumna")


def test_location_matching_uses_profile_location_or_bio() -> None:
    assert location_matches("Toronto", "Toronto, Ontario")
    assert location_matches("Toronto", "", "Recruiter based in Toronto")
    assert not location_matches("Toronto", "Vancouver", "Recruiter")
