"""`V2-P5-001` and `V2-P5-002`: the heuristic construction policy and the limits it reads."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.portfolio import (
    LIMITS_ENFORCED_BY_THE_SIMULATOR,
    PortfolioLimits,
)
from openalpha_cn.backtest.portfolio_policy import (
    CONSTRUCTION_LIMITATION_CODES,
    CONSTRUCTION_METHOD,
    KNOWN_CONSTRUCTION_LIMITATIONS,
    LIMITS_ENFORCED_BY_THE_CONSTRUCTION_POLICY,
    ConstructionCandidate,
    PortfolioConstruction,
    PortfolioConstructionError,
    PortfolioConstructionPolicy,
    candidates_from_shortlist_answer,
    construct_portfolio,
    construction_view,
)

EVEN_TIERS = (Decimal("0.5"), Decimal("0.3"), Decimal("0.2"))


def candidates(
    count: int, *, industries: tuple[str, ...] | None = None
) -> tuple[ConstructionCandidate, ...]:
    """`count` names at ranks `1..count`, optionally cycling through `industries`."""
    return tuple(
        ConstructionCandidate(
            subject=f"{index:06d}.SZ",
            rank=index,
            score=1.0 - index / 100,
            industry_code=None if industries is None else industries[(index - 1) % len(industries)],
        )
        for index in range(1, count + 1)
    )


def policy(**limits: object) -> PortfolioConstructionPolicy:
    return PortfolioConstructionPolicy(
        tier_weights=EVEN_TIERS,
        limits=PortfolioLimits(**limits),  # type: ignore[arg-type]
    )


def weights(construction: object) -> dict[str, Decimal]:
    return {target.subject: target.weight for target in construction.targets}  # type: ignore[attr-defined]


# --- the label the row is about ------------------------------------------------------------------


def test_every_construction_declares_itself_a_heuristic_on_the_record_and_on_the_rendering() -> (
    None
):
    """`V2-P5-001`'s stated requirement, on both the object and the bytes a face hands out.

    Asserted against the literal sentence rather than against `CONSTRUCTION_METHOD`, because a
    test that compares the constant to itself stays green when the constant is edited to say
    something weaker -- which is the only way this claim can actually regress.
    """
    construction = construct_portfolio(candidates=candidates(9), policy=policy())

    assert construction.method == "heuristic, not optimized"
    assert CONSTRUCTION_METHOD == "heuristic, not optimized"
    assert construction_view(construction)["method"] == "heuristic, not optimized"


# --- tiered ranking ------------------------------------------------------------------------------


def test_a_tier_splits_its_declared_share_equally_and_the_tiers_do_not_share_a_weight() -> None:
    """Nine names, three tiers of three, 50/30/20 of an 80% book: 13.33%, 8%, 5.33% each.

    The three tier weights have to produce three *different* per-name weights or the test cannot
    tell a tiered policy from an equal-weight one -- which is why the fixture is nine names under
    an uneven tier vector rather than the six-under-50/50 that reads the same either way.
    """
    construction = construct_portfolio(candidates=candidates(9), policy=policy())

    held = weights(construction)
    assert [target.tier for target in construction.targets] == [1, 1, 1, 2, 2, 2, 3, 3, 3]
    assert held["000001.SZ"] == held["000002.SZ"] == held["000003.SZ"] == Decimal("0.133333")
    assert held["000004.SZ"] == Decimal("0.080000")
    assert held["000007.SZ"] == Decimal("0.053333")
    assert construction.invested_weight <= Decimal("0.80")


def test_the_remainder_of_an_uneven_cut_goes_to_the_earlier_tiers() -> None:
    """Eight names over three tiers is 3/3/2, not 2/3/3 -- the higher-ranked block keeps it."""
    construction = construct_portfolio(candidates=candidates(8), policy=policy())

    assert [target.tier for target in construction.targets] == [1, 1, 1, 2, 2, 2, 3, 3]


def test_a_list_shorter_than_the_tier_vector_is_refused_rather_than_leaving_a_tier_empty() -> None:
    """An empty tier's share would be redistributed with nothing saying so."""
    with pytest.raises(PortfolioConstructionError, match="cannot fill 3 tiers"):
        construct_portfolio(candidates=candidates(2), policy=policy())


def test_ranks_that_are_not_one_through_n_are_refused_because_a_tier_is_a_block_of_them() -> None:
    gapped = (
        ConstructionCandidate(subject="000001.SZ", rank=1, score=1.0),
        ConstructionCandidate(subject="000002.SZ", rank=2, score=0.9),
        ConstructionCandidate(subject="000003.SZ", rank=4, score=0.8),
    )

    with pytest.raises(PortfolioConstructionError, match=r"ranks must be exactly 1\.\.3"):
        construct_portfolio(candidates=gapped, policy=policy())


def test_a_tier_vector_that_does_not_sum_to_one_is_an_undeclared_second_cash_position() -> None:
    with pytest.raises(ValueError, match="sum to exactly 1"):
        PortfolioConstructionPolicy(tier_weights=(Decimal("0.5"), Decimal("0.3")))


# --- cap trimming --------------------------------------------------------------------------------


def test_the_position_cap_trims_the_top_tier_and_the_freed_weight_reaches_the_others() -> None:
    """A 10% cap over a top tier that wanted 13.33% each: the excess must land on the names with
    headroom, and both the trimmed and the raised names must report that they moved.

    **The cap has to bind on one tier and not on all three**, which is why it is 10% and not the
    6% this test was first written with: at 6% every one of the nine names is at the cap, nothing
    has headroom, and the assertion cannot tell redistribution from dropping the freed weight to
    cash. Under 10% the arithmetic is closed -- 3x10% + 3x9% + 3x7.6666% adds back to the 80%
    book, so the freed 10% is fully placed and each figure below is the only one it can be.
    """
    construction = construct_portfolio(
        candidates=candidates(9), policy=policy(max_position_weight=Decimal("0.10"))
    )

    held = weights(construction)
    assert held["000001.SZ"] == Decimal("0.100000")
    assert held["000004.SZ"] == Decimal("0.090000")
    assert held["000007.SZ"] == Decimal("0.076666")
    assert construction.targets[0].was_adjusted is True
    assert construction.targets[3].was_adjusted is True
    assert construction.unallocated_weight < Decimal("0.000010")


def test_weight_the_caps_will_not_take_becomes_cash_and_is_reported_rather_than_hidden() -> None:
    """Nine names under a 5% cap can hold at most 45% of an 80% book, so 35% is unallocatable.

    The number is asserted exactly. `unallocated_weight` existing but being zero on every fixture
    is the shape that would pass while the residue was quietly added to the last name -- the
    trick `V2-P5-005` exists to delete out of `backtest/validation.py`.
    """
    construction = construct_portfolio(
        candidates=candidates(9), policy=policy(max_position_weight=Decimal("0.05"))
    )

    assert construction.invested_weight == Decimal("0.450000")
    assert construction.unallocated_weight == Decimal("0.350000")
    assert construction.cash_weight == Decimal("0.550000")
    assert all(target.weight == Decimal("0.050000") for target in construction.targets)


def test_an_industry_cap_binds_a_whole_group_and_moves_the_weight_to_the_other_group() -> None:
    """Six names alternating between two industries, one of them capped at 20%.

    The fixture has to make the cap bind on an industry whose members are individually *under*
    the position cap, or the assertion cannot separate an industry cap from the position cap it
    would otherwise be reading.
    """
    construction = construct_portfolio(
        candidates=candidates(6, industries=("801010", "801020")),
        policy=policy(max_industry_weight=Decimal("0.20"), max_position_weight=Decimal("0.5")),
    )

    held = weights(construction)
    by_industry = {
        code: sum(held[t.subject] for t in construction.targets if t.industry_code == code)
        for code in ("801010", "801020")
    }

    assert by_industry == {code: Decimal("0.200000") for code in ("801010", "801020")}
    assert max(held.values()) == Decimal("0.100000") < Decimal("0.5")
    assert held["000001.SZ"] == Decimal("0.100000")
    assert held["000005.SZ"] == Decimal("0.040000")
    assert construction.unallocated_weight == Decimal("0.400000")


def test_an_industry_cap_over_candidates_that_carry_no_industry_is_refused_by_name() -> None:
    """The measured state of every shipped face: `shortlist_view` builds the ranking with
    `exposures=None`, so no candidate reaching a construction carries an industry today.

    Fail-closed rather than fail-open, because a cap that sees no industries is trivially
    satisfied by every book, and a report that says the cap held would be true and useless.
    """
    with pytest.raises(PortfolioConstructionError, match="carry no `industry_code`"):
        construct_portfolio(
            candidates=candidates(6),
            policy=policy(max_industry_weight=Decimal("0.20")),
        )


# --- turnover budget -----------------------------------------------------------------------------


def test_without_a_budget_the_whole_move_is_made_and_the_turnover_is_still_measured() -> None:
    """The control for the test below: the same move, unbudgeted, arrives in full."""
    construction = construct_portfolio(
        candidates=candidates(9),
        policy=policy(),
        previous={"000001.SZ": Decimal("0.10")},
    )

    assert construction.turnover_damping is None
    assert construction.turnover == construction.turnover_before_budget
    assert construction.turnover > Decimal("0.60")
    assert weights(construction)["000001.SZ"] == Decimal("0.133333")


def test_a_budget_scales_the_whole_move_and_the_book_lands_between_the_two() -> None:
    """The heuristic itself: `previous + (budget / turnover) * (target - previous)`.

    Asserted as three facts that no single one implies -- the realised turnover is at the budget
    and not merely under it, the damping factor is on the answer, and a name that had to *rise*
    lands strictly between where it was and where the policy wanted it.
    """
    previous = {f"{index:06d}.SZ": Decimal("0.05") for index in range(1, 10)}
    construction = construct_portfolio(
        candidates=candidates(9),
        policy=policy(turnover_budget=Decimal("0.10")),
        previous=previous,
    )

    assert construction.turnover_before_budget > Decimal("0.10")
    assert construction.turnover <= Decimal("0.10")
    assert construction.turnover > Decimal("0.09")
    assert construction.turnover_damping is not None
    held = weights(construction)
    assert Decimal("0.05") < held["000001.SZ"] < Decimal("0.133333")


def test_a_budget_the_move_already_fits_inside_changes_nothing() -> None:
    """The other side of the branch: a generous budget must not damp, or the test above would
    pass on an implementation that always damps."""
    previous = {f"{index:06d}.SZ": Decimal("0.05") for index in range(1, 10)}
    construction = construct_portfolio(
        candidates=candidates(9),
        policy=policy(turnover_budget=Decimal("5")),
        previous=previous,
    )

    assert construction.turnover_damping is None
    assert weights(construction)["000001.SZ"] == Decimal("0.133333")


def test_damping_out_of_a_book_already_over_a_cap_reports_the_breach_and_does_not_retrim() -> None:
    """The honest cost of applying the budget last, stated as a behaviour.

    The previous book holds one name at 40% under a 25% cap. Any partial move toward a compliant
    target leaves it over, and re-trimming would spend turnover the budget just refused -- so the
    breach is named on the answer and the weight is left where the budget put it.
    """
    previous = {"000001.SZ": Decimal("0.40")}
    construction = construct_portfolio(
        candidates=candidates(9),
        policy=policy(turnover_budget=Decimal("0.05")),
        previous=previous,
    )

    assert construction.caps_breached_after_turnover_damping == ("position:000001.SZ",)
    assert weights(construction)["000001.SZ"] > Decimal("0.25")


def test_a_previous_book_that_sums_over_one_is_refused_as_leverage_nothing_here_accounts_for() -> (
    None
):
    with pytest.raises(PortfolioConstructionError, match="more than the whole book"):
        construct_portfolio(
            candidates=candidates(9),
            policy=policy(),
            previous={"000001.SZ": Decimal("0.7"), "000002.SZ": Decimal("0.5")},
        )


# --- the limits contract (`V2-P5-002`) -----------------------------------------------------------


def test_every_declared_limit_is_enforced_by_the_simulator_or_by_the_construction_policy() -> None:
    """`V2-P5-002`'s guard: a limit no consumer reads is a fail-open dressed as a feature.

    A covering and not a partition -- three fields are read in both places, once against a plan
    and once against a fill -- so the assertion is on the union, plus the two structural facts
    that make the split what it is.
    """
    declared = set(PortfolioLimits.model_fields)

    covering = LIMITS_ENFORCED_BY_THE_SIMULATOR | LIMITS_ENFORCED_BY_THE_CONSTRUCTION_POLICY

    assert covering == declared
    assert "max_industry_weight" not in LIMITS_ENFORCED_BY_THE_SIMULATOR
    assert "turnover_budget" not in LIMITS_ENFORCED_BY_THE_SIMULATOR


def test_the_cash_floor_and_the_exposure_ceiling_are_one_inequality_and_the_tighter_one_binds() -> (
    None
):
    """Measured rather than argued: `equity == cash + market_value`, so a 30% cash floor and a
    70% exposure ceiling fund exactly the same book.

    This is the falsification of the roadmap row's premise that a cash floor is a third limit.
    The field is still carried, because declaring intent as a floor is legible; what it is not is
    a constraint the ceiling could not already express.
    """
    by_floor = PortfolioConstructionPolicy(
        tier_weights=EVEN_TIERS,
        limits=PortfolioLimits(max_total_exposure=Decimal("1"), min_cash_weight=Decimal("0.30")),
    )
    by_ceiling = PortfolioConstructionPolicy(
        tier_weights=EVEN_TIERS,
        limits=PortfolioLimits(max_total_exposure=Decimal("0.70")),
    )

    assert by_floor.invested_weight == by_ceiling.invested_weight == Decimal("0.70")
    assert weights(construct_portfolio(candidates=candidates(9), policy=by_floor)) == weights(
        construct_portfolio(candidates=candidates(9), policy=by_ceiling)
    )


def test_the_tighter_of_the_two_binds_when_a_caller_declares_both() -> None:
    both = PortfolioConstructionPolicy(
        tier_weights=EVEN_TIERS,
        limits=PortfolioLimits(max_total_exposure=Decimal("0.60"), min_cash_weight=Decimal("0.30")),
    )

    assert both.invested_weight == Decimal("0.60")


# --- the stored-answer adapter -------------------------------------------------------------------


def test_a_shortlist_the_gate_refused_cannot_be_turned_into_a_portfolio() -> None:
    """`admitted` is `null` for a refused list and `[]` for an admitted empty one -- two answers
    `V2-P4-032` separated on purpose, and this reads them as two."""
    with pytest.raises(PortfolioConstructionError, match="refused by the gate"):
        candidates_from_shortlist_answer({"admitted": None})

    with pytest.raises(PortfolioConstructionError, match="holds no names"):
        candidates_from_shortlist_answer({"admitted": []})


def test_the_stored_answer_adapter_reads_the_order_and_carries_no_industry() -> None:
    """The measured shape of `shortlist_view`'s `admitted` rows, which carry no industry at all."""
    read = candidates_from_shortlist_answer(
        {
            "admitted": [
                {"subject": "000002.SZ", "rank": 2, "score": -0.5, "direction": "bearish"},
                {"subject": "000001.SZ", "rank": 1, "score": 1.5, "direction": "bullish"},
            ]
        }
    )

    assert [candidate.rank for candidate in read] == [2, 1]
    assert {candidate.subject for candidate in read} == {"000001.SZ", "000002.SZ"}
    assert all(candidate.industry_code is None for candidate in read)


# --- the registry --------------------------------------------------------------------------------


def test_the_construction_registry_names_exactly_these_seven_boundaries() -> None:
    """Every code as an executable literal, which is what
    `tests/unit/test_known_limitation_registries.py::
    test_every_declared_limitation_code_is_named_in_executable_test_code` requires of a
    registry."""
    declared = {
        "the_policy_is_a_heuristic_and_optimises_nothing",
        "the_tiers_cut_on_rank_and_the_scores_decide_nothing_inside_one",
        "an_industry_cap_is_unenforceable_on_the_shipped_shortlist_face",
        "the_turnover_budget_can_leave_a_cap_breached_and_says_so_instead_of_retrimming",
        "the_cash_floor_is_the_exposure_ceiling_restated_and_not_a_second_constraint",
        "no_capacity_liquidity_or_cost_term_enters_a_weight",
        "the_previous_book_is_declared_by_the_caller_and_never_read_from_a_ledger",
    }
    rendered = construction_view(construct_portfolio(candidates=candidates(9), policy=policy()))

    assert declared == CONSTRUCTION_LIMITATION_CODES
    assert len(KNOWN_CONSTRUCTION_LIMITATIONS) == 7
    assert [limitation["code"] for limitation in rendered["limitations"]] == [
        limitation.code for limitation in KNOWN_CONSTRUCTION_LIMITATIONS
    ]


def test_a_tier_weighted_exactly_zero_is_refused_as_a_shorter_list_said_in_a_hidden_way() -> None:
    """The `<=` in the tier-weight validator, pinned. A sweep found `<=` and `<` identical on
    every fixture here, because nobody had declared a tier weighted exactly zero.

    A zero tier holds names that are ranked, reported and unfunded, which is a shorter candidate
    list stated in a way the report does not show.
    """
    with pytest.raises(ValueError, match="must each be positive"):
        PortfolioConstructionPolicy(tier_weights=(Decimal("0.5"), Decimal("0.5"), Decimal("0")))


def test_a_move_exactly_equal_to_the_budget_is_made_whole_and_is_not_damped() -> None:
    """The `>` in the budget check, pinned by reading the requested move off an unbudgeted run
    and declaring exactly that number as the budget.

    Under `>=` the same move is damped by a factor of one: the weights come back identical and
    only `turnover_damping` changes from `None` to `1.000000`, which is why no assertion about
    the weights could ever separate the two.
    """
    previous = {f"{index:06d}.SZ": Decimal("0.05") for index in range(1, 10)}
    unbudgeted = construct_portfolio(candidates=candidates(9), policy=policy(), previous=previous)
    at_the_budget = construct_portfolio(
        candidates=candidates(9),
        policy=policy(turnover_budget=unbudgeted.turnover),
        previous=previous,
    )

    assert unbudgeted.turnover_damping is None
    assert at_the_budget.turnover_damping is None
    assert at_the_budget.turnover == unbudgeted.turnover
    assert weights(at_the_budget) == weights(unbudgeted)


def test_the_simulator_names_the_three_limits_it_checks_and_no_others() -> None:
    """The covering equality above is satisfied by a simulator set that under-claims, because the
    policy set already names every field -- a sweep dropping `min_cash_weight` from the simulator
    side was green.

    So the simulator's own set is pinned as an equality. It is a small, finite claim about three
    `if` statements in `_buy`, and each of the three has a rejection test naming its reason.
    """
    checked = {"max_position_weight", "max_total_exposure", "min_cash_weight"}

    assert checked == LIMITS_ENFORCED_BY_THE_SIMULATOR


def test_the_method_label_is_the_only_one_the_contract_accepts_and_the_record_is_frozen() -> None:
    """The `Literal` on `method`, closed at run time rather than left to `mypy`.

    A sweep mutating the `Literal` **member** while leaving the default alone survives pytest --
    pydantic does not validate a default -- and is caught only by `uv run mypy src scripts`,
    reproducing `V2-P4-115`'s finding that a sweep whose oracle is pytest alone under-reports
    whenever a second gate ships with the build. Stating the label as an *accepted* value closes
    it here too: under that mutant, passing the real sentence is a `ValidationError`.

    Frozen for the reason every record in this package is: a caveat a caller can assign over is
    not a caveat.
    """
    construction = construct_portfolio(candidates=candidates(9), policy=policy())
    rebuilt = construction.model_copy(update={})

    assert (
        PortfolioConstruction(
            policy=construction.policy,
            method="heuristic, not optimized",
            targets=construction.targets,
            cash_weight=construction.cash_weight,
            unallocated_weight=construction.unallocated_weight,
            turnover=construction.turnover,
            turnover_before_budget=construction.turnover_before_budget,
        ).method
        == "heuristic, not optimized"
    )
    with pytest.raises(ValidationError):
        PortfolioConstruction(
            policy=construction.policy,
            method="approximately optimal",  # type: ignore[arg-type]
            targets=construction.targets,
            cash_weight=construction.cash_weight,
            unallocated_weight=construction.unallocated_weight,
            turnover=construction.turnover,
            turnover_before_budget=construction.turnover_before_budget,
        )
    with pytest.raises(ValidationError):
        rebuilt.method = "approximately optimal"  # type: ignore[misc]
