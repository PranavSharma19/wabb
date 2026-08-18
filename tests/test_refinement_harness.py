from __future__ import annotations

import json

import pytest

from mock_x_platform.refinement import build_refinement_cases
from mock_x_platform.store import MockXStore


def _store(tmp_path, *, description: str, location: str) -> MockXStore:
    store = MockXStore(tmp_path / "x.sqlite3")
    store.replace_profiles(
        [
            {
                "id": "9001",
                "name": "Joe Bart",
                "username": "joebart9001",
                "description": description,
                "location": location,
                "profile_image_url": "",
                "verified": False,
                "receives_your_dm": True,
                "tier": "clean",
                "follower_count": 10,
            }
        ]
    )
    store.replace_evaluation_cases(
        [
            {
                "id": 1,
                "description": "Joe Bart",
                "criteria_json": json.dumps(
                    {
                        "name": "Joe Bart",
                        "company": "Meta",
                        "role": "Engineer",
                        "location": "San Francisco, CA",
                        "school": "McGill University",
                        "extra_clues": [],
                    }
                ),
                "expected_profile_id": "9001",
            }
        ]
    )
    return store


def test_a_clue_the_profile_carries_is_labeled_on_profile(tmp_path) -> None:
    store = _store(
        tmp_path,
        description="Engineer at Meta. McGill University alum.",
        location="San Francisco, CA",
    )

    case = build_refinement_cases(store)[0]
    types = {turn.field: turn.type for turn in case.turns}

    assert types["role"] == "on_profile"
    assert types["school"] == "on_profile"
    assert types["location"] == "on_profile"


def test_a_clue_the_profile_does_not_carry_is_labeled_off_profile(tmp_path) -> None:
    # The partial tier: no bio, no location. Everything the sender knows about
    # this person is true and invisible to the platform.
    store = _store(tmp_path, description="", location="")

    case = build_refinement_cases(store)[0]

    assert {turn.type for turn in case.turns if turn.field in {"role", "school", "location"}} == {
        "off_profile"
    }


def test_every_ladder_ends_with_the_handle(tmp_path) -> None:
    store = _store(tmp_path, description="", location="")

    case = build_refinement_cases(store)[0]

    assert case.turns[-1].type == "handle"
    assert case.turns[-1].value == "joebart9001"


def test_the_initial_criteria_are_deliberately_underspecified(tmp_path) -> None:
    store = _store(tmp_path, description="", location="")

    case = build_refinement_cases(store)[0]

    assert case.initial_criteria.name == "Joe Bart"
    assert case.initial_criteria.company == "Meta"
    assert case.initial_criteria.role == ""


def test_a_correctable_name_earns_a_narrowing_turn(tmp_path) -> None:
    store = _store(tmp_path, description="", location="")
    store.replace_profiles(
        [
            {
                "id": "9001",
                "name": "Joe Bart",
                "username": "joebart9001",
                "description": "",
                "location": "",
                "profile_image_url": "",
                "verified": False,
                "receives_your_dm": True,
                "tier": "handle_name",
                "follower_count": 10,
            }
        ]
    )
    store.replace_evaluation_cases(
        [
            {
                "id": 1,
                "description": "Joseph Bart",
                "criteria_json": json.dumps(
                    {"name": "Joseph Bart", "company": "Meta", "extra_clues": []}
                ),
                "expected_profile_id": "9001",
            }
        ]
    )

    case = build_refinement_cases(store)[0]

    # "He goes by Joe, not Joseph" -- shares the surname, not the whole name.
    assert [turn.type for turn in case.turns if turn.type == "narrowing_name"] == [
        "narrowing_name"
    ]


@pytest.mark.parametrize("display_name", ["Joe Bart", "jbart"])
def test_a_name_nobody_could_guess_earns_no_narrowing_turn(
    tmp_path, display_name: str
) -> None:
    # Two exclusions, for opposite reasons. An identical display name has
    # nothing to correct. A handle-style one ("jbart") shares no token, and a
    # user able to produce that string would have used handle mode -- crediting
    # search with it would hide the population the recovery path exists for.
    store = _store(tmp_path, description="", location="")
    store.replace_profiles(
        [
            {
                "id": "9001",
                "name": display_name,
                "username": "joebart9001",
                "description": "",
                "location": "",
                "profile_image_url": "",
                "verified": False,
                "receives_your_dm": True,
                "tier": "handle_name",
                "follower_count": 10,
            }
        ]
    )

    case = build_refinement_cases(store)[0]

    assert not any(turn.type == "narrowing_name" for turn in case.turns)


def test_case_generation_is_deterministic_under_a_fixed_seed(tmp_path) -> None:
    store = _store(tmp_path, description="Engineer at Meta.", location="")

    first = build_refinement_cases(store, seed=7)
    second = build_refinement_cases(store, seed=7)

    assert [turn.field for turn in first[0].turns] == [
        turn.field for turn in second[0].turns
    ]


def test_each_turn_carries_every_clue_said_before_it(tmp_path) -> None:
    # The ladder is cumulative by definition: refinement is the user ADDING to
    # what they already said. If a turn carried only its own field, every turn
    # would be a fresh single-clue search and every convergence number this
    # harness produces would be wrong in the same direction, silently.
    store = _store(
        tmp_path,
        description="Engineer at Meta. McGill University alum.",
        location="San Francisco, CA",
    )

    case = build_refinement_cases(store, seed=7)[0]
    clue_turns = [turn for turn in case.turns if turn.field in {"role", "location", "school"}]
    assert len(clue_turns) == 3

    # The opening criteria never gain the later clues.
    assert case.initial_criteria.role == ""
    assert case.initial_criteria.location == ""
    assert case.initial_criteria.school == ""

    # Every turn keeps the name and company it started from...
    for turn in clue_turns:
        assert turn.criteria.name == "Joe Bart"
        assert turn.criteria.company == "Meta"

    # ...and the last clue turn carries all three clues at once, whatever order
    # the seed shuffled them into.
    final = clue_turns[-1].criteria
    assert final.role and final.location and final.school

    # ...while each earlier turn carries strictly fewer of them than the next.
    filled = [
        sum(bool(getattr(turn.criteria, field)) for field in ("role", "location", "school"))
        for turn in clue_turns
    ]
    assert filled == [1, 2, 3]


def test_determinism_holds_across_a_multi_case_run(tmp_path) -> None:
    # One randomizer is shared across the whole call and consumed in case order,
    # so a single-case store cannot exercise the sequencing at all.
    from mock_x_platform.dataset import build_dataset

    database = tmp_path / "corpus.sqlite3"
    build_dataset(database, count=2_000, seed=42)
    store = MockXStore(database)

    first = build_refinement_cases(store, limit=40, seed=7)
    second = build_refinement_cases(store, limit=40, seed=7)

    assert len(first) == len(second) > 1
    assert [
        [(turn.type, turn.field, turn.value) for turn in case.turns] for case in first
    ] == [
        [(turn.type, turn.field, turn.value) for turn in case.turns] for case in second
    ]


def test_off_profile_refinement_changes_nothing_at_all(tmp_path) -> None:
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.refinement import run_refinement

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=3_000, seed=42)

    summary, _, _ = run_refinement(
        database, output_directory=tmp_path / "reports", case_limit=25
    )

    # The finding this whole phase exists to establish, asserted against the code
    # as it stands. build_search_query returns the first non-empty of name,
    # company, school, role -- location is not even a fallback -- so adding a
    # clue leaves the query byte-identical and the same ten profiles come back.
    off_profile = summary["by_turn_type"]["off_profile"]
    assert off_profile["turns"] > 0
    assert off_profile["retrieval_changed"] == 0

    # The target can never rise on such a turn: scoring only ever adds points,
    # the expected profile earns none from a clue its account does not carry,
    # and competitors may earn some. So an off-profile clue is either inert or
    # actively harmful, and `worsened` is left free to be non-zero because a
    # refinement that demotes the target is a real result worth reporting.
    assert off_profile["improved"] == 0


def test_the_handle_turn_converges_where_search_cannot(tmp_path) -> None:
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.refinement import run_refinement

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=3_000, seed=42)

    summary, _, _ = run_refinement(
        database, output_directory=tmp_path / "reports", case_limit=25
    )

    assert summary["by_turn_type"]["handle"]["visible"] == 1.0


def test_an_unreachable_person_is_reported_as_unconvergeable_not_as_a_failure(
    tmp_path,
) -> None:
    from mock_x_platform.refinement import run_refinement

    # Built by hand rather than sampled, so the case is unreachable by
    # construction: the account displays "jbart" and shares no token with the
    # name the sender has. This is the handle_name tier in miniature, and no
    # ranking change, refinement turn or extra depth can ever recover it.
    store = _store(tmp_path, description="", location="")
    store.replace_profiles(
        [
            {
                "id": "9001",
                "name": "jbart",
                "username": "jbart",
                "description": "",
                "location": "",
                "profile_image_url": "",
                "verified": False,
                "receives_your_dm": True,
                "tier": "handle_name",
                "follower_count": 10,
            }
        ]
    )

    summary, _, _ = run_refinement(
        store.path, output_directory=tmp_path / "reports"
    )

    assert summary["convergence"]["structurally_unconvergeable"] == 1.0
    assert summary["convergence"]["unconvergeable_cases"] == 1
    assert summary["convergence"]["rate"] == 0.0
    # And yet the handle turn still resolves them. That contrast is the product
    # finding: past a certain amount of dirt, only the handle works.
    assert summary["by_turn_type"]["handle"]["visible"] == 1.0


def test_the_run_reports_what_it_would_have_cost(tmp_path) -> None:
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.refinement import run_refinement

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=3_000, seed=42)

    summary, _, _ = run_refinement(
        database, output_directory=tmp_path / "reports", case_limit=25
    )

    assert summary["cost"]["distinct_profiles"] > 0
    assert summary["cost"]["estimated_usd"] > 0


def test_reordering_the_same_ten_is_not_a_re_retrieval() -> None:
    # The single most load-bearing decision in the harness. rank_candidates
    # reorders the same ten profiles on every turn; counting that as
    # re-retrieval would invert the phase's central finding. The end-to-end
    # tests above cannot reliably distinguish this from a buggy list
    # comparison -- rank_candidates sorts by a deterministic total order, so a
    # wholly inert clue leaves the *ordered* list identical too, and a list
    # comparison only gets caught when a fixture happens to contain a
    # competitor the clue promotes. This test pins it directly.
    from models.criteria import RecipientCriteria

    from mock_x_platform.refinement import RefinementCase, _row

    case = RefinementCase(
        id=1,
        expected_profile_id="3",
        initial_criteria=RecipientCriteria(name="Joe Bart"),
        turns=(),
    )
    reordered = _row(
        case,
        index=1,
        turn_type="on_profile",
        field="role",
        rank=2,
        previous=5,
        top_ids=["3", "1", "2"],
        previous_ids=["1", "2", "3"],
    )
    assert reordered["retrieval_changed"] is False

    swapped = _row(
        case,
        index=1,
        turn_type="on_profile",
        field="role",
        rank=2,
        previous=5,
        top_ids=["1", "2", "4"],
        previous_ids=["1", "2", "3"],
    )
    assert swapped["retrieval_changed"] is True


def _unreachable_store(tmp_path):
    """One case whose account shares no token with the name the sender has."""

    store = _store(tmp_path, description="", location="")
    store.replace_profiles(
        [
            {
                "id": "9001",
                "name": "jbart",
                "username": "jbart",
                "description": "",
                "location": "",
                "profile_image_url": "",
                "verified": False,
                "receives_your_dm": True,
                "tier": "handle_name",
                "follower_count": 10,
            }
        ]
    )
    return store


def test_the_handle_turn_does_not_inflate_a_by_index_bucket(tmp_path) -> None:
    # The handle turn is the last turn of every case and is 100% visible by
    # construction. Bucketing it by turn_index puts a guaranteed hit into
    # whichever index it happens to land in, so the by-index series stops being
    # a picture of search refinement and becomes a picture of ladder length.
    from mock_x_platform.refinement import run_refinement

    store = _unreachable_store(tmp_path)

    summary, _, _ = run_refinement(store.path, output_directory=tmp_path / "reports")

    # Search never put this person on screen at any turn...
    assert summary["convergence"]["rate"] == 0.0
    assert {metrics["visible"] for metrics in summary["by_turn_index"].values()} == {0.0}
    # ...and the handle turn is still reported, on its own, by type.
    assert summary["by_turn_type"]["handle"]["visible"] == 1.0
    assert summary["by_turn_type"]["handle"]["turns"] == 1


def test_by_turn_index_counts_every_search_turn_and_no_handle_turn(tmp_path) -> None:
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.refinement import run_refinement

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=3_000, seed=42)

    summary, _, _ = run_refinement(
        database, output_directory=tmp_path / "reports", case_limit=25
    )

    handle_turns = summary["by_turn_type"]["handle"]["turns"]
    assert handle_turns == summary["case_count"]
    indexed = sum(metrics["turns"] for metrics in summary["by_turn_index"].values())
    # Every row except the handle rows: turn 0 for each case, plus every
    # refinement turn, minus the one handle turn each case ends on.
    assert indexed == summary["case_count"] + summary["turn_count"] - handle_turns


def test_the_run_reports_profiles_purchased_per_convergence(tmp_path) -> None:
    # Spec 4 names profiles purchased per convergence as a primary metric: the
    # per-case figure divides by cases that never converged as well, so it
    # cannot answer "what does one found person cost".
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.refinement import run_refinement

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=3_000, seed=42)

    summary, _, _ = run_refinement(
        database, output_directory=tmp_path / "reports", case_limit=25
    )
    convergence = summary["convergence"]

    per_case = convergence["marginal_profiles_purchased_per_case"]
    per_convergence = convergence["marginal_profiles_purchased_per_convergence"]
    converged = round(convergence["rate"] * summary["case_count"])
    assert converged > 0
    assert per_convergence == pytest.approx(
        per_case * summary["case_count"] / converged, rel=1e-3
    )
    # The per-case key stays: it is the one that divides by the whole run.
    assert per_case > 0


def test_profiles_purchased_per_convergence_is_none_when_nothing_converged(
    tmp_path,
) -> None:
    # Not 0.0. Zero purchases per convergence would read as "free", where the
    # truth is that the denominator does not exist.
    from mock_x_platform.refinement import run_refinement

    store = _unreachable_store(tmp_path)

    summary, _, _ = run_refinement(store.path, output_directory=tmp_path / "reports")

    assert summary["convergence"]["rate"] == 0.0
    assert summary["convergence"]["marginal_profiles_purchased_per_convergence"] is None
    assert summary["convergence"]["marginal_profiles_purchased_per_case"] > 0


def test_the_report_fingerprints_the_rows_it_observed(tmp_path) -> None:
    # `match_scope` and `result_order` in the header name the settings a run
    # used. They say nothing about whether the settings changed the answer, and
    # a reader who sees two reports with different headers will assume they are
    # two measurements. The digest is what lets the output itself settle that.
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.refinement import run_refinement

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=3_000, seed=42)
    reports = tmp_path / "reports"

    first, _, _ = run_refinement(database, output_directory=reports, case_limit=25)
    again, _, _ = run_refinement(database, output_directory=reports, case_limit=25)
    fewer, _, _ = run_refinement(database, output_directory=reports, case_limit=10)

    assert len(first["observation_digest"]) == 64
    # Same observations, same digest -- it must not pick up the clock or the run
    # duration, or every run would look different from every other run.
    assert first["observation_digest"] == again["observation_digest"]
    # Different observations, different digest -- otherwise it proves nothing.
    assert first["observation_digest"] != fewer["observation_digest"]


def test_two_flag_settings_that_measure_the_same_thing_share_a_digest(tmp_path) -> None:
    # The finding this mechanism exists to expose. Every profile matching all of
    # a query's tokens ties on overlap, and MockXApplication re-sorts the pool by
    # (-overlap, id) before cutting the page, so either result_order collapses to
    # the same page; and build_search_query is name-only, so the bio index is
    # never consulted. Both flags are therefore inert here, and four reports that
    # look like a sensitivity sweep are one measurement filed four times.
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.refinement import run_refinement

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=3_000, seed=42)
    reports = tmp_path / "reports"

    digests = {
        (scope, order): run_refinement(
            database,
            output_directory=reports,
            case_limit=25,
            match_scope=scope,
            result_order=order,
        )[0]["observation_digest"]
        for scope in ("name_username", "name_username_bio")
        for order in ("bm25", "follower_weighted")
    }

    assert len(set(digests.values())) == 1


def test_the_digest_covers_reachability_not_only_the_turns(tmp_path) -> None:
    # The gap that made the sweep's verdict false on the 100k corpus. `_reachable`
    # probes the store at depth 1000, well below the ten rows any turn sees, so
    # result_order reorders what falls inside that probe and moves
    # unconvergeable_cases while every turn of every case lands in exactly the
    # same place. A digest over turns alone is identical across that difference,
    # and the sweep would print "result_order inert -- one measurement, not
    # several" over two reports whose unconvergeable counts differ (58 vs 76 on
    # the 100k corpus). Hand-built rather than run through the harness: the flag
    # is invariant on every corpus small enough to build in a test, so the only
    # way to fail for this claim is to feed the digest the difference directly.
    from mock_x_platform.refinement import _observation_digest

    rows = [
        {
            "case_id": "case-1",
            "turn_index": 0,
            "rank": 3,
            "visible": True,
            "retrieval_changed": False,
        }
    ]
    reachable = [{"case_id": "case-1", "reachable": True, "profiles_purchased": 10}]
    unreachable = [{"case_id": "case-1", "reachable": False, "profiles_purchased": 10}]
    pricier = [{"case_id": "case-1", "reachable": True, "profiles_purchased": 11}]

    assert _observation_digest(rows, reachable) == _observation_digest(rows, reachable)
    assert _observation_digest(rows, reachable) != _observation_digest(rows, unreachable)
    # Purchases too: a flag that changes which profiles a case buys has changed
    # what the run cost, and "inert" must not be said over a cost difference.
    assert _observation_digest(rows, reachable) != _observation_digest(rows, pricier)


def test_the_digest_still_covers_the_turns(tmp_path) -> None:
    # The other half, pinned separately so widening the payload cannot quietly
    # drop what it was originally for.
    from mock_x_platform.refinement import _observation_digest

    per_case = [{"case_id": "case-1", "reachable": True, "profiles_purchased": 10}]
    base = {
        "case_id": "case-1",
        "turn_index": 0,
        "rank": 3,
        "visible": True,
        "retrieval_changed": False,
    }

    for field, changed in (
        ("rank", 4),
        ("visible", False),
        ("retrieval_changed", True),
        ("turn_index", 1),
    ):
        assert _observation_digest([base], per_case) != _observation_digest(
            [{**base, field: changed}], per_case
        ), f"digest ignores {field}"


def test_the_report_explains_what_a_shared_digest_means(tmp_path) -> None:
    # A reader of one JSON file, with no access to this test suite, has to be
    # able to reach the right conclusion.
    from mock_x_platform.refinement import run_refinement

    store = _unreachable_store(tmp_path)

    summary, json_path, _ = run_refinement(
        store.path, output_directory=tmp_path / "reports"
    )
    written = json.loads(json_path.read_text(encoding="utf-8"))

    note = summary["flag_sensitivity"]
    assert written["flag_sensitivity"] == note
    assert written["observation_digest"] == summary["observation_digest"]
    assert "observation_digest" in note
    # The mechanism, not just the rule: overlap ties and the name-only query are
    # why a reader should expect the flags to be inert rather than suspect a bug.
    assert "overlap" in note
    assert "build_search_query" in note


def test_the_sweep_names_the_flags_that_moved_nothing(tmp_path, capsys) -> None:
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.refinement import main

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=1_500, seed=42)

    argv = [
        "--database",
        str(database),
        "--output",
        str(tmp_path / "reports"),
        "--limit",
        "6",
        "--scope",
        "both",
        "--order",
        "both",
    ]
    import sys

    original, sys.argv = sys.argv, ["refinement", *argv]
    try:
        assert main() == 0
    finally:
        sys.argv = original

    printed = capsys.readouterr().out
    line = next(
        text for text in printed.splitlines() if text.startswith("Flag sensitivity:")
    )
    assert "match_scope" in line
    assert "result_order" in line
    assert "inert" in line
    assert "moved" not in line


def test_the_sweep_line_separates_an_inert_flag_from_one_that_moved() -> None:
    # Pinned directly rather than through a run: no corpus small enough to build
    # in a test is flag-sensitive, and on the 100k corpus it is result_order --
    # through per-case reachability, not through any turn -- that moves. Without
    # this, a line that said "inert" unconditionally would pass every other test
    # here.
    from mock_x_platform.refinement import _flag_sensitivity_line

    scopes = ["name_username", "name_username_bio"]
    orders = ["bm25", "follower_weighted"]
    line = _flag_sensitivity_line(
        {
            ("name_username", "bm25"): "aaa",
            ("name_username", "follower_weighted"): "bbb",
            ("name_username_bio", "bm25"): "aaa",
            ("name_username_bio", "follower_weighted"): "bbb",
        },
        scopes,
        orders,
    )

    assert "match_scope inert" in line
    assert "result_order changed what the run observed" in line
    assert "separate measurements" in line


def test_a_single_combination_run_says_it_has_nothing_to_compare() -> None:
    from mock_x_platform.refinement import _flag_sensitivity_line

    line = _flag_sensitivity_line(
        {("name_username", "bm25"): "aaa"}, ["name_username"], ["bm25"]
    )

    assert "nothing to compare" in line
