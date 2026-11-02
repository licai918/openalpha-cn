"""The decile readout is the forward report's arithmetic (`V2-P7-004`): one cross section read by
`decile_readout` and by `strategy_survivor_forward.record_outcome` gives the same numbers, bit
for bit -- on the forward tests' twenty-name scope with its tie across the first decile boundary,
and on scopes of 19 and 23 names (where `n * k // 10` and `10 p // n` are not exact), with names
that lost their label among the removed tail, among the survivors and at the very top."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest
from test_strategy_survivor_forward import TWENTY_SCORES, hand_outcome

from openalpha_cn.backtest.decile_readout import read_cross_section

HALTED = ("halted_session",)


def permuted_scope(size: int) -> dict[str, float]:
    """`size` names (a prime) whose scores are a permutation of 0..size-1: no two tie, and the
    order of the codes is not the order of the scores."""
    return {f"6000{n:02d}.SH": float(n * 7 % size) for n in range(size)}


def rising(position: int) -> float:
    """A label that grows with the score: the rank IC is 1 when every name is labelled."""
    return 0.001 * position


def scrambled(position: int) -> float:
    """A label with ties and no order: a rank IC between the extremes."""
    return 0.001 * (position * 5 % 11) - 0.004


@pytest.mark.parametrize("unlabelled", [(), (3, 11), (12,), (-1,), (0, 1, 2, 3, 4)])
@pytest.mark.parametrize("label", [rising, scrambled], ids=["rising", "scrambled"])
@pytest.mark.parametrize(
    "scope",
    [TWENTY_SCORES, permuted_scope(19), permuted_scope(23)],
    ids=["twenty_with_a_tie", "nineteen", "twenty_three"],
)
def test_the_readout_is_the_forward_reports_arithmetic(
    scope: Mapping[str, float], label: Callable[[int], float], unlabelled: tuple[int, ...]
) -> None:
    """`unlabelled` are positions in worst-first order (a negative one counts from the best)."""
    worst_first = sorted(scope, key=lambda code: (scope[code], code))
    lost = {position % len(scope) for position in unlabelled}
    answers = {
        code: HALTED if position in lost else label(position)
        for position, code in enumerate(worst_first)
    }
    labels = {code: value for code, value in answers.items() if isinstance(value, float)}

    outcome = hand_outcome(scope, answers)
    read = read_cross_section(tuple(reversed(scope.items())), labels, loser_decile_count=2)

    assert read.rank_ic == outcome.rank_ic
    assert read.rank_ic_absence == dict(outcome.absent).get("rank_ic")
    assert read.equal_weight == outcome.investable_ew
    assert read.survivors_minus_ew == outcome.survivors_minus_ew
    assert read.ew_minus_worst_decile == outcome.ew_minus_worst_decile
    assert read.top_minus_bottom == outcome.top_minus_bottom
    assert read.scope == outcome.scope
    assert read.scope_by_decile == outcome.scope_by_decile
    assert (read.labelled, read.unlabelled) == (outcome.labelled, outcome.unlabelled)
    assert read.labelled_by_decile == tuple(
        in_scope - lost_there
        for in_scope, lost_there in zip(
            outcome.scope_by_decile, outcome.unlabelled_by_decile, strict=True
        )
    )
