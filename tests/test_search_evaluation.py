from __future__ import annotations

import csv
import json

import pytest

from models.criteria import RecipientCriteria
from mock_x_platform.dataset import build_dataset
from mock_x_platform.dataset import TIER_NAMES
from mock_x_platform.evaluation import (
    VARIANT_NAMES,
    _percentile,
    criteria_variants,
    run_evaluation,
)
from mock_x_platform.store import MockXStore


def test_variants_are_repeatable_and_change_only_the_intended_clue() -> None:
    criteria = RecipientCriteria(
        name="Priya Patel",
        company="Cloud Works",
        role="Engineer",
        location="Toronto",
        school="University of Toronto",
    )

    first = criteria_variants(criteria)
    second = criteria_variants(criteria)

    assert tuple(first) == VARIANT_NAMES
    assert [item.to_dict() for item in first.values()] == [
        item.to_dict() for item in second.values()
    ]
    assert first["without_school"].school == ""
    assert first["without_school"].company == criteria.company
    assert first["name_typo"].name != criteria.name


def test_percentile_uses_nearest_rank() -> None:
    assert _percentile([1, 2, 3, 4, 5], 0.95) == 5
    assert _percentile([5, 1, 4, 2, 3], 0.50) == 3


def test_small_evaluation_writes_comparable_reports(tmp_path) -> None:
    database = tmp_path / "profiles.sqlite3"
    output = tmp_path / "reports"
    build_dataset(database, count=100, seed=42)

    summary, json_path, csv_path = run_evaluation(
        database,
        output_directory=output,
        case_limit=2,
        determinism_cases=1,
        run_http=False,
    )

    assert summary["profile_count"] == 100
    assert summary["case_count"] == 2
    assert summary["search_count"] == 12
    assert summary["dataset_metadata"]["seed"] == "42"
    assert "variant" in summary["failure_groups"]
    assert "company" in summary["failure_groups"]
    assert json.loads(json_path.read_text(encoding="utf-8"))["database_sha256"]
    with csv_path.open(encoding="utf-8", newline="") as source:
        assert len(list(csv.DictReader(source))) == 12
    assert MockXStore(database).profile_count() == 100


def test_evaluation_breaks_results_out_by_dirt_tier(tmp_path) -> None:
    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=400, seed=42)

    summary, _, csv_path = run_evaluation(
        database,
        output_directory=tmp_path / "reports",
        case_limit=40,
        determinism_cases=1,
        run_http=False,
    )

    assert set(summary["tier_counts"]) == set(TIER_NAMES)
    assert set(summary["tiers"]) <= set(TIER_NAMES)
    assert summary["tiers"], "per-tier metrics must not be blank"
    assert sum(metrics["count"] for metrics in summary["tiers"].values()) == summary["search_count"]

    for tier, metrics in summary["tiers"].items():
        assert 0.0 <= metrics["retrieval_recall"] <= 1.0
        assert metrics["mean_rank_when_found"] is None or metrics["mean_rank_when_found"] >= 1.0
        assert tuple(summary["tier_variants"][tier]) == VARIANT_NAMES

    # The aggregate survives alongside the breakdown.
    assert summary["overall"]["count"] == summary["search_count"]
    assert tuple(summary["variants"]) == VARIANT_NAMES
    assert "tier" in summary["failure_groups"]

    ceiling = summary["retrieval_ceiling"]
    assert 0.0 <= ceiling["overall"]["unreachable_share"] <= 1.0
    assert set(ceiling["by_tier"]) <= set(TIER_NAMES)

    with csv_path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    # Tier rides on every row so results can be joined back to the corpus.
    assert all(row["tier"] in TIER_NAMES for row in rows)


def test_every_run_reports_what_it_would_have_cost(tmp_path) -> None:
    from mock_x_platform.dataset import build_dataset
    from mock_x_platform.evaluation import run_evaluation

    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=300, seed=42)

    summary, _, _ = run_evaluation(
        database,
        output_directory=tmp_path / "reports",
        case_limit=5,
        determinism_cases=1,
        run_http=False,
    )

    cost = summary["cost"]
    # run_http=False is the common case in this suite, so a ledger hung off the
    # HTTP layer would report $0.00 here -- a number that looks like a pass.
    assert cost["distinct_profiles"] > 0
    assert cost["estimated_usd"] > 0
    assert cost["per_case_usd"] == pytest.approx(cost["estimated_usd"] / 5)
    assert cost["rates_checked_on"]
