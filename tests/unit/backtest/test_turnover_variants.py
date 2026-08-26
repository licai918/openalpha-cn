"""The band's saving, the price that equals it, and the arms a caller cannot ask for one of.

`V2-P5-024` asks for a buffered variant reported beside the unbuffered one **by default**. Two
things below carry most of the weight: that a one-armed report is unrepresentable, and that the
turnover a band saves is exactly the distance it puts between the book you hold and the book
the ranking asked for.

## The corpus is a staircase of five distinct moves

Four equally ranked candidates over two equal tiers give every name a target of exactly
`0.200000` under an 80% exposure ceiling. Against `PREVIOUS` the five requested moves are all
different and all exact:

    S2  0.195000 -> 0.200000   move 0.005000    the smallest
    S1  0.180000 -> 0.200000   move 0.020000
    S9  0.030000 -> 0          move 0.030000    a name the ranking dropped
    S3  0.100000 -> 0.200000   move 0.100000
    S4  0        -> 0.200000   move 0.200000    the largest

Total requested turnover is `0.355000` on the both-sides convention. Because no two moves are
equal, a band set at any one of them suppresses a **known** subset and nothing else, and
`BAND_STAIRCASE` asserts the whole ladder rather than one rung. Two moves of the same size would
have made the ladder blind to an off-by-one in the comparison.

**The bands are set exactly on the move sizes on purpose.** `0.005` and `0.020` are the moves of
`S2` and `S1` to the last digit, so they drive the `<=` in `_banded`: an implementation using
`<` trades those names and the rung is one step off. And `S3`'s move of `0.100` is not
suppressed at a band of `0.030`, so `test_the_band_takes_a_move_whole_rather_than_to_the_edge`
can check it arrives at `0.200000` and not at `0.130000` -- which is what a *damping* would have
produced, and damping is the device `V2-P5-001` already has.
"""

from decimal import Decimal
from typing import Final

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.portfolio import PortfolioLimits
from openalpha_cn.backtest.portfolio_policy import (
    ConstructionCandidate,
    PortfolioConstructionPolicy,
)
from openalpha_cn.backtest.turnover_variants import (
    KNOWN_TURNOVER_VARIANT_LIMITATIONS,
    TurnoverArm,
    TurnoverCostModel,
    TurnoverVariantError,
    TurnoverVariantReport,
    report_turnover_variants,
    turnover_variant_view,
)

CANDIDATES: Final[tuple[ConstructionCandidate, ...]] = tuple(
    ConstructionCandidate(subject=f"S{index}", rank=index, score=1.0 - index / 10)
    for index in range(1, 5)
)
"""Four names, so two equal tiers of two give every one of them the same target."""

POLICY: Final[PortfolioConstructionPolicy] = PortfolioConstructionPolicy(
    tier_weights=(Decimal("0.5"), Decimal("0.5")),
    limits=PortfolioLimits(
        max_position_weight=Decimal("0.25"),
        max_total_exposure=Decimal("0.8"),
        min_cash_weight=Decimal("0.2"),
    ),
)
"""An 80% book in four equal 20% positions, each comfortably under the 25% cap."""

TARGET_WEIGHT: Final[Decimal] = Decimal("0.200000")

PREVIOUS: Final[dict[str, Decimal]] = {
    "S1": Decimal("0.180000"),
    "S2": Decimal("0.195000"),
    "S3": Decimal("0.100000"),
    "S9": Decimal("0.030000"),
}
"""Three held names the ranking still admits and one -- `S9` -- that it has dropped."""

REQUESTED_TURNOVER: Final[Decimal] = Decimal("0.355000")
"""`0.005 + 0.020 + 0.030 + 0.100 + 0.200`, both sides counted."""

BAND_STAIRCASE: Final[tuple[tuple[str, str, int], ...]] = (
    ("0", "0.355000", 5),
    ("0.005", "0.350000", 4),
    ("0.020", "0.330000", 3),
    ("0.030", "0.300000", 2),
    ("0.100", "0.200000", 1),
    ("0.200", "0.000000", 0),
)
"""`(band, buffered turnover, names traded)` at every distinct move size in the corpus.

The whole ladder rather than one rung, because a single rung passes for an implementation whose
comparison is off by one move.
"""


def _report(
    *,
    buffer: str = "0.030",
    previous: dict[str, Decimal] | None = None,
    cost_model: TurnoverCostModel | None = None,
    policy: PortfolioConstructionPolicy = POLICY,
) -> TurnoverVariantReport:
    return report_turnover_variants(
        candidates=CANDIDATES,
        policy=policy,
        buffer=Decimal(buffer),
        previous=PREVIOUS if previous is None else previous,
        cost_model=cost_model,
    )


def _weights(arm: TurnoverArm) -> dict[str, Decimal]:
    return dict(arm.weights)


# --- both arms, always, and no way to ask for one ----------------------------------------------


def test_a_one_armed_report_is_unrepresentable_rather_than_discouraged() -> None:
    """`默认并列出报` expressed as a required field rather than as a convention.

    A caller who can ask for the flattering arm alone eventually will, so the type is what
    refuses it: there is no argument that suppresses an arm and no report that omits one.
    """
    with pytest.raises(ValidationError):
        TurnoverVariantReport(
            policy=POLICY,
            buffer=Decimal("0.03"),
            unbuffered=TurnoverArm(
                label="unbuffered",
                weights=(),
                turnover=Decimal(0),
                invested_weight=Decimal(0),
                names_traded=0,
            ),
            turnover_reduction=Decimal(0),
            cost_absence_reason="none declared",
        )


def test_both_arms_answer_to_one_policy_and_one_construction() -> None:
    """Every difference between the arms is the band and nothing else."""
    report = _report()

    assert report.policy == POLICY
    assert report.method == "heuristic, not optimized"
    assert report.unbuffered.label == "unbuffered"
    assert report.buffered.label == "buffered"
    assert _weights(report.unbuffered) == {name: TARGET_WEIGHT for name in ("S1", "S2", "S3", "S4")}


def test_the_arms_are_mislabelled_at_the_document_boundary_and_refused() -> None:
    """Reachable from a stored report read back, not from `report_turnover_variants`."""
    arm = TurnoverArm(
        label="buffered",
        weights=(),
        turnover=Decimal(0),
        invested_weight=Decimal(0),
        names_traded=0,
    )
    with pytest.raises(ValidationError):
        TurnoverVariantReport(
            policy=POLICY,
            buffer=Decimal("0.03"),
            unbuffered=arm,
            buffered=arm,
            turnover_reduction=Decimal(0),
            cost_absence_reason="none declared",
        )


# --- the band trades less, and by exactly how much ---------------------------------------------


def test_the_unbuffered_arm_requests_every_move_the_ranking_asked_for() -> None:
    report = _report(buffer="0")

    assert report.unbuffered.turnover == REQUESTED_TURNOVER
    assert report.unbuffered.names_traded == 5


@pytest.mark.parametrize(("band", "turnover", "traded"), BAND_STAIRCASE)
def test_a_wider_band_trades_strictly_fewer_names_down_the_whole_staircase(
    band: str, turnover: str, traded: int
) -> None:
    """The row's own integration seam -- 缓冲版换手显著低于无缓冲版 -- as exact arithmetic.

    Every rung is asserted, including the two whose band sits exactly on a move size, which is
    where a `<` instead of a `<=` shows up.
    """
    report = _report(buffer=band)

    assert report.buffered.turnover == Decimal(turnover)
    assert report.buffered.names_traded == traded
    assert report.unbuffered.turnover == REQUESTED_TURNOVER
    assert report.turnover_reduction == REQUESTED_TURNOVER - Decimal(turnover)
    assert report.turnover_reduction >= 0


def test_a_band_of_zero_leaves_the_two_arms_identical() -> None:
    """A band that suppresses nothing is not a saving, and the report says zero."""
    report = _report(buffer="0")

    assert report.buffered.turnover == report.unbuffered.turnover
    assert report.turnover_reduction == Decimal("0.000000")
    assert _weights(report.buffered) == _weights(report.unbuffered)
    assert report.turnover_ratio == 1


def test_a_band_wider_than_every_move_holds_the_previous_book_whole() -> None:
    """Zero turnover, and a book that is exactly what was already held."""
    report = _report(buffer="0.200")

    assert report.buffered.turnover == Decimal("0.000000")
    assert report.buffered.names_traded == 0
    assert _weights(report.buffered) == PREVIOUS
    assert report.turnover_reduction == REQUESTED_TURNOVER


def test_the_band_takes_a_move_whole_rather_than_to_the_edge_of_the_band() -> None:
    """The line between a band and a damping, and `V2-P5-001` already has the damping.

    `S3` is asked to move `0.100` against a band of `0.030`. A band takes the move whole, so it
    arrives at `0.200000`. A damping to the edge of the band would have left it at `0.130000`,
    and every name would have traded a little -- the outcome a band exists to avoid.
    """
    report = _report(buffer="0.030")
    weights = _weights(report.buffered)

    assert weights["S3"] == TARGET_WEIGHT
    assert weights["S3"] != Decimal("0.130000")
    assert weights["S1"] == PREVIOUS["S1"]
    assert weights["S2"] == PREVIOUS["S2"]


def test_the_saving_and_the_distance_from_the_target_are_one_number() -> None:
    """The identity that removed a column, asserted at every rung of the staircase.

    A banded weight is the target or the previous weight, so a traded name contributes nothing
    to either quantity and a suppressed name contributes its whole move to both. There is no
    `tracking_deviation` field because a column that cannot disagree with its parents cannot
    detect anything, which is `V2-P5-005`'s rule.
    """
    for band, turnover, _traded in BAND_STAIRCASE:
        report = _report(buffer=band)
        distance = sum(
            (
                abs(
                    _weights(report.buffered).get(name, Decimal(0))
                    - _weights(report.unbuffered).get(name, Decimal(0))
                )
                for name in set(_weights(report.buffered)) | set(_weights(report.unbuffered))
            ),
            start=Decimal(0),
        )
        assert report.deviation_from_intended_book == report.turnover_reduction
        assert distance == report.turnover_reduction
        assert report.buffered.turnover == Decimal(turnover)


def test_the_turnover_ratio_is_absent_when_nothing_was_requested() -> None:
    """No ratio rather than a number a reader would take for a saving the band produced."""
    report = _report(buffer="0.030", previous={})

    assert report.unbuffered.turnover > 0
    assert report.turnover_ratio is not None

    idle = report_turnover_variants(
        candidates=CANDIDATES,
        policy=POLICY,
        buffer=Decimal("0.030"),
        previous={name: TARGET_WEIGHT for name in ("S1", "S2", "S3", "S4")},
    )
    assert idle.unbuffered.turnover == 0
    assert idle.turnover_ratio is None


def test_an_empty_previous_book_makes_the_band_do_nothing_and_says_nothing_false() -> None:
    """`the_previous_book_is_declared_by_the_caller_and_is_never_read_from_a_ledger`."""
    report = _report(buffer="0.030", previous={})

    assert report.turnover_reduction == 0
    assert _weights(report.buffered) == _weights(report.unbuffered)


# --- what the band costs, named rather than folded away ----------------------------------------


def test_a_name_the_ranking_drops_and_the_band_keeps_is_named_rather_than_hidden() -> None:
    """`S9` is not in the target and the band refuses to sell it; the report says which name."""
    report = _report(buffer="0.030")

    assert report.retained_positions == ("S9",)
    assert _weights(report.buffered)["S9"] == PREVIOUS["S9"]
    assert "S9" not in _weights(report.unbuffered)


def test_a_band_too_narrow_to_keep_a_dropped_name_retains_nothing() -> None:
    report = _report(buffer="0.020")

    assert report.retained_positions == ()
    assert "S9" not in _weights(report.buffered)


def test_a_retained_position_above_the_cap_is_reported_and_never_repaired() -> None:
    """Suppressing a trade suppresses the trim it carried; repairing it would spend the saving."""
    heavy = dict(PREVIOUS) | {"S1": Decimal("0.300000")}
    report = _report(buffer="0.100", previous=heavy)

    assert _weights(report.buffered)["S1"] == Decimal("0.300000")
    assert report.position_caps_breached == ("S1",)
    assert Decimal("0.300000") > POLICY.limits.max_position_weight


def test_a_book_inside_every_cap_reports_no_breach() -> None:
    report = _report(buffer="0.030")

    assert report.position_caps_breached == ()


# --- the cost of turnover is declared or it is absent by name ----------------------------------


def test_no_declared_rate_publishes_no_cost_and_says_why() -> None:
    """An invented default would be multiplied by every turnover number in the report."""
    report = _report(buffer="0.030")

    assert report.cost_model is None
    assert report.cost_saved is None
    assert report.unbuffered.turnover_cost is None
    assert report.buffered.turnover_cost is None
    assert report.cost_absence_reason is not None
    assert "would be a number this module invented" in report.cost_absence_reason


def test_a_declared_rate_costs_both_arms_and_the_saving_between_them() -> None:
    """`0.355 * 0.001` and `0.300 * 0.001`, exact in `Decimal` and asserted with `==`."""
    model = TurnoverCostModel(
        cost_per_unit_turnover=Decimal("0.001"), definition="commission and stamp duty"
    )
    report = _report(buffer="0.030", cost_model=model)

    assert report.unbuffered.turnover_cost == Decimal("0.000355000")
    assert report.buffered.turnover_cost == Decimal("0.000300000")
    assert report.cost_saved == Decimal("0.000055000")
    assert report.cost_absence_reason is None


def test_a_zero_rate_is_a_declaration_and_not_a_refusal() -> None:
    """A caller modelling a zero-cost venue is making a claim, not making a mistake."""
    model = TurnoverCostModel(cost_per_unit_turnover=Decimal("0"), definition="a zero-cost venue")
    report = _report(buffer="0.030", cost_model=model)

    assert report.cost_saved == 0
    assert report.cost_absence_reason is None


def test_the_cost_columns_stand_or_fall_together_at_the_document_boundary() -> None:
    arm = TurnoverArm(
        label="unbuffered",
        weights=(),
        turnover=Decimal(0),
        invested_weight=Decimal(0),
        names_traded=0,
        turnover_cost=Decimal(0),
    )
    with pytest.raises(ValidationError):
        TurnoverVariantReport(
            policy=POLICY,
            buffer=Decimal("0.03"),
            unbuffered=arm,
            buffered=TurnoverArm(
                label="buffered",
                weights=(),
                turnover=Decimal(0),
                invested_weight=Decimal(0),
                names_traded=0,
            ),
            turnover_reduction=Decimal(0),
            cost_absence_reason="a cost that is half present",
        )


def test_a_report_that_neither_costs_nor_explains_its_silence_is_refused() -> None:
    arm = TurnoverArm(
        label="unbuffered",
        weights=(),
        turnover=Decimal(0),
        invested_weight=Decimal(0),
        names_traded=0,
    )
    with pytest.raises(ValidationError):
        TurnoverVariantReport(
            policy=POLICY,
            buffer=Decimal("0.03"),
            unbuffered=arm,
            buffered=arm.model_copy(update={"label": "buffered"}),
            turnover_reduction=Decimal(0),
        )


# --- refusals, the view, and the registry ------------------------------------------------------


@pytest.mark.parametrize("buffer", ["-0.01", "1.5"])
def test_a_band_outside_the_unit_interval_is_refused(buffer: str) -> None:
    with pytest.raises(TurnoverVariantError):
        _report(buffer=buffer)


def test_a_negative_previous_weight_is_refused_rather_than_banded() -> None:
    """Nothing in this construction plane declares a short position."""
    with pytest.raises(TurnoverVariantError):
        _report(previous={"S1": Decimal("-0.10")})


def test_a_negative_turnover_reduction_is_refused_because_a_band_cannot_add_a_trade() -> None:
    with pytest.raises(ValidationError):
        TurnoverVariantReport(
            policy=POLICY,
            buffer=Decimal("0.03"),
            unbuffered=TurnoverArm(
                label="unbuffered",
                weights=(),
                turnover=Decimal("0.1"),
                invested_weight=Decimal(0),
                names_traded=0,
            ),
            buffered=TurnoverArm(
                label="buffered",
                weights=(),
                turnover=Decimal("0.2"),
                invested_weight=Decimal(0),
                names_traded=0,
            ),
            turnover_reduction=Decimal("-0.1"),
            cost_absence_reason="none declared",
        )


def test_a_reduction_that_is_not_the_difference_of_the_two_arms_is_refused() -> None:
    """The join between the arms and the headline, at the document boundary.

    `report_turnover_variants` computes the reduction from the two arms, so nothing that drives
    the function reaches this branch -- a mutation sweep measured that deleting it changed no
    test, which is how this case came to be written down. It is reachable from a *stored*
    report, where a saving that does not match the two turnovers beside it is exactly the number
    a reader would quote.
    """
    with pytest.raises(ValidationError):
        TurnoverVariantReport(
            policy=POLICY,
            buffer=Decimal("0.03"),
            unbuffered=TurnoverArm(
                label="unbuffered",
                weights=(),
                turnover=Decimal("0.3"),
                invested_weight=Decimal(0),
                names_traded=0,
            ),
            buffered=TurnoverArm(
                label="buffered",
                weights=(),
                turnover=Decimal("0.1"),
                invested_weight=Decimal(0),
                names_traded=0,
            ),
            turnover_reduction=Decimal("0.05"),
            cost_absence_reason="none declared",
        )


def test_the_view_renders_both_arms_in_order_and_every_decimal_as_a_string() -> None:
    """`construction_view`'s convention: a JSON reader cannot take a weight through a float."""
    report = _report(buffer="0.030")
    view = turnover_variant_view(report)

    assert [arm["label"] for arm in view["arms"]] == ["unbuffered", "buffered"]
    assert view["turnover_reduction"] == "0.055000"
    assert view["deviation_from_intended_book"] == "0.055000"
    assert view["arms"][1]["turnover"] == "0.300000"
    assert view["retained_positions"] == ["S9"]
    assert view["cost_model"] is None
    assert isinstance(view["buffer"], str)
    for arm in view["arms"]:
        assert isinstance(arm["turnover"], str)
        for weight in arm["weights"]:
            assert isinstance(weight["weight"], str)
    assert len(view["limitations"]) == len(KNOWN_TURNOVER_VARIANT_LIMITATIONS)


def test_the_same_request_reports_the_same_numbers_twice() -> None:
    assert turnover_variant_view(_report()) == turnover_variant_view(_report())


def test_the_registry_names_every_limitation_this_module_declares() -> None:
    """Equality rather than membership, the form every registry in this repository has."""
    assert {limitation.code for limitation in KNOWN_TURNOVER_VARIANT_LIMITATIONS} == {
        "the_buffer_is_a_no_trade_band_and_not_the_turnover_budget_v2_p5_001_already_has",
        "the_previous_book_is_declared_by_the_caller_and_is_never_read_from_a_ledger",
        "the_cost_model_is_one_declared_linear_rate_and_not_an_execution_simulation",
        "a_retained_position_is_named_but_its_future_is_not_modelled",
        "the_band_can_leave_a_position_cap_breached_and_says_so_instead_of_retrimming",
        "the_distance_from_the_intended_book_is_a_weight_distance_and_not_a_return_difference",
    }
