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

# --- Acceptance calibration -------------------------------------------------
#
# Calibrated against the documented default run: 100,000 profiles, the full
# 1,000-case set, no --limit, measured on seeds 42 and 43. Every threshold below
# is the lower of the two seeds minus HEADROOM.
#
# Measure before you move any of these. Two traps:
#
#   Sample.   `--limit N` takes the N lowest case ids, and profile ids run in
#             generation order, which is also how bm25 ties are broken. Limited
#             runs are therefore optimistic -- clean-corpus top-1 reads 1.000 at
#             --limit 200 and 0.175 across all 1,000 cases on the same database.
#             Calibrate from full runs only.
#
#   Size.     The generator draws from 41 first names and 43 surnames, so a
#             corpus of N profiles puts about N/1763 people behind every exact
#             name while the mock returns ten. Top-1 is therefore capped near
#             10/(N/1763): about 1.00 at 5k, 0.88 at 20k, 0.44 at 40k and 0.18 at
#             100k. These thresholds encode the 100k figure. Runs on a smaller
#             corpus clear them easily -- that is the name space desaturating,
#             not the search improving. Widening the name pool would restore the
#             gate's sensitivity and is the right follow-up.
ACCEPTANCE_CALIBRATION = {
    "profile_count": 100_000,
    "case_count": 1_000,
    "seeds": (42, 43),
    "note": "Thresholds assume the full case set at the 100k default corpus.",
}

# Largest metric movement observed between seeds 42 and 43 was 0.016 absolute,
# mean 0.008. A headroom of 0.04 is 2.5x the worst observed seed movement, so
# corpus noise cannot trip the gate while a regression of more than about four
# points still does.
HEADROOM = 0.04

FORMATTING_PARITY_TOLERANCE = 0.001
HTTP_P95_LIMIT_MS = 1000.0

CORPUS_THRESHOLDS: dict[str, dict[str, float]] = {
    # Every profile carries a full bio and a real location, so the ranker has all
    # the evidence it scores. A shortfall here is a defect, not a corpus effect.
    "clean": {
        "clean_top_1": 0.13,  # measured 0.175 / 0.191
        "clean_top_10": 0.13,  # measured 0.175 / 0.191
        "clean_retrieval_recall": 0.96,  # measured 1.000 / 1.000
        "missing_clue_top_1": 0.13,  # measured 0.175 / 0.191
        "name_typo_retrieval_recall": 0.06,  # measured 0.108 / 0.109
        "name_typo_top_10": 0.001,  # measured 0.005 / 0.005
        "handle_name_retrieval_recall": 0.0,  # tier absent from a clean corpus
        "determinism": 1.0,
        "http_success_rate": 1.0,
    },
    # Thirty percent of profiles have no bio and no location, ten percent do not
    # display their owner's name at all. Most of the gap to the clean corpus is
    # the corpus; these thresholds gate what is left.
    "dirty": {
        "clean_top_1": 0.07,  # measured 0.111 / 0.122
        "clean_top_10": 0.13,  # measured 0.176 / 0.186
        "clean_retrieval_recall": 0.89,  # measured 0.933 / 0.934
        "missing_clue_top_1": 0.05,  # measured 0.099 / 0.112
        "name_typo_retrieval_recall": 0.06,  # measured 0.105 / 0.111
        "name_typo_top_10": 0.001,  # measured 0.006 / 0.003
        "handle_name_retrieval_recall": 0.16,  # measured 0.225 / 0.200
        "determinism": 1.0,
        "http_success_rate": 1.0,
    },
}


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
    corpus = _corpus_kind(store)
    checks = _acceptance_checks(
        variant_metrics, tier_metrics, determinism, http_metrics, run_http, corpus=corpus
    )
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
        "corpus": corpus,
        "acceptance": checks,
        "acceptance_calibration": {
            **ACCEPTANCE_CALIBRATION,
            "seeds": list(ACCEPTANCE_CALIBRATION["seeds"]),
            "corpus": corpus,
            "matches_calibration": (
                store.profile_count() == ACCEPTANCE_CALIBRATION["profile_count"]
                and len(cases) == ACCEPTANCE_CALIBRATION["case_count"]
            ),
        },
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
    tiers: dict[str, dict[str, Any]],
    determinism: dict[str, Any],
    http: dict[str, Any],
    http_enabled: bool,
    *,
    corpus: str,
) -> list[dict[str, Any]]:
    """Gate the run against thresholds calibrated for this corpus.

    A clean corpus and a dirty one measure different things, so one threshold
    cannot serve both: on a clean corpus every profile carries the bio and
    location the ranker scores, and a shortfall is a real bug; on a dirty corpus
    most of the shortfall is the corpus itself. See CORPUS_THRESHOLDS.
    """

    limits = CORPUS_THRESHOLDS[corpus]
    checks: list[dict[str, Any]] = []

    def add_minimum(name: str, actual: float, key: str, catches: str) -> None:
        minimum = limits[key]
        checks.append(
            {
                "name": name,
                "actual": actual,
                "minimum": minimum,
                "passed": actual >= minimum,
                "catches": catches,
            }
        )

    add_minimum(
        "clean top-1",
        metrics["clean"]["top_1"],
        "clean_top_1",
        "The ranker, or the ten rows it is handed, degrading on the happy path.",
    )
    add_minimum(
        "clean top-10",
        metrics["clean"]["top_10"],
        "clean_top_10",
        "The expected profile falling out of the ten rows the mock returns. All "
        "five well-formed variants build the same query and therefore share this "
        "number exactly, so this one check covers the whole family.",
    )
    add_minimum(
        "clean retrieval recall",
        metrics["clean"]["retrieval_recall"],
        "clean_retrieval_recall",
        "A pooling regression: a candidate pool that stops admitting profiles it "
        "used to reach, such as gating the OR pass behind the AND result again.",
    )

    # Formatting noise must not survive sanitize_query, so the formatting variant
    # has to score identically to clean rather than merely well. An absolute
    # threshold here would duplicate the clean check; the difference is the signal.
    formatting_gap = abs(metrics["formatting"]["top_1"] - metrics["clean"]["top_1"])
    checks.append(
        {
            "name": "formatting parity with clean",
            "actual": round(formatting_gap, 6),
            "maximum": FORMATTING_PARITY_TOLERANCE,
            "passed": formatting_gap <= FORMATTING_PARITY_TOLERANCE,
            "catches": "sanitize_query no longer absorbing case, spacing or "
            "punctuation noise, so formatted criteria retrieve differently.",
        }
    )

    # The three missing-clue variants share top-10 with clean by construction
    # (same query, same ten rows), so only their top-1 carries information about
    # how the ranker copes with an absent clue. Gate the worst of the three.
    worst_missing = min(metrics[variant]["top_1"] for variant in MISSING_VARIANTS)
    add_minimum(
        "missing-clue top-1 (worst of school/role/location)",
        worst_missing,
        "missing_clue_top_1",
        "The ranker mishandling an absent criterion, for example crediting or "
        "penalising a clue the sender never supplied.",
    )

    add_minimum(
        "name_typo retrieval recall",
        metrics["name_typo"]["retrieval_recall"],
        "name_typo_retrieval_recall",
        "Retrieval collapsing for near-miss names. This is the stable signal for "
        "the typo path; its top-1 and top-10 are a handful of cases in a thousand.",
    )
    add_minimum(
        "name_typo top-10",
        metrics["name_typo"]["top_10"],
        "name_typo_top_10",
        "The typo path going completely dead. It cannot be set tighter: the "
        "metric is 3-6 cases per thousand and moves by most of its own value "
        "between seeds.",
    )

    if "handle_name" in tiers and tiers["handle_name"]["count"]:
        add_minimum(
            "handle_name retrieval recall",
            tiers["handle_name"]["retrieval_recall"],
            "handle_name_retrieval_recall",
            "A pooling regression that only shows up on handle-style accounts, "
            "which are 9% of searches and therefore invisible in the aggregate.",
        )

    add_minimum(
        "determinism",
        determinism["rate"],
        "determinism",
        "Identical queries returning different orders, which would make every "
        "other number in this report unreproducible.",
    )

    if http_enabled:
        total_requests = sum(result["requests"] for result in http.values())
        total_errors = sum(result["errors"] for result in http.values())
        add_minimum(
            "HTTP success rate",
            _ratio(total_requests - total_errors, total_requests),
            "http_success_rate",
            "The HTTP layer erroring or returning malformed payloads under "
            "concurrency, which in-process runs cannot see.",
        )
        checks.append(
            {
                "name": "8-client HTTP p95 latency",
                "actual": http["8"]["latency_ms"]["p95"],
                "maximum": HTTP_P95_LIMIT_MS,
                "passed": http["8"]["latency_ms"]["p95"] < HTTP_P95_LIMIT_MS,
                "catches": "A serialisation or locking regression that only "
                "appears with several clients on the socket at once.",
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


def _corpus_kind(store: MockXStore) -> str:
    """Which threshold set applies, read from the corpus rather than metadata.

    Uses the profile rows so a database generated before dirt tiers, or one
    whose metadata was not written, still classifies correctly.
    """

    counts = store.tier_counts()
    return "clean" if not any(count for tier, count in counts.items() if tier != "clean") else "dirty"


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
    calibration = summary["acceptance_calibration"]
    print(f"\nAcceptance: {'PASS' if summary['passed'] else 'FAIL'}  "
          f"({summary['corpus']} corpus thresholds)")
    if not calibration["matches_calibration"]:
        print(
            f"  NOTE thresholds are calibrated for {calibration['profile_count']:,} profiles "
            f"and {calibration['case_count']:,} cases; this run used "
            f"{summary['profile_count']:,} and {summary['case_count']:,}. Both metrics improve "
            "on smaller corpora, so a pass here is weaker evidence than a calibrated run."
        )
    for check in summary["acceptance"]:
        bound = (
            f"min {check['minimum']}" if "minimum" in check else f"max {check['maximum']}"
        )
        print(
            f"  [{'PASS' if check['passed'] else 'FAIL'}] {check['name']}: "
            f"{check['actual']:.3f} ({bound})"
        )
        if not check["passed"]:
            print(f"          catches: {check['catches']}")
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
