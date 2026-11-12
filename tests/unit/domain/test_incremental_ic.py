"""The incremental-IC arithmetic of `V2-P7-014`, as pure domain functions.

Two kinds of test live here and both are load-bearing:

* the **arithmetic** tests, which compare `domain/incremental_ic.py` against an independent slow
  reference (`statistics.linear_regression` + `statistics.correlation`) and against
  `backtest/power_analysis.py`'s survivor power block -- the formula `V2-P7-004` already prints;
* two **layering** guards, which read the module's own import list and its `grimp` graph, so a
  numeric library or a sibling `openalpha_cn` subpackage reached from this module goes red here
  rather than being argued about. The `domain-purity` contract covers the package; these read the
  single module, for the reason `tests/unit/domain/test_alpha_model_layering.py` states.
"""

from __future__ import annotations

import ast
import random
import statistics
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Final

import grimp
import pytest

from openalpha_cn.backtest.power_analysis import smallest_mean_at_power
from openalpha_cn.domain.incremental_ic import (
    REASON_CONSTANT_FACTOR_RANK,
    REASON_LENGTH_MISMATCH,
    REASON_NON_FINITE_INPUT,
    REASON_TOO_FEW_NAMES,
    REASON_ZERO_RESIDUAL_VARIANCE,
    IncrementalICError,
    SessionIC,
    detectable_mean,
    incremental_ic,
    non_overlapping,
    partial_rank_ic,
    partial_rank_ic_with_reason,
)

ROOT: Final[Path] = Path(__file__).resolve().parents[3]
MODULE_PATH: Final[Path] = ROOT / "src" / "openalpha_cn" / "domain" / "incremental_ic.py"

NUMERIC_LIBRARIES: Final[frozenset[str]] = frozenset(
    {"numpy", "pandas", "scipy", "sklearn", "duckdb", "sqlite3"}
)
STDLIB_IMPORTS: Final[frozenset[str]] = frozenset(
    {"__future__", "collections", "dataclasses", "datetime", "math", "statistics", "typing"}
)


def _imported_top_level_names(tree: ast.AST) -> set[str]:
    """Every top-level module name the AST imports, `from a.b import c` read as `a`."""
    imported = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported |= {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    return imported


def test_incremental_ic_imports_nothing_numeric() -> None:
    """`domain/` imports no numeric library; this reads the module's own import list."""
    imported = _imported_top_level_names(ast.parse(MODULE_PATH.read_text(encoding="utf-8")))
    assert not imported & NUMERIC_LIBRARIES, f"numeric import in incremental_ic.py: {imported}"
    assert imported <= STDLIB_IMPORTS, (
        f"unexpected import in incremental_ic.py: {sorted(imported - STDLIB_IMPORTS)}"
    )


def test_incremental_ic_reaches_no_numeric_stack_and_no_sibling_subpackage() -> None:
    """The graph reading, independent of the contract's static enumeration.

    `grimp.find_upstream_modules` is the module's import closure (`V2-P7-014`'s own reading of
    the API), so an empty closure is the claim that it reaches nothing else in the package --
    and the AST check beside it is where the external numeric stack would show, because grimp's
    graph holds `openalpha_cn` modules only.
    """
    graph = grimp.build_graph("openalpha_cn", cache_dir=None)
    upstream = graph.find_upstream_modules("openalpha_cn.domain.incremental_ic")
    assert upstream == frozenset(), f"incremental_ic.py reaches {sorted(upstream)}"

    imported = _imported_top_level_names(ast.parse(MODULE_PATH.read_text(encoding="utf-8")))
    assert not imported & NUMERIC_LIBRARIES, f"numeric import in incremental_ic.py: {imported}"


def _average_ranks(values: Sequence[float]) -> list[float]:
    """The slow, deliberately independent rank reference: 1-based, ties averaged."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        average = (start + 1 + end + 1) / 2
        for position in range(start, end + 1):
            ranks[order[position]] = average
        start = end + 1
    return ranks


def _reference_partial_ic(
    agent: Sequence[float], factor: Sequence[float], label: Sequence[float]
) -> float:
    """The slow reference: OLS by `statistics.linear_regression`, correlation by `statistics`."""
    factor_ranks = _average_ranks(factor)
    agent_slope, agent_intercept = statistics.linear_regression(factor_ranks, _average_ranks(agent))
    label_slope, label_intercept = statistics.linear_regression(factor_ranks, _average_ranks(label))
    agent_residual = [
        rank - (agent_intercept + agent_slope * x)
        for x, rank in zip(factor_ranks, _average_ranks(agent), strict=True)
    ]
    label_residual = [
        rank - (label_intercept + label_slope * x)
        for x, rank in zip(factor_ranks, _average_ranks(label), strict=True)
    ]
    return statistics.correlation(agent_residual, label_residual)


def test_a_planted_increment_is_recovered_within_tolerance() -> None:
    """A label driven partly by information the factor score does not carry.

    The plant is on the rank scale because that is the scale the statistic is defined on: the
    label's rank is driven by the factor's rank plus an independent driver, and the agent's rank
    by the same factor plus the same driver. The partial rank IC must therefore be strongly
    positive, and must equal the slow reference of the module's own definition.
    """
    rng = random.Random(20261112)
    size = 120
    factor = [rng.gauss(0.0, 1.0) for _ in range(size)]
    driver = [rng.gauss(0.0, 1.0) for _ in range(size)]
    agent = [f + 0.6 * d for f, d in zip(factor, driver, strict=True)]
    label = [d + 0.4 * rng.gauss(0.0, 1.0) for d in driver]

    expected = _reference_partial_ic(agent, factor, label)
    assert expected > 0.5, "the increment was planted strongly enough to be recovered"

    value = partial_rank_ic(agent, factor, label)
    assert value is not None
    assert abs(value - expected) <= 1e-12


def test_ties_and_too_few_names_give_none_with_reason() -> None:
    """Every refusal this API makes is reported: a `None` with the reason beside it.

    A reason-carrying companion rather than a wider return type keeps the plan's
    `partial_rank_ic -> float | None` signature, and lets `SessionIC.reason` be filled from the
    same arithmetic that decided the value. None of these raise.
    """
    assert partial_rank_ic_with_reason([0.1, 0.2, 0.3], [1.0, 2.0, 3.0], [0.3, 0.2, 0.1]) == (
        None,
        REASON_TOO_FEW_NAMES,
    )
    assert partial_rank_ic([0.1, 0.2, 0.3], [1.0, 2.0, 3.0], [0.3, 0.2, 0.1]) is None

    # ties in the factor: every name carries the same factor rank, so there is no design to
    # residualise on and the partial IC is undefined rather than zero
    assert partial_rank_ic_with_reason(
        [1.0, 2.0, 3.0, 4.0, 5.0], [7.0] * 5, [1.0, 2.0, 3.0, 4.0, 5.0]
    ) == (
        None,
        REASON_CONSTANT_FACTOR_RANK,
    )

    assert partial_rank_ic_with_reason(
        [1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0, 3.0, 4.0]
    ) == (
        None,
        REASON_LENGTH_MISMATCH,
    )

    assert partial_rank_ic_with_reason(
        [1.0, 2.0, float("nan"), 4.0], [1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0]
    ) == (
        None,
        REASON_NON_FINITE_INPUT,
    )


def test_an_agent_equal_to_the_factor_has_zero_partial_ic() -> None:
    """The degenerate copy and the near copy: no incremental information.

    The exact copy has an identically zero residual, so there is no coefficient to compute; that
    is reported as `None` with a reason rather than as a fabricated `0.0`, because `0.0` is a
    measurement and this is an undefined statistic. Ties that flatten the agent (or the label)
    produce the same identically zero residual and the same reason.
    """
    rng = random.Random(7)
    size = 120
    factor = [rng.gauss(0.0, 1.0) for _ in range(size)]
    label = [rng.gauss(0.0, 1.0) for _ in range(size)]

    assert partial_rank_ic(factor, factor, label) is None
    assert partial_rank_ic_with_reason(factor, factor, label) == (
        None,
        REASON_ZERO_RESIDUAL_VARIANCE,
    )
    assert partial_rank_ic_with_reason(
        [5.0] * 5, [1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0, 3.0, 4.0, 5.0]
    ) == (
        None,
        REASON_ZERO_RESIDUAL_VARIANCE,
    )
    assert partial_rank_ic_with_reason(
        [1.0, 2.0, 3.0, 4.0, 5.0], [1.0, 2.0, 3.0, 4.0, 5.0], [9.0] * 5
    ) == (
        None,
        REASON_ZERO_RESIDUAL_VARIANCE,
    )

    # a near copy: the label is independent of the factor, so whatever survives of the agent's
    # rank after residualising on the factor is noise and the statistic is ~0
    near_copy = [f + 0.05 * rng.gauss(0.0, 1.0) for f in factor]
    value = partial_rank_ic(near_copy, factor, label)
    assert value is not None
    assert abs(value) < 0.25


def test_h5_takes_every_fifth_session_from_the_anchor() -> None:
    """`h=5` labels overlap within five sessions and not across, so every fifth one is used.

    The anchor is an ordinal (`date.toordinal()`) rather than an index because the evaluator
    knows the registration's date, not its position in a list -- and the sessions it receives
    may arrive in any order. The choice is a pure function of the set and the anchor.
    """
    anchor = date(2026, 1, 5)
    sessions = tuple(
        SessionIC(
            session=anchor + timedelta(days=offset),
            n_scored=10 if offset != 5 else 3,
            value=float(offset) if offset != 5 else None,
            reason=None if offset != 5 else REASON_TOO_FEW_NAMES,
        )
        for offset in range(12)
    )

    chosen = non_overlapping(sessions, horizon=5, anchor_ordinal=anchor.toordinal())
    assert [item.session for item in chosen] == [
        anchor,
        anchor + timedelta(days=5),
        anchor + timedelta(days=10),
    ]
    assert [item.value for item in chosen] == [0.0, None, 10.0]
    assert chosen[1] == sessions[5], "the SessionIC's own reason and count travel with it"

    assert (
        non_overlapping(tuple(reversed(sessions)), horizon=5, anchor_ordinal=anchor.toordinal())
        == chosen
    ), "the input order is not allowed to change the choice"
    assert non_overlapping(sessions, horizon=1, anchor_ordinal=anchor.toordinal()) == sessions
    assert (
        non_overlapping(
            sessions, horizon=5, anchor_ordinal=(anchor + timedelta(days=100)).toordinal()
        )
        == ()
    )

    # an anchor that is not itself a session (a registration date, say) starts at the first
    # session at or after it
    shifted = non_overlapping(
        sessions, horizon=5, anchor_ordinal=(anchor - timedelta(days=2)).toordinal()
    )
    assert shifted == chosen


def _reference_spearman(values: Sequence[float], label: Sequence[float]) -> float:
    """The slow reference for a plain rank IC: Pearson of the two average-rank vectors."""
    return statistics.correlation(_average_ranks(values), _average_ranks(label))


def test_incremental_ic_is_the_combined_ic_minus_the_factor_ic_and_is_exploratory() -> None:
    """The exploratory companion, defined as its own sentence: `IC(agent + factor) - IC(factor)`.

    The combination is on the rank scale -- the sum of the two average-rank vectors, any positive
    multiple of which gives the same Spearman correlation -- because that is the scale both IC
    terms are defined on, so the two scores' arbitrary units cannot enter the comparison. The
    module documents it as exploratory; the docstring is asserted so the label is not quietly
    dropped when the evaluator starts importing it.
    """
    rng = random.Random(11)
    size = 150
    factor = [rng.gauss(0.0, 1.0) for _ in range(size)]
    driver = [rng.gauss(0.0, 1.0) for _ in range(size)]
    agent = [f + 0.8 * d for f, d in zip(factor, driver, strict=True)]
    label = [d + 0.5 * rng.gauss(0.0, 1.0) for d in driver]

    combined = [a + f for a, f in zip(_average_ranks(agent), _average_ranks(factor), strict=True)]
    expected = _reference_spearman(combined, label) - _reference_spearman(factor, label)
    assert expected > 0.1, "the agent carries information the factor does not"

    value = incremental_ic(agent, factor, label)
    assert value is not None
    assert abs(value - expected) <= 1e-12
    assert incremental_ic(factor, factor, label) == 0.0

    assert "exploratory" in (incremental_ic.__doc__ or "").lower()


def test_detectable_mean_agrees_with_the_survivor_power_block() -> None:
    """The one number the plan is decided on must not have a second spelling.

    `backtest/power_analysis.py`'s normal `smallest_mean_at_power` already prints
    `(z_(1-level) + z_power) * sd / sqrt(periods)`, and `V2-P7-014`'s report is calibrated on the
    same arithmetic. Domain may not import `backtest/`, so the formula is re-stated -- and this
    test holds the two statements equal across the range of designs the report will use. Without
    it a later "small improvement" to either spelling would silently move the power the protocol
    quotes.
    """
    designs = (
        (0.15, 120, 0.05, 0.8),
        (0.15, 56, 0.05, 0.8),
        (0.05, 60, 0.01, 0.9),
        (0.20, 12, 0.05, 0.50),
        (0.10, 1000, 0.10, 0.95),
        (0.07, 30, 0.025, 0.75),
    )
    for sd, periods, level, power in designs:
        ours = detectable_mean(sd=sd, periods=periods, level=level, power=power)
        theirs = smallest_mean_at_power(
            power=power, sd=sd, periods=periods, test_level=level, kind="normal"
        )
        assert abs(ours - theirs) <= 1e-12, (
            f"detectable_mean and the survivor power block disagree for "
            f"sd={sd}, periods={periods}, level={level}, power={power}: {ours!r} vs {theirs!r}"
        )


def test_detectable_mean_refuses_a_design_it_cannot_answer() -> None:
    """A design question with no answer is refused by name, in `power_analysis`'s own idiom.

    The bounds are the survivor power block's bounds: a planning sd that is positive and finite,
    at least two periods, a one-sided level strictly between 0 and 0.5, and a target power
    strictly between 0 and 1. Answering `nan` or a `StatisticsError` from `NormalDist` would put
    the wrong number in a report.
    """
    for kwargs in (
        {"sd": 0.0, "periods": 120, "level": 0.05, "power": 0.8},
        {"sd": float("nan"), "periods": 120, "level": 0.05, "power": 0.8},
        {"sd": float("inf"), "periods": 120, "level": 0.05, "power": 0.8},
        {"sd": 0.15, "periods": 1, "level": 0.05, "power": 0.8},
        {"sd": 0.15, "periods": 120, "level": 0.0, "power": 0.8},
        {"sd": 0.15, "periods": 120, "level": 0.5, "power": 0.8},
        {"sd": 0.15, "periods": 120, "level": 0.05, "power": 0.0},
        {"sd": 0.15, "periods": 120, "level": 0.05, "power": 1.0},
    ):
        with pytest.raises(IncrementalICError):
            detectable_mean(**kwargs)  # type: ignore[arg-type]
