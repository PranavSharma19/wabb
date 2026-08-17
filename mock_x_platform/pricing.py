"""X API rates, and a ledger that bills the way X bills.

Checked against docs.x.com on the date in RATES_CHECKED_ON. The subscription
tiers are retired; X charges per resource returned, against credits bought up
front. Two facts drive every number below:

  * A User costs $0.010. `max_results` defaults to 100, so a search that forgets
    the parameter costs a dollar and `max_results=1000` costs ten.
  * Resources are deduplicated inside 24-hour UTC windows. The billable unit is
    therefore *distinct profiles seen*, not requests made: fetching the same ten
    profiles ten times costs $0.10, while fetching ten new ones ten times costs
    $1.00. That is why lazy incremental depth is the economically correct
    architecture rather than merely a nice optimisation.

Update this module, and the date, when the rates move.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


RATES_CHECKED_ON = "2026-08-17"

USER_USD = 0.010
# X publishes DM sends in a $0.010-$0.015 band. Taking the top of it means an
# estimate here never understates what a real run would have cost.
DM_EVENT_USD = 0.015

# X documents "24-hour windows" without pinning the boundary, so this models them
# as UTC calendar days. Close enough for an estimate, and wrong in the safe
# direction: a real rolling window would deduplicate at least as much.
DEDUP_WINDOW = "UTC calendar day"


def window_key(moment: datetime) -> str:
    """Which deduplication window a moment falls in."""

    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d")


class BillingLedger:
    """What a run would have cost on the real API.

    Deliberately not a request counter. A counter would report the metric that
    looks natural and is wrong; this reports the one X actually charges on.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, set[str]] = {}
        self.searches = 0
        self.lookups = 0
        self.dm_sends = 0

    def record_profiles(
        self, profile_ids: Iterable[str], *, at: datetime | None = None
    ) -> int:
        """Record profiles served. Returns how many of them were newly billed."""

        window = window_key(at or datetime.now(timezone.utc))
        seen = self._profiles.setdefault(window, set())
        before = len(seen)
        seen.update(str(identifier) for identifier in profile_ids)
        return len(seen) - before

    def record_search(self) -> None:
        self.searches += 1

    def record_lookup(self) -> None:
        self.lookups += 1

    def record_dm_send(self) -> None:
        self.dm_sends += 1

    def reset(self) -> None:
        self._profiles.clear()
        self.searches = self.lookups = self.dm_sends = 0

    @property
    def distinct_profiles(self) -> int:
        return sum(len(identifiers) for identifiers in self._profiles.values())

    @property
    def billed_windows(self) -> int:
        return len(self._profiles)

    @property
    def estimated_usd(self) -> float:
        return round(
            self.distinct_profiles * USER_USD + self.dm_sends * DM_EVENT_USD, 6
        )

    def summary(self, *, case_count: int = 0) -> dict[str, Any]:
        return {
            "rates_checked_on": RATES_CHECKED_ON,
            "user_usd": USER_USD,
            "dedup_window": DEDUP_WINDOW,
            "distinct_profiles": self.distinct_profiles,
            "billed_windows": self.billed_windows,
            "searches": self.searches,
            "lookups": self.lookups,
            "dm_sends": self.dm_sends,
            "estimated_usd": self.estimated_usd,
            "per_case_usd": (
                round(self.estimated_usd / case_count, 6) if case_count else None
            ),
            # Said out loud so nobody reads a $400 evaluation as a $400 product:
            # the run total is the price of sweeping the corpus, and per_case_usd
            # is the one that resembles a real recipient search.
            "scope": "corpus sweep, not one device session",
        }
