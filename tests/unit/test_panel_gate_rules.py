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
    GATE_REFUSAL_CODES,
    SESSION_SCOPED_CROSS_CHECKS,
    UNVERIFIED_DAILY_COVERAGE,
    DependencyClearance,
    DependencyRequest,
    GateBlock,
    PanelGateError,
    blocks_from_report,
)

AS_OF = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)


def _finding(code: str, *, dataset: str = "daily") -> HealthFinding:
    return HealthFinding(
        code=code,
        category=HEALTH_CODE_CATEGORY[code],
        severity=HEALTH_CODE_SEVERITY[code],
        datasets=(dataset,),
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


def _block(code: str, *, dataset: str = "daily") -> GateBlock:
    return GateBlock(
        code=code,
        dataset=dataset,
        datasets=(dataset,),
        severity="blocking",
        detail=f"{code} on {dataset}",
    )


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
        cleared_or_none=None if blocks else ask.datasets,
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
        "coverage_missing": True,
        "coverage_stale": True,
        "date_gap": True,
        "subject_missing": True,
        "field_missing": True,
        "stale": True,
        # Two datasets contradicting each other, and the report saying it could not look.
        # `return_path_disagreement` is the one thing standing between a caller and Task 29's
        # -0.530973%, and `check_unavailable` is "I could not look", which must never be read
        # as "I looked and it was fine".
        "subject_set_disagreement": True,
        "close_disagreement": True,
        "return_path_disagreement": True,
        "unexplained_unpriced": True,
        "check_unavailable": True,
        # Measured to be ordinary on this corpus, and each already refused where it is
        # decidable: a read of a disagreeing statement field is refused by
        # `financial_ambiguity_report`, not here.
        "ambiguous_filing": False,
        "duplicate_versions": False,
        "revised_rows": False,
    }


def test_every_declared_health_code_has_a_gate_verdict_and_no_code_has_two() -> None:
    """Total over the closed set, so a twenty-first code added to `PANEL_HEALTH_CODES` fails
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

    All five `warning` codes block. Every one of them is a statement two datasets make that
    cannot both be true, or the report saying it could not look -- and a gate that let those
    through would clear precisely the panels Tasks 29 and 30 were about.
    """
    blocked_by_severity = {
        severity: {
            code
            for code, blocks in GATE_CODE_BLOCKS.items()
            if blocks and HEALTH_CODE_SEVERITY[code] == severity
        }
        for severity in HEALTH_SEVERITIES
    }

    assert len(blocked_by_severity["blocking"]) == 12
    assert blocked_by_severity["warning"] == {
        "subject_set_disagreement",
        "close_disagreement",
        "return_path_disagreement",
        "unexplained_unpriced",
        "check_unavailable",
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
    """One report carrying one finding of all twenty codes, driven through the selector.

    A per-code injection into a real store cannot reach every code -- `empty_requirement` in
    particular is unreachable through `panel_health_report`, whose requirement builders never
    state an empty expectation -- so the totality of the table is proved here and the
    injections prove that the codes which matter do reach it.
    """
    report = _report(cross=tuple(_finding(code) for code in sorted(PANEL_HEALTH_CODES)))

    blocks = blocks_from_report(report)

    assert {block.code for block in blocks} == {
        code for code, blocks_it in GATE_CODE_BLOCKS.items() if blocks_it
    }
    assert len(blocks) == 17


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
    """`adj_factor` is the measured instance: of this repository's fifteen datasets it is the
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
    """Twelve of the fifteen datasets waive `required_dates` and eleven of them are right to:
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
    assert cleared.cleared_or_none == ("daily",)
    assert cleared.cleared == ("daily",)
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
        cleared_or_none=("adj_factor",),
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
        cleared_or_none=request.datasets,
    )

    assert cleared.unverified("daily") == ()
    assert cleared.unverified("adj_factor") == ("required_dates",)


def test_the_blocking_codes_of_a_clearance_are_a_set_the_caller_can_branch_on() -> None:
    blocked = _clearance(
        request=_request("daily", "adj_factor"),
        blocks=(_block("stale"), _block("stale", dataset="adj_factor")),
    )

    assert blocked.blocking_codes() == frozenset({"stale"})
    assert blocked.blocked_datasets == ("daily", "adj_factor")
