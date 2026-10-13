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
        ({"close": "348.26"}, "unaffordable_lot"),
    ],
)
def test_each_reason_is_reported_on_its_own(overrides: dict[str, object], reason: str) -> None:
    result = screen([fact(code(1), **overrides)], isolated())  # type: ignore[arg-type]

    assert result.verdicts[0].reasons == (reason,)
    assert not result.verdicts[0].eligible
    assert result.survivors == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"board": "growth"},
        {"listed": 250},
        {"close": "348.25"},
    ],
)
def test_the_boundary_value_of_each_reason_is_still_eligible(overrides: dict[str, object]) -> None:
    # 100 shares at 348.25 cost 34,825 -- exactly the sizing budget (350,000 / 10, less the 0.5%
    # reserve), which is affordable; one cent more is not (the case above).
    result = screen([fact(code(1), **overrides)], isolated())  # type: ignore[arg-type]

    assert result.verdicts[0].reasons == ()
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


def test_the_lot_test_follows_the_equity_it_is_given() -> None:
    held = [fact(code(1), close="100")]

    assert screen(held, isolated()).verdicts[0].eligible
    # 100,000 / 10 less the reserve is 9,950, and 100 shares at 100 cost 10,000.
    poorer = screen(held, isolated(), equity=Decimal("100000"))
    assert poorer.verdicts[0].reasons == ("unaffordable_lot",)


def test_the_liquidity_cut_drops_the_thinnest_share_of_the_allowed_boards_only() -> None:
    main = [fact(code(position), amount=str(position)) for position in range(1, 11)]
    # Ten more names on a board the account cannot trade, thinner than every allowed name: if the
    # cut counted them it would be 6 of 20 and reach into the allowed names' middle.
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
    noisy = [
        *clean,
        fact(code(101), special=True, values=(99.0,) * 4),
        fact(code(102), suspended=True, values=(-99.0,) * 4),
    ]
    config = isolated(loser_decile_count=2)

    assert [n.ts_code for n in screen(noisy, config).survivors] == [
        n.ts_code for n in screen(clean, config).survivors
    ]


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
