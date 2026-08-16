from __future__ import annotations

from typing import Any

import pytest

from mock_x_platform.dataset import build_dataset
from mock_x_platform.evaluation import (
    ACCEPTANCE_CALIBRATION,
    CORPUS_THRESHOLDS,
    MISSING_VARIANTS,
    VARIANT_NAMES,
    _acceptance_checks,
    _corpus_kind,
    run_evaluation,
)
from mock_x_platform.store import MockXStore


def _metrics(**overrides: float) -> dict[str, dict[str, Any]]:
    """Healthy-looking metrics for every variant, at the calibrated corpus."""

    base = {"retrieval_recall": 0.95, "top_1": 0.30, "top_3": 0.30, "top_10": 0.30}
    metrics = {variant: dict(base) for variant in VARIANT_NAMES}
    metrics["name_typo"] = {
        "retrieval_recall": 0.11,
        "top_1": 0.005,
        "top_3": 0.006,
        "top_10": 0.006,
    }
    for key, value in overrides.items():
        variant, _, metric = key.partition("__")
        metrics[variant][metric] = value
    return metrics


def _tiers(handle_recall: float = 0.22) -> dict[str, dict[str, Any]]:
    return {
        "clean": {"count": 2250, "retrieval_recall": 0.86},
        "handle_name": {"count": 546, "retrieval_recall": handle_recall},
    }


def _run(metrics=None, tiers=None, corpus="dirty"):
    return _acceptance_checks(
        metrics or _metrics(),
        tiers if tiers is not None else _tiers(),
        {"rate": 1.0},
        {},
        False,
        corpus=corpus,
    )


def _named(checks: list[dict[str, Any]], fragment: str) -> dict[str, Any]:
    return next(check for check in checks if fragment in check["name"])


def test_a_healthy_run_passes_the_gate() -> None:
    checks = _run()

    assert checks, "the gate must actually contain checks"
    assert all(check["passed"] for check in checks), [
        check["name"] for check in checks if not check["passed"]
    ]


def test_every_check_records_the_breakage_it_detects() -> None:
    # A threshold nobody can tie to a regression is decoration; the description
    # is what stops one being added back.
    for check in _run(corpus="clean") + _run(corpus="dirty"):
        assert check["catches"].strip(), check["name"]
        assert ("minimum" in check) ^ ("maximum" in check), check["name"]


def test_clean_and_dirty_corpora_use_different_thresholds() -> None:
    clean = {check["name"]: check.get("minimum") for check in _run(corpus="clean")}
    dirty = {check["name"]: check.get("minimum") for check in _run(corpus="dirty")}

    assert clean["clean top-1"] > dirty["clean top-1"]
    assert CORPUS_THRESHOLDS["clean"]["clean_retrieval_recall"] > (
        CORPUS_THRESHOLDS["dirty"]["clean_retrieval_recall"]
    )


def test_a_ranking_regression_on_the_happy_path_fails_the_gate() -> None:
    checks = _run(_metrics(clean__top_1=0.01))

    assert not all(check["passed"] for check in checks)
    assert not _named(checks, "clean top-1")["passed"]


def test_a_pooling_regression_fails_the_gate() -> None:
    checks = _run(_metrics(clean__retrieval_recall=0.40))

    assert not _named(checks, "clean retrieval recall")["passed"]


def test_a_handle_tier_only_regression_fails_the_gate() -> None:
    # The regression this gate was rebuilt for: reverting AND-as-preference moves
    # handle_name recall 0.225 -> 0.005 while aggregate recall only slips 0.933 ->
    # 0.909, which clears its own threshold. Without the per-tier check the gate
    # would pass. Verified at 100k against the real code path, not just here.
    checks = _run(tiers=_tiers(handle_recall=0.005))

    assert not _named(checks, "handle_name")["passed"]
    assert _named(checks, "clean retrieval recall")["passed"]


def test_formatting_noise_changing_results_fails_the_gate() -> None:
    checks = _run(_metrics(formatting__top_1=0.20))

    parity = _named(checks, "formatting parity")
    assert not parity["passed"]
    assert parity["actual"] == pytest.approx(0.10)


def test_the_worst_missing_clue_variant_is_the_one_gated() -> None:
    checks = _run(_metrics(without_role__top_1=0.001))

    assert not _named(checks, "missing-clue")["passed"]
    assert set(MISSING_VARIANTS) == {"without_school", "without_role", "without_location"}


def test_a_clean_corpus_run_skips_the_handle_tier_check() -> None:
    checks = _run(tiers={"clean": {"count": 6000, "retrieval_recall": 1.0}}, corpus="clean")

    assert not any("handle_name" in check["name"] for check in checks)


def test_corpus_kind_is_read_from_the_profiles(tmp_path) -> None:
    dirty = tmp_path / "dirty.sqlite3"
    clean = tmp_path / "clean.sqlite3"
    build_dataset(dirty, count=200, seed=42)
    build_dataset(
        clean,
        count=200,
        seed=42,
        tier_mix={"clean": 1.0, "partial": 0.0, "decorated": 0.0, "handle_name": 0.0},
    )

    assert _corpus_kind(MockXStore(dirty)) == "dirty"
    assert _corpus_kind(MockXStore(clean)) == "clean"


def test_evaluation_reports_whether_it_matched_the_calibrated_corpus(tmp_path) -> None:
    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=300, seed=42)

    summary, _, _ = run_evaluation(
        database,
        output_directory=tmp_path / "reports",
        case_limit=3,
        determinism_cases=1,
        run_http=False,
    )

    # The gap round 1 left open: nothing asserted on the gate itself, so it went
    # on failing for three rounds without a single test noticing.
    assert isinstance(summary["passed"], bool)
    assert summary["corpus"] == "dirty"
    assert summary["acceptance"], "a run must produce checks"
    assert all("catches" in check for check in summary["acceptance"])

    calibration = summary["acceptance_calibration"]
    assert calibration["profile_count"] == ACCEPTANCE_CALIBRATION["profile_count"]
    # 300 profiles and 3 cases is nowhere near the calibrated corpus, and the
    # report has to say so rather than implying the pass means what it would at 100k.
    assert calibration["matches_calibration"] is False


def test_the_gate_fails_when_pooling_collapses(tmp_path, monkeypatch) -> None:
    # Large enough that several people share each generated name, so a starved
    # pool genuinely loses the expected profile rather than trivially keeping it.
    database = tmp_path / "profiles.sqlite3"
    build_dataset(database, count=5_000, seed=42)
    original = MockXStore.search_profiles

    def starved(self, query: str, *, limit: int = 250):
        return original(self, query, limit=limit)[:1]

    monkeypatch.setattr(MockXStore, "search_profiles", starved)
    summary, _, _ = run_evaluation(
        database,
        output_directory=tmp_path / "reports",
        case_limit=8,
        determinism_cases=1,
        run_http=False,
    )

    assert summary["passed"] is False
    failed = [check["name"] for check in summary["acceptance"] if not check["passed"]]
    assert any("retrieval recall" in name for name in failed), failed
