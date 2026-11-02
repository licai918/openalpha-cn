"""The decile readout is the forward report's arithmetic (`V2-P7-004`): one cross section read by
`decile_readout` and by `strategy_survivor_forward.record_outcome` gives the same numbers, bit
for bit, on the forward tests' twenty-name scope with its tie across the first decile boundary."""

from __future__ import annotations

from test_strategy_survivor_forward import TWENTY_SCORES, by_position, hand_outcome

from openalpha_cn.backtest.decile_readout import read_cross_section


def test_the_readout_is_the_forward_reports_arithmetic() -> None:
    answers = by_position(
        lambda position: ("halted_session",) if position in (3, 11) else 0.001 * position
    )
    labels = {code: value for code, value in answers.items() if isinstance(value, float)}

    outcome = hand_outcome(TWENTY_SCORES, answers)
    read = read_cross_section(tuple(TWENTY_SCORES.items()), labels, loser_decile_count=2)

    assert read.rank_ic == outcome.rank_ic
    assert read.equal_weight == outcome.investable_ew
    assert read.survivors_minus_ew == outcome.survivors_minus_ew
    assert read.ew_minus_worst_decile == outcome.ew_minus_worst_decile
    assert read.top_minus_bottom == outcome.top_minus_bottom
    assert read.scope_by_decile == outcome.scope_by_decile
    assert (read.labelled, read.unlabelled) == (outcome.labelled, outcome.unlabelled)
