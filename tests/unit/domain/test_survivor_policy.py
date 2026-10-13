"""The sticky holding policy (`V2-P7-005`).

The policy is exercised on screens written by hand rather than produced by `screen()`, so each
test names exactly the situation it is about: who the survivors are and in what order, who was
dropped, who is ineligible. Which entrant the selection rule draws is the rule's business and is
pinned in `test_survivor_selection.py`; here the expectation is "the first k of the rule's own
order over the entrants", so the two files cannot drift apart silently.
"""

import random
from decimal import Decimal

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.survivor_book import (
    EligibilityVerdict,
    RankedName,
    ScreenResult,
    SurvivorBookConfig,
)
from openalpha_cn.domain.survivor_policy import BookState, Cooldown, Holding, decide_book
from openalpha_cn.domain.survivor_selection import SeededHashRule

SESSION = 100


def config(**overrides: object) -> SurvivorBookConfig:
    settings: dict[str, object] = {
        "seed": 1,
        "holding_count": 4,
        "capital": Decimal("40000"),
        "entry_floor_fraction": Decimal("0"),
        "near_exit_fraction": Decimal("0.25"),
        "cooldown_sessions": 5,
        "max_replacements_per_session": 2,
    }
    settings.update(overrides)
    return SurvivorBookConfig(**settings)  # type: ignore[arg-type]


def name(position: int) -> str:
    return f"{600000 + position:06d}.SH"


def screen_of(
    ranked: list[int],
    *,
    dropped: tuple[tuple[int, float], ...] = (),
    ineligible: tuple[int, ...] = (),
    halted: tuple[int, ...] = (),
    halted_and_thin: tuple[int, ...] = (),
    sick_and_halted: tuple[int, ...] = (),
    close: dict[int, str] | None = None,
) -> ScreenResult:
    """Survivors best-first (`ranked` lists name positions), then dropped losers and the rest.

    `ineligible` names carry special treatment; `halted` names are suspended and nothing else;
    `sick_and_halted` names are both.
    """
    prices = close or {}
    total = len(ranked)
    survivors = tuple(
        RankedName(
            ts_code=name(position),
            composite=float(total - place),
            rank=place + 1,
            close=Decimal(prices.get(position, "10")),
        )
        for place, position in enumerate(ranked)
    )
    verdicts = [
        EligibilityVerdict(ts_code=survivor.ts_code, composite=survivor.composite)
        for survivor in survivors
    ]
    verdicts += [EligibilityVerdict(ts_code=name(p), composite=c) for p, c in dropped]
    verdicts += [
        EligibilityVerdict(ts_code=name(p), reasons=("special_treatment",)) for p in ineligible
    ]
    verdicts += [EligibilityVerdict(ts_code=name(p), reasons=("suspended",)) for p in halted]
    verdicts += [
        EligibilityVerdict(
            ts_code=name(p), reasons=("suspended", "missing_factor_value", "illiquid")
        )
        for p in halted_and_thin
    ]
    verdicts += [
        EligibilityVerdict(ts_code=name(p), reasons=("special_treatment", "suspended"))
        for p in sick_and_halted
    ]
    return ScreenResult(
        verdicts=tuple(sorted(verdicts, key=lambda v: v.ts_code)), survivors=survivors
    )


def state(
    *,
    held: tuple[int, ...] = (),
    initialized: bool = True,
    pending: tuple[int, ...] = (),
    selling: tuple[int, ...] = (),
    cooling: tuple[tuple[int, int], ...] = (),
    equity: str = "40000",
) -> BookState:
    return BookState(
        session=SESSION,
        initialized=initialized,
        equity=Decimal(equity),
        holdings=tuple(Holding(ts_code=name(p), quantity=100) for p in sorted(held)),
        pending_buys=tuple(name(p) for p in pending),
        pending_sells=tuple(name(p) for p in selling),
        cooldowns=tuple(Cooldown(ts_code=name(p), since_session=s) for p, s in cooling),
    )


def rule_order(positions: list[int], cfg: SurvivorBookConfig) -> list[str]:
    return list(
        SeededHashRule().order([name(p) for p in positions], seed=cfg.seed, session=SESSION)
    )


def test_the_first_build_buys_a_full_book_at_once_in_the_rules_order() -> None:
    cfg = config()
    decision = decide_book(
        config=cfg,
        screen=screen_of(list(range(8))),
        state=state(initialized=False),
    )

    expected = rule_order(list(range(8)), cfg)
    assert [e.ts_code for e in decision.entries] == expected[:4]
    assert {e.reason for e in decision.entries} == {"INITIAL_BUILD"}
    assert decision.exits == ()
    assert decision.watch_list == tuple(expected[4:8])
    assert decision.target_holdings == tuple(sorted(expected[:4]))


def test_a_book_whose_holdings_are_all_survivors_does_nothing() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of(list(range(8))),
        state=state(held=(0, 1, 2, 3)),
    )

    assert decision.exits == ()
    assert decision.entries == ()
    assert decision.deferred_exits == ()
    assert decision.target_holdings == tuple(name(p) for p in range(4))


def test_a_holding_that_drops_out_of_the_survivors_is_sold_and_replaced() -> None:
    cfg = config()
    decision = decide_book(
        config=cfg,
        screen=screen_of([0, 1, 2, 4, 5, 6], dropped=((3, -9.0),)),
        state=state(held=(0, 1, 2, 3)),
    )

    assert [(e.ts_code, e.reason) for e in decision.exits] == [(name(3), "EXIT_SURVIVOR_LOST")]
    entrants = rule_order([4, 5, 6], cfg)
    assert [(e.ts_code, e.reason) for e in decision.entries] == [(entrants[0], "ENTRY_REPLACEMENT")]
    assert name(3) not in decision.target_holdings
    assert entrants[0] in decision.target_holdings


def test_a_holding_that_is_no_longer_eligible_is_sold_even_with_the_best_composite() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([0, 1, 2, 4], ineligible=(3,)),
        state=state(held=(0, 1, 2, 3)),
    )

    assert [(e.ts_code, e.reason) for e in decision.exits] == [(name(3), "EXIT_ELIGIBILITY_LOST")]


def test_a_holding_the_screen_never_assessed_is_sold_as_ineligible() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([0, 1, 2, 4]),
        state=state(held=(0, 1, 2, 9)),
    )

    assert [(e.ts_code, e.reason) for e in decision.exits] == [(name(9), "EXIT_ELIGIBILITY_LOST")]


def test_forced_exits_are_never_deferred_but_the_refill_is_still_rate_limited() -> None:
    cfg = config()
    decision = decide_book(
        config=cfg,
        screen=screen_of([3, 4, 5, 6, 7], ineligible=(0, 1, 2)),
        state=state(held=(0, 1, 2, 3)),
    )

    assert [e.ts_code for e in decision.exits] == [name(0), name(1), name(2)]
    assert {e.reason for e in decision.exits} == {"EXIT_ELIGIBILITY_LOST"}
    # Three vacancies, but at most two replacements a session: the third waits for tomorrow.
    assert len(decision.entries) == 2
    assert decision.target_holdings == tuple(
        sorted([name(3), *[e.ts_code for e in decision.entries]])
    )


def test_discretionary_exits_are_capped_and_the_worst_composite_goes_first() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([3, 4, 5, 6], dropped=((0, -1.0), (1, -3.0), (2, -2.0))),
        state=state(held=(0, 1, 2, 3)),
    )

    # Three holdings fell out of the survivors; two may go today, worst first (-3.0, then -2.0).
    assert [e.ts_code for e in decision.exits] == [name(1), name(2)]
    assert decision.deferred_exits == (name(0),)
    assert name(0) in decision.target_holdings


def test_a_name_still_cooling_down_is_not_bought_but_one_whose_cooldown_ran_out_is() -> None:
    survivors = screen_of([0, 1, 2, 3])
    held = state(held=(0, 1, 2), cooling=((3, SESSION - 4),))
    assert decide_book(config=config(), screen=survivors, state=held).entries == ()

    expired = state(held=(0, 1, 2), cooling=((3, SESSION - 5),))
    decision = decide_book(config=config(), screen=survivors, state=expired)
    assert [e.ts_code for e in decision.entries] == [name(3)]


def position_of(ts_code: str) -> int:
    return int(ts_code[:6]) - 600000


def test_a_live_buy_order_fills_a_slot_and_its_name_is_not_drawn_again() -> None:
    cfg = config()
    everyone = screen_of(list(range(8)))
    decision = decide_book(config=cfg, screen=everyone, state=state(held=(0, 1), pending=(2, 3)))

    assert decision.entries == ()

    # The name the rule would draw first is the one already on order: it must not be drawn twice.
    first = position_of(rule_order(list(range(8)), cfg)[0])
    others = tuple(p for p in range(8) if p != first)[:2]
    decision = decide_book(config=cfg, screen=everyone, state=state(held=others, pending=(first,)))

    drawn = [e.ts_code for e in decision.entries]
    assert len(drawn) == 1
    assert drawn[0] not in {name(first), *[name(p) for p in others]}


def test_an_entrant_the_current_equity_cannot_buy_a_lot_of_is_skipped_and_reported() -> None:
    cfg = config()
    # Equity 40,000 over four names, less the reserve, is 9,950 a name: a 5,000 share price buys
    # none; a 10 share price buys 900.
    decision = decide_book(
        config=cfg,
        screen=screen_of([0, 1, 2, 3], close={3: "5000"}),
        state=state(held=(0, 1, 2)),
    )

    assert decision.entries == ()
    assert decision.unaffordable == (name(3),)
    assert decision.watch_list == ()


def test_an_unaffordable_name_is_skipped_in_favour_of_the_next_draw() -> None:
    cfg = config()
    # The name the rule would draw FIRST is the one the book cannot afford a lot of: the slot has
    # to be filled by the second draw, and the third is the next one on the watch list.
    first, second, third = (position_of(code) for code in rule_order([4, 5, 6], cfg))
    screen = screen_of([0, 1, 2, 4, 5, 6], close={first: "5000"})

    decision = decide_book(config=cfg, screen=screen, state=state(held=(0, 1, 2)))

    assert [e.ts_code for e in decision.entries] == [name(second)]
    assert decision.unaffordable == (name(first),)
    assert decision.watch_list == (name(third),)


def test_the_entry_floor_keeps_the_worst_survivors_out_of_the_draw() -> None:
    # Eight survivors and a floor of a quarter: only the best six are entrants.
    for seed in range(25):
        cfg = config(seed=seed, entry_floor_fraction=Decimal("0.25"))
        decision = decide_book(
            config=cfg, screen=screen_of(list(range(8))), state=state(initialized=False)
        )

        assert {e.ts_code for e in decision.entries} <= {name(p) for p in range(6)}


def test_a_book_with_too_few_entrants_is_built_part_way() -> None:
    decision = decide_book(
        config=config(), screen=screen_of([0, 1]), state=state(initialized=False)
    )

    assert len(decision.entries) == 2
    assert decision.watch_list == ()


def test_holdings_in_the_worst_share_of_the_survivors_are_reported_as_near_exit() -> None:
    # Eight survivors and a quarter: the worst two (ranks 7 and 8) are the near-exit band; the
    # holding at rank 6 is the last one outside it.
    decision = decide_book(
        config=config(),
        screen=screen_of(list(range(8))),
        state=state(held=(0, 5, 6, 7)),
    )

    assert decision.near_exit == (name(6), name(7))


def test_a_name_leaving_today_is_not_also_reported_as_near_exit() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([0, 1, 2, 3, 4, 5, 6, 7], ineligible=(8,)),
        state=state(held=(0, 1, 7, 8)),
    )

    assert name(8) not in decision.near_exit
    assert decision.near_exit == (name(7),)


def test_the_decision_is_the_same_however_the_holdings_are_listed() -> None:
    # Three forced exits and a multi-name near-exit band, so that every ordered output has more
    # than one element and a decision that followed the order of the holdings would show it.
    cfg = config(holding_count=6, capital=Decimal("60000"))
    screen = screen_of([3, 4, 5, 6, 7, 8, 9, 10], ineligible=(0, 1, 2), dropped=((11, -9.0),))
    forward = state(held=(0, 1, 2, 3, 9, 10), equity="60000")
    reference = decide_book(config=cfg, screen=screen, state=forward)

    assert [e.ts_code for e in reference.exits] == [name(0), name(1), name(2)]
    assert reference.near_exit == (name(9), name(10))
    for shuffle_seed in range(6):
        listed = list(forward.holdings)
        random.Random(shuffle_seed).shuffle(listed)
        shuffled = forward.model_copy(update={"holdings": tuple(listed)})

        assert decide_book(config=cfg, screen=screen, state=shuffled) == reference


def test_a_book_holding_more_than_it_may_is_refused() -> None:
    with pytest.raises(ValueError, match="exceed holding_count 4"):
        decide_book(
            config=config(),
            screen=screen_of(list(range(8))),
            state=state(held=(0, 1, 2), pending=(3, 4)),
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"holdings": (Holding(ts_code="a", quantity=100), Holding(ts_code="a", quantity=100))},
        {"pending_buys": ("a", "a")},
        {"holdings": (Holding(ts_code="a", quantity=100),), "pending_buys": ("a",)},
    ],
)
def test_a_book_state_that_contradicts_itself_is_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BookState(session=1, initialized=True, equity=Decimal("1"), **bad)  # type: ignore[arg-type]


def test_an_empty_screen_sells_everything_it_cannot_see_and_buys_nothing() -> None:
    empty = ScreenResult(verdicts=(), survivors=())

    decision = decide_book(config=config(), screen=empty, state=state(held=(0, 1)))

    assert [(e.ts_code, e.reason) for e in decision.exits] == [
        (name(0), "EXIT_ELIGIBILITY_LOST"),
        (name(1), "EXIT_ELIGIBILITY_LOST"),
    ]
    assert decision.entries == ()
    assert decision.target_holdings == ()


def test_an_empty_screen_on_the_first_day_builds_nothing() -> None:
    empty = ScreenResult(verdicts=(), survivors=())

    decision = decide_book(config=config(), screen=empty, state=state(initialized=False))

    assert decision.entries == ()
    assert decision.exits == ()


def test_a_suspended_holding_is_left_alone_until_it_trades_again() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([0, 1, 2], halted=(3,)),
        state=state(held=(0, 1, 2, 3)),
    )

    assert decision.exits == ()
    assert decision.frozen == (name(3),)
    assert decision.entries == ()
    assert name(3) in decision.target_holdings
    assert name(3) not in decision.near_exit


def test_a_suspended_holding_with_another_problem_is_still_sold() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([0, 1, 2, 4], sick_and_halted=(3,)),
        state=state(held=(0, 1, 2, 3)),
    )

    assert [(e.ts_code, e.reason) for e in decision.exits] == [(name(3), "EXIT_ELIGIBILITY_LOST")]
    assert decision.frozen == ()


def test_a_holding_whose_sell_is_still_live_is_not_sold_again_and_frees_its_slot() -> None:
    cfg = config()
    screen = screen_of([0, 1, 2, 4, 5], ineligible=(3,))

    # The same screen without the pending sell would plan a second sell of name 3.
    assert [
        e.ts_code
        for e in decide_book(config=cfg, screen=screen, state=state(held=(0, 1, 2, 3))).exits
    ] == [name(3)]

    # Five names are held, one of them already being sold: four are active, so the book is full.
    full = decide_book(config=cfg, screen=screen, state=state(held=(0, 1, 2, 3, 4), selling=(3,)))
    assert full.exits == ()
    assert full.entries == ()
    assert full.target_holdings == (name(0), name(1), name(2), name(4))

    # Its slot is free: name 3 is on the way out, so one of the four slots is open.
    open_slot = decide_book(config=cfg, screen=screen, state=state(held=(0, 1, 2, 3), selling=(3,)))
    assert open_slot.exits == ()
    assert len(open_slot.entries) == 1
    assert name(3) not in {e.ts_code for e in open_slot.entries}


def test_a_name_being_sold_is_never_bought_back_even_if_it_is_a_top_survivor() -> None:
    cfg = config()
    screen = screen_of([0, 1, 2, 3])

    decision = decide_book(config=cfg, screen=screen, state=state(held=(0, 1, 2, 3), selling=(3,)))

    assert decision.entries == ()
    assert name(3) not in decision.target_holdings


@pytest.mark.parametrize(
    "bad",
    [
        {"pending_sells": ("a",)},  # not held
        {"holdings": (Holding(ts_code="a", quantity=100),), "pending_sells": ("a", "a")},
    ],
)
def test_a_pending_sell_the_book_cannot_have_is_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BookState(session=1, initialized=True, equity=Decimal("1"), **bad)  # type: ignore[arg-type]


def test_forced_exits_do_not_use_up_the_cap_on_discretionary_ones() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([3, 4, 5, 6], ineligible=(0,), dropped=((1, -1.0), (2, -2.0))),
        state=state(held=(0, 1, 2, 3)),
    )

    assert [(e.ts_code, e.reason) for e in decision.exits] == [
        (name(0), "EXIT_ELIGIBILITY_LOST"),
        (name(2), "EXIT_SURVIVOR_LOST"),
        (name(1), "EXIT_SURVIVOR_LOST"),
    ]
    assert decision.deferred_exits == ()


def test_equal_composites_are_sold_in_code_order_and_the_rest_wait() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([0, 4, 5, 6], dropped=((1, -1.0), (2, -1.0), (3, -1.0))),
        state=state(held=(1, 2, 3, 0)),
    )

    assert [e.ts_code for e in decision.exits] == [name(1), name(2)]
    assert decision.deferred_exits == (name(3),)


def test_the_watch_list_is_no_longer_than_the_book() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of(list(range(12))),
        state=state(initialized=False),
    )

    assert len(decision.entries) == 4
    assert len(decision.watch_list) == 4


def test_a_long_suspension_does_not_unfreeze_a_holding_by_dragging_its_own_numbers_down() -> None:
    # A name halted for weeks has no turnover (`illiquid`) and too few observations for its rolling
    # factors (`missing_factor_value`): both follow from the halt, so it stays frozen.
    decision = decide_book(
        config=config(),
        screen=screen_of([0, 1, 2], halted_and_thin=(3,)),
        state=state(held=(0, 1, 2, 3)),
    )

    assert decision.exits == ()
    assert decision.frozen == (name(3),)


def test_a_holding_that_is_halted_and_has_a_real_problem_is_not_frozen() -> None:
    decision = decide_book(
        config=config(),
        screen=screen_of([0, 1, 2], sick_and_halted=(3,)),
        state=state(held=(0, 1, 2, 3)),
    )

    assert [e.ts_code for e in decision.exits] == [name(3)]
    assert decision.frozen == ()
