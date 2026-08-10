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
    _LIFECYCLE_YEAR_TARGETS,
    CLICK_USAGE_EXIT_CODE,
    PANEL_BUILD_COUPLED_DATASETS,
    PANEL_BUILD_TARGETS,
    PanelExit,
)
from openalpha_cn.domain.adjustment import ADJ_FACTOR_DATASET
from openalpha_cn.domain.daily_prices import DAILY_BASIC_DATASET, DAILY_DATASET
from openalpha_cn.domain.price_limits import PRICE_LIMIT_DATASET, SUSPENSION_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET
from openalpha_cn.panel_doctor import PanelHealthReport
from openalpha_cn.panel_gate import DependencyClearance, DependencyRequest, PanelGateError

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
    """The table is also the build order. `adj_factor`, `price` and `stk_limit` each need the
    stored calendar, and `price` fetches its own halt corpus before the pair that consumes it,
    so a build that ran the targets in the order the flags happened to arrive would fail on a
    fresh store for a reason that has nothing to do with the data."""
    assert dict(PANEL_BUILD_TARGETS) == {
        "trade_cal": (TRADING_CALENDAR_DATASET,),
        "stock_basic": (STOCK_BASIC_DATASET,),
        "adj_factor": (ADJ_FACTOR_DATASET,),
        "price": (SUSPENSION_DATASET, DAILY_DATASET, DAILY_BASIC_DATASET),
        "stk_limit": (PRICE_LIMIT_DATASET,),
    }
    assert list(PANEL_BUILD_TARGETS) == [
        "trade_cal",
        "stock_basic",
        "adj_factor",
        "price",
        "stk_limit",
    ]


def test_only_the_halt_corpus_treats_an_empty_session_as_ordinary() -> None:
    """The tolerance is a closed set of exactly one, and it must not widen by accident.

    `write_suspensions` gives the measurement: a session on which nothing was halted and nothing
    resumed serves zero rows, so an absent session and an empty one are indistinguishable in
    that dataset by construction. Every other dataset here publishes on every open session, so
    dropping its empty batch would discard the provider's explicit "no data" for a day it
    publishes on, and the writer that knows what a missing session costs would never see it."""
    assert set(_EMPTY_SESSION_IS_ORDINARY) == {SUSPENSION_DATASET}
    assert set(PANEL_BUILD_TARGETS["price"]) > _EMPTY_SESSION_IS_ORDINARY


def test_the_lifecycle_year_exemption_is_stock_basic_and_nothing_else() -> None:
    """`_audit_written_partitions` requires every partition a build writes to carry the `--year`
    it was asked for, because a partition's year comes from the rows and `--year` only bounds
    what is fetched. `stock_basic` genuinely cannot satisfy that -- the registry has no date
    filter and `write_stock_universe` splits one request into one partition per *lifecycle*
    year -- so it is exempt, and the exemption is a closed set rather than a condition a future
    target could drift into."""
    assert set(_LIFECYCLE_YEAR_TARGETS) == {STOCK_BASIC_DATASET}
    assert set(PANEL_BUILD_TARGETS) > _LIFECYCLE_YEAR_TARGETS


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
