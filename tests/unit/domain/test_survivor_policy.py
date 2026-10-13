"""The sticky holding policy (`V2-P7-005`).

The policy is exercised on screens written by hand rather than produced by `screen()`, so each
test names exactly the situation it is about: who the survivors are and in what order, who was
dropped, who is ineligible. Which entrant the selection rule draws is the rule's business and is
pinned in `test_survivor_selection.py`; here the expectation is "the first k of the rule's own
order over the entrants", so the two files cannot drift apart silently.
"""

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
    close: dict[int, str] | None = None,
) -> ScreenResult:
    """Survivors best-first (`ranked` lists name positions), then dropped losers and ineligibles."""
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
    verdicts += [EligibilityVerdict(ts_code=name(p), reasons=("suspended",)) for p in ineligible]
    return ScreenResult(
        verdicts=tuple(sorted(verdicts, key=lambda v: v.ts_code)), survivors=survivors
    )


def state(
    *,
    held: tuple[int, ...] = (),
    initialized: bool = True,
    pending: tuple[int, ...] = (),
    cooling: tuple[tuple[int, int], ...] = (),
    equity: str = "40000",
) -> BookState:
    return BookState(
        session=SESSION,
        initialized=initialized,
        equity=Decimal(equity),
        holdings=tuple(Holding(ts_code=name(p), quantity=100) for p in sorted(held)),
        pending_buys=tuple(name(p) for p in pending),
        cooldowns=tuple(Cooldown(ts_code=name(p), since_session=s) for p, s in cooling),
    )


def rule_order(positions: list[int], cfg: SurvivorBookConfig) -> list[str]:
    return list(SeededHashRule().order([name(p) for p in positions], seed=cfg.seed))


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
    prices = {4: "5000"}
    screen = screen_of([0, 1, 2, 4, 5], close=prices)
    decision = decide_book(config=cfg, screen=screen, state=state(held=(0, 1, 2)))

    assert [e.ts_code for e in decision.entries] == [name(5)]
    assert decision.unaffordable == (name(4),)


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
    screen = screen_of([0, 1, 2, 4, 5, 6], dropped=((3, -9.0),))
    forward = state(held=(0, 1, 2, 3))
    backward = forward.model_copy(update={"holdings": tuple(reversed(forward.holdings))})

    assert decide_book(config=config(), screen=screen, state=forward) == decide_book(
        config=config(), screen=screen, state=backward
    )


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
