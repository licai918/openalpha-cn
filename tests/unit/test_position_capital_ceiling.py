"""`V2-P4-058`. The `position_capital` ceiling: its two literals, and the range it holds over.

`V2-P4-045` found that `ShortlistSpec.position_capital` was bounded below and nowhere above, and
that `10**26` was the first budget whose own fill the build could not price -- an
`InvalidOperation` from `quantize`, which is an `ArithmeticError` and so slipped past every
`except TwoStageFunnelError` and arrived on all three faces as a bare `500`. Two things were
left unguarded when that closed, and this file is both of them.

**One number, written twice.** `shortlist_view.POSITION_CAPITAL_CEILING` is `Decimal(10) ** 26`,
and commit `3e83587` applied the reported dependency by giving `ShortlistSpec.position_capital`
its own `lt=Decimal(10) ** 26` in `backtest/cross_section.py`. Both are right today and nothing
made them agree: `grep -rn POSITION_CAPITAL_CEILING tests/` returned **nothing**. They cannot be
de-duplicated in place, because `backtest-no-numeric-stack-or-panel-plane` forbids
`openalpha_cn.shortlist_view` to everything under `backtest/`, so the field literal cannot read
the constant. What is available is to require the two to be equal, which is what this file does
-- and to read the bound off the field's own metadata rather than off a copy of the source, so
the check is about the constraint pydantic enforces and not about the text that spells it.

**A claim wider than its measurement.** `POSITION_CAPITAL_CEILING`'s docstring said the ceiling
is "the same at every close price". Half of that is sound: `position_quantity` floors the lot
count, so `notional <= capital`, and the notional `AShareExecutionPolicy.execute` quantizes is
bounded by the budget alone -- verified below across `0.01` to `10000.00`, the range `V2-P4-045`
measured. The other half was not. The crash site is a **division**,
`int(capital // (market.close * SHARE_LOT))`, whose quotient grows as `close` falls, and
`MarketBar.close` is `Field(gt=0)` with no lower bound -- so `close=1e-12` with `capital=1e20`,
four orders of magnitude below the ceiling and satisfying both bounds, still raises. No
two-decimal price feed produces such a bar, so the shipped surface is fine and nothing here is a
defect report; the sentence was simply wider than what was measured, and `V2-P4-058` narrowed it
to the range it holds over. Both halves are pinned below, including the limit, so that a later
reader who widens the sentence again has to delete a red test to do it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

import pytest
from annotated_types import Gt, Lt

from openalpha_cn.backtest.cross_section import ShortlistSpec
from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.factor_portfolio import position_quantity
from openalpha_cn.shortlist_view import POSITION_CAPITAL_CEILING

MEASURED_PRICE_RANGE = ("0.01", "1.00", "10.00", "100.00", "1000.00", "10000.00")
"""The close prices `V2-P4-045` measured the ceiling over, ends included.

Two decimals throughout, which is what a real A-share daily feed publishes and what
`AShareExecutionPolicy` quantizes to. The range is named here rather than inlined because the
narrowed claim in `POSITION_CAPITAL_CEILING`'s docstring is *about* this range: widening the
sentence means widening this tuple and watching what happens.
"""


def _bar(close: str) -> MarketBar:
    """A flat, unsuspended main-board bar at one close price."""
    price = Decimal(close)
    return MarketBar(
        subject="600000.SH",
        trade_date=date(2026, 8, 14),
        board="main",
        previous_close=price,
        open=price,
        high=price,
        low=price,
        close=price,
        suspended=False,
        is_st=False,
    )


def test_the_two_position_capital_ceilings_are_one_number() -> None:
    """The constant and the field bound, required to be equal off the field's own metadata.

    `V2-P4-058`: both were correct and neither was measured, so the pair could drift silently in
    either direction -- and the direction that matters is a field bound left *above* the
    constant, which would readmit exactly the budgets `V2-P4-045` found unpriceable while the
    three faces still refused them, so the API and a directly-constructed spec would disagree
    about what is legal.

    Read off `model_fields[...].metadata` rather than by re-parsing the source: the question is
    what pydantic enforces, and a source-text check would pass on a literal that pydantic never
    applied.
    """
    field = ShortlistSpec.model_fields["position_capital"]
    upper = [constraint for constraint in field.metadata if isinstance(constraint, Lt)]
    lower = [constraint for constraint in field.metadata if isinstance(constraint, Gt)]

    assert len(upper) == 1, (
        f"ShortlistSpec.position_capital carries {len(upper)} upper bounds, not one: "
        f"{field.metadata}. V2-P4-045's ceiling is a single exclusive bound, and a field with "
        "none is the unbounded budget that arrived on three faces as a bare 500"
    )
    assert upper[0].lt == POSITION_CAPITAL_CEILING, (
        f"ShortlistSpec.position_capital is bounded at {upper[0].lt} and "
        f"shortlist_view.POSITION_CAPITAL_CEILING is {POSITION_CAPITAL_CEILING}. This number is "
        "written twice -- the field cannot import the constant, because "
        "backtest-no-numeric-stack-or-panel-plane forbids openalpha_cn.shortlist_view to "
        "backtest/ -- so the two literals have to be changed together or the faces and a "
        "directly-constructed spec will disagree about which budgets are legal"
    )
    assert lower and lower[0].gt == 0, (
        f"ShortlistSpec.position_capital lost its lower bound: {field.metadata}. A budget of "
        "zero or less reports every security as below_board_minimum and calls it a market fact"
    )


def test_the_capital_ceiling_holds_across_the_price_range_it_was_measured_over() -> None:
    """`notional <= capital` at every measured close, which is what makes the ceiling a ceiling.

    This is the half of `V2-P4-045`'s reasoning that is sound, kept as a measurement rather than
    a sentence. `position_quantity` floors to whole lots off STAR, so the notional handed to
    `AShareExecutionPolicy.execute` -- the value whose `quantize` raised -- never exceeds the
    budget, whatever the price. Drive the largest legal budget at each price and check it.
    """
    capital = POSITION_CAPITAL_CEILING - 1

    for close in MEASURED_PRICE_RANGE:
        quantity = position_quantity(capital=capital, market=_bar(close))
        notional = Decimal(close) * quantity

        assert notional <= capital, (
            f"at close={close} the largest legal budget sized a notional of {notional}, above "
            f"the budget itself. V2-P4-045 read the ceiling off the assumption that flooring "
            "makes notional <= capital; if that stops holding, the ceiling is no longer a fact "
            "about the budget alone and the constant has to be re-derived"
        )


def test_a_close_price_below_the_feeds_own_resolution_still_overflows_under_the_ceiling() -> None:
    """The limit of the narrowed claim, pinned so it cannot be quietly re-widened.

    Not a defect report: `MarketBar.close` is `Field(gt=0)`, and no two-decimal price feed
    produces `1e-12`, so this is unreachable on every shipped surface. It is here because
    `POSITION_CAPITAL_CEILING`'s docstring used to say the ceiling is "the same at every close
    price" full stop, and that is false -- the crash site
    `int(capital // (market.close * SHARE_LOT))` is a division, and a quotient's digit count
    depends on the divisor. Four orders of magnitude below the ceiling is enough.

    `V2-P4-058` chose to narrow the sentence rather than bound `close` or catch the
    `InvalidOperation`, and this test is what keeps that choice honest: it fails the moment
    either of the other two repairs is made, which is the moment somebody should revisit the
    wording rather than leave a now-untrue qualifier in place.
    """
    capital = Decimal(10) ** 20

    assert capital < POSITION_CAPITAL_CEILING, (
        "this test is only interesting below the ceiling; a capital at or above it is refused "
        "by shortlist_request before any arithmetic happens"
    )

    with pytest.raises(InvalidOperation):
        position_quantity(capital=capital, market=_bar("0.000000000001"))
