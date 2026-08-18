"""Multi-turn recipient refinement: cases, the harness, and its report.

Measures whether the loop can converge -- whether saying more about a person
brings them onto the visible screen -- against the search path exactly as it is,
so a no-op is proven rather than asserted.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import PROJECT_ROOT, load_settings
from models.candidate import Candidate
from models.criteria import RecipientCriteria
from ranking.normalization import normalize_text
from ranking.ranker import rank_candidates
from search.query_builder import build_search_query

from .application import MockXApplication, MockXHttpError
from .store import MATCH_SCOPES, RESULT_ORDERS, MockXStore


TURN_TYPES = ("on_profile", "off_profile", "narrowing_name", "handle")

# Stamping `match_scope` and `result_order` into a report says which store
# settings produced it. It does not, on its own, make a second run under a
# different setting an independent measurement, and two of this harness's
# reports have been observed to be bit-identical across the whole `result_order`
# sweep. `observation_digest` is what settles it from the output alone.
FLAG_SENSITIVITY_NOTE = (
    "`observation_digest` fingerprints what this run observed, in two halves: "
    "the (case_id, turn_index, rank, visible, retrieval_changed) tuple of each "
    "turn, and the (case_id, reachable, profiles_purchased) tuple of each case. "
    "Two reports that share a digest put the target at the same rank on every "
    "turn AND agree on which cases were reachable and how many profiles each "
    "bought, so the flag that differs between them was INERT for this corpus "
    "and quoting them as two results is quoting one measurement twice. The "
    "per-case half is what makes that claim safe: `_reachable` probes the store "
    "at depth 1000, so a flag can move `unconvergeable_cases` while every turn "
    "lands identically, and a turns-only digest would call that inert. What is "
    "still NOT covered: the identities of the other nine profiles on screen. "
    "The digest counts each case's purchases rather than naming them, so a flag "
    "that reshuffled the also-rans without moving the target, the reachability "
    "or the count would read as inert here. "
    "Expect that outcome: (1) MockXApplication re-sorts the store's candidate "
    "pool by (-overlap, id) before cutting the page, and every profile matching "
    "all the query's tokens ties on overlap, so either result_order collapses "
    "to 'AND-matches by ascending id'; (2) build_search_query is name-only, so "
    "no query ever carries a bio term and the name_username_bio index is never "
    "consulted. Compare digests before treating a flag as a sensitivity check. "
    "`python -m mock_x_platform.refinement --scope both --order both` runs every "
    "combination and prints which flags moved nothing."
)

# The clues a sender adds after leading with a name and a company, in the order
# they are shuffled into each ladder.
CLUE_FIELDS = ("role", "location", "school")


@dataclass(frozen=True, slots=True)
class RefinementTurn:
    """One thing the user says, and the criteria that result from saying it."""

    type: str
    field: str
    value: str
    criteria: RecipientCriteria


@dataclass(frozen=True, slots=True)
class RefinementCase:
    id: int
    expected_profile_id: str
    initial_criteria: RecipientCriteria
    turns: tuple[RefinementTurn, ...]


def build_refinement_cases(
    store: MockXStore, *, limit: int | None = None, seed: int = 42
) -> list[RefinementCase]:
    """One refinement ladder per evaluation case, labeled from the real profile.

    The initial criteria are name plus company: the Joe Bart scenario, where the
    sender leads with the strongest clue they have and it happens to be one the
    account does not carry. Each later turn adds one clue the sender knows.

    A turn's type is read off the stored profile rather than assumed. If every
    token of the clue appears in the profile's bio or location it is on_profile
    and could in principle discriminate; if it does not, it is off_profile and
    provably cannot, whatever the ranker does with it.
    """

    randomizer = random.Random(seed)
    cases: list[RefinementCase] = []
    for record in store.evaluation_cases(limit=limit):
        truth = json.loads(record["criteria_json"])
        profile = store.get_profile(str(record["expected_profile_id"]))
        if profile is None:
            continue

        # What the platform can actually see about this person. Whole tokens,
        # because whole tokens are what the mock's FTS index matches.
        visible = set(
            normalize_text(
                f"{profile['name']} {profile['description']} {profile['location']}"
            ).split()
        )
        initial = RecipientCriteria.from_dict(
            {"name": truth.get("name", ""), "company": truth.get("company", "")}
        )

        running = initial
        turns: list[RefinementTurn] = []
        order = list(CLUE_FIELDS)
        randomizer.shuffle(order)
        for field_name in order:
            value = str(truth.get(field_name, "") or "")
            if not value:
                continue
            running = RecipientCriteria.from_dict(
                {**running.to_dict(), field_name: value}
            )
            turns.append(
                RefinementTurn(
                    type="on_profile" if _visible(visible, value) else "off_profile",
                    field=field_name,
                    value=value,
                    criteria=running,
                )
            )

        # The user remembers the account goes by a different form of the name --
        # "he goes by Joe, not Joseph". Generated only where the display name
        # shares *some but not all* tokens with the name the sender started
        # from, which is the knowledge a person could plausibly have.
        #
        # The two excluded ends are excluded on purpose. Sharing every token
        # means there is nothing to correct. Sharing none means the account
        # displays something like "jbart", and a user who could produce that
        # string would have used handle mode instead -- crediting search with
        # that turn would quietly launder the handle shortcut into a search win
        # and hide the population the recovery path exists for.
        display_name = str(profile["name"])
        display_tokens = set(normalize_text(display_name).split())
        truth_tokens = set(normalize_text(str(truth.get("name", ""))).split())
        shared = display_tokens & truth_tokens
        if shared and shared != truth_tokens:
            running = RecipientCriteria.from_dict(
                {**running.to_dict(), "name": display_name}
            )
            turns.append(
                RefinementTurn("narrowing_name", "name", display_name, running)
            )

        # Every ladder ends in handle mode. Its share of the convergences is the
        # product finding: how much of the corpus is reachable no other way.
        turns.append(
            RefinementTurn("handle", "handle", str(profile["username"]), running)
        )
        cases.append(
            RefinementCase(
                id=int(record["id"]),
                expected_profile_id=str(profile["id"]),
                initial_criteria=initial,
                turns=tuple(turns),
            )
        )
    return cases


def _visible(profile_tokens: set[str], clue: str) -> bool:
    tokens = set(normalize_text(clue).split())
    return bool(tokens) and tokens <= profile_tokens


# The visible screen is ten profiles. Success is arriving on it, not arriving
# first: the device's own principle is that the AI ranks and the human confirms.
VISIBLE_DEPTH = 10

# How deep to look when asking whether a person is reachable at all. This probe
# goes through the store rather than the API, so it is never billed.
REACHABILITY_DEPTH = 1000


def run_refinement(
    database_path: str | Path,
    *,
    output_directory: str | Path,
    case_limit: int | None = None,
    match_scope: str = "name_username",
    result_order: str = "bm25",
    seed: int = 42,
) -> tuple[dict[str, Any], Path, Path]:
    """Drive every ladder and report whether refinement converges."""

    database = Path(database_path)
    store = MockXStore(database, match_scope=match_scope, result_order=result_order)
    application = MockXApplication(store)
    cases = build_refinement_cases(store, limit=case_limit, seed=seed)
    if not cases:
        raise RuntimeError("No evaluation cases exist. Generate the dataset first.")

    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    per_case: list[dict[str, Any]] = []
    for case in cases:
        purchased_before = store.ledger.distinct_profiles
        # Turn 0 is the first search, before any refinement. Every later index
        # is one thing the user said.
        rank, identifiers = _rank_of(
            application, case.initial_criteria, case.expected_profile_id
        )
        ranks = [rank]
        rows.append(
            _row(
                case,
                index=0,
                turn_type="initial",
                field="",
                rank=rank,
                previous=None,
                top_ids=identifiers,
                previous_ids=None,
            )
        )
        for index, turn in enumerate(case.turns, start=1):
            previous_ids = identifiers
            rank, identifiers = (
                _handle_rank(application, turn.value, case.expected_profile_id)
                if turn.type == "handle"
                else _rank_of(application, turn.criteria, case.expected_profile_id)
            )
            rows.append(
                _row(
                    case,
                    index=index,
                    turn_type=turn.type,
                    field=turn.field,
                    rank=rank,
                    previous=ranks[-1],
                    top_ids=identifiers,
                    previous_ids=previous_ids,
                )
            )
            ranks.append(rank)

        search_ranks = ranks[: len(ranks) - 1]  # everything before the handle turn
        visible_at = next(
            (index for index, rank in enumerate(search_ranks) if rank is not None), None
        )
        per_case.append(
            {
                "case_id": case.id,
                "expected_profile_id": case.expected_profile_id,
                "turns": len(case.turns),
                "turns_to_visible": visible_at,
                "converged_by_search": visible_at is not None,
                "reachable": _reachable(store, case),
                "profiles_purchased": store.ledger.distinct_profiles - purchased_before,
            }
        )

    summary = _summarize(
        cases,
        rows,
        per_case,
        store=store,
        match_scope=match_scope,
        result_order=result_order,
        seed=seed,
        database=database,
        duration=time.perf_counter() - started,
    )
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output / f"refinement-{match_scope}-{result_order}-{stamp}.json"
    csv_path = output / f"refinement-{match_scope}-{result_order}-{stamp}.csv"
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_csv(csv_path, rows)
    _print_summary(summary, json_path, csv_path)
    return summary, json_path, csv_path


def _rank_of(
    application: MockXApplication, criteria: RecipientCriteria, expected_id: str
) -> tuple[int | None, list[str]]:
    """Where the expected profile lands on the visible screen, and who is on it.

    Both halves matter and they are not the same measurement. The rank can move
    while the retrieved set does not: `build_search_query` returns the first
    non-empty of name, company, school, role, so adding a role or a location
    leaves the query byte-identical and the page byte-identical, and only
    `rank_candidates` reorders what was already there. Recording the ids is what
    lets the report say "re-ranked" rather than "re-retrieved".

    A query the mock rejects (outside its allowed character set) is recorded as
    "not visible" rather than allowed to raise, so one malformed case cannot
    abort a multi-hour sweep over a corpus we have not seen before.
    """

    query = build_search_query(criteria)
    try:
        response = application.search_users(query, VISIBLE_DEPTH)
    except MockXHttpError:
        return None, []
    ranked = rank_candidates(
        [Candidate.from_dict(profile) for profile in response["data"]], criteria
    )
    identifiers = [candidate.id for candidate in ranked]
    rank = identifiers.index(expected_id) + 1 if expected_id in identifiers else None
    return rank, identifiers


def _handle_rank(
    application: MockXApplication, handle: str, expected_id: str
) -> tuple[int | None, list[str]]:
    """A handle turn resolves one account, so it is rank 1 or nothing."""

    try:
        resolved = application.lookup_user_by_username(handle)["data"]
    except MockXHttpError:
        return None, []
    identifier = str(resolved["id"])
    return (1 if identifier == expected_id else None), [identifier]


def _reachable(store: MockXStore, case: RefinementCase) -> bool:
    """Is this person reachable by any search anywhere in the ladder?

    Every criteria state is checked except the handle turn's, because
    narrowing_name changes the query and can rescue a case the opening name
    could not reach. Checking only the opening state would overstate the
    unconvergeable share.

    False here is the structurally unconvergeable case: no ranking change, no
    refinement turn and no amount of extra depth recovers them, and only the
    handle does. That share is the product finding.

    Goes through the store rather than the API, so asking the question costs
    nothing and never appears in the bill.
    """

    states = [
        case.initial_criteria,
        *(turn.criteria for turn in case.turns if turn.type != "handle"),
    ]
    for criteria in states:
        query = build_search_query(criteria)
        if not query:
            continue
        rows = store.search_profiles(query, limit=REACHABILITY_DEPTH)
        if any(str(row["id"]) == case.expected_profile_id for row in rows):
            return True
    return False


def _row(
    case: RefinementCase,
    *,
    index: int,
    turn_type: str,
    field: str,
    rank: int | None,
    previous: int | None,
    top_ids: list[str],
    previous_ids: list[str] | None,
) -> dict[str, Any]:
    return {
        "case_id": case.id,
        "expected_profile_id": case.expected_profile_id,
        "turn_index": index,
        "turn_type": turn_type,
        "field": field,
        "rank": rank,
        "visible": rank is not None,
        # Turn 0 has no prior turn to compare against, so it cannot have
        # "improved" or "worsened" -- gated on `index > 0`, not on `previous is
        # not None`. Those are not the same condition: `previous` is also None
        # whenever the prior turn was a genuine miss (rank not found), and a
        # later turn recovering from that miss -- rank going from None to
        # found -- is a real improvement that must still be counted. Gating on
        # `previous is not None` would silently zero out exactly the
        # miss-to-hit transitions the handle turn exists to produce.
        "improved": index > 0 and _moved(previous, rank) > 0,
        "worsened": index > 0 and _moved(previous, rank) < 0,
        # The structural question, separate from the ranking one: did this turn
        # change *who* was retrieved, or only the order they were shown in?
        # Compared as sets on purpose -- rank_candidates reorders the same ten
        # every turn, and reordering is exactly what this must not count.
        "retrieval_changed": (
            previous_ids is not None and set(top_ids) != set(previous_ids)
        ),
        "top_ids": "|".join(top_ids),
    }


def _moved(previous: int | None, current: int | None) -> int:
    """Positive when the target rose, negative when it fell, zero when it stayed.

    A miss is treated as one place worse than the deepest visible rank, so
    falling off the screen counts as falling rather than as no change.
    """

    if previous is None and current is None:
        return 0
    before = VISIBLE_DEPTH + 1 if previous is None else previous
    after = VISIBLE_DEPTH + 1 if current is None else current
    return before - after


def _summarize(
    cases: list[RefinementCase],
    rows: list[dict[str, Any]],
    per_case: list[dict[str, Any]],
    *,
    store: MockXStore,
    match_scope: str,
    result_order: str,
    seed: int,
    database: Path,
    duration: float,
) -> dict[str, Any]:
    refinement_rows = [row for row in rows if row["turn_index"] > 0]
    by_type = {
        turn_type: _turn_metrics(
            [row for row in refinement_rows if row["turn_type"] == turn_type]
        )
        for turn_type in TURN_TYPES
    }
    # Search turns only. The handle turn is the last turn of every case and is
    # 100% visible by construction, so bucketing it by index drops a guaranteed
    # hit into whichever index that case's ladder happened to end on -- the
    # by-index series then reports ladder length rather than whether saying more
    # about a person brings them onto the screen. It is reported on its own,
    # unchanged, in `by_turn_type`.
    search_rows = [row for row in rows if row["turn_type"] != "handle"]
    indexes = sorted({row["turn_index"] for row in search_rows})
    by_index = {
        str(index): _turn_metrics(
            [row for row in search_rows if row["turn_index"] == index]
        )
        for index in indexes
    }
    converged = [item for item in per_case if item["converged_by_search"]]
    unreachable = [item for item in per_case if not item["reachable"]]
    purchased = [item["profiles_purchased"] for item in per_case]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_seconds": round(duration, 3),
        "database": str(database.resolve()),
        "match_scope": match_scope,
        "result_order": result_order,
        # The two keys above name the settings; this one names the result. See
        # FLAG_SENSITIVITY_NOTE -- without it a reader has no way to tell a
        # sensitivity check from the same measurement filed twice.
        "observation_digest": _observation_digest(rows, per_case),
        "flag_sensitivity": FLAG_SENSITIVITY_NOTE,
        "seed": seed,
        "case_count": len(cases),
        "turn_count": len(refinement_rows),
        "by_turn_type": by_type,
        "by_turn_index": by_index,
        "convergence": {
            "rate": _ratio(len(converged), len(per_case)),
            "mean_turns_to_visible": (
                round(
                    sum(item["turns_to_visible"] for item in converged) / len(converged),
                    4,
                )
                if converged
                else None
            ),
            "monotonicity_violations": sum(row["worsened"] for row in refinement_rows),
            "structurally_unconvergeable": _ratio(len(unreachable), len(per_case)),
            "unconvergeable_cases": len(unreachable),
            # Marginal, not per-session: BillingLedger.record_profiles dedups
            # against the whole run, so a profile an earlier case already paid
            # for costs a later case nothing. This is systematically below what
            # one case run in isolation would buy, and moves with how much the
            # corpus overlaps across cases rather than with the phenomenon being
            # measured. See the same caveat on the "cost" block below.
            "marginal_profiles_purchased_per_case": (
                round(sum(purchased) / len(purchased), 4) if purchased else 0.0
            ),
            # Spec 4's primary metric: what one *found* person costs. The
            # per-case figure above divides by cases that never converged too,
            # so it answers a different question and both are kept. Same
            # marginal caveat as above -- the numerator is the whole run's
            # deduplicated purchases, not a per-session bill. None rather than
            # 0.0 when nothing converged: zero purchases per convergence would
            # read as "free", where the truth is the denominator does not exist.
            "marginal_profiles_purchased_per_convergence": (
                round(sum(purchased) / len(converged), 4) if converged else None
            ),
        },
        "cost": store.ledger.summary(case_count=len(cases)),
        "cases": per_case[:200],
    }


def _turn_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    if not count:
        # None, not 0.0 -- an unmeasured turn type (for example narrowing_name
        # on a corpus where no case earns one) must be distinguishable from a
        # measured 0% visible rate. Conflating the two would make an absence
        # read as a finding, and narrowing_name is the one search turn that can
        # actually change the query, so its absence would silently turn the
        # "0 re-retrieved" headline into a tautology computed only over turn
        # types that provably cannot re-retrieve.
        return {
            "turns": 0,
            "visible": None,
            "improved": 0,
            "worsened": 0,
            "unchanged": 0,
            "retrieval_changed": 0,
        }
    return {
        "turns": count,
        "visible": _ratio(sum(row["visible"] for row in rows), count),
        "improved": sum(row["improved"] for row in rows),
        "worsened": sum(row["worsened"] for row in rows),
        "unchanged": sum(
            not row["improved"] and not row["worsened"] for row in rows
        ),
        "retrieval_changed": sum(row["retrieval_changed"] for row in rows),
    }


def _observation_digest(
    rows: list[dict[str, Any]], per_case: list[dict[str, Any]]
) -> str:
    """A stable fingerprint of everything a run actually observed.

    Two halves, because the harness reports two kinds of number and a flag can
    move one without the other. Per turn: which case, which turn, where the
    target landed, whether it was on screen, whether the retrieved set moved.
    Per case: whether the target was reachable at all, and how many profiles the
    case bought.

    The per-case half is not decoration. `_reachable` probes the store at depth
    1000, far below the ten rows a turn ever sees, so `result_order` reorders
    what falls inside that probe and changes `unconvergeable_cases` while every
    turn of every case lands identically. A digest over turns alone is identical
    across that difference, and anything reading "identical digest" as "the flag
    moved nothing" would then be wrong in the harness's own report.

    Excluded on purpose: when the run happened, how long it took, and which
    flags were asked for -- a digest that moved with those could never answer
    the question it exists to answer.
    """

    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            "\x1f".join(
                (
                    str(row["case_id"]),
                    str(row["turn_index"]),
                    "-" if row["rank"] is None else str(row["rank"]),
                    "1" if row["visible"] else "0",
                    "1" if row["retrieval_changed"] else "0",
                )
            ).encode("utf-8")
        )
        digest.update(b"\x1e")
    digest.update(b"\x1d")
    for item in per_case:
        digest.update(
            "\x1f".join(
                (
                    str(item["case_id"]),
                    "1" if item["reachable"] else "0",
                    str(item["profiles_purchased"]),
                )
            ).encode("utf-8")
        )
        digest.update(b"\x1e")
    return digest.hexdigest()


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _print_summary(summary: dict[str, Any], json_path: Path, csv_path: Path) -> None:
    print(
        f"\nRefinement: {summary['case_count']:,} cases, "
        f"{summary['turn_count']:,} turns  "
        f"[scope={summary['match_scope']} order={summary['result_order']}]"
    )
    print(
        f"  {'turn type':18}{'turns':>8}{'visible':>10}{'improved':>10}"
        f"{'worsened':>10}{'re-retrieved':>14}"
    )
    for turn_type, metrics in summary["by_turn_type"].items():
        visible = "n/a" if metrics["visible"] is None else f"{metrics['visible']:.1%}"
        print(
            f"  {turn_type:18}{metrics['turns']:>8,}{visible:>10}"
            f"{metrics['improved']:>10,}{metrics['worsened']:>10,}"
            f"{metrics['retrieval_changed']:>14,}"
        )
    convergence = summary["convergence"]
    mean_turns = convergence["mean_turns_to_visible"]
    print(
        f"\n  convergence by search   {convergence['rate']:.1%}"
        f"   mean turns to visible: {'n/a' if mean_turns is None else f'{mean_turns:.2f}'}"
    )
    print(
        f"  monotonicity violations {convergence['monotonicity_violations']:,}"
        f"   marginal profiles/case: "
        f"{convergence['marginal_profiles_purchased_per_case']:.1f}"
    )
    per_convergence = convergence["marginal_profiles_purchased_per_convergence"]
    print(
        f"  unconvergeable          {convergence['structurally_unconvergeable']:.1%}"
        f"   ({convergence['unconvergeable_cases']:,} cases reachable only by handle)"
    )
    print(
        "  marginal profiles/convergence: "
        f"{'n/a' if per_convergence is None else f'{per_convergence:.1f}'}"
        "   (purchases over converged cases; marginal, not per-session)"
    )
    cost = summary["cost"]
    print(
        f"  estimated cost          ${cost['per_case_usd'] or 0:.4f}/case"
        f"   (run total ${cost['estimated_usd']:,.2f})"
    )
    print(
        f"  observation digest      {summary['observation_digest'][:16]}"
        "   (same digest as another run = same ranks, same reachability, same"
        " purchases: that flag was inert, not a second measurement)"
    )
    print(f"JSON report: {json_path}")
    print(f"CSV detail: {csv_path}")


def main() -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Measure recipient refinement convergence")
    parser.add_argument("--database", default=str(settings.mock_x_database_path))
    parser.add_argument("--output", default=str(PROJECT_ROOT / ".cache" / "refinement"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--scope",
        default="both",
        choices=[*MATCH_SCOPES, "both"],
        help="What the query matches. 'both' reports under each, because we do not know.",
    )
    parser.add_argument(
        "--order",
        default="bm25",
        choices=[*RESULT_ORDERS, "both"],
        help="How results are sorted. A sensitivity check on the headline number.",
    )
    args = parser.parse_args()
    scopes = list(MATCH_SCOPES) if args.scope == "both" else [args.scope]
    orders = list(RESULT_ORDERS) if args.order == "both" else [args.order]
    digests: dict[tuple[str, str], str] = {}
    for scope in scopes:
        for order in orders:
            summary, _, _ = run_refinement(
                args.database,
                output_directory=args.output,
                case_limit=args.limit,
                match_scope=scope,
                result_order=order,
                seed=args.seed,
            )
            digests[(scope, order)] = summary["observation_digest"]
    print(_flag_sensitivity_line(digests, scopes, orders))
    return 0


def _flag_sensitivity_line(
    digests: dict[tuple[str, str], str], scopes: list[str], orders: list[str]
) -> str:
    """Say which of the swept flags moved nothing, from the digests just produced.

    The sweep is the only place that has every combination in hand, so it is the
    only place that can answer this without a human diffing report files. A flag
    counts as inert when changing it leaves the digest identical at every setting
    of the other flag -- not merely at one of them.
    """

    swept = [
        name
        for name, values in (("match_scope", scopes), ("result_order", orders))
        if len(values) > 1
    ]
    if not swept:
        return (
            "\nFlag sensitivity: one combination run, so nothing to compare. "
            "Sweep --scope both --order both to find out whether either flag "
            "moves anything on this corpus."
        )
    inert = []
    if len(scopes) > 1 and all(
        len({digests[(scope, order)] for scope in scopes}) == 1 for order in orders
    ):
        inert.append("match_scope")
    if len(orders) > 1 and all(
        len({digests[(scope, order)] for order in orders}) == 1 for scope in scopes
    ):
        inert.append("result_order")
    moved = [name for name in swept if name not in inert]
    parts = []
    if inert:
        parts.append(
            f"{' and '.join(inert)} inert -- identical observation_digest at every "
            "setting, so those reports are one measurement, not several"
        )
    if moved:
        parts.append(
            f"{' and '.join(moved)} changed what the run observed -- those "
            "reports are separate measurements"
        )
    return f"\nFlag sensitivity: {'; '.join(parts)}."


if __name__ == "__main__":
    raise SystemExit(main())
