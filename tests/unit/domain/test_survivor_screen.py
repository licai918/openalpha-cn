"""The eligibility-and-survivor screen (`V2-P7-005`).

The fixtures are small enough to compute by hand. Where a number matters it is derived in the
test's own comment, not copied from a run, so a wrong implementation cannot make its own answer
the expectation.
"""

import random
from decimal import Decimal

import pytest

from openalpha_cn.domain.survivor_book import SecurityFacts, SurvivorBookConfig
from openalpha_cn.domain.survivor_screen import composite_of, screen


def fact(
    code: str,
    *,
    board: str = "main",
    listed: int = 500,
    special: bool = False,
    suspended: bool = False,
    close: str = "10",
    amount: str = "100",
    values: tuple[float | None, ...] = (1.0, 1.0, 1.0, 1.0),
) -> SecurityFacts:
    return SecurityFacts(
        ts_code=code,
        board=board,  # type: ignore[arg-type]
        listed_sessions=listed,
        special_treatment=special,
        suspended=suspended,
        close=Decimal(close),
        avg_amount=Decimal(amount),
        oriented_values=values,
    )


def isolated(**overrides: object) -> SurvivorBookConfig:
    """A configuration with the liquidity cut and the loser tail switched off."""
    settings: dict[str, object] = {
        "seed": 1,
        "liquidity_drop_fraction": Decimal("0"),
        "loser_decile_count": 0,
    }
    settings.update(overrides)
    return SurvivorBookConfig(**settings)  # type: ignore[arg-type]


def code(position: int) -> str:
    return f"{600000 + position:06d}.SH"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"board": "star"}, "board_not_allowed"),
        ({"board": "bse"}, "board_not_allowed"),
        ({"listed": 249}, "listing_too_recent"),
        ({"special": True}, "special_treatment"),
        ({"suspended": True}, "suspended"),
        ({"values": (1.0, None, 1.0, 1.0)}, "missing_factor_value"),
    ],
)
def test_each_reason_is_reported_on_its_own(overrides: dict[str, object], reason: str) -> None:
    result = screen([fact(code(1), **overrides)], isolated())  # type: ignore[arg-type]

    assert result.verdicts[0].reasons == (reason,)
    assert not result.verdicts[0].eligible
    assert result.survivors == ()


def test_the_listing_age_boundary_is_still_eligible() -> None:
    result = screen([fact(code(1), listed=250)], isolated())

    assert result.verdicts[0].reasons == ()
    assert result.verdicts[0].eligible


def test_by_default_only_the_main_board_is_tradable() -> None:
    boards = ("main", "growth", "star", "bse")
    result = screen(
        [fact(code(position), board=board) for position, board in enumerate(boards)], isolated()
    )

    assert [(v.ts_code, v.reasons) for v in result.verdicts] == [
        (code(0), ()),
        (code(1), ("board_not_allowed",)),
        (code(2), ("board_not_allowed",)),
        (code(3), ("board_not_allowed",)),
    ]


def test_a_board_the_configuration_allows_is_eligible() -> None:
    result = screen([fact(code(1), board="growth")], isolated(boards=("main", "growth")))

    assert result.verdicts[0].eligible


def test_every_applicable_reason_is_listed_in_the_fixed_order() -> None:
    result = screen(
        [fact(code(1), board="star", listed=3, special=True, suspended=True)], isolated()
    )

    assert result.verdicts[0].reasons == (
        "board_not_allowed",
        "listing_too_recent",
        "special_treatment",
        "suspended",
    )


def test_a_high_priced_name_is_not_screened_out_for_its_price() -> None:
    # Whether the book can afford a lot is a question about the book's equity, asked when buying;
    # it must not move who is eligible, or a holding that has risen past one lot's price would be
    # sold for it and the survivor set would drift with the book's equity.
    result = screen([fact(code(1), close="5000")], isolated())

    assert result.verdicts[0].eligible
    assert [name.ts_code for name in result.survivors] == [code(1)]


def test_the_liquidity_cut_drops_the_thinnest_share_of_the_allowed_boards_only() -> None:
    main = [fact(code(position), amount=str(position)) for position in range(1, 11)]
    # Ten more names on a board the account cannot trade, thinner than every allowed name: if the
    # cut counted them it would be 6 of 20, all six of them on that board, and no allowed name
    # would be cut at all.
    star = [fact(code(100 + position), board="star", amount="0") for position in range(10)]

    result = screen([*main, *star], isolated(liquidity_drop_fraction=Decimal("0.30")))

    illiquid = {v.ts_code for v in result.verdicts if "illiquid" in v.reasons}
    assert illiquid == {code(1), code(2), code(3)}


def test_equally_liquid_names_are_cut_by_code_so_the_cut_never_depends_on_input_order() -> None:
    names = [fact(code(position), amount="5") for position in range(10)]
    shuffled = list(names)
    random.Random(3).shuffle(shuffled)

    for facts in (names, shuffled):
        result = screen(facts, isolated(liquidity_drop_fraction=Decimal("0.30")))
        assert {v.ts_code for v in result.verdicts if "illiquid" in v.reasons} == {
            code(0),
            code(1),
            code(2),
        }


def test_the_composite_is_the_mean_of_the_oriented_values() -> None:
    assert composite_of((1.0, 2.0, 3.0, 6.0)) == 3.0
    assert composite_of((-1.0, 1.0)) == 0.0


def test_a_missing_value_makes_the_composite_missing_rather_than_a_smaller_mean() -> None:
    assert composite_of((1.0, None, 3.0)) is None
    assert composite_of(()) is None


def test_the_composite_is_summed_exactly() -> None:
    # `sum([0.1] * 10)` is 0.9999999999999999; the exactly rounded sum is 1.0.
    assert composite_of((0.1,) * 10) == 0.1


def survivors_of(composites: list[float], **overrides: object) -> tuple[list[str], list[int]]:
    facts = [fact(code(position), values=(c, c, c, c)) for position, c in enumerate(composites)]
    result = screen(facts, isolated(**overrides))
    return [name.ts_code for name in result.survivors], [name.rank for name in result.survivors]


def test_the_two_worst_deciles_are_dropped_and_the_rest_are_ranked_best_first() -> None:
    # Twenty names scored 1..20; two deciles of twenty is four, so scores 1-4 go and 5-20 stay.
    codes, ranks = survivors_of([float(c) for c in range(1, 21)], loser_decile_count=2)

    assert ranks == list(range(1, 17))
    assert codes == [code(position) for position in range(19, 3, -1)]


def test_the_loser_cut_rounds_down() -> None:
    # Fifteen names, two deciles: 15 * 2 // 10 = 3 dropped, 12 kept. Nine names: 1 dropped.
    assert len(survivors_of([float(c) for c in range(15)], loser_decile_count=2)[0]) == 12
    assert len(survivors_of([float(c) for c in range(9)], loser_decile_count=2)[0]) == 8


def test_tied_composites_are_cut_and_ranked_by_code() -> None:
    codes, ranks = survivors_of([1.0] * 10, loser_decile_count=2)

    # The two lowest codes are the ones dropped; the survivors are listed by ascending code.
    assert codes == [code(position) for position in range(2, 10)]
    assert ranks == list(range(1, 9))


def test_ineligible_names_neither_survive_nor_shift_the_cut() -> None:
    clean = [fact(code(position), values=(float(position),) * 4) for position in range(1, 21)]
    # Five names with a reason, which would move the cut from 4 of 20 to 5 of 25 if they were
    # counted in the population the loser cut is taken over.
    noisy = [
        *clean,
        fact(code(101), special=True, values=(99.0,) * 4),
        fact(code(102), suspended=True, values=(-99.0,) * 4),
        fact(code(103), special=True, values=(50.0,) * 4),
        fact(code(104), suspended=True, values=(-50.0,) * 4),
        fact(code(105), listed=1, values=(0.5,) * 4),
    ]
    config = isolated(loser_decile_count=2)

    survivors = [n.ts_code for n in screen(noisy, config).survivors]

    assert survivors == [n.ts_code for n in screen(clean, config).survivors]
    assert len(survivors) == 16


def test_a_survivor_carries_its_composite_and_its_close() -> None:
    result = screen([fact(code(1), close="12.34", values=(2.0, 4.0, 6.0, 8.0))], isolated())

    assert result.survivors[0].composite == 5.0
    assert result.survivors[0].close == Decimal("12.34")


def test_the_result_does_not_depend_on_the_order_the_facts_arrive_in() -> None:
    facts = [
        fact(code(position), amount=str(position % 7), values=(float(position % 11),) * 4)
        for position in range(60)
    ]
    reference = screen(facts, SurvivorBookConfig(seed=1))
    for shuffle_seed in range(5):
        shuffled = list(facts)
        random.Random(shuffle_seed).shuffle(shuffled)

        assert screen(shuffled, SurvivorBookConfig(seed=1)) == reference


def test_verdicts_are_listed_by_code() -> None:
    result = screen([fact(code(3)), fact(code(1)), fact(code(2))], isolated())

    assert [v.ts_code for v in result.verdicts] == [code(1), code(2), code(3)]


def test_a_security_listed_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="more than once"):
        screen([fact(code(1)), fact(code(1))], isolated())


def test_a_fact_with_the_wrong_number_of_factor_values_is_refused() -> None:
    with pytest.raises(ValueError, match="oriented values for 4 composite factors"):
        screen([fact(code(1), values=(1.0, 1.0))], isolated())


def test_an_empty_universe_screens_to_nothing() -> None:
    result = screen([], isolated())

    assert result.verdicts == ()
    assert result.survivors == ()


def test_the_liquidity_cut_comes_first_and_the_loser_cut_is_taken_over_what_is_left() -> None:
    # Twelve main-board names, thinnest first: n1..n3 are the 30% (floor of 3.6) that are
    # illiquid, whatever their scores. n1 has the best score of all and n2 the worst, so a screen
    # that cut losers first, or let an illiquid name rank, would answer differently. The nine left
    # (n4..n12) lose floor(9 * 2 / 10) = 1 name, the lowest score, n12; n4..n11 survive, best first.
    scores = {1: 100.0, 2: -100.0, 3: 5.0, 4: 4.0, 5: 3.0, 6: 2.0, 7: 1.0, 8: 0.0}
    scores.update({9: -1.0, 10: -2.0, 11: -3.0, 12: -4.0})
    names = [fact(code(n), amount=str(n), values=(scores[n],) * 4) for n in range(1, 13)]

    result = screen(names, isolated(liquidity_drop_fraction=Decimal("0.30"), loser_decile_count=2))

    illiquid = [v.ts_code for v in result.verdicts if v.reasons == ("illiquid",)]
    assert illiquid == [code(1), code(2), code(3)]
    assert [n.ts_code for n in result.survivors] == [code(n) for n in range(4, 12)]
    assert code(12) not in [n.ts_code for n in result.survivors]
    assert next(v for v in result.verdicts if v.ts_code == code(12)).eligible


def test_a_name_that_fails_a_static_reason_is_not_in_the_liquidity_population() -> None:
    """Pinned: the 30% is cut over the names that could be held, and over nothing else.

    Ten main-board names, thinnest first, plus a halted name with no turnover. If the halted name
    counted, eleven names would lose floor(11 * 0.3) = 3 and the halted one would be the thinnest;
    as it does not, ten names lose floor(10 * 0.3) = 3 and they are the three thinnest of the ten.
    """
    names = [fact(code(n), amount=str(n)) for n in range(1, 11)]
    names.append(fact(code(50), amount="0", suspended=True))

    result = screen(names, isolated(liquidity_drop_fraction=Decimal("0.30")))

    illiquid = {v.ts_code for v in result.verdicts if "illiquid" in v.reasons}
    assert illiquid == {code(1), code(2), code(3)}
    assert next(v for v in result.verdicts if v.ts_code == code(50)).reasons == ("suspended",)


def test_the_liquidity_boundary_does_not_move_when_other_names_halt_or_are_special_treated() -> (
    None
):
    base = [fact(code(n), amount=str(n)) for n in range(1, 11)]
    crowded = [
        *base,
        fact(code(50), amount="0", suspended=True),
        fact(code(51), amount="0.5", special=True),
        fact(code(52), amount="1000", listed=3),
        fact(code(53), amount="2", board="growth"),
    ]
    config = isolated(liquidity_drop_fraction=Decimal("0.30"))

    thin = lambda facts: {  # noqa: E731
        v.ts_code for v in screen(facts, config).verdicts if "illiquid" in v.reasons
    }

    assert thin(crowded) == thin(base) == {code(1), code(2), code(3)}


def test_the_securities_listed_twice_are_named_in_the_refusal() -> None:
    with pytest.raises(ValueError, match=r"more than once.*600001\.SH"):
        screen([fact(code(1)), fact(code(2)), fact(code(1))], isolated())
