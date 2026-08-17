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
from openalpha_cn.domain.factor import FactorObservation, cross_section_digest
from openalpha_cn.domain.factor_transform import observation_digest
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
from openalpha_cn.panel.catalog import (
    KNOWN_STORAGE_LIMITATIONS,
    READINESS_ISSUE_CODES,
    DatasetReadiness,
    ReadinessIssue,
)
from openalpha_cn.panel_doctor import (
    BLOCKS_A_READ,
    DATASET_CADENCE,
    DOCTOR_ISSUE_CODES,
    FACTOR_PLANE_SEALS,
    FACTOR_SEAL_OBSERVATION_FIELDS,
    FRESHNESS_PUBLICATION_SLACK,
    HEALTH_CATEGORIES,
    HEALTH_CODE_CATEGORY,
    HEALTH_CODE_SEVERITY,
    HEALTH_SEVERITIES,
    KNOWN_PANEL_LIMITATIONS,
    PANEL_HEALTH_CODES,
    QUARTERLY_DISCLOSURE_BOUND,
    SUBJECT_CONTAINMENTS,
    PanelDoctorError,
    calendar_lookahead_findings,
    findings_from_readiness,
    freshness_policy,
    is_derived_factor_dataset,
    known_limitations,
    storage_limitations,
    subject_containment_findings,
)
from openalpha_cn.panel_factors import (
    FACTOR_MANIFEST_DATASET_PREFIX,
    FACTOR_MANIFEST_PANEL_COLUMNS,
    FACTOR_OBSERVATION_DATASET_PREFIX,
    FACTOR_OBSERVATION_PANEL_COLUMNS,
    FACTOR_PROCESSED_DATASET_PREFIX,
    FACTOR_TRANSFORM_MANIFEST_DATASET_PREFIX,
    PROCESSED_OBSERVATION_PANEL_COLUMNS,
    TRANSFORM_MANIFEST_PANEL_COLUMNS,
)
from openalpha_cn.panel_neutralization import (
    FACTOR_NEUTRALIZATION_MANIFEST_DATASET_PREFIX,
    FACTOR_NEUTRALIZED_DATASET_PREFIX,
    NEUTRALIZATION_MANIFEST_PANEL_COLUMNS,
    NEUTRALIZED_OBSERVATION_PANEL_COLUMNS,
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
    """Nine registries, and the report must carry all of them: a limitation that is recorded
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
    # dated instances of one defect, so it folds into a single entry carrying those dates; and
    # the storage plane's fold in one for one, naming no dataset because they hold for all.
    assert len(KNOWN_PANEL_LIMITATIONS) == registry_total + 1 + len(KNOWN_STORAGE_LIMITATIONS)


def test_the_storage_limitations_are_the_entries_that_name_no_dataset() -> None:
    """The two selectors partition one list rather than reading two.

    `known_limitations` answers "what can this dataset not tell me" and `storage_limitations`
    answers "what can this plane not tell me about any dataset", and a boundary that ended up
    in neither -- or in both -- would either vanish from every report or be shown twice.
    """
    scoped = {item.code for item in KNOWN_PANEL_LIMITATIONS if item.datasets}
    unscoped = {item.code for item in storage_limitations()}

    assert unscoped == {item.code for item in KNOWN_STORAGE_LIMITATIONS}
    assert not (scoped & unscoped)
    assert scoped | unscoped == {item.code for item in KNOWN_PANEL_LIMITATIONS}
    # A dataset-scoped question never returns them, which is what keeps
    # `known_limitations('adj_factor')` meaning "what the adjustment corpus cannot answer".
    assert not ({item.code for item in known_limitations(("adj_factor",))} & unscoped)


def test_the_two_quantified_storage_limitations_carry_the_count_they_were_measured_at() -> None:
    """A disclosure with no number in it is the prose these two replace.

    One says every insertion and deletion behind the store is now refused and a value edited in
    place is not; the other says `PanelStore.query()` passes no point-in-time gate. Each is
    only actionable if the reader is told how big it is, so each carries the count it was
    measured at on a real partition.

    Named "the two quantified" rather than "both": `KNOWN_STORAGE_LIMITATIONS` has more than two
    entries now, and the ones this test does not name carry a *category* of risk rather than a
    quantity -- see `test_the_visibility_filtered_read_is_disclosed_with_what_it_cannot_promise`.
    The assertions below are unchanged and were always about these two; what was wrong was the
    name, which counted the registry rather than the cases, and which therefore goes stale every
    time the registry grows. A test name is documentation, and a false one is the drift this
    repository refuses everywhere else.
    """
    by_code = {item.code: item.detail for item in KNOWN_STORAGE_LIMITATIONS}

    assert (
        "partition_row_count_mismatch"
        in by_code["a_value_edited_in_place_leaves_the_census_intact"]
    )
    assert "pct_chg" in by_code["a_value_edited_in_place_leaves_the_census_intact"]
    query_detail = by_code["panel_store_query_is_public_and_passes_no_point_in_time_gate"]
    assert "152" in query_detail and "92" in query_detail
    assert "read_if_ready" in query_detail


def test_the_visibility_filtered_read_is_disclosed_with_what_it_cannot_promise() -> None:
    """`V2-P3-002`'s third storage-plane boundary, held to the same standard as the two above.

    Written as a separate test rather than folded into
    `test_the_two_quantified_storage_limitations_carry_the_count_they_were_measured_at`, whose
    assertions are left exactly as they were: they name the two they name, and both claims are
    still true of them. Only that test's *name* changed, because "both" counted the registry
    rather than the two cases it actually checks, and the registry has since grown.

    What this one has to carry is different in kind from a count, and that difference is the
    reason `read_visible_at` is disclosed at all. `PanelStore.query()`'s entry is sized by a
    number (152 rows, 92 unknowable) because the risk is quantity. This one's risk is a
    *category*: filtering a partition written months after the sessions in it replays what the
    stored rows say was knowable then, which is not what a fetch made at that instant would have
    returned wherever the upstream restates or re-scopes. So the disclosure must name the
    mechanism (`available_time`), the bound on how much it changes
    (`ROW_FILTERABLE_ISSUE_CODES` -- one code, every other issue still refuses), and at least
    one measured instance of the divergence rather than a general worry.

    **The review found the last of those three too weak and it is strengthened here.** The
    assertion was `"81.7%" in detail`, which pins a string and no behaviour, and the entry it
    passed on named the *mechanism* without the *magnitude* -- `V2-P3-002`'s own Task-37 failure
    mode, existence checked and size not. Two things are now required of the sentence and both
    are facts a reader can act on: that 81.7% is stated as the affected **share** rather than as
    a citation, and that the entry says this path is where the bias first becomes reachable at
    all, because `read_if_ready` refuses a year partition at every `as_of` inside it. An entry
    that describes a divergence without saying it was previously unreachable describes an
    inherited condition, and this one is not.
    """
    by_code = {item.code: item.detail for item in KNOWN_STORAGE_LIMITATIONS}

    detail = by_code["a_visibility_filtered_read_replays_a_partition_that_was_not_there_yet"]
    assert "available_time" in detail
    assert "ROW_FILTERABLE_ISSUE_CODES" in detail
    assert "81.7%" in detail and "fina_indicator" in detail
    assert "withheld" in detail
    assert "affected share of keys is 81.7%" in detail
    assert "REACHABLE AT ALL" in detail and "read_if_ready refuses" in detail


def test_the_scope_carry_residue_of_the_filtered_read_is_disclosed_with_what_bounds_it() -> None:
    """`V2-P3-002`'s fourth boundary, added by the review that found the third one incomplete.

    A readiness check that *passed* over the whole partition was being carried to an answer made
    of a subset of its rows. Two of the three checks that can break under that are re-decided
    against the returned rows; `date_gap` is not, and an exclusion is only a disclosure if it
    says what still bounds the gap. So the entry has to name the mechanism, the reason the
    re-check is not run, and the two checks that do still catch the ordinary cases -- otherwise
    it reads as "we know" rather than as "here is the shape of what gets through".
    """
    by_code = {item.code: item.detail for item in KNOWN_STORAGE_LIMITATIONS}

    detail = by_code["date_gap_clears_on_partition_rows_the_filtered_read_withholds"]
    assert "SCOPE_SENSITIVE_ISSUE_CODES" in detail
    assert "_date_census" in detail
    assert "no-op" in detail and "_sessions_published_through" in detail
    assert "stale" in detail


def test_the_storage_plane_discloses_every_boundary_it_declares_and_the_report_carries_them() -> (
    None
):
    """The total, so a disclosure that quietly stopped reaching a reader fails.

    Every other assertion about these entries is per-entry and would be satisfied by a registry
    that lost one. `KNOWN_PANEL_LIMITATIONS` is where a reader of `panel doctor` actually meets
    them, so the count is asserted on both sides of the fold.

    **The set grew from three to four in `V2-P3-002`'s remediation, and that is the assertion
    working rather than an exception to it.** The name carried the number `three` and no longer
    does, because a count in a test name is a second copy of the table that nothing checks.

    The judgement criterion, since more than one branch may add an entry: this set is the union
    of every code `KNOWN_STORAGE_LIMITATIONS` declares. A diff that adds an entry adds it here in
    the same diff; a diff that finds this failing after a merge adds the missing code rather than
    deleting the one it does not recognise. Losing a member to a merge is the exact failure
    `V2-P2`'s remediation paid for once already.
    """
    codes = {item.code for item in KNOWN_STORAGE_LIMITATIONS}

    assert codes == {
        "a_value_edited_in_place_leaves_the_census_intact",
        "panel_store_query_is_public_and_passes_no_point_in_time_gate",
        "a_visibility_filtered_read_replays_a_partition_that_was_not_there_yet",
        "date_gap_clears_on_partition_rows_the_filtered_read_withholds",
        "a_derived_partition_may_outlive_the_build_its_rows_point_at",
    }
    assert codes <= {item.code for item in KNOWN_PANEL_LIMITATIONS}
    assert all(item.datasets == () for item in KNOWN_PANEL_LIMITATIONS if item.code in codes)


def test_the_calendar_limitation_carries_its_proven_instances_as_dates() -> None:
    (entry,) = [item for item in KNOWN_PANEL_LIMITATIONS if item.datasets == ("trade_cal",)]

    assert entry.dates == tuple(
        sorted(instance.calendar_date for instance in KNOWN_CALENDAR_LOOKAHEAD)
    )
    assert len(entry.dates) == 3


def test_the_calendar_fold_is_the_one_panel_code_no_dataset_registry_declares() -> None:
    """`_limitations()` folds eight registries and then appends a code of its own.

    Seven bound to the datasets they describe, `KNOWN_STORAGE_LIMITATIONS` bound to none
    because a storage boundary holds for every dataset at once -- but a declared registry
    either way, pinned by a set literal in its own module's tests. That covers every
    `KNOWN_PANEL_LIMITATIONS` entry except this one: it is constructed here rather than
    declared anywhere, so no domain test can name it and nothing did. Renaming it was a green
    change, and the name is what a reader of a health report searches for.

    Asserted as the exact difference rather than as a membership, so the arithmetic in
    `test_every_entry_of_every_known_registry_reaches_the_report` gains the name the count was
    standing in for -- and so a second locally constructed code has to be added here too. A new
    *registry* belongs on the folded side; only a code with no registry behind it belongs on
    the right.
    """
    folded = {
        item.code
        for registry in (
            KNOWN_UNIVERSE_LIMITATIONS,
            KNOWN_ADJUSTMENT_LIMITATIONS,
            KNOWN_PRICE_LIMITATIONS,
            KNOWN_SUSPENSION_LIMITATIONS,
            KNOWN_INDEX_MEMBERSHIP_LIMITATIONS,
            KNOWN_INDUSTRY_LIMITATIONS,
            KNOWN_FINANCIAL_STATEMENT_LIMITATIONS,
            KNOWN_STORAGE_LIMITATIONS,
        )
        for item in registry
    }

    assert {item.code for item in KNOWN_PANEL_LIMITATIONS} - folded == {
        "the_published_schedule_can_be_amended_after_it_becomes_answerable"
    }
    assert folded - {item.code for item in KNOWN_PANEL_LIMITATIONS} == set()


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


# --- the calendar's own disclosed look-ahead, made conditional on the panel -------------------


def test_a_calendar_covering_a_proven_lookahead_date_reports_it_with_the_date_and_the_size() -> (
    None
):
    """The check `TradingCalendar.known_lookahead()` was built for and never had.

    Its docstring says it exists "so `V2-P1-013`'s gate can see them", and until this check no
    module under `src/` called it: on a real 2015 partition `panel doctor --json` answered
    `is_clean: true, findings: []` while the three dates sat in `limitations` in a sentence
    that reads the same whether the panel reaches them or not.
    """
    covering = _weekday_calendar(first=date(2015, 1, 1), last=date(2015, 12, 31))

    (finding,) = calendar_lookahead_findings(covering)
    assert finding.code == "calendar_lookahead_in_horizon"
    assert finding.category == "unanswerable"
    assert finding.severity == "notice"
    assert finding.datasets == ("trade_cal",)
    assert finding.dates == (date(2015, 9, 3), date(2015, 9, 4))
    assert finding.count == 2
    # The size, not only the date: 2015-01-01 to the 2015-05-13 announcement is 132 days, and a
    # reader deciding whether to scope around it needs the width rather than the fact.
    assert "132 days of look-ahead" in finding.detail
    assert finding.related_limitations == (
        "the_published_schedule_can_be_amended_after_it_becomes_answerable",
    )


def test_a_calendar_that_reaches_none_of_them_reports_nothing() -> None:
    """The conditional half, which is the whole improvement over the static prose: a 2026 panel
    is not contaminated by a 2015 amendment, and a check that fired anyway would be that prose
    with extra steps. That silence is distinguishable from a run with no calendar at all is the
    `cross_checks` entry's job, pinned in
    `tests/integration/panel/test_panel_doctor.py`."""
    clean = _weekday_calendar(first=date(2026, 1, 1), last=date(2026, 12, 31))

    assert calendar_lookahead_findings(clean) == ()


def test_every_proven_lookahead_instance_is_reachable_by_some_horizon() -> None:
    """A registry entry no window can surface is a disclosure that never reaches a reader.

    Both amendments are covered: 2015's two dates and 2020's one, each by a calendar spanning
    its own year, so an instance added to `KNOWN_CALENDAR_LOOKAHEAD` with a date this check
    cannot reach fails here rather than sitting unreported.
    """
    reported: set[date] = set()
    for instance in KNOWN_CALENDAR_LOOKAHEAD:
        year = instance.calendar_date.year
        horizon = _weekday_calendar(first=date(year, 1, 1), last=date(year, 12, 31))
        reported.update(
            day for finding in calendar_lookahead_findings(horizon) for day in finding.dates
        )

    assert reported == {instance.calendar_date for instance in KNOWN_CALENDAR_LOOKAHEAD}


# --- the event clock, which readiness never compares against as_of ---------------------------


def test_the_only_cadence_exempt_from_the_event_clock_is_the_one_that_publishes_ahead() -> None:
    """`event_after_as_of` cannot be a blanket rule, and the measurement is why: on the real
    panel this was taken against, `trade_cal` 2026 reaches 2026-12-30 -- five months past the
    read -- while every other partition's newest event is the day before it. The exemption is
    therefore the `published_in_advance` cadence, which exists to say exactly that, and
    `trade_cal` is its only member. A second dataset would earn the exemption by declaring the
    cadence rather than by being named in `panel_doctor`."""
    ahead = {name for name, cadence in DATASET_CADENCE.items() if cadence == "published_in_advance"}

    assert ahead == {"trade_cal"}


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


def test_the_severity_of_every_declared_code_is_pinned_code_by_code() -> None:
    """The table, written out.

    Severity is what decides `PanelHealthReport.is_clean`, so a single entry quietly demoted
    to `notice` is the one change to this module that turns a sick panel into a clean report
    without failing anything else. Spelling the whole mapping out here means any such change
    is a diff against this literal rather than a line nobody is looking at -- a per-code
    assertion scattered across the injection tests would have left `close_disagreement` in
    particular unpinned, because no test there asks what its severity is.
    """
    assert dict(HEALTH_CODE_SEVERITY) == {
        # `evaluate_readiness`'s own verdict, carried through: the dataset cannot be read.
        "no_years_requested": "blocking",
        "empty_requirement": "blocking",
        "not_yet_knowable": "blocking",
        "partition_missing": "blocking",
        "partition_file_missing": "blocking",
        "partition_file_unreadable": "blocking",
        "partition_row_count_mismatch": "blocking",
        "coverage_missing": "blocking",
        "coverage_stale": "blocking",
        "date_gap": "blocking",
        "subject_missing": "blocking",
        "field_missing": "blocking",
        "stale": "blocking",
        # Disagreement between two datasets, the report saying it could not look, and one
        # dataset whose stored rows its own reader refuses.
        "subject_set_disagreement": "warning",
        "close_disagreement": "warning",
        "return_path_disagreement": "warning",
        "unexplained_unpriced": "warning",
        "check_unavailable": "warning",
        "domain_rebuild_refused": "warning",
        # The event clock nothing in `evaluate_readiness` compares against `as_of`, measured
        # the same way `domain_rebuild_refused` was: the read this report clears is the read
        # that raised.
        "event_after_as_of": "warning",
        # Measured facts `V2-P1-011` showed to be ordinary on this corpus.
        "ambiguous_filing": "notice",
        "duplicate_versions": "notice",
        "revised_rows": "notice",
        # An inherent limitation of `trade_cal` with no read-side remedy, made conditional on
        # the panel's own horizon. See `HEALTH_CODE_SEVERITY` for the three-part argument.
        "calendar_lookahead_in_horizon": "notice",
        # `V2-P3-019`. The two derived-plane codes are the only ones this module concludes
        # for itself at `blocking`, because a broken seal is a read that succeeds and
        # returns different numbers rather than one that raises.
        "factor_seal_broken": "blocking",
        "factor_build_unaddressed": "blocking",
    }


def test_every_declared_code_has_a_severity_and_no_code_has_two() -> None:
    """Total over the closed set, so a code added to `DOCTOR_ISSUE_CODES` or upstream to
    `READINESS_ISSUE_CODES` fails here rather than reaching `_finding` with no verdict."""
    assert set(HEALTH_CODE_SEVERITY) == set(PANEL_HEALTH_CODES)
    assert set(HEALTH_CODE_SEVERITY.values()) <= HEALTH_SEVERITIES


def test_every_readiness_code_is_blocking_because_the_evaluator_already_said_so() -> None:
    """The doctor does not re-judge a verdict it did not make: `evaluate_readiness` emits a
    code only for a dataset it has decided cannot be read."""
    assert {HEALTH_CODE_SEVERITY[code] for code in READINESS_ISSUE_CODES} == {"blocking"}


def test_only_a_notice_is_excluded_from_the_verdict() -> None:
    """`is_clean` is "no blocking and no warning". Stated here against the constant the
    property reads, so that widening it to include `notice` -- which would make a panel with a
    duplicate filing look sick -- or narrowing it to `blocking` alone -- which would make a
    panel whose cross section cannot be adjusted look healthy -- fails a test."""
    assert frozenset({"blocking", "warning"}) == BLOCKS_A_READ
    assert BLOCKS_A_READ < HEALTH_SEVERITIES
    assert frozenset({"notice"}) == HEALTH_SEVERITIES - BLOCKS_A_READ


def test_a_readiness_issue_and_a_cross_dataset_finding_do_not_share_a_severity() -> None:
    """The three severities are not decoration: at least one code sits in each, or the field
    is a constant `V2-P1-013` would be branching on for nothing."""
    assert set(HEALTH_CODE_SEVERITY.values()) == HEALTH_SEVERITIES


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


def test_the_month_the_horizon_ends_on_its_last_day_is_complete_and_counts() -> None:
    """The other edge of the same filter, which the start-of-horizon test does not reach.

    A month is dropped when its last calendar day is *past* the horizon, so a horizon ending
    exactly on 30 June leaves June complete and June's month end in the census. Asserted on a
    calendar where June carries the widest gap -- 2026-05-29 to 2026-06-30 is 32 days against
    April-to-May's 29 -- so a filter that dropped the final month would answer 29 here rather
    than shrugging.
    """
    ends_on_a_month_end = _weekday_calendar(first=date(2026, 4, 1), last=date(2026, 6, 30))

    policy = freshness_policy("index_weight", calendar=ends_on_a_month_end)

    assert policy.max_staleness == timedelta(days=32) + FRESHNESS_PUBLICATION_SLACK
    assert policy.max_staleness != timedelta(days=29) + FRESHNESS_PUBLICATION_SLACK


def test_a_finding_names_the_dataset_it_is_primarily_about() -> None:
    (finding,) = subject_containment_findings(
        {"daily": frozenset({"000001.SZ"}), "adj_factor": frozenset()}
    )

    assert finding.dataset == "daily"
    assert finding.datasets[0] == finding.dataset


# --- the derived factor planes (`V2-P3-019`) ------------------------------------------------------


def test_every_declared_factor_plane_seal_matches_the_plane_it_describes() -> None:
    """The runtime audit that makes `FACTOR_PLANE_SEALS` a declaration rather than a copy.

    `panel_doctor` may not import the three factor planes --
    `tests/unit/test_panel_ingest_import_isolation.py::
    test_panel_doctor_joins_domain_panel_and_panel_ingest_and_nothing_else` pins its sibling set
    by equality -- so the shape of those planes is declared here as data. A declaration nobody
    reconciles is exactly the "表与实现漂移" this repository has closed three times with a run-time
    audit, and a *test* may import both sides, so this is where the two are held together.

    Every field of every row is checked against the plane's own constant, and the *set* of rows
    against the set of observation prefixes the three planes declare -- so a fourth derived tier
    fails here rather than arriving unsealed.
    """
    declared = {seal.tier: seal for seal in FACTOR_PLANE_SEALS}

    assert set(declared) == {"raw", "processed", "neutralized"}
    assert declared["raw"].observation_prefix == FACTOR_OBSERVATION_DATASET_PREFIX
    assert declared["raw"].manifest_prefix == FACTOR_MANIFEST_DATASET_PREFIX
    assert declared["processed"].observation_prefix == FACTOR_PROCESSED_DATASET_PREFIX
    assert declared["processed"].manifest_prefix == FACTOR_TRANSFORM_MANIFEST_DATASET_PREFIX
    assert declared["neutralized"].observation_prefix == FACTOR_NEUTRALIZED_DATASET_PREFIX
    assert declared["neutralized"].manifest_prefix == FACTOR_NEUTRALIZATION_MANIFEST_DATASET_PREFIX

    # The two columns each row names have to be columns the plane actually stores, under those
    # exact names, or the seal check would read `None` out of a partition and compare it to a
    # digest -- which is the fail-open a hand-copied table is for.
    assert declared["raw"].build_column in FACTOR_OBSERVATION_PANEL_COLUMNS
    assert declared["raw"].digest_column in FACTOR_MANIFEST_PANEL_COLUMNS
    assert declared["processed"].build_column in PROCESSED_OBSERVATION_PANEL_COLUMNS
    assert declared["processed"].digest_column in TRANSFORM_MANIFEST_PANEL_COLUMNS
    assert declared["neutralized"].build_column in NEUTRALIZED_OBSERVATION_PANEL_COLUMNS
    assert declared["neutralized"].digest_column in NEUTRALIZATION_MANIFEST_PANEL_COLUMNS

    # And the value column the seal hashes, which is shared by all three and is the one column
    # `FACTOR_SEAL_OBSERVATION_FIELDS` names that no row above pins.
    for columns in (
        FACTOR_OBSERVATION_PANEL_COLUMNS,
        PROCESSED_OBSERVATION_PANEL_COLUMNS,
        NEUTRALIZED_OBSERVATION_PANEL_COLUMNS,
    ):
        assert set(FACTOR_SEAL_OBSERVATION_FIELDS) <= set(columns)


def test_each_tier_is_addressed_under_the_prefix_its_own_digest_function_stamps() -> None:
    """The third field of every seal row, against the function the plane writes the address with.

    A tag that disagreed would make the check compare `obs_...` against `prc_...` for every build
    of one tier, which reads as "every stored build is tampered" -- a report that cries wolf is
    switched off, which is the failure mode this module argues about at length elsewhere.
    """
    raw = FactorObservation(
        subject="000001.SZ",
        as_of=datetime(2026, 1, 12, 4, 0, tzinfo=UTC),
        value=1.0,
        coverage="computed",
        factor_id="fct_probe",
        manifest_id="fmn_probe",
        input_row_count=1,
        input_session_first=None,
        input_session_last=None,
    )
    cells = ((raw.subject, raw.coverage, raw.value),)
    by_tier = {seal.tier: seal.digest_prefix for seal in FACTOR_PLANE_SEALS}

    assert observation_digest((raw,)) == cross_section_digest(cells, prefix=by_tier["raw"])
    assert len(set(by_tier.values())) == 3


def test_a_derived_partition_gets_a_bound_of_none_on_the_record_rather_than_a_refusal() -> None:
    """The `derived` cadence, and what it is a claim about.

    `V2-P3-002` gave the factor datasets no cadence and `freshness_policy` refused them by name,
    which meant `panel doctor --dataset factor_obs_reversal_1d_v1` never got past the first line.
    The bound is still `None` -- nothing publishes into a derived partition, so no event-clock
    bound can be right -- and the difference is that the absence is now *stated* and the rest of
    the report runs.

    `event_driven` is asserted to be a different answer because it was the near-miss: it means
    "no schedule, and a year with no rows is an ordinary year", which is a claim about an upstream
    that publishes irregularly rather than about having none.
    """
    for dataset in ("factor_obs_probe_v1", "factor_procmn_probe_v1", "factor_neut_probe_v1"):
        policy = freshness_policy(dataset)

        assert policy.cadence == "derived"
        assert policy.max_staleness is None
        assert "derived rather than fetched" in policy.basis

    assert freshness_policy("suspend_d").cadence == "event_driven"


def test_a_dataset_that_is_neither_fetched_nor_derived_is_still_refused_by_name() -> None:
    """The fail-closed direction the `derived` branch must not have opened.

    A predicate is a wider door than a table, so the refusal is measured rather than assumed: a
    name that matches no cadence entry and none of the six derived prefixes still raises, and the
    message still names both ways out.
    """
    with pytest.raises(PanelDoctorError, match="has no declared publication cadence"):
        freshness_policy("factor_something_else_v1")


def test_the_derived_prefixes_do_not_overlap_the_fetched_datasets() -> None:
    """The two vocabularies are disjoint, so no dataset can be answered by both branches.

    `freshness_policy` asks the predicate first, so an overlap would silently give a *fetched*
    dataset a `derived` bound of `None` -- a freshness check switched off by a name collision,
    which is precisely the silent default `DATASET_CADENCE` exists to prevent.
    """
    assert not any(is_derived_factor_dataset(name) for name in DATASET_CADENCE)
    assert not any(name in DATASET_CADENCE for name in ("factor_obs_x_v1", "factor_neutmn_x_v1"))
