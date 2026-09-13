"""The dependency gate's rule tables, without a store (`V2-P1-013`).

Everything here is pure: which codes block, which of the gate's own refusals exist, how a
clearance behaves as a value, and the one rule that reads a report's shape rather than its
findings. `tests/integration/panel/test_panel_gate.py` is the other half -- it injects a
defect into a real store and asserts the gate refuses *and* that the downstream cannot get an
empty result out of the refusal.

The division is `panel/catalog.py`'s and `test_panel_doctor_rules.py`'s: a rule table is
tested as a rule table, and the I/O that feeds it is tested against real files.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from openalpha_cn.panel.catalog import DatasetReadiness
from openalpha_cn.panel_doctor import (
    BLOCKS_A_READ,
    HEALTH_CODE_CATEGORY,
    HEALTH_CODE_SEVERITY,
    HEALTH_SEVERITIES,
    PANEL_HEALTH_CODES,
    CrossCheckOutcome,
    DatasetHealth,
    FreshnessPolicy,
    HealthFinding,
    PanelHealthReport,
)
from openalpha_cn.panel_gate import (
    GATE_BLOCK_CODES,
    GATE_BLOCKING_SEVERITIES,
    GATE_CODE_BLOCKS,
    GATE_CODE_CATEGORIES,
    GATE_CODE_CATEGORY,
    GATE_REFUSAL_CODES,
    SESSION_SCOPED_CROSS_CHECKS,
    UNVERIFIED_DAILY_COVERAGE,
    ClearedDataset,
    DependencyClearance,
    DependencyRequest,
    GateBlock,
    PanelGateError,
    blocks_from_report,
    cleared_datasets,
)

AS_OF = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)


def _finding(code: str, *, dataset: str = "daily", also: str | None = None) -> HealthFinding:
    return HealthFinding(
        code=code,
        category=HEALTH_CODE_CATEGORY[code],
        severity=HEALTH_CODE_SEVERITY[code],
        datasets=(dataset,) if also is None else (dataset, also),
        detail=f"{code} on {dataset}",
    )


def _health(
    dataset: str,
    *,
    cadence: str = "daily",
    waived: tuple[str, ...] = (),
    findings: tuple[HealthFinding, ...] = (),
) -> DatasetHealth:
    readiness = DatasetReadiness(
        dataset=dataset,
        as_of=AS_OF,
        state="ready",
        issues=(),
        years_present=(2026,),
        row_count=1,
        subject_count=1,
        last_event_time=None,
        last_event_date=None,
        checks_waived=waived,
    )
    return DatasetHealth(
        dataset=dataset,
        readiness=readiness,
        freshness=FreshnessPolicy(
            dataset=dataset,
            cadence=cadence,  # type: ignore[arg-type]
            max_staleness=None,
            basis="hand-built for this test",
        ),
        findings=findings,
        years_requested=(2026,),
        event_age=None,
        fetch_age=None,
        revised_row_count=0,
        revision_labels=(),
    )


def _report(
    *,
    datasets: tuple[DatasetHealth, ...] = (),
    cross: tuple[HealthFinding, ...] = (),
    checks: tuple[CrossCheckOutcome, ...] = (),
) -> PanelHealthReport:
    return PanelHealthReport(
        as_of=AS_OF,
        datasets=datasets,
        cross_dataset_findings=cross,
        cross_checks=checks,
        limitations=(),
    )


def _request(*names: str) -> DependencyRequest:
    return DependencyRequest(
        datasets=names or ("daily",),
        as_of=AS_OF,
        years=(2026,),
        sessions=(date(2026, 1, 16),),
        calendar=None,
    )


def _block(code: str, *, dataset: str = "daily", also: str | None = None) -> GateBlock:
    return GateBlock(
        code=code,
        dataset=dataset,
        datasets=(dataset,) if also is None else (dataset, also),
        category=GATE_CODE_CATEGORIES[code],
        severity="blocking",
        detail=f"{code} on {dataset}",
    )


def _cleared(*names: str) -> tuple[ClearedDataset, ...]:
    return tuple(ClearedDataset(dataset=name, years=(2026,)) for name in names)


def _clearance(
    *, blocks: tuple[GateBlock, ...] = (), request: DependencyRequest | None = None
) -> DependencyClearance:
    ask = request if request is not None else _request()
    return DependencyClearance(
        request=ask,
        report=_report(),
        blocks=blocks,
        notices=(),
        unverified_checks=(),
        cleared_or_none=None if blocks else _cleared(*ask.datasets),
    )


# --- the table: which codes block ---------------------------------------------------------


def test_the_gate_s_verdict_on_every_health_code_is_written_out() -> None:
    """The table, spelled out, for `HEALTH_CODE_SEVERITY`'s reason one layer up.

    This mapping is what decides whether a downstream read happens at all, so a single entry
    quietly flipped to `False` is the one change to this module that lets a sick panel through
    while every injection test that does not happen to use that code keeps passing. Written as
    a literal so that such a change is a diff against this block rather than a line nobody
    reads.
    """
    assert dict(GATE_CODE_BLOCKS) == {
        # `evaluate_readiness`'s own verdict: the dataset cannot be read at all.
        "no_years_requested": True,
        "empty_requirement": True,
        "not_yet_knowable": True,
        "partition_missing": True,
        "partition_file_missing": True,
        "partition_file_unreadable": True,
        "partition_row_count_mismatch": True,
        "coverage_missing": True,
        "coverage_stale": True,
        "date_gap": True,
        "subject_missing": True,
        "field_missing": True,
        "stale": True,
        # Two datasets contradicting each other, the report saying it could not look, and one
        # dataset contradicting itself. `return_path_disagreement` is the one thing standing
        # between a caller and Task 29's -0.530973%, `check_unavailable` is "I could not look",
        # which must never be read as "I looked and it was fine", and `domain_rebuild_refused`
        # is this gate clearing a partition whose own reader raises.
        "subject_set_disagreement": True,
        "close_disagreement": True,
        "return_path_disagreement": True,
        "unexplained_unpriced": True,
        "check_unavailable": True,
        "domain_rebuild_refused": True,
        # The event clock, which `evaluate_readiness` never compares against `as_of`: a row
        # about an event that has not happened yet passes every readiness dimension and then
        # makes `load_stock_universe` raise on the partition this gate would have cleared.
        "event_after_as_of": True,
        # Measured to be ordinary on this corpus, and each already refused where it is
        # decidable: a read of a disagreeing statement field is refused by
        # `financial_ambiguity_report`, not here.
        "ambiguous_filing": False,
        "duplicate_versions": False,
        "revised_rows": False,
        # An inherent, unremediable limitation of `trade_cal` made conditional on this panel's
        # horizon. A block would permanently refuse every panel reaching 2015-09-03/04 or
        # 2020-01-31; it rides on `notices` instead.
        "calendar_lookahead_in_horizon": False,
        # The three derived factor tiers held to their own build manifests (`V2-P3-019`).
        # Both block: a broken seal is a cleared read that succeeds and returns different
        # numbers, which is one notch sharper than `domain_rebuild_refused`'s raise.
        "factor_seal_broken": True,
        "factor_build_unaddressed": True,
    }


def test_every_declared_health_code_has_a_gate_verdict_and_no_code_has_two() -> None:
    """Total over the closed set, so a twenty-fifth code added to `PANEL_HEALTH_CODES` fails
    here rather than arriving with no verdict and being silently waved through."""
    assert set(GATE_CODE_BLOCKS) == set(PANEL_HEALTH_CODES)


def test_the_gate_blocks_exactly_what_the_health_report_refuses_to_call_clean() -> None:
    """Two tables that must agree, kept separate on purpose.

    `HEALTH_CODE_SEVERITY` says what a finding *is*; `GATE_CODE_BLOCKS` says what this gate
    *does* about it. Deriving the second from the first would mean a future demotion of, say,
    `close_disagreement` to `notice` silently widened the gate with no test to fail. Stating
    both and asserting the agreement here means such a change has to be made in two places and
    is visible in this one.
    """
    blocking = {code for code, blocks in GATE_CODE_BLOCKS.items() if blocks}

    assert blocking == {
        code for code in PANEL_HEALTH_CODES if HEALTH_CODE_SEVERITY[code] in BLOCKS_A_READ
    }
    assert GATE_BLOCKING_SEVERITIES == BLOCKS_A_READ


def test_a_warning_blocks_this_gate_and_a_notice_does_not() -> None:
    """The brief's question, answered as three sets rather than as prose.

    All seven `warning` codes block. Every one of them is a statement two datasets make that
    cannot both be true, the report saying it could not look, a partition its own reader
    refuses, or a row about an event that has not happened -- and a gate that let those through
    would clear precisely the panels Tasks 29 and 30 were about, the 2026 `suspend_d` partition
    the P1 acceptance found, and the `stock_basic` row P2's product acceptance injected.

    The `blocking` count moved from 13 to 15 with `V2-P3-019`'s two derived-plane codes, which
    are the first two this repository *concludes* at that severity rather than passing through
    from `evaluate_readiness`; see `panel_doctor.HEALTH_CODE_SEVERITY` for why nothing weaker
    than `blocking` would be honest about a read that succeeds and returns different numbers.
    """
    blocked_by_severity = {
        severity: {
            code
            for code, blocks in GATE_CODE_BLOCKS.items()
            if blocks and HEALTH_CODE_SEVERITY[code] == severity
        }
        for severity in HEALTH_SEVERITIES
    }

    assert len(blocked_by_severity["blocking"]) == 15
    assert blocked_by_severity["warning"] == {
        "subject_set_disagreement",
        "close_disagreement",
        "return_path_disagreement",
        "unexplained_unpriced",
        "check_unavailable",
        "domain_rebuild_refused",
        "event_after_as_of",
    }
    assert blocked_by_severity["notice"] == set()


def test_the_gate_s_own_refusals_are_a_closed_set_disjoint_from_the_health_codes() -> None:
    """The gate refuses for one reason that is not a finding about the panel: a request whose
    answer would rest on a question nobody put. That reason needs a code of its own, and it
    must not collide with a health code, or `blocks_with_code` would answer two different
    questions under one name."""
    assert frozenset({UNVERIFIED_DAILY_COVERAGE}) == GATE_REFUSAL_CODES
    assert not (GATE_REFUSAL_CODES & PANEL_HEALTH_CODES)
    assert frozenset(GATE_CODE_BLOCKS) | GATE_REFUSAL_CODES == GATE_BLOCK_CODES


def test_every_code_this_gate_can_issue_files_under_one_of_the_report_s_headings() -> None:
    """`HEALTH_CODE_CATEGORY` is total over the twenty-six health codes and silent about the one
    the gate invented, so a facet grouping blocks by category -- `V2-P1-016`'s REST surface --
    would have had to special-case `unverified_daily_coverage` or drop it. It is filed under
    `unanswerable`, the heading `panel_doctor` already gives to a question that could not be
    put, rather than under `missing`, which would say rows are absent when the truth is that
    nobody looked."""
    assert dict(GATE_CODE_CATEGORY) == {UNVERIFIED_DAILY_COVERAGE: "unanswerable"}
    assert set(GATE_CODE_CATEGORIES) == set(GATE_BLOCK_CODES)
    assert GATE_CODE_CATEGORIES["date_gap"] == HEALTH_CODE_CATEGORY["date_gap"]


def test_the_session_scoped_cross_checks_are_named_rather_than_inferred() -> None:
    """The three `panel_doctor` checks that read a named session. `subject_containment` and
    `statement_ambiguity` are deliberately absent: neither opens a session, so neither can
    testify that a dataset's sessions are all there."""
    assert (
        frozenset({"close_agreement", "unpriced_explained", "return_paths"})
        == SESSION_SCOPED_CROSS_CHECKS
    )


# --- findings become blocks ------------------------------------------------------------------


def test_every_blocking_code_produces_a_block_and_every_notice_produces_none() -> None:
    """One report carrying one finding of every declared code, driven through the selector.

    The totality of the table is proved here because a per-code injection into a real store is
    a slow way to prove twenty-six entries; the injections prove separately that the codes reach it
    from a store. An earlier version of this docstring claimed `empty_requirement` was
    unreachable through `panel_health_report` "because the requirement builders never state an
    empty expectation". That is false and the review measured it: a price requirement built
    inside a year that has begun but published no session yet states `required_dates=()`, which
    is declared-but-empty. It happens on the real calendar every January and is pinned by
    `test_a_year_that_has_begun_but_published_no_session_blocks_with_an_empty_requirement`.
    """
    report = _report(cross=tuple(_finding(code) for code in sorted(PANEL_HEALTH_CODES)))

    blocks = blocks_from_report(report)

    assert {block.code for block in blocks} == {
        code for code, blocks_it in GATE_CODE_BLOCKS.items() if blocks_it
    }
    assert len(blocks) == 22
    assert {block.category for block in blocks} == {
        "missing",
        "stale",
        "inconsistent",
        "unanswerable",
    }
    assert {block.code: block.category for block in blocks} == {
        block.code: GATE_CODE_CATEGORIES[block.code] for block in blocks
    }


def test_a_block_carries_the_finding_it_came_from_rather_than_only_its_code() -> None:
    """A caller told "blocked: date_gap" and nothing else has to re-run the report to learn
    which dates are missing. The finding rides along."""
    report = _report(cross=(_finding("date_gap", dataset="daily"),))

    (block,) = blocks_from_report(report)

    assert block.code == "date_gap"
    assert block.dataset == "daily"
    assert block.severity == "blocking"
    assert block.finding is not None
    assert block.finding.detail == "date_gap on daily"


def test_a_clean_report_produces_no_blocks_at_all() -> None:
    assert blocks_from_report(_report()) == ()


# --- the one rule that reads the report's shape rather than its findings ---------------------


def test_a_daily_dataset_whose_sessions_were_never_required_is_refused_if_nothing_read_them() -> (
    None
):
    """`adj_factor` is the measured instance: of this repository's sixteen datasets it is the
    only one that is both on the `daily` cadence and waives `required_dates`, so readiness
    alone proves its partition exists, is readable and is recent -- and proves nothing about a
    hole inside it. That is exactly Task 29's shape, and a gate that cleared on readiness alone
    would repeat it.
    """
    report = _report(datasets=(_health("adj_factor", waived=("required_dates",)),))

    (block,) = blocks_from_report(report)

    assert block.code == UNVERIFIED_DAILY_COVERAGE
    assert block.dataset == "adj_factor"
    assert block.severity == "blocking"
    assert block.category == "unanswerable"
    assert "required_dates" in block.detail


def test_the_same_dataset_clears_once_a_session_scoped_cross_check_has_read_it() -> None:
    """The refusal is about the *request*, not about the dataset: a request that also names
    `daily` and a session gets `return_paths`, which recomputes the session's return on both
    paths and would have caught the hole. Nothing about `adj_factor` changed."""
    report = _report(
        datasets=(_health("adj_factor", waived=("required_dates",)),),
        checks=(
            CrossCheckOutcome(
                name="return_paths", datasets=("daily", "adj_factor"), ran=True, finding_count=0
            ),
        ),
    )

    assert blocks_from_report(report) == ()


def test_a_cross_check_that_did_not_run_does_not_count_as_having_looked() -> None:
    """`ran=False` is the whole reason `CrossCheckOutcome` carries the flag."""
    report = _report(
        datasets=(_health("adj_factor", waived=("required_dates",)),),
        checks=(
            CrossCheckOutcome(
                name="return_paths",
                datasets=("daily", "adj_factor"),
                ran=False,
                skipped_reason="the factors would not load",
            ),
        ),
    )

    (block,) = blocks_from_report(report)

    assert block.code == UNVERIFIED_DAILY_COVERAGE


def test_a_cross_check_that_reads_no_session_does_not_corroborate_a_date_waiver() -> None:
    """`subject_containment` compares subject lists taken off the catalog and never opens a
    partition, so it cannot testify that a dataset's sessions are all there."""
    report = _report(
        datasets=(_health("adj_factor", waived=("required_dates",)),),
        checks=(
            CrossCheckOutcome(
                name="subject_containment", datasets=("daily", "adj_factor"), ran=True
            ),
        ),
    )

    (block,) = blocks_from_report(report)

    assert block.code == UNVERIFIED_DAILY_COVERAGE


def test_a_cross_check_that_ran_on_other_datasets_does_not_corroborate_this_one() -> None:
    """`ran=True` is not enough: the check has to have read *this* dataset."""
    report = _report(
        datasets=(_health("adj_factor", waived=("required_dates",)),),
        checks=(
            CrossCheckOutcome(name="close_agreement", datasets=("daily", "daily_basic"), ran=True),
        ),
    )

    (block,) = blocks_from_report(report)

    assert block.code == UNVERIFIED_DAILY_COVERAGE


def test_a_waived_date_check_on_a_dataset_that_is_not_daily_is_not_refused() -> None:
    """Twelve of the sixteen datasets waive `required_dates` and eleven of them are right to:
    a quarterly filing corpus has no session census to be missing sessions from, and a gate
    that blocked on the waiver itself would refuse every real panel and be switched off. The
    rule is scoped to the cadence that *has* a per-session expectation.
    """
    report = _report(
        datasets=(
            _health("income", cadence="quarterly", waived=("required_dates", "required_subjects")),
            _health("suspend_d", cadence="event_driven", waived=("required_dates",)),
            _health("index_weight", cadence="monthly", waived=("required_dates",)),
            _health("trade_cal", cadence="published_in_advance", waived=("required_dates",)),
        )
    )

    assert blocks_from_report(report) == ()


def test_a_daily_dataset_that_was_asked_for_its_sessions_needs_no_corroboration() -> None:
    """`daily`, `daily_basic` and `stk_limit` carry a calendar-derived `required_dates`, so
    `date_gap` is decidable on them and this rule has nothing to add."""
    report = _report(
        datasets=(
            _health("daily", waived=("required_subjects",)),
            _health("daily_basic", waived=("required_subjects",)),
            _health("stk_limit", waived=("required_subjects",)),
        )
    )

    assert blocks_from_report(report) == ()


# --- the width of a clearance -------------------------------------------------------------------


def _corroborated() -> PanelHealthReport:
    return _report(
        datasets=(
            _health("daily", waived=("required_subjects",)),
            _health("adj_factor", waived=("required_dates",)),
            _health("income", cadence="quarterly"),
        ),
        checks=(
            CrossCheckOutcome(
                name="return_paths", datasets=("daily", "adj_factor"), ran=True, finding_count=0
            ),
        ),
    )


def test_a_dataset_corroborated_only_by_a_session_check_records_which_sessions_those_were() -> None:
    """The `V2-P1-013` review's Critical, as a rule rather than as an injection.

    The three session-scoped cross-checks run on `cross_section_days` and nothing else, so
    `return_paths` having run says something about the sessions the request named and nothing
    at all about the rest of the year. `cleared` used to hand back the bare name `adj_factor`
    granted over `years`, and a downstream that read it that way got Task 29's `-0.530973%`
    out of a *cleared* gate. The record now carries the sessions the evidence reaches and the
    code that is still open outside them -- and it carries the sessions for `daily` too, which
    the same three checks are equally silent about outside them.
    """
    named = (date(2026, 1, 15),)

    prices, factors, income = cleared_datasets(_corroborated(), named)

    assert factors == ClearedDataset(
        dataset="adj_factor",
        years=(2026,),
        corroborated_sessions=named,
        caveats=(UNVERIFIED_DAILY_COVERAGE,),
    )
    assert prices == ClearedDataset(dataset="daily", years=(2026,), corroborated_sessions=named)
    assert income == ClearedDataset(dataset="income", years=(2026,))


def test_the_record_says_which_sessions_were_opened_rather_than_judging_the_others() -> None:
    """`corroborates` is a fact, not a verdict: `False` means no cross-dataset check looked at
    that session, which is why it is not folded into the year-scoped evidence. For `daily` the
    year census *does* reach 2026-01-13 while the cross-checks do not, so one boolean would
    have had to pick one of two true answers."""
    prices, factors, income = cleared_datasets(_corroborated(), (date(2026, 1, 15),))

    assert factors.corroborates(date(2026, 1, 15)) is True
    assert factors.corroborates(date(2026, 1, 13)) is False
    assert prices.corroborates(date(2026, 1, 15)) is True
    assert prices.corroborates(date(2026, 1, 13)) is False
    assert income.corroborates(date(2026, 1, 15)) is False
    assert income.years == (2026,)


def test_a_dataset_nothing_read_carries_the_caveat_with_no_session_behind_it() -> None:
    """The same rule at its other end. `require_datasets` never reaches this state -- a dataset
    in this shape that nothing read is a *block* -- but `cleared_datasets` is public and total,
    and answering `corroborated_sessions=(2026-01-15,)` for a dataset no check opened would be
    the manufactured fact this whole module is about."""
    report = _report(datasets=(_health("adj_factor", waived=("required_dates",)),))

    (factors,) = cleared_datasets(report, (date(2026, 1, 15),))

    assert factors.corroborated_sessions == ()
    assert factors.caveats == (UNVERIFIED_DAILY_COVERAGE,)
    assert factors.corroborates(date(2026, 1, 15)) is False


def test_the_caveat_is_pollable_by_code_and_a_code_the_gate_cannot_issue_raises() -> None:
    """`blocks_with_code`'s rule applied to the other half of the verdict, and it matters more
    here: this is how a caller asks "was anything cleared only narrowly", and a typo answering
    `()` reads as "no, everything was cleared outright"."""
    request = _request("daily", "adj_factor", "income")
    clearance = DependencyClearance(
        request=request,
        report=_corroborated(),
        blocks=(),
        notices=(),
        unverified_checks=(),
        cleared_or_none=cleared_datasets(_corroborated(), (date(2026, 1, 15),)),
    )

    assert clearance.caveat_codes() == frozenset({UNVERIFIED_DAILY_COVERAGE})
    assert [
        entry.dataset for entry in clearance.cleared_with_caveat(UNVERIFIED_DAILY_COVERAGE)
    ] == ["adj_factor"]
    assert clearance.cleared_with_caveat("date_gap") == ()
    with pytest.raises(PanelGateError, match=r"'unverified_daily_covrage' is not one of the codes"):
        clearance.cleared_with_caveat("unverified_daily_covrage")


def test_the_permission_for_a_dataset_the_request_never_named_raises_rather_than_defaulting() -> (
    None
):
    """`unverified`'s rule: a caller handed a default-shaped record for a name the gate never
    considered would read it as a permission. The second half is the guard for a clearance
    whose records do not cover its own request, which `require_datasets` cannot produce but a
    hand-built one can."""
    request = _request("adj_factor", "income")
    clearance = DependencyClearance(
        request=request,
        report=_corroborated(),
        blocks=(),
        notices=(),
        unverified_checks=(),
        cleared_or_none=_cleared("adj_factor"),
    )

    assert clearance.cleared_for("adj_factor").dataset == "adj_factor"
    with pytest.raises(PanelGateError, match=r"'daily' was not one of the datasets"):
        clearance.cleared_for("daily")
    with pytest.raises(PanelGateError, match=r"'income' was named by this request but"):
        clearance.cleared_for("income")


# --- the clearance as a value ------------------------------------------------------------------


def test_reading_the_cleared_datasets_of_a_blocked_clearance_raises() -> None:
    """`PanelReadOutcome.rows` one layer up: the plainly-named accessor is the strict one."""
    blocked = _clearance(blocks=(_block("date_gap"),))

    with pytest.raises(PanelGateError, match=r"blocked by \['date_gap'\]"):
        _ = blocked.cleared


def test_the_merged_shape_is_reachable_only_under_a_name_that_says_what_it_is() -> None:
    blocked = _clearance(blocks=(_block("stale"),))
    cleared = _clearance()

    assert blocked.cleared_or_none is None
    assert blocked.is_blocked is True
    assert cleared.cleared_or_none == _cleared("daily")
    assert cleared.cleared == _cleared("daily")
    assert cleared.is_blocked is False


def _walk(clearance: DependencyClearance) -> list[str]:
    """Iterate a clearance the way a `for` loop does.

    Deliberately not `list(clearance)`: `list()` asks `__len__` for a length hint *before* it
    iterates, so a broken `__iter__` behind a strict `__len__` is invisible to it -- which is
    exactly what an earlier cut of this file proved, by passing with `__iter__` mutated to
    answer normally.
    """
    collected: list[str] = []
    for name in clearance:
        collected.append(name)
    return collected


def test_a_clearance_is_not_a_collection_and_refuses_to_pretend_otherwise() -> None:
    """The three lines people actually write -- `if not result:`, `result or []`, `len(result)`
    -- plus iteration, all refused, and each exercised through its own dunder.

    They raise on a *cleared* clearance too, and that is the point rather than an oversight.
    An accessor that answers on a cleared clearance and raises on a blocked one passes every
    test written against a healthy panel and fails only in production, which is the landmine
    `CalendarDayStatus.__bool__` already refuses to be: it raises for every member, not only
    for the unknown one.
    """
    for clearance in (_clearance(), _clearance(blocks=(_block("stale"),))):
        with pytest.raises(PanelGateError, match=r"a clearance is a verdict, not a collection"):
            bool(clearance)
        with pytest.raises(PanelGateError, match=r"a clearance is a verdict, not a collection"):
            len(clearance)
        with pytest.raises(PanelGateError, match=r"a clearance is a verdict, not a collection"):
            _walk(clearance)
        with pytest.raises(PanelGateError, match=r"a clearance is a verdict, not a collection"):
            list(clearance)


def test_the_gate_is_all_or_nothing_rather_than_clearing_the_datasets_that_survived() -> None:
    """A partial clearance is the empty success this issue exists to remove: a downstream
    handed "these two of three are fine" reads two and quietly produces a result missing the
    third. A request is answered or refused as a whole."""
    request = _request("daily", "adj_factor", "income")
    blocked = _clearance(request=request, blocks=(_block("partition_missing", dataset="income"),))

    assert blocked.cleared_or_none is None
    assert blocked.blocked_datasets == ("income",)


def test_asking_for_the_blocks_of_a_dataset_the_request_never_named_raises() -> None:
    """`findings_with_code`'s rule applied to the gate's own accessors: a caller who asks
    about `parition_missing`, or about a dataset it never requested, and receives `()` reads
    it as "nothing wrong there"."""
    blocked = _clearance(blocks=(_block("stale"),))

    assert blocked.blocks_for("daily")
    with pytest.raises(PanelGateError, match=r"'namechange' was not one of the datasets"):
        blocked.blocks_for("namechange")
    with pytest.raises(PanelGateError, match=r"'parition_missing' is not one of the codes"):
        blocked.blocks_with_code("parition_missing")
    assert blocked.blocks_with_code("stale")
    assert blocked.blocks_with_code("date_gap") == ()


def test_the_unverified_checks_a_clearance_carries_are_readable_per_dataset() -> None:
    """A cleared clearance still says which questions were never put -- twelve of the fifteen
    datasets waive `required_dates` structurally -- and asking about a dataset outside the
    request raises rather than answering an empty tuple."""
    request = _request("adj_factor")
    cleared = DependencyClearance(
        request=request,
        report=_report(),
        blocks=(),
        notices=(),
        unverified_checks=(("adj_factor", ("required_dates", "required_subjects")),),
        cleared_or_none=_cleared("adj_factor"),
    )

    assert cleared.unverified("adj_factor") == ("required_dates", "required_subjects")
    with pytest.raises(PanelGateError, match=r"'daily' was not one of the datasets"):
        cleared.unverified("daily")


def test_a_dataset_that_waived_nothing_answers_an_empty_tuple_and_means_it() -> None:
    """`()` from `unverified` is the strongest answer this accessor gives -- every check ran --
    and it is only unambiguous because a dataset outside the request raises instead of
    reaching it. No dataset in the fifteen currently declared waives nothing, so this is the
    one case that has to be built by hand rather than injected."""
    request = _request("daily", "adj_factor")
    cleared = DependencyClearance(
        request=request,
        report=_report(),
        blocks=(),
        notices=(),
        unverified_checks=(("adj_factor", ("required_dates",)),),
        cleared_or_none=_cleared(*request.datasets),
    )

    assert cleared.unverified("daily") == ()
    assert cleared.unverified("adj_factor") == ("required_dates",)


def test_both_datasets_of_a_cross_dataset_block_answer_rather_than_only_the_first() -> None:
    """`GateBlock.dataset` is `HealthFinding.datasets[0]` -- a filing label, not the answer to
    "is this dataset implicated". Matching on it made both accessors lie about the second half
    of every cross-dataset block: the `daily_basic` that published the close `daily` does not
    corroborate answered `blocks_for('daily_basic') == ()`, which is "nothing wrong there" told
    to a caller polling dataset by dataset -- the precise confusion these two exist to refuse.
    """
    request = _request("daily", "daily_basic")
    clearance = _clearance(
        request=request, blocks=(_block("close_disagreement", dataset="daily", also="daily_basic"),)
    )

    assert clearance.blocked_datasets == ("daily", "daily_basic")
    assert len(clearance.blocks_for("daily")) == 1
    assert len(clearance.blocks_for("daily_basic")) == 1


def test_a_dataset_named_by_no_block_still_answers_an_empty_tuple_and_means_it() -> None:
    """The other direction, so that widening the match cannot be mutated into "every dataset is
    blocked"."""
    request = _request("daily", "daily_basic", "income")
    clearance = _clearance(
        request=request, blocks=(_block("close_disagreement", dataset="daily", also="daily_basic"),)
    )

    assert clearance.blocks_for("income") == ()
    assert "income" not in clearance.blocked_datasets


def test_the_blocking_codes_of_a_clearance_are_a_set_the_caller_can_branch_on() -> None:
    blocked = _clearance(
        request=_request("daily", "adj_factor"),
        blocks=(_block("stale"), _block("stale", dataset="adj_factor")),
    )

    assert blocked.blocking_codes() == frozenset({"stale"})
    assert blocked.blocked_datasets == ("daily", "adj_factor")
