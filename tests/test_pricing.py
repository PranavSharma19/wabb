from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mock_x_platform.pricing import USER_USD, BillingLedger


MOMENT = datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc)


def test_the_same_profile_inside_one_window_is_billed_once() -> None:
    ledger = BillingLedger()

    ledger.record_profiles(["1", "2", "3"], at=MOMENT)
    newly_billed = ledger.record_profiles(["2", "3", "4"], at=MOMENT + timedelta(hours=2))

    # This is the fact the whole design rests on: the billable unit is distinct
    # profiles seen, not requests made.
    assert newly_billed == 1
    assert ledger.distinct_profiles == 4
    assert ledger.estimated_usd == round(4 * USER_USD, 6)


def test_the_same_profile_in_the_next_window_is_billed_again() -> None:
    ledger = BillingLedger()

    ledger.record_profiles(["1"], at=MOMENT)
    ledger.record_profiles(["1"], at=MOMENT + timedelta(days=1))

    assert ledger.billed_windows == 2
    assert ledger.distinct_profiles == 2


def test_a_lookup_bills_one_user_where_a_search_bills_ten() -> None:
    search_ledger = BillingLedger()
    search_ledger.record_search()
    search_ledger.record_profiles([str(index) for index in range(10)], at=MOMENT)

    lookup_ledger = BillingLedger()
    lookup_ledger.record_lookup()
    lookup_ledger.record_profiles(["1"], at=MOMENT)

    assert search_ledger.estimated_usd == round(10 * USER_USD, 6)
    assert lookup_ledger.estimated_usd == round(USER_USD, 6)


def test_the_summary_leads_with_the_per_case_figure() -> None:
    ledger = BillingLedger()
    ledger.record_profiles([str(index) for index in range(100)], at=MOMENT)

    summary = ledger.summary(case_count=50)

    assert summary["per_case_usd"] == round(100 * USER_USD / 50, 6)
    assert summary["distinct_profiles"] == 100
    assert summary["rates_checked_on"]
