"""The survivor book over many sessions of a simulated market (`V2-P7-005`, `V2-P7-008`).

A walk through 120 sessions of a made-up market (a seeded random walk of scores and prices, with
suspensions and special treatment) drives the whole core in the order the daily flow will use
it: **settle** the orders planned the session before (a sell on a suspended name does not fill, so
it stays live), then `screen`, `decide_book` and `build_trade_list`. It asserts the properties
the design promises on **every** session, not on the last one: the book never exceeds its size,
never overspends, never churns faster than the cap, never sells what should be kept, never sells
a name twice, leaves a suspended holding alone, and never buys what is cooling. The mutation
checks recorded in the plan are run against these tests.
"""

import hashlib
import json
import os
import random
import subprocess
import sys
from collections import Counter
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

from openalpha_cn.domain.survivor_book import SecurityFacts, SurvivorBookConfig
from openalpha_cn.domain.survivor_lifecycle import advance_orders, update_cooldowns
from openalpha_cn.domain.survivor_orders import TradeOrder, build_trade_list
from openalpha_cn.domain.survivor_policy import BookState, Cooldown, Holding, decide_book
from openalpha_cn.domain.survivor_screen import screen

UNIVERSE = 80
SESSIONS = 120
EXERCISED: Counter[str] = Counter()
"""What the simulated markets actually made the book do, so a test can assert that a branch it
claims to cover was reached rather than assume it was."""


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
            if rng.random() < 0.02:
                halted[position] = 4
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
    live: list[TradeOrder] = []
    cooldowns: tuple[Cooldown, ...] = ()
    initialized = False
    sold_at: dict[str, int] = {}
    log: list[dict[str, object]] = []
    for session, facts in enumerate(walk(market_seed)):
        close = {fact.ts_code: fact.close for fact in facts}
        halted = {fact.ts_code for fact in facts if fact.suspended}
        result = screen(facts, config)
        survivors = {name.ts_code for name in result.survivors}
        eligible = {verdict.ts_code for verdict in result.verdicts if verdict.eligible}

        # 1. Settle what was planned the session before, at its reference price. A halted name
        #    cannot trade, so its order does not fill and stays live; a buy needs the cash.
        filled: set[str] = set()
        sold: set[str] = set()
        for order in sorted(live, key=lambda o: (o.side != "sell", o.ts_code)):
            value = order.reference_price * order.quantity
            if order.ts_code in halted or (order.side == "buy" and value > cash):
                EXERCISED["unfilled"] += 1
                continue
            if order.side == "sell":
                cash += value
                del held[order.ts_code]
                sold.add(order.ts_code)
                sold_at[order.ts_code] = session
            else:
                cash -= value
                held[order.ts_code] = order.quantity
            filled.add(order.order_id)
        settled = advance_orders(
            live, session=session, filled_order_ids=filled, survivors=survivors
        )
        live = list(settled.live)
        EXERCISED["expired"] += len(settled.expired)
        EXERCISED["cancelled"] += len(settled.cancelled)
        for expired in settled.expired:
            sold_at[expired.ts_code] = session
        cooldowns = update_cooldowns(
            cooldowns,
            session=session,
            sold=sold,
            expired_buys={order.ts_code for order in settled.expired},
            config=config,
        )
        assert cash >= 0

        # 2. Decide, from the state after settlement.
        equity = cash + sum(Decimal(q) * close[c] for c, q in held.items())
        pending_buys = tuple(sorted(o.ts_code for o in live if o.side == "buy"))
        pending_sells = tuple(sorted(o.ts_code for o in live if o.side == "sell"))
        state = BookState(
            session=session,
            initialized=initialized,
            equity=equity,
            holdings=tuple(Holding(ts_code=c, quantity=q) for c, q in sorted(held.items())),
            pending_buys=pending_buys,
            pending_sells=pending_sells,
            cooldowns=cooldowns,
        )
        decision = decide_book(config=config, screen=result, state=state)
        plan = build_trade_list(
            config=config,
            state=state,
            decision=decision,
            reference_prices=close,
            cash=cash,
            carried=tuple(live),
        )
        # The plan itself must fit in the cash: what today's buys cost plus what buys already live
        # will cost cannot exceed the cash plus the proceeds of today's sells. (Live buys alone may
        # exceed it -- then nothing new is planned.)
        planned_buys = sum(
            (o.reference_price * o.quantity for o in plan.orders if o.side == "buy"), Decimal(0)
        )
        live_buys = sum(
            (o.reference_price * o.quantity for o in live if o.side == "buy"), Decimal(0)
        )
        planned_sells = sum(
            (o.reference_price * o.quantity for o in plan.orders if o.side == "sell"), Decimal(0)
        )
        assert planned_buys == 0 or planned_buys + live_buys <= cash + planned_sells
        EXERCISED["frozen"] += len(decision.frozen)
        EXERCISED["pending_sells"] += len(pending_sells)

        # 3. The promises, on every session.
        for exited in decision.exits:
            assert exited.ts_code in held
            assert exited.ts_code not in pending_sells, "a name is sold twice"
            if exited.reason == "EXIT_SURVIVOR_LOST":
                assert exited.ts_code in eligible and exited.ts_code not in survivors
            else:
                assert exited.ts_code not in eligible
                assert exited.ts_code not in decision.frozen
        assert {c for c in held if c in survivors}.isdisjoint(e.ts_code for e in decision.exits)
        assert set(decision.frozen) <= halted
        assert set(decision.frozen).isdisjoint(e.ts_code for e in decision.exits)

        discretionary = [e for e in decision.exits if e.reason == "EXIT_SURVIVOR_LOST"]
        assert len(discretionary) <= config.max_replacements_per_session
        if initialized:
            assert len(decision.entries) <= config.max_replacements_per_session
        for entry in decision.entries:
            assert entry.ts_code not in held
            assert entry.ts_code not in pending_buys
            assert entry.ts_code in survivors
            last = sold_at.get(entry.ts_code)
            assert last is None or session - last >= config.cooldown_sessions

        active = [c for c in held if c not in pending_sells]
        assert len(decision.target_holdings) + len(pending_buys) <= config.holding_count
        assert len(active) + len(pending_buys) <= config.holding_count
        assert all(o.quantity % config.lot_size == 0 for o in plan.orders)

        live.extend(plan.orders)
        initialized = True
        log.append(
            {
                "trade_list": plan.trade_list_id,
                "held": sorted(held),
                "live": sorted(o.order_id for o in live),
                "cash": str(cash),
            }
        )
    return log


def config(**overrides: object) -> SurvivorBookConfig:
    return SurvivorBookConfig(seed=3, **overrides)  # type: ignore[arg-type]


def test_the_book_keeps_its_promises_on_every_session_of_several_markets() -> None:
    for market_seed in range(6):
        run(market_seed, config())


def test_the_markets_really_do_reach_the_branches_the_promises_are_about() -> None:
    before = Counter(EXERCISED)
    for market_seed in range(6):
        run(market_seed, config())
    reached = EXERCISED - before

    # A halted holding that is left alone, a sell that stays live because its name is halted, and
    # an order that could not fill: if the markets never got here the assertions above are idle.
    assert reached["frozen"] > 0
    assert reached["pending_sells"] > 0
    assert reached["unfilled"] > 0


def test_the_first_session_builds_the_book_and_most_later_sessions_do_nothing() -> None:
    log = run(0, config())

    assert log[0]["held"] == []  # the first orders fill at the next session's open
    # A name halted on its first day cannot be bought then, so the first fill may be a name short;
    # the book is whole soon after.
    assert len(log[1]["held"]) >= 8  # type: ignore[arg-type]
    assert max(len(record["held"]) for record in log) == 10  # type: ignore[arg-type]
    quiet = sum(1 for before, after in pairwise(log[1:]) if before["held"] == after["held"])
    assert quiet > len(log) // 2


def test_the_whole_run_is_reproducible() -> None:
    assert run(2, config()) == run(2, config())


def test_a_smaller_book_with_a_longer_cooldown_keeps_the_same_promises() -> None:
    run(1, config(holding_count=5, cooldown_sessions=40, max_replacements_per_session=1))


def test_the_whole_chain_gives_the_same_bytes_in_processes_with_different_hash_seeds() -> None:
    """Not only the selection rule: screen, policy, orders and lifecycle together, end to end."""
    here = Path(__file__).resolve()
    program = (
        "import hashlib, importlib.util, json, sys;"
        "spec = importlib.util.spec_from_file_location('sim', sys.argv[1]);"
        "sim = importlib.util.module_from_spec(spec);"
        "spec.loader.exec_module(sim);"
        "log = sim.run(2, sim.config());"
        "print(hashlib.sha256(json.dumps(log, sort_keys=True).encode()).hexdigest())"
    )
    source_root = str(here.parents[3] / "src")
    reference = hashlib.sha256(json.dumps(run(2, config()), sort_keys=True).encode()).hexdigest()

    for hash_seed in ("0", "1", "424242"):
        completed = subprocess.run(
            [sys.executable, "-c", program, str(here)],
            env={
                **os.environ,
                "PYTHONHASHSEED": hash_seed,
                "PYTHONPATH": os.pathsep.join(
                    part for part in (source_root, os.environ.get("PYTHONPATH")) if part
                ),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == reference
