"""The survivor book over many sessions, with every order assumed to fill (`V2-P7-005`).

A walk through 120 sessions of a made-up market (a seeded random walk of scores and prices, with
the occasional suspension and special treatment) that drives `screen` -> `decide_book` ->
`build_trade_list` and books each plan's orders at its reference prices. It asserts the
properties the design promises on **every** session, not on the last one: the book never exceeds
its size, never overspends, never churns faster than the cap, never sells what should be kept and
never buys what is cooling. The mutation checks in the plan are run against these tests.
"""

import random
from decimal import Decimal
from itertools import pairwise

from openalpha_cn.domain.survivor_book import SecurityFacts, SurvivorBookConfig
from openalpha_cn.domain.survivor_lifecycle import update_cooldowns
from openalpha_cn.domain.survivor_orders import build_trade_list
from openalpha_cn.domain.survivor_policy import BookState, Cooldown, Holding, decide_book
from openalpha_cn.domain.survivor_screen import screen

UNIVERSE = 80
SESSIONS = 120


def code(position: int) -> str:
    return f"{600000 + position:06d}.SH"


def walk(market_seed: int) -> list[list[SecurityFacts]]:
    """`SESSIONS` cross sections whose scores and prices drift and whose problems come and go."""
    rng = random.Random(market_seed)
    scores = [rng.gauss(0, 1) for _ in range(UNIVERSE)]
    prices = [rng.uniform(5, 40) for _ in range(UNIVERSE)]
    amounts = [rng.uniform(1, 100) for _ in range(UNIVERSE)]
    halted: dict[int, int] = {}
    days: list[list[SecurityFacts]] = []
    for _ in range(SESSIONS):
        for position in range(UNIVERSE):
            scores[position] = 0.9 * scores[position] + rng.gauss(0, 0.5)
            prices[position] = max(2.0, prices[position] * (1 + rng.gauss(0, 0.02)))
            if rng.random() < 0.01:
                halted[position] = 3
        facts = []
        for position in range(UNIVERSE):
            suspended = halted.get(position, 0) > 0
            if suspended:
                halted[position] -= 1
            facts.append(
                SecurityFacts(
                    ts_code=code(position),
                    board="growth" if position % 5 == 0 else "main",
                    listed_sessions=400,
                    special_treatment=position % 37 == 0,
                    suspended=suspended,
                    close=Decimal(f"{prices[position]:.2f}"),
                    avg_amount=Decimal(f"{amounts[position]:.2f}"),
                    oriented_values=(scores[position],) * 4,
                )
            )
        days.append(facts)
    return days


def run(market_seed: int, config: SurvivorBookConfig) -> list[dict[str, object]]:
    """Play the market through the book; return one record per session, asserting as it goes."""
    cash = config.capital
    held: dict[str, int] = {}
    cooldowns: tuple[Cooldown, ...] = ()
    initialized = False
    last_sold: dict[str, int] = {}
    log: list[dict[str, object]] = []
    for session, facts in enumerate(walk(market_seed)):
        close = {fact.ts_code: fact.close for fact in facts}
        equity = cash + sum(Decimal(q) * close[c] for c, q in held.items())
        result = screen(facts, config)
        survivors = {n.ts_code for n in result.survivors}
        eligible = {v.ts_code for v in result.verdicts if v.eligible}
        state = BookState(
            session=session,
            initialized=initialized,
            equity=equity,
            holdings=tuple(Holding(ts_code=c, quantity=q) for c, q in sorted(held.items())),
            cooldowns=cooldowns,
        )
        decision = decide_book(config=config, screen=result, state=state)
        plan = build_trade_list(
            config=config,
            state=state,
            decision=decision,
            reference_prices=close,
            cash=cash,
        )

        # Stickiness: a holding that is eligible and a survivor is never sold.
        for exited in decision.exits:
            assert exited.ts_code in held
            if exited.reason == "EXIT_SURVIVOR_LOST":
                assert exited.ts_code in eligible and exited.ts_code not in survivors
            else:
                assert exited.ts_code not in eligible
        kept = {c for c in held if c in survivors}
        assert kept.isdisjoint(e.ts_code for e in decision.exits)

        # Rate limits and cool-down.
        discretionary = [e for e in decision.exits if e.reason == "EXIT_SURVIVOR_LOST"]
        assert len(discretionary) <= config.max_replacements_per_session
        if initialized:
            assert len(decision.entries) <= config.max_replacements_per_session
        for entry in decision.entries:
            assert entry.ts_code not in held
            assert entry.ts_code in survivors
            sold_at = last_sold.get(entry.ts_code)
            assert sold_at is None or session - sold_at >= config.cooldown_sessions

        # Book the plan at its reference prices (every order fills).
        for order in plan.orders:
            value = order.reference_price * order.quantity
            if order.side == "sell":
                cash += value
                del held[order.ts_code]
                last_sold[order.ts_code] = session
            else:
                cash -= value
                held[order.ts_code] = order.quantity
        sold = {o.ts_code for o in plan.orders if o.side == "sell"}
        cooldowns = update_cooldowns(
            cooldowns, session=session, sold=sold, expired_buys=(), config=config
        )
        initialized = True

        assert len(held) <= config.holding_count
        assert cash >= 0
        assert all(q % config.lot_size == 0 and q > 0 for q in held.values())
        log.append({"trade_list": plan.trade_list_id, "held": sorted(held), "cash": str(cash)})
    return log


def config(**overrides: object) -> SurvivorBookConfig:
    return SurvivorBookConfig(seed=3, **overrides)  # type: ignore[arg-type]


def test_the_book_keeps_its_promises_on_every_session_of_several_markets() -> None:
    for market_seed in range(6):
        run(market_seed, config())


def test_the_first_session_builds_the_book_and_most_later_sessions_do_nothing() -> None:
    log = run(0, config())

    assert len(log[0]["held"]) == 10  # type: ignore[arg-type]
    quiet = sum(1 for before, after in pairwise(log) if before["held"] == after["held"])
    assert quiet > len(log) // 2


def test_the_whole_run_is_reproducible() -> None:
    assert run(2, config()) == run(2, config())


def test_a_smaller_book_with_a_longer_cooldown_keeps_the_same_promises() -> None:
    run(1, config(holding_count=5, cooldown_sessions=40, max_replacements_per_session=1))
