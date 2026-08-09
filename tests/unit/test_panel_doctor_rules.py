"""The health report's rule tables, without a store (`V2-P1-012`).

Everything here is pure: code/category/severity closure, the freshness thresholds and where
each one comes from, the known-limitation registry join, and the cross-dataset containment
rules. `tests/integration/panel/test_panel_doctor.py` is the other half -- it injects one
defect of each kind into a real store and asserts the report names it.

The division is `panel/catalog.py`'s: a rule table is tested as a rule table, and the I/O that
feeds it is tested against real files.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from openalpha_cn.domain.adjustment import KNOWN_ADJUSTMENT_LIMITATIONS
from openalpha_cn.domain.daily_prices import KNOWN_PRICE_LIMITATIONS
from openalpha_cn.domain.financial_statements import KNOWN_FINANCIAL_STATEMENT_LIMITATIONS
from openalpha_cn.domain.index_membership import KNOWN_INDEX_MEMBERSHIP_LIMITATIONS
from openalpha_cn.domain.industry_classification import KNOWN_INDUSTRY_LIMITATIONS
from openalpha_cn.domain.price_limits import KNOWN_SUSPENSION_LIMITATIONS
from openalpha_cn.domain.stock_universe import KNOWN_UNIVERSE_LIMITATIONS
from openalpha_cn.domain.trading_calendar import (
    KNOWN_CALENDAR_LOOKAHEAD,
    CalendarDay,
    TradingCalendar,
    build_trading_calendar,
)
from openalpha_cn.panel.catalog import READINESS_ISSUE_CODES, DatasetReadiness, ReadinessIssue
from openalpha_cn.panel_doctor import (
    DATASET_CADENCE,
    DOCTOR_ISSUE_CODES,
    FRESHNESS_PUBLICATION_SLACK,
    HEALTH_CATEGORIES,
    HEALTH_CODE_CATEGORY,
    HEALTH_SEVERITIES,
    KNOWN_PANEL_LIMITATIONS,
    PANEL_HEALTH_CODES,
    QUARTERLY_DISCLOSURE_BOUND,
    SUBJECT_CONTAINMENTS,
    PanelDoctorError,
    findings_from_readiness,
    freshness_policy,
    known_limitations,
    subject_containment_findings,
)

AS_OF = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)


def _calendar(
    opens: set[date], *, first: date, last: date, exchange: str = "SZSE"
) -> TradingCalendar:
    span = (last - first).days + 1
    return build_trading_calendar(
        exchange,
        [
            CalendarDay(
                calendar_date=first + timedelta(days=offset),
                is_trading=first + timedelta(days=offset) in opens,
            )
            for offset in range(span)
        ],
    )


def _weekday_calendar(
    *, first: date = date(2026, 1, 1), last: date = date(2026, 12, 31)
) -> TradingCalendar:
    span = (last - first).days + 1
    opens = {
        first + timedelta(days=offset)
        for offset in range(span)
        if (first + timedelta(days=offset)).weekday() < 5
    }
    return _calendar(opens, first=first, last=last)


# --- the closed code set -------------------------------------------------------------------


def test_every_readiness_code_the_evaluator_can_emit_has_a_category_here() -> None:
    """The doctor groups `evaluate_readiness`'s codes; a code it does not know would be
    reported with no category and silently lost from every by-category view. Equality rather
    than containment, so a code *added* upstream fails here instead of passing through."""
    readiness_codes = {code for code in HEALTH_CODE_CATEGORY if code in READINESS_ISSUE_CODES}

    assert readiness_codes == set(READINESS_ISSUE_CODES)


def test_the_health_code_set_is_exactly_the_readiness_codes_plus_this_module_s_own() -> None:
    assert PANEL_HEALTH_CODES == READINESS_ISSUE_CODES | DOCTOR_ISSUE_CODES
    assert not (READINESS_ISSUE_CODES & DOCTOR_ISSUE_CODES)
    assert set(HEALTH_CODE_CATEGORY) == set(PANEL_HEALTH_CODES)


def test_every_declared_category_is_reachable_from_some_code() -> None:
    """A category no code maps to is a heading `V2-P1-013` would branch on and never enter."""
    assert set(HEALTH_CODE_CATEGORY.values()) == set(HEALTH_CATEGORIES)


def test_the_four_categories_the_roadmap_names_are_all_present() -> None:
    assert {"missing", "stale", "duplicate", "revised"} <= HEALTH_CATEGORIES


# --- readiness issues become findings, keeping their codes ---------------------------------


def _readiness(*issues: ReadinessIssue) -> DatasetReadiness:
    return DatasetReadiness(
        dataset="daily",
        as_of=AS_OF,
        state="blocked" if issues else "ready",
        issues=issues,
        years_present=(2026,),
        row_count=0,
        subject_count=0,
        last_event_time=None,
        last_event_date=None,
        checks_waived=(),
    )


def test_a_readiness_issue_keeps_its_code_and_gains_a_category_and_a_blocking_severity() -> None:
    """`V2-P1-013` branches on the code, so the code must survive the trip through the report
    unchanged -- a doctor that renamed `partition_missing` would break the gate it exists for."""
    issue = ReadinessIssue(
        code="partition_missing", dataset="daily", year=2025, detail="no partition"
    )

    (finding,) = findings_from_readiness(_readiness(issue))

    assert finding.code == "partition_missing"
    assert finding.category == "missing"
    assert finding.severity == "blocking"
    assert finding.datasets == ("daily",)
    assert finding.year == 2025


def test_a_ready_dataset_contributes_no_findings() -> None:
    assert findings_from_readiness(_readiness()) == ()


def test_every_issue_is_carried_rather_than_only_the_first() -> None:
    issues = (
        ReadinessIssue(
            code="date_gap", dataset="daily", detail="a", missing_dates=(date(2026, 1, 5),)
        ),
        ReadinessIssue(code="stale", dataset="daily", detail="b"),
    )

    findings = findings_from_readiness(_readiness(*issues))

    assert [finding.code for finding in findings] == ["date_gap", "stale"]
    assert findings[0].dates == (date(2026, 1, 5),)


# --- freshness, by publication cadence -----------------------------------------------------


def test_every_dataset_the_ingest_module_writes_has_a_declared_cadence() -> None:
    from openalpha_cn import panel_ingest

    written = {
        panel_ingest.TRADING_CALENDAR_DATASET,
        panel_ingest.STOCK_BASIC_DATASET,
        panel_ingest.NAMECHANGE_DATASET,
        panel_ingest.ADJ_FACTOR_DATASET,
        panel_ingest.DAILY_DATASET,
        panel_ingest.DAILY_BASIC_DATASET,
        panel_ingest.SUSPENSION_DATASET,
        panel_ingest.PRICE_LIMIT_DATASET,
        panel_ingest.INDEX_WEIGHT_DATASET,
        panel_ingest.INDUSTRY_MEMBERSHIP_DATASET,
        panel_ingest.INDUSTRY_TREE_DATASET,
        *panel_ingest.FINANCIAL_STATEMENT_DATASETS,
    }

    assert written == set(DATASET_CADENCE)


def test_an_unknown_dataset_is_refused_rather_than_given_a_default_bound() -> None:
    with pytest.raises(
        PanelDoctorError, match=r"'not_a_dataset' has no declared publication cadence"
    ):
        freshness_policy("not_a_dataset")


def test_a_daily_bound_is_the_calendars_longest_closure_plus_the_publication_slack() -> None:
    """Derived, not chosen. A weekday calendar's longest closure is the weekend (Friday to
    Monday, three days), so the bound is three days plus the slack."""
    policy = freshness_policy("daily", calendar=_weekday_calendar())

    assert policy.cadence == "daily"
    assert policy.max_staleness == timedelta(days=3) + FRESHNESS_PUBLICATION_SLACK


def test_the_daily_bound_moves_with_the_calendar_it_was_derived_from() -> None:
    """The number is a property of the exchange's own schedule; a calendar carrying a long
    holiday must produce a wider bound than one without, or the derivation is a constant in
    disguise."""
    weekdays = _weekday_calendar()
    opens = set(weekdays.trading_days) - {
        date(2026, 2, 16) + timedelta(days=offset) for offset in range(9)
    }
    with_holiday = _calendar(opens, first=date(2026, 1, 1), last=date(2026, 12, 31))

    narrow = freshness_policy("daily", calendar=weekdays).max_staleness
    wide = freshness_policy("daily", calendar=with_holiday).max_staleness

    assert narrow is not None and wide is not None
    assert wide > narrow
    # 2026-02-13 (Friday) to 2026-02-25 (Wednesday), the first session after the nine-day
    # closure: twelve days.
    assert wide == timedelta(days=12) + FRESHNESS_PUBLICATION_SLACK


def test_a_daily_dataset_with_no_calendar_waives_the_bound_on_the_record() -> None:
    policy = freshness_policy("daily")

    assert policy.max_staleness is None
    assert "calendar" in policy.basis


def test_the_monthly_dataset_s_bound_is_the_longest_gap_between_month_end_sessions() -> None:
    """`index_weight` publishes on one session a month, so the daily bound would refuse a
    completely healthy panel for most of every month -- the failure `index_weight_requirement`
    names. The gap between consecutive month-end sessions is what a monthly publication can
    legitimately be behind by."""
    policy = freshness_policy("index_weight", calendar=_weekday_calendar())

    assert policy.cadence == "monthly"
    # The widest month-end gap on a weekday 2026 is 2026-05-29 (Friday) to 2026-06-30
    # (Tuesday): thirty-two days.
    assert policy.max_staleness == timedelta(days=32) + FRESHNESS_PUBLICATION_SLACK


def test_the_monthly_bound_is_wider_than_the_daily_one_on_the_same_calendar() -> None:
    calendar = _weekday_calendar()

    daily = freshness_policy("daily", calendar=calendar).max_staleness
    monthly = freshness_policy("index_weight", calendar=calendar).max_staleness

    assert daily is not None and monthly is not None
    assert monthly > daily


def test_a_monthly_bound_needs_two_month_ends_and_says_so_when_it_has_one() -> None:
    """A horizon of February plus ten days of March sees exactly one month end, because the
    March one is past the horizon. Reported as a waiver rather than as the eleven-day gap the
    truncated March would imply -- a bound that tight calls a healthy monthly panel stale for
    most of every month."""
    stops_mid_march = _weekday_calendar(first=date(2026, 2, 1), last=date(2026, 3, 10))

    policy = freshness_policy("index_weight", calendar=stops_mid_march)

    assert policy.max_staleness is None
    assert "two complete months" in policy.basis


def test_the_quarterly_bound_is_the_statutory_deadline_spacing_and_needs_no_calendar() -> None:
    """Filings arrive in four bursts fixed by the disclosure rules, not by the trading
    calendar, so this one is cited rather than derived."""
    policy = freshness_policy("income")

    assert policy.cadence == "quarterly"
    assert policy.max_staleness == QUARTERLY_DISCLOSURE_BOUND
    assert freshness_policy("income", calendar=_weekday_calendar()).max_staleness == (
        QUARTERLY_DISCLOSURE_BOUND
    )


def test_the_quarterly_bound_is_the_31_october_to_30_april_interval() -> None:
    """Stated as arithmetic rather than as a number, because the number is the arithmetic:
    the Q3 deadline is 31 October and the next one -- the annual report's -- is 30 April."""
    assert date(2028, 4, 30) - date(2027, 10, 31) == QUARTERLY_DISCLOSURE_BOUND


def test_an_event_driven_dataset_gets_no_event_clock_bound_and_names_why() -> None:
    policy = freshness_policy("stock_basic")

    assert policy.cadence == "event_driven"
    assert policy.max_staleness is None
    assert "schedule" in policy.basis


def test_the_calendar_dataset_is_published_in_advance_so_an_event_clock_bound_is_vacuous() -> None:
    policy = freshness_policy("trade_cal")

    assert policy.cadence == "published_in_advance"
    assert policy.max_staleness is None


def test_the_four_price_shaped_datasets_share_the_daily_cadence() -> None:
    assert {
        DATASET_CADENCE[name] for name in ("daily", "daily_basic", "adj_factor", "stk_limit")
    } == {"daily"}


def test_every_statement_dataset_is_quarterly() -> None:
    from openalpha_cn.panel_ingest import FINANCIAL_STATEMENT_DATASETS

    assert {DATASET_CADENCE[name] for name in FINANCIAL_STATEMENT_DATASETS} == {"quarterly"}


# --- the inherent-limitation registries ----------------------------------------------------


def test_every_entry_of_every_known_registry_reaches_the_report() -> None:
    """Eight registries, and the report must carry all of them: a limitation that is recorded
    in the codebase but absent from the health report is one a reader of the report will
    mistake for a defect of this fetch."""
    registry_total = sum(
        len(registry)
        for registry in (
            KNOWN_UNIVERSE_LIMITATIONS,
            KNOWN_ADJUSTMENT_LIMITATIONS,
            KNOWN_PRICE_LIMITATIONS,
            KNOWN_SUSPENSION_LIMITATIONS,
            KNOWN_INDEX_MEMBERSHIP_LIMITATIONS,
            KNOWN_INDUSTRY_LIMITATIONS,
            KNOWN_FINANCIAL_STATEMENT_LIMITATIONS,
        )
    )

    # The seven `(code, detail)` registries carry one entry each; the calendar's is a list of
    # dated instances of one defect, so it folds into a single entry carrying those dates.
    assert len(KNOWN_PANEL_LIMITATIONS) == registry_total + 1


def test_the_calendar_limitation_carries_its_proven_instances_as_dates() -> None:
    (entry,) = [item for item in KNOWN_PANEL_LIMITATIONS if item.datasets == ("trade_cal",)]

    assert entry.dates == tuple(
        sorted(instance.calendar_date for instance in KNOWN_CALENDAR_LOOKAHEAD)
    )
    assert len(entry.dates) == 3


def test_every_limitation_names_datasets_that_have_a_declared_cadence() -> None:
    """A limitation attached to a dataset the report never assesses is one no reader will ever
    be shown."""
    named = {dataset for item in KNOWN_PANEL_LIMITATIONS for dataset in item.datasets}

    assert named <= set(DATASET_CADENCE)


def test_a_limitation_is_identified_by_its_code_and_the_datasets_it_speaks_for() -> None:
    """The registry codes are unique *within* a registry and deliberately not across them:
    four separate registries record `silent_truncation_at_the_response_cap`, because the same
    defect really does recur at four different caps (`suspend_d`/`stk_limit` records a fifth
    instance of it under a different name). So the identity is the pair, and it is the pair
    that has to be unique -- a code alone would collapse four distinct disclosures into one
    line and hide three of the dataset groups it applies to."""
    keys = [(item.code, item.datasets) for item in KNOWN_PANEL_LIMITATIONS]

    assert len(keys) == len(set(keys))
    shared = [
        item.datasets
        for item in KNOWN_PANEL_LIMITATIONS
        if item.code == "silent_truncation_at_the_response_cap"
    ]
    assert len(shared) == 4


def test_a_report_scoped_to_one_dataset_carries_only_that_dataset_s_limitations() -> None:
    selected = known_limitations(("adj_factor",))

    assert selected
    assert {item.code for item in selected} == {item.code for item in KNOWN_ADJUSTMENT_LIMITATIONS}


def test_selecting_no_dataset_selects_no_limitation() -> None:
    assert known_limitations(()) == ()


def test_the_price_registry_reaches_both_price_datasets() -> None:
    """`KNOWN_PRICE_LIMITATIONS` speaks for `daily` and `daily_basic` jointly -- one of its
    entries is about `daily_basic` alone -- so a report on either must carry it."""
    from_daily = {item.code for item in known_limitations(("daily",))}
    from_basic = {item.code for item in known_limitations(("daily_basic",))}

    assert from_daily == from_basic == {item.code for item in KNOWN_PRICE_LIMITATIONS}


# --- cross-dataset composition -------------------------------------------------------------


def test_the_declared_containments_point_at_datasets_and_limitations_that_exist() -> None:
    codes = {item.code for item in KNOWN_PANEL_LIMITATIONS}

    for rule in SUBJECT_CONTAINMENTS:
        assert rule.subset in DATASET_CADENCE
        assert rule.superset in DATASET_CADENCE
        assert set(rule.related_limitations) <= codes


def test_a_wider_superset_is_the_normal_direction_and_reports_nothing() -> None:
    """The measured shape: 5,338 `daily` names against 5,387 `adj_factor` and 6,867
    `stk_limit`. A report that called that a defect would be one every reader learns to
    ignore, so only the *subset* side having something the superset lacks is a finding."""
    findings = subject_containment_findings(
        {
            "daily": frozenset({"000001.SZ", "000002.SZ"}),
            "adj_factor": frozenset({"000001.SZ", "000002.SZ", "000003.SZ"}),
            "stk_limit": frozenset({"000001.SZ", "000002.SZ", "000003.SZ", "150001.SZ"}),
            "daily_basic": frozenset({"000001.SZ", "000002.SZ"}),
            "stock_basic": frozenset({"000001.SZ", "000002.SZ", "000003.SZ"}),
        }
    )

    assert findings == ()


def test_a_priced_security_with_no_adjustment_factor_is_reported() -> None:
    findings = subject_containment_findings(
        {
            "daily": frozenset({"000001.SZ", "000002.SZ"}),
            "adj_factor": frozenset({"000001.SZ"}),
        }
    )

    (finding,) = findings
    assert finding.code == "subject_set_disagreement"
    assert finding.category == "inconsistent"
    assert finding.severity == "warning"
    assert finding.datasets == ("daily", "adj_factor")
    assert finding.items == ("000002.SZ",)
    assert finding.count == 1


def test_a_containment_whose_two_datasets_are_not_both_in_scope_is_not_checked() -> None:
    """Silence here would be indistinguishable from a pass, which is why the caller gets the
    findings and the report separately records which containments ran."""
    assert subject_containment_findings({"daily": frozenset({"000001.SZ"})}) == ()


def test_each_declared_containment_can_actually_fire() -> None:
    """Every rule is exercised: a rule that no input can trigger is a claim of coverage that
    is never paid for."""
    for rule in SUBJECT_CONTAINMENTS:
        findings = subject_containment_findings(
            {rule.subset: frozenset({"XXXXXX.SZ"}), rule.superset: frozenset()}
        )

        assert [finding.datasets for finding in findings] == [(rule.subset, rule.superset)], rule


def test_a_containment_finding_carries_the_limitations_that_could_explain_it() -> None:
    """Separating the two kinds of fault happens per finding, not only in a footer: a reader
    looking at a `daily`-minus-`stk_limit` residue needs to be told, right there, that the
    published-band corpus starts in 2007."""
    findings = subject_containment_findings(
        {"daily": frozenset({"920924.BJ"}), "stk_limit": frozenset()}
    )

    (finding,) = findings
    assert finding.related_limitations
    assert set(finding.related_limitations) <= {item.code for item in KNOWN_PANEL_LIMITATIONS}


# --- severities ------------------------------------------------------------------------------


def test_the_declared_severities_are_the_three_the_report_uses() -> None:
    assert frozenset({"blocking", "warning", "notice"}) == HEALTH_SEVERITIES


# --- the edges of the derivation and of the report's accessors -------------------------------


def test_a_calendar_with_one_session_cannot_supply_a_daily_bound() -> None:
    """A closure is a gap between two sessions, and one session has none. Reported as a waiver
    rather than as `timedelta(0)`, which would call every panel stale."""
    single = _calendar({date(2026, 1, 5)}, first=date(2026, 1, 1), last=date(2026, 1, 31))

    policy = freshness_policy("daily", calendar=single)

    assert policy.max_staleness is None
    assert "closure" in policy.basis


def test_a_month_the_horizon_starts_inside_still_contributes_its_own_month_end() -> None:
    """This horizon runs 2026-05-15..2026-07-31. June and July are complete; May is entered
    mid-month, and its last visible session -- 2026-05-29 -- *is* May's month end, because
    every later day of May is closed. Keeping it is what makes the widest gap the true 32 days
    (2026-05-29 to 2026-06-30) rather than the 31 (2026-06-30 to 2026-07-31) a rule that threw
    the month away would report, and too tight a bound is what calls a healthy panel stale.
    """
    through_july = _weekday_calendar(first=date(2026, 5, 15), last=date(2026, 7, 31))

    policy = freshness_policy("index_weight", calendar=through_july)

    assert policy.max_staleness == timedelta(days=32) + FRESHNESS_PUBLICATION_SLACK
    assert policy.max_staleness != timedelta(days=31) + FRESHNESS_PUBLICATION_SLACK


def test_a_finding_names_the_dataset_it_is_primarily_about() -> None:
    (finding,) = subject_containment_findings(
        {"daily": frozenset({"000001.SZ"}), "adj_factor": frozenset()}
    )

    assert finding.dataset == "daily"
    assert finding.datasets[0] == finding.dataset
