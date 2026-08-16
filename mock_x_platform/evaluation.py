from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from config import PROJECT_ROOT, load_settings
from models.candidate import Candidate
from models.criteria import RecipientCriteria
from ranking.normalization import normalize_text
from ranking.ranker import rank_candidates
from search.query_builder import build_search_query

from .application import MockXApplication
from .server import create_server
from .store import MockXStore


VARIANT_NAMES = (
    "clean",
    "formatting",
    "without_school",
    "without_role",
    "without_location",
    "name_typo",
)
MISSING_VARIANTS = {"without_school", "without_role", "without_location"}


@dataclass(slots=True)
class EvaluationRow:
    case_id: int
    variant: str
    tier: str
    expected_profile_id: str
    query: str
    retrieved: bool
    rank: int | None
    latency_ms: float
    top_ids: list[str]
    name: str
    company: str
    role: str
    location: str
    school: str


def criteria_variants(criteria: RecipientCriteria) -> dict[str, RecipientCriteria]:
    base = criteria.to_dict()
    variants = {"clean": RecipientCriteria.from_dict(base)}
    formatted = {
        key: _format_noise(str(value)) if key != "extra_clues" else value
        for key, value in base.items()
    }
    variants["formatting"] = RecipientCriteria.from_dict(formatted)
    for field in ("school", "role", "location"):
        changed = dict(base)
        changed[field] = ""
        variants[f"without_{field}"] = RecipientCriteria.from_dict(changed)
    typo = dict(base)
    typo["name"] = _deterministic_typo(criteria.name)
    variants["name_typo"] = RecipientCriteria.from_dict(typo)
    return variants


def run_evaluation(
    database_path: str | Path,
    *,
    output_directory: str | Path,
    case_limit: int | None = None,
    http_requests: int = 100,
    determinism_cases: int = 100,
    run_http: bool = True,
) -> tuple[dict[str, Any], Path, Path]:
    database = Path(database_path)
    store = MockXStore(database)
    cases = store.evaluation_cases(limit=case_limit)
    if not cases:
        raise RuntimeError("No evaluation cases exist. Generate the dataset first.")

    application = MockXApplication(store)
    labels = store.profile_labels(str(case["expected_profile_id"]) for case in cases)
    rows: list[EvaluationRow] = []
    started = time.perf_counter()
    total = len(cases) * len(VARIANT_NAMES)
    print(f"Evaluating {len(cases):,} cases across {len(VARIANT_NAMES)} variants ({total:,} searches).")
    completed = 0
    for case in cases:
        criteria = RecipientCriteria.from_dict(json.loads(case["criteria_json"]))
        expected_id = str(case["expected_profile_id"])
        tier = labels.get(expected_id, {}).get("tier", "unknown")
        for variant, changed in criteria_variants(criteria).items():
            rows.append(
                _evaluate_one(
                    application,
                    int(case["id"]),
                    variant,
                    tier,
                    expected_id,
                    changed,
                )
            )
            completed += 1
        if completed % 300 == 0 or completed == total:
            elapsed = time.perf_counter() - started
            rate = completed / elapsed if elapsed else 0
            remaining = (total - completed) / rate if rate else 0
            print(f"  {completed:,}/{total:,} searches; approximately {remaining / 60:.1f} minutes remaining")

    variant_metrics = {
        variant: _search_metrics(row for row in rows if row.variant == variant)
        for variant in VARIANT_NAMES
    }
    overall_metrics = _search_metrics(rows)
    tier_metrics, tier_variant_metrics = _tier_metrics(rows)
    ceiling = _retrieval_ceiling(rows, labels)
    determinism = _determinism_check(application, cases[:determinism_cases])
    http_metrics = _http_load(database, cases, http_requests) if run_http else {}
    checks = _acceptance_checks(variant_metrics, determinism, http_metrics, run_http)
    failures = sorted(
        (row for row in rows if row.rank != 1),
        key=lambda row: (row.retrieved, row.rank is not None, -(row.rank or 999)),
    )
    finished_at = datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "generated_at": finished_at.isoformat().replace("+00:00", "Z"),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "database": str(database.resolve()),
        "database_sha256": _sha256(database),
        "dataset_metadata": store.dataset_metadata(),
        "profile_count": store.profile_count(),
        "case_count": len(cases),
        "search_count": len(rows),
        "tier_counts": store.tier_counts(),
        "overall": overall_metrics,
        "variants": variant_metrics,
        "tiers": tier_metrics,
        "tier_variants": tier_variant_metrics,
        "retrieval_ceiling": ceiling,
        "determinism": determinism,
        "http_load": http_metrics,
        "acceptance": checks,
        "passed": all(check["passed"] for check in checks),
        "failure_groups": _failure_groups(rows),
        "failure_examples": [_row_dict(row) for row in failures[:50]],
    }
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    stamp = finished_at.strftime("%Y%m%dT%H%M%SZ")
    json_path = output / f"search-evaluation-{stamp}.json"
    csv_path = output / f"search-evaluation-{stamp}.csv"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_csv(csv_path, rows)
    _print_summary(summary, json_path, csv_path)
    return summary, json_path, csv_path


def _evaluate_one(
    application: MockXApplication,
    case_id: int,
    variant: str,
    tier: str,
    expected_id: str,
    criteria: RecipientCriteria,
) -> EvaluationRow:
    started = time.perf_counter()
    query = build_search_query(criteria)
    response, preliminary_ids = application.search_users_with_diagnostics(query, 10)
    ranked = rank_candidates(
        [Candidate.from_dict(profile) for profile in response["data"]], criteria
    )
    latency_ms = (time.perf_counter() - started) * 1000
    top_ids = [candidate.id for candidate in ranked]
    rank = top_ids.index(expected_id) + 1 if expected_id in top_ids else None
    return EvaluationRow(
        case_id=case_id,
        variant=variant,
        tier=tier,
        expected_profile_id=expected_id,
        query=query,
        retrieved=expected_id in preliminary_ids,
        rank=rank,
        latency_ms=latency_ms,
        top_ids=top_ids,
        name=criteria.name,
        company=criteria.company,
        role=criteria.role,
        location=criteria.location,
        school=criteria.school,
    )


def _search_metrics(rows: Iterable[EvaluationRow]) -> dict[str, Any]:
    materialized = list(rows)
    ranks = [row.rank for row in materialized]
    found = [rank for rank in ranks if rank is not None]
    latencies = [row.latency_ms for row in materialized]
    count = len(materialized)
    if not count:
        # Tier x variant slices can legitimately be empty on a small run.
        return {
            "count": 0,
            "retrieval_recall": 0.0,
            "top_1": 0.0,
            "top_3": 0.0,
            "top_10": 0.0,
            "mean_rank_when_found": None,
            "mean_reciprocal_rank": 0.0,
            "ranking_top_10_given_retrieved": 0.0,
            "latency_ms": None,
        }
    return {
        "count": count,
        "retrieval_recall": _ratio(sum(row.retrieved for row in materialized), count),
        "top_1": _ratio(sum(rank == 1 for rank in ranks), count),
        "top_3": _ratio(sum(rank is not None and rank <= 3 for rank in ranks), count),
        "top_10": _ratio(sum(rank is not None for rank in ranks), count),
        # Averaged over the searches that found the person at all; a mean rank
        # over misses would silently mix "ranked badly" with "never retrieved".
        "mean_rank_when_found": round(sum(found) / len(found), 4) if found else None,
        "mean_reciprocal_rank": round(
            sum(1 / rank for rank in found) / count, 6
        ),
        "ranking_top_10_given_retrieved": _ratio(
            sum(row.retrieved and row.rank is not None for row in materialized),
            sum(row.retrieved for row in materialized),
        ),
        "latency_ms": _latency_metrics(latencies),
    }


def _tier_metrics(rows: list[EvaluationRow]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recall and mean rank per dirt tier, and per tier x query variant.

    A blended number only says the corpus got harder; the split says which kind
    of dirt did it.
    """

    tiers = sorted({row.tier for row in rows})
    by_tier = {tier: _search_metrics(row for row in rows if row.tier == tier) for tier in tiers}
    by_tier_variant = {
        tier: {
            variant: _search_metrics(
                row for row in rows if row.tier == tier and row.variant == variant
            )
            for variant in VARIANT_NAMES
        }
        for tier in tiers
    }
    return by_tier, by_tier_variant


def _retrieval_ceiling(
    rows: list[EvaluationRow], labels: dict[str, dict[str, str]]
) -> dict[str, Any]:
    """Share of cases a name-primary query can never reach.

    The mock store matches whole tokens, so a person is structurally
    unreachable when their real name shares no token with the display name or
    the username. No ranking or parsing change recovers those.
    """

    # Use the clean variant only: the ceiling is about the profile being
    # unreachable from a correctly spelled name, not about typo robustness.
    expected = {row.expected_profile_id: row for row in rows if row.variant == "clean"}
    per_tier: dict[str, dict[str, Any]] = {}
    for profile_id, row in expected.items():
        label = labels.get(profile_id)
        if label is None:
            continue
        wanted = set(normalize_text(row.name).split())
        reachable = set(normalize_text(f"{label['name']} {label['username']}").split())
        bucket = per_tier.setdefault(row.tier, {"cases": 0, "unreachable": 0, "examples": []})
        bucket["cases"] += 1
        if not wanted & reachable:
            bucket["unreachable"] += 1
            if len(bucket["examples"]) < 5:
                bucket["examples"].append(
                    {"real_name": row.name, "display_name": label["name"], "username": label["username"]}
                )
    for bucket in per_tier.values():
        bucket["unreachable_share"] = _ratio(bucket["unreachable"], bucket["cases"])
    total_cases = sum(bucket["cases"] for bucket in per_tier.values())
    total_unreachable = sum(bucket["unreachable"] for bucket in per_tier.values())
    return {
        "overall": {
            "cases": total_cases,
            "unreachable": total_unreachable,
            "unreachable_share": _ratio(total_unreachable, total_cases),
        },
        "by_tier": dict(sorted(per_tier.items())),
    }


def _determinism_check(
    application: MockXApplication, cases: list[dict[str, Any]]
) -> dict[str, Any]:
    matched = 0
    for case in cases:
        criteria = RecipientCriteria.from_dict(json.loads(case["criteria_json"]))
        query = build_search_query(criteria)
        first = application.search_users(query, 10)["data"]
        second = application.search_users(query, 10)["data"]
        matched += [profile["id"] for profile in first] == [profile["id"] for profile in second]
    return {"cases": len(cases), "identical": matched, "rate": _ratio(matched, len(cases))}


def _http_load(
    database: Path, cases: list[dict[str, Any]], request_count: int
) -> dict[str, Any]:
    server = create_server("127.0.0.1", 0, database_path=database)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/2/users/search"
    queries = [
        build_search_query(RecipientCriteria.from_dict(json.loads(case["criteria_json"])))
        for case in cases
    ]
    thread_state = threading.local()

    def request(index: int) -> tuple[float, str | None]:
        if not hasattr(thread_state, "session"):
            thread_state.session = requests.Session()
        started = time.perf_counter()
        try:
            response = thread_state.session.get(
                url,
                params={"query": queries[index % len(queries)], "max_results": 10},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            if len(payload.get("data", [])) > 10:
                raise ValueError("Server returned more than 10 profiles")
            error = None
        except Exception as exc:
            error = str(exc)
        return (time.perf_counter() - started) * 1000, error

    results: dict[str, Any] = {}
    try:
        for concurrency in (1, 4, 8):
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                observations = list(executor.map(request, range(request_count)))
            duration = time.perf_counter() - started
            latencies = [item[0] for item in observations]
            errors = [item[1] for item in observations if item[1] is not None]
            results[str(concurrency)] = {
                "requests": request_count,
                "errors": len(errors),
                "error_examples": errors[:5],
                "throughput_requests_per_second": round(request_count / duration, 3),
                "latency_ms": _latency_metrics(latencies),
            }
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
    return results


def _acceptance_checks(
    metrics: dict[str, dict[str, Any]],
    determinism: dict[str, Any],
    http: dict[str, Any],
    http_enabled: bool,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, actual: float, minimum: float) -> None:
        checks.append(
            {"name": name, "actual": actual, "minimum": minimum, "passed": actual >= minimum}
        )

    for variant in ("clean", "formatting"):
        add(f"{variant} top-1", metrics[variant]["top_1"], 0.95)
        add(f"{variant} top-10", metrics[variant]["top_10"], 0.99)
    for variant in sorted(MISSING_VARIANTS):
        add(f"{variant} top-10", metrics[variant]["top_10"], 0.90)
    add("name_typo top-10", metrics["name_typo"]["top_10"], 0.70)
    add("determinism", determinism["rate"], 1.0)
    if http_enabled:
        total_requests = sum(result["requests"] for result in http.values())
        total_errors = sum(result["errors"] for result in http.values())
        add("HTTP success rate", _ratio(total_requests - total_errors, total_requests), 1.0)
        checks.append(
            {
                "name": "8-client HTTP p95 latency",
                "actual": http["8"]["latency_ms"]["p95"],
                "maximum": 1000.0,
                "passed": http["8"]["latency_ms"]["p95"] < 1000.0,
            }
        )
    return checks


def _latency_metrics(values: list[float]) -> dict[str, float]:
    return {
        "median": round(statistics.median(values), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "p99": round(_percentile(values, 0.99), 3),
        "maximum": round(max(values), 3),
    }


def _failure_groups(rows: list[EvaluationRow]) -> dict[str, dict[str, int]]:
    failures = [row for row in rows if row.rank != 1]
    grouped: dict[str, dict[str, int]] = {}
    for field in ("tier", "variant", "company", "role", "location", "school"):
        counts: dict[str, int] = {}
        for row in failures:
            value = str(getattr(row, field) or "(missing)")
            counts[value] = counts.get(value, 0) + 1
        grouped[field] = dict(
            sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        )
    return grouped


def _format_rank(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _percentile(values: list[float], proportion: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * proportion) - 1)]


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _format_noise(value: str) -> str:
    return f"  {value.upper().replace(' ', '   ')} !!!  " if value else ""


def _deterministic_typo(value: str) -> str:
    characters = list(value)
    indices = [index for index, char in enumerate(characters) if char.isalpha()]
    for first, second in zip(indices, indices[1:]):
        if second == first + 1 and characters[first].casefold() != characters[second].casefold():
            characters[first], characters[second] = characters[second], characters[first]
            return "".join(characters)
    return f"{value}x"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_dict(row: EvaluationRow) -> dict[str, Any]:
    result = asdict(row)
    result["latency_ms"] = round(row.latency_ms, 3)
    return result


def _write_csv(path: Path, rows: list[EvaluationRow]) -> None:
    fieldnames = list(asdict(rows[0]).keys())
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            data = _row_dict(row)
            data["top_ids"] = "|".join(row.top_ids)
            writer.writerow(data)


def _print_summary(summary: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    print("\nSearch evaluation summary")
    overall = summary["overall"]
    print(
        f"  {'ALL':18} recall={overall['retrieval_recall']:.1%} "
        f"top-1={overall['top_1']:.1%} top-10={overall['top_10']:.1%} "
        f"mean rank={_format_rank(overall['mean_rank_when_found'])}"
    )
    for variant, metrics in summary["variants"].items():
        print(
            f"  {variant:18} top-1={metrics['top_1']:.1%} "
            f"top-10={metrics['top_10']:.1%} p95={metrics['latency_ms']['p95']:.1f}ms"
        )

    print("\nBy dirt tier")
    for tier, metrics in summary["tiers"].items():
        print(
            f"  {tier:18} n={metrics['count']:<6} recall={metrics['retrieval_recall']:.1%} "
            f"top-1={metrics['top_1']:.1%} top-10={metrics['top_10']:.1%} "
            f"mean rank={_format_rank(metrics['mean_rank_when_found'])}"
        )
    print("\nBy dirt tier x query variant (recall / top-1 / mean rank)")
    header = " " * 20 + "".join(f"{variant:>22}" for variant in VARIANT_NAMES)
    print(header)
    for tier, variants in summary["tier_variants"].items():
        cells = "".join(
            f"{metrics['retrieval_recall']:.2f}/{metrics['top_1']:.2f}/"
            f"{_format_rank(metrics['mean_rank_when_found']):>5}".rjust(22)
            for metrics in variants.values()
        )
        print(f"  {tier:18}{cells}")

    ceiling = summary["retrieval_ceiling"]
    print("\nRetrieval ceiling (name shares no token with display name or username)")
    print(
        f"  {'overall':18} {ceiling['overall']['unreachable']}/{ceiling['overall']['cases']} "
        f"= {ceiling['overall']['unreachable_share']:.1%} of cases unreachable at any rank"
    )
    for tier, bucket in ceiling["by_tier"].items():
        print(
            f"  {tier:18} {bucket['unreachable']}/{bucket['cases']} "
            f"= {bucket['unreachable_share']:.1%}"
        )
    print(f"\n  determinism        {summary['determinism']['rate']:.1%}")
    for concurrency, metrics in summary["http_load"].items():
        print(
            f"  HTTP x{concurrency:<2}          errors={metrics['errors']} "
            f"p95={metrics['latency_ms']['p95']:.1f}ms "
            f"throughput={metrics['throughput_requests_per_second']:.1f}/s"
        )
    print(f"\nAcceptance: {'PASS' if summary['passed'] else 'FAIL'}")
    for check in summary["acceptance"]:
        print(f"  [{'PASS' if check['passed'] else 'FAIL'}] {check['name']}")
    print(f"JSON report: {json_path}")
    print(f"CSV detail: {csv_path}")


def main() -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Evaluate Mock X search robustness")
    parser.add_argument("--database", default=str(settings.mock_x_database_path))
    parser.add_argument(
        "--output", default=str(PROJECT_ROOT / ".cache" / "evaluation")
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--http-requests", type=int, default=100)
    parser.add_argument("--determinism-cases", type=int, default=100)
    parser.add_argument("--skip-http", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    summary, _, _ = run_evaluation(
        args.database,
        output_directory=args.output,
        case_limit=args.limit,
        http_requests=max(args.http_requests, 1),
        determinism_cases=max(args.determinism_cases, 0),
        run_http=not args.skip_http,
    )
    return 0 if summary["passed"] or args.report_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
