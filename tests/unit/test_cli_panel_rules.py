"""The three panel commands' decision tables, without a store or a transport (`V2-P1-015`).

The behaviour these tables produce is exercised end to end in
`tests/integration/test_cli_panel.py`; what is asserted here is the tables themselves, whole,
for `panel_gate.GATE_CODE_BLOCKS`' reason: a verdict per entry over a closed set is a diff
against a literal when it changes, where the same facts spread across per-test assertions are a
set of independent judgements nobody can total up.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from openalpha_cn.cli import (
    _EMPTY_SESSION_IS_ORDINARY,
    _NEEDS_STORED_UNIVERSE,
    _PANEL_WRITE_REFUSALS,
    _REGISTERED_PARTITION_RESUME,
    _UNPINNED_PARTITION_YEAR_TARGETS,
    CLICK_USAGE_EXIT_CODE,
    PANEL_BUILD_COUPLED_DATASETS,
    PANEL_BUILD_SPAN_TARGETS,
    PANEL_BUILD_TARGETS,
    PanelExit,
    _resume_evidence,
)
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET, AdjustmentError
from openalpha_cn.domain.daily_prices import (
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    PriceDataError,
)
from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIAL_INDICATOR_DATASET,
    FINANCIAL_STATEMENT_DATASETS,
    INCOME_DATASET,
    FinancialStatementError,
)
from openalpha_cn.domain.index_membership import INDEX_WEIGHT_DATASET, IndexMembershipError
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATASET, IndexPriceError
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_TREE_DATASET,
    IndustryClassificationError,
)
from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
from openalpha_cn.domain.panel_batch import PanelBatchError
from openalpha_cn.domain.price_limits import (
    PRICE_LIMIT_DATASET,
    SUSPENSION_DATASET,
    SuspensionError,
)
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET, StockUniverseError
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    TradingCalendarError,
)
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel_doctor import _LOAD_FAILURES, PanelHealthReport
from openalpha_cn.panel_gate import DependencyClearance, DependencyRequest, PanelGateError
from openalpha_cn.providers.tushare import TUSHARE_DATASETS

NOW = datetime(2026, 1, 17, 4, 0, tzinfo=UTC)


def test_the_exit_code_table_is_five_distinct_codes_and_none_of_them_is_clicks() -> None:
    """Click raises its own `UsageError` with exit code 2 for a misspelled flag or a missing
    required option. A panel command that also used 2 would make "you typed the command wrong"
    and "the panel refused you" the same observation in CI.

    `internal_error` is the fifth and was added by this issue's review: without it, an
    exception no branch anticipated reached Typer's own handler and exited **1**, which is
    `unhealthy` -- so "the CLI crashed" and "the panel failed its check" arrived at a CI job as
    the same number, and the one situation where nothing at all was checked looked exactly like
    the one where something was and it failed."""
    assert {member.name: int(member) for member in PanelExit} == {
        "ok": 0,
        "unhealthy": 1,
        "bad_request": 3,
        "provider_failure": 4,
        "internal_error": 5,
    }
    assert CLICK_USAGE_EXIT_CODE == 2
    assert CLICK_USAGE_EXIT_CODE not in {int(member) for member in PanelExit}


def test_the_build_targets_are_a_closed_table_in_dependency_order() -> None:
    """The table is also the build order, and every dependency in it runs one way.

    `adj_factor`, `price` and `stk_limit` each need the stored calendar, and `price` fetches its
    own halt corpus before the pair that consumes it; the three announcement-year statement
    targets read the stored registry, so `stock_basic` precedes them; and `index_member_all`
    slices by `l1_code` codes it reads off the stored tree, so `index_classify` precedes it. A
    build that ran the targets in the order the flags happened to arrive would fail on a fresh
    store for a reason that has nothing to do with the data.

    Eight entries were added by the P3 prerequisite that wired the datasets `providers/tushare.py`
    declared and this command could not build. The list is asserted whole rather than by
    membership for `panel_gate.GATE_CODE_BLOCKS`' reason: a thirteen-row table is a diff against
    a literal when it changes, where thirteen independent membership assertions are a set of
    judgements nobody can total up.
    """
    assert dict(PANEL_BUILD_TARGETS) == {
        "trade_cal": (TRADING_CALENDAR_DATASET,),
        "stock_basic": (STOCK_BASIC_DATASET,),
        "adj_factor": (ADJ_FACTOR_DATASET,),
        "price": (SUSPENSION_DATASET, DAILY_DATASET, DAILY_BASIC_DATASET),
        "stk_limit": (PRICE_LIMIT_DATASET,),
        "namechange": (NAMECHANGE_DATASET,),
        "index_weight": (INDEX_WEIGHT_DATASET,),
        "index_daily": (INDEX_DAILY_DATASET,),
        "income": (INCOME_DATASET,),
        "balancesheet": (BALANCE_SHEET_DATASET,),
        "cashflow": (CASH_FLOW_DATASET,),
        "index_classify": (INDUSTRY_TREE_DATASET,),
        "index_member_all": (INDUSTRY_MEMBERSHIP_DATASET,),
        "fina_indicator": (FINANCIAL_INDICATOR_DATASET,),
    }
    assert list(PANEL_BUILD_TARGETS) == [
        "trade_cal",
        "stock_basic",
        "adj_factor",
        "price",
        "stk_limit",
        "namechange",
        "index_weight",
        "index_daily",
        "income",
        "balancesheet",
        "cashflow",
        "index_classify",
        "index_member_all",
        "fina_indicator",
    ]


def test_every_dataset_the_tushare_table_declares_now_has_a_build_target() -> None:
    """The gap this task closed, asserted as the property rather than as a count.

    `providers/tushare.py` declared fifteen datasets and `panel_ingest` twelve writers while this
    command offered five targets, so eight datasets could be fetched, projected, written and read
    back by this repository and could not be *built* by it: `panel build --dataset income` was
    refused by name and `panel doctor --dataset income` therefore reported `partition_missing`
    for ever. Three independent acceptance passes reported the same hole.

    Read off the descriptor table rather than restated, so a sixteenth dataset added to the
    provider fails this test instead of quietly joining the unbuildable set. It did:
    `V2-P3-016`'s `index_daily` arrived here as a failure of the count below, and both halves
    of this assertion had to be satisfied in the same edit -- a descriptor with no target
    would break the equality and a target with no descriptor would break it the other way.
    """
    declared = {descriptor.dataset for descriptor in TUSHARE_DATASETS}
    buildable = {name for datasets in PANEL_BUILD_TARGETS.values() for name in datasets}

    assert declared == buildable
    assert len(declared) == 16


def test_the_span_targets_are_the_three_whose_requests_carry_no_usable_year() -> None:
    """A closed set of three, and each is here for a measured reason rather than for tidiness.

    `index_classify` takes a `src` and no date; `index_member_all` takes an `(l1_code, is_new)`
    slice and no date; `fina_indicator` takes a report-period year and files by announcement
    year, so a per-year loop would replace announcement year *A*'s annual-of-*A-1* rows with
    *A*'s interims -- more rows, the same securities, and nothing in `panel_ingest` able to see
    it. Every one of them therefore also fails to write the `--year` it was given, which is why
    the subset relation below is not a coincidence but a consequence.
    """
    assert set(PANEL_BUILD_SPAN_TARGETS) == {
        INDUSTRY_TREE_DATASET,
        INDUSTRY_MEMBERSHIP_DATASET,
        FINANCIAL_INDICATOR_DATASET,
    }
    assert PANEL_BUILD_SPAN_TARGETS < _UNPINNED_PARTITION_YEAR_TARGETS
    assert set(PANEL_BUILD_TARGETS) > PANEL_BUILD_SPAN_TARGETS
    # The span phase has no `--year` to check a partition against, so `_audit_written_partitions`
    # is given `None` there. That is only sound while every span target is exempt anyway.
    assert PANEL_BUILD_SPAN_TARGETS.isdisjoint(_REGISTERED_PARTITION_RESUME)


def test_only_the_statement_targets_take_a_subject_and_read_the_stored_registry() -> None:
    """`--subject` and the registry dependency are the same set, and it is the four endpoints
    whose `ts_code` parameter is mandatory. Every other target's partition is the whole market,
    so narrowing one would replace a full partition rather than build a smaller panel."""
    assert set(_NEEDS_STORED_UNIVERSE) == set(FINANCIAL_STATEMENT_DATASETS)
    assert set(_NEEDS_STORED_UNIVERSE) == {
        INCOME_DATASET,
        BALANCE_SHEET_DATASET,
        CASH_FLOW_DATASET,
        FINANCIAL_INDICATOR_DATASET,
    }
    assert set(_NEEDS_STORED_UNIVERSE) < set(PANEL_BUILD_TARGETS)


def test_the_weaker_resume_rule_names_its_members_rather_than_being_a_fallback() -> None:
    """`--resume` has two rules and they are not equally strong.

    The session rule reads a census the writers already validated. This set is skipped on a
    registered partition alone, because its datasets have no session census at all -- a security
    that announced nothing in a year is absent and indistinguishable from one never fetched. That
    is worth having at 5,881 requests a year and it is worth *naming*, so a future target cannot
    acquire the weaker rule by falling through a condition. `namechange` is deliberately outside
    it: one request a year, so `trade_cal`'s argument applies instead.
    """
    assert set(_REGISTERED_PARTITION_RESUME) == {
        INDEX_WEIGHT_DATASET,
        INCOME_DATASET,
        BALANCE_SHEET_DATASET,
        CASH_FLOW_DATASET,
    }
    assert NAMECHANGE_DATASET not in _REGISTERED_PARTITION_RESUME
    assert set(_REGISTERED_PARTITION_RESUME) < set(PANEL_BUILD_TARGETS)


def test_a_resumed_target_says_which_of_the_two_rules_skipped_it() -> None:
    """The disclosure has to reach the output, not only the docstring.

    `RESUMED income year=2024` and `RESUMED adj_factor year=2024` mean different things: the
    second was checked against a session census the writers already validated, the first only
    against the existence of a partition. A caller reading the two identical lines has no way to
    know that one of them cannot tell a whole-market year from a `--subject`-narrowed one, so the
    line carries the difference. Asserted per target over the closed table for the reason every
    other test in this module asserts a table whole.
    """
    weak = "partition registered; this rule does not check which securities it holds"
    evidence = {target: _resume_evidence(target) for target in PANEL_BUILD_TARGETS}

    assert {target for target, said in evidence.items() if said == weak} == set(
        _REGISTERED_PARTITION_RESUME
    )
    assert evidence[ADJ_FACTOR_DATASET] == "already complete"
    assert evidence["price"] == "already complete"
    assert evidence[PRICE_LIMIT_DATASET] == "already complete"


def test_only_the_halt_corpus_treats_an_empty_session_as_ordinary() -> None:
    """The tolerance is a closed set of exactly one, and it must not widen by accident.

    `write_suspensions` gives the measurement: a session on which nothing was halted and nothing
    resumed serves zero rows, so an absent session and an empty one are indistinguishable in
    that dataset by construction. Every other dataset here publishes on every open session, so
    dropping its empty batch would discard the provider's explicit "no data" for a day it
    publishes on, and the writer that knows what a missing session costs would never see it."""
    assert set(_EMPTY_SESSION_IS_ORDINARY) == {SUSPENSION_DATASET}
    assert set(PANEL_BUILD_TARGETS["price"]) > _EMPTY_SESSION_IS_ORDINARY


def test_the_partition_year_exemption_names_its_four_targets_one_by_one() -> None:
    """`_audit_written_partitions` requires every partition a build writes to carry the `--year`
    it was asked for, because a partition's year comes from the rows and `--year` only bounds
    what is fetched. Four targets genuinely cannot satisfy that, each for its own reason:

    - `stock_basic` -- the registry has no date filter, and `write_stock_universe` splits one
      request into one partition per *lifecycle* year.
    - `index_classify` -- the response carries no date column at all, so a vintage's nodes are
      dated at that vintage's effective day: SW2014 is a 2014 partition, SW2021 a 2021 one.
    - `index_member_all` -- filed by membership *event* year, so one 62-request sweep lands in
      roughly 38 partitions at once.
    - `fina_indicator` -- asked for a report-period year and filed by *announcement* year, which
      are different years even in the ordinary case.

    Asserted as a closed set rather than as a condition a future target could drift into, and
    named one by one so that widening it is a decision someone had to write down.
    """
    assert set(_UNPINNED_PARTITION_YEAR_TARGETS) == {
        STOCK_BASIC_DATASET,
        INDUSTRY_TREE_DATASET,
        INDUSTRY_MEMBERSHIP_DATASET,
        FINANCIAL_INDICATOR_DATASET,
    }
    assert set(PANEL_BUILD_TARGETS) > _UNPINNED_PARTITION_YEAR_TARGETS
    # The five targets that were here before this set grew still write the year they were asked
    # for, which is what keeps the audit's second check load-bearing rather than vestigial.
    assert _UNPINNED_PARTITION_YEAR_TARGETS.isdisjoint(
        {ADJ_FACTOR_DATASET, "price", PRICE_LIMIT_DATASET, TRADING_CALENDAR_DATASET}
    )


def test_every_dataset_the_price_target_couples_is_refused_on_its_own() -> None:
    """`write_daily_panel` takes `daily` and `daily_basic` together and `halts` has no default,
    so the smallest honest unit of work is all three. Naming any one of them alone has to be
    refused with `price` as the answer rather than accepted and silently widened."""
    assert dict(PANEL_BUILD_COUPLED_DATASETS) == {
        DAILY_DATASET: "price",
        DAILY_BASIC_DATASET: "price",
        SUSPENSION_DATASET: "price",
    }
    assert set(PANEL_BUILD_COUPLED_DATASETS) == set(PANEL_BUILD_TARGETS["price"])
    assert set(PANEL_BUILD_COUPLED_DATASETS) & set(PANEL_BUILD_TARGETS) == set()


def _empty_clearance() -> DependencyClearance:
    return DependencyClearance(
        request=DependencyRequest(
            datasets=(DAILY_DATASET,),
            as_of=NOW,
            years=(2026,),
            sessions=(),
            calendar=None,
        ),
        report=PanelHealthReport(
            as_of=NOW,
            datasets=(),
            cross_dataset_findings=(),
            cross_checks=(),
            limitations=(),
        ),
        blocks=(),
        notices=(),
        unverified_checks=(),
        cleared_or_none=(),
    )


@pytest.mark.parametrize("consume", [bool, len, list])
def test_a_cleared_clearance_still_refuses_to_be_used_as_a_collection(consume: object) -> None:
    """Task 36 made `__bool__`, `__len__` and `__iter__` raise on a *cleared* clearance too,
    because an accessor that answered when the panel was healthy and raised when it was not
    would pass every test written against a healthy panel and fail only in production. The
    three panel commands must go through `is_blocked` / `cleared` / `cleared_or_none` instead
    of routing back around that, so this pins the property they depend on."""
    clearance = _empty_clearance()

    assert clearance.is_blocked is False
    with pytest.raises(PanelGateError, match="a clearance is a verdict, not a collection"):
        consume(clearance)  # type: ignore[operator]


def test_the_write_refusals_and_the_doctors_load_failures_are_one_set() -> None:
    """The two modules must agree about which exceptions are facts about stored data.

    `panel_doctor._LOAD_FAILURES` is the doctor's answer -- the domain errors a cross-check
    catches so it can report that it did not run rather than take the report down -- and
    `cli._PANEL_WRITE_REFUSALS` is the same question asked by `panel build`, which maps them to
    `PanelExit.unhealthy` and prints the message. They drifted: the doctor named all nine, the
    CLI named four, and the five it omitted included `SuspensionError`. So a live 2026 build
    that fetched for 22 minutes and then hit a real contradiction in `suspend_d` exited 5 --
    `internal_error`, "a defect in the command, not a verdict about the panel" -- with the
    exception's message withheld on the grounds that an unanticipated failure might carry a
    credential. That refusal names one ticker and one session.

    Asserted as sets rather than as sequences because neither module's order is load-bearing --
    both are `except` tuples -- and asserted whole rather than by membership so that a tenth
    domain error added to one list fails here instead of being caught in one place and crashing
    in the other.

    `IndustryClassificationError` **is** that tenth, added when `panel build` gained the two
    industry targets: `write_industry_memberships`, `write_industry_tree`, `build_industry_tree`
    (a vintage whose parent chain is broken -- what a partial read of the tree partition looks
    like) and `load_industry_trees` all raise it, and `index_member_all`'s fetch plan reads its
    31 `l1_code` slices *through* that loader. Without the entry, a malformed stored tree would
    have stopped a build as `internal_error` with the message withheld, which is precisely the
    defect this test was written for. The doctor learns it in the same edit, which is what this
    test demands and the reason it demands it.

    `IndexPriceError` is the **eleventh** (`V2-P3-016`), and it arrives with a raiser rather
    than as defence: `panel_ingest._refuse_unrebuildable_index_prices` runs the reader's own
    reconstruction over every `index_daily` batch before it is stored, so a duplicated
    session or a null level is a fact about the data. It matters more here than the tenth
    did, because that dataset's rows are the regressor of every residual volatility in the
    cross section -- a build stopped by it as `internal_error` would withhold the one
    message naming which index and which session.
    """
    assert set(_PANEL_WRITE_REFUSALS) == set(_LOAD_FAILURES)
    assert len(_PANEL_WRITE_REFUSALS) == len(set(_PANEL_WRITE_REFUSALS))
    assert set(_PANEL_WRITE_REFUSALS) == {
        PanelStorageError,
        PanelBatchError,
        PriceDataError,
        AdjustmentError,
        SuspensionError,
        StockUniverseError,
        IndexMembershipError,
        IndexPriceError,
        IndustryClassificationError,
        FinancialStatementError,
        TradingCalendarError,
    }
