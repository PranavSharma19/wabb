from __future__ import annotations

import pytest

from device_app.search_contract import ProfileCandidate, SearchResult
from device_app.search_providers import ExistingSearchAdapter, MockSearchProvider
from models.candidate import Candidate
from models.criteria import RecipientCriteria


def test_search_result_schema_round_trips_as_plain_data() -> None:
    original = SearchResult(
        query="John Doe XYZ Toronto",
        candidates=(
            ProfileCandidate(
                id="1",
                name="John Doe",
                username="john",
                company="XYZ",
                bio="Recruiter at XYZ",
                profile_image_url="https://example.test/john.png",
                profile_url="https://x.com/john",
                score=91.5,
                match_reasons=("Matched name",),
            ),
        ),
    )

    assert SearchResult.from_dict(original.to_dict()) == original


def test_contract_rejects_more_than_ten_candidates() -> None:
    candidates = tuple(
        ProfileCandidate(id=str(index), name=f"Person {index}", username=f"person{index}")
        for index in range(11)
    )
    with pytest.raises(ValueError, match="at most 10"):
        SearchResult(query="people", candidates=candidates)


def test_mock_provider_returns_ten_ranked_ui_ready_profiles() -> None:
    result = MockSearchProvider().search(
        RecipientCriteria(name="John Doe", company="XYZ", location="Toronto")
    )

    assert result.schema_version == 1
    assert len(result.candidates) == 10
    assert result.candidates[0].username == "johndoe_xyz"
    assert result.candidates[0].company == "XYZ"
    assert result.candidates[0].profile_image_url


def test_existing_finder_adapter_converts_domain_candidates() -> None:
    adapter = ExistingSearchAdapter(
        lambda _criteria: [
            Candidate(
                id="x-1",
                name="Maya Chen",
                username="maya",
                bio="VP Product",
                score=88,
            )
        ]
    )

    result = adapter.search(RecipientCriteria(name="Maya Chen"))

    assert result.query == "Maya Chen"
    assert result.candidates[0].id == "x-1"
    assert result.candidates[0].profile_url == "https://x.com/maya"


def test_the_readme_documents_every_method_the_protocol_requires() -> None:
    # A provider written from the README is a provider the device calls lookup()
    # on the moment the user presses the handle button (jobs.WorkflowRunner
    # ._run_handle_lookup). A README that documents only search() therefore
    # ships an AttributeError to anyone who follows it.
    import re
    from pathlib import Path

    from device_app.search_contract import SearchProvider

    required = {name for name in vars(SearchProvider) if not name.startswith("_")}
    assert required == {"search", "lookup"}

    readme = (
        Path(__file__).resolve().parents[1] / "device_app" / "README.md"
    ).read_text(encoding="utf-8")
    documented = next(
        block
        for block in re.findall(r"```python\n(.*?)```", readme, re.DOTALL)
        if "class SearchProvider(Protocol)" in block
    )

    for name in sorted(required):
        assert f"def {name}(" in documented, f"README omits SearchProvider.{name}"
