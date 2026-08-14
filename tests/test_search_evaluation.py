from __future__ import annotations

import csv
import json

from models.criteria import RecipientCriteria
from mock_x_platform.dataset import build_dataset
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
