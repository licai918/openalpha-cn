"""`AShareExecutionPolicy` against the exchange's own band (`V2-P1-008`).

Every `(previous_close, up_limit, down_limit)` triple here is a real row of
`stk_limit(trade_date=20240628)` joined to `daily(trade_date=20240628)`, recorded on
2026-08-09. The bars around them are constructed, because the point of each test is a verdict
that flips, and a flip needs a bar positioned relative to *both* bands.

`tests/unit/backtest/test_execution.py` pins the derived-band behaviour and is untouched: every
bar it builds omits the two new fields and therefore takes the same path it always did. What
this file adds is the other path, and the three cases where the two disagree on a verdict rather
than only on a number.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    ExecutionRequest,
    MarketBar,
    published_limit_fields,
)
from openalpha_cn.domain.price_limits import PriceLimit

SESSION = date(2024, 6, 28)


def _bar(**updates: object) -> MarketBar:
    values: dict[str, object] = {
        "subject": "000001.SZ",
        "trade_date": SESSION,
        "board": "main",
        "previous_close": Decimal("10.00"),
        "open": Decimal("10.20"),
        "high": Decimal("10.80"),
        "low": Decimal("10.10"),
        "close": Decimal("10.50"),
        "suspended": False,
        "is_st": False,
    }
    values.update(updates)
    return MarketBar.model_validate(values)


def _buy(bar: MarketBar) -> str:
    return AShareExecutionPolicy().execute(ExecutionRequest(side="buy", quantity=100), bar).status


def _sell(bar: MarketBar) -> str:
    return AShareExecutionPolicy().execute(ExecutionRequest(side="sell", quantity=100), bar).status


# --- the default path is byte-for-byte what it was ------------------------------------------


def test_a_bar_without_published_limits_takes_the_derived_path() -> None:
    """The whole safety property of this issue's change to the policy, stated directly: with no
    band supplied, the verdict is the one the board-plus-`is_st` rule gives. `10.00 * 1.10`
    quantized half-up is `11.00`, so a bar locked at `11.00` is a one-price limit-up bar."""
    locked = _bar(
        previous_close=Decimal("10.00"),
        open=Decimal("11.00"),
        high=Decimal("11.00"),
        low=Decimal("11.00"),
        close=Decimal("11.00"),
    )

    assert locked.has_published_limits is False
    assert _buy(locked) == "rejected"
    assert _buy(_bar()) == "filled"


# --- where the two bands give different verdicts --------------------------------------------


def test_the_beijing_boards_inward_rounding_flips_a_locked_limit_up_bar() -> None:
    """`920924.BJ` on 2024-06-28: `pre_close` 7.32, published `up_limit` 9.51. The nominal 30%
    gives `7.32 * 1.30 = 9.516`, which the policy's `ROUND_HALF_UP` turns into 9.52 -- one fen
    above the price the security could not trade past. A session locked at the real limit is
    therefore *not* a limit-up bar as far as the derived rule is concerned, and the buy fills at
    a price no buyer could have got. All 249 `.BJ` names that session round inward and 131 of
    them land on a different fen from half-up."""
    locked = {
        "subject": "920924.BJ",
        "board": "bse",
        "previous_close": Decimal("7.32"),
        "open": Decimal("9.51"),
        "high": Decimal("9.51"),
        "low": Decimal("9.51"),
        "close": Decimal("9.51"),
    }

    assert _buy(_bar(**locked)) == "filled"
    assert _buy(_bar(**locked, up_limit=Decimal("9.51"), down_limit=Decimal("5.13"))) == "rejected"


def test_an_st_chinext_name_is_not_a_five_percent_name() -> None:
    """`300029.SZ` (`ST天龙`) on 2024-06-28: `pre_close` 1.92, published band 1.54 / 2.30 -- the
    board's 20%, not ST's 5%. `_rejection_reason` lets `is_st` win over the board, so it derives
    2.02 and reads an ordinary bar that opened at 2.05 as locked limit-up. 25 of that session's
    128 ST names are on ChiNext or STAR."""
    ordinary = {
        "subject": "300029.SZ",
        "board": "growth",
        "is_st": True,
        "previous_close": Decimal("1.92"),
        "open": Decimal("2.05"),
        "high": Decimal("2.29"),
        "low": Decimal("2.05"),
        "close": Decimal("2.20"),
    }

    assert _buy(_bar(**ordinary)) == "rejected"
    assert _buy(_bar(**ordinary, up_limit=Decimal("2.30"), down_limit=Decimal("1.54"))) == "filled"


def test_a_security_in_its_first_five_sessions_has_no_band_at_all() -> None:
    """`301580.SZ` listed 2024-06-26 and on the 28th -- its third session -- had no price limit.
    Tushare publishes that as `up_limit=999999.999` / `down_limit=0.01`, and passing those
    through is all it takes: the sentinel is simply never reached by a real high or low, so the
    order fills without this policy needing a limit-free branch. The derived rule instead
    invents a 20% band and rejects."""
    unbounded = {
        "subject": "301580.SZ",
        "board": "growth",
        "previous_close": Decimal("79.46"),
        "open": Decimal("160.00"),
        "high": Decimal("268.00"),
        "low": Decimal("150.00"),
        "close": Decimal("250.00"),
    }

    assert _buy(_bar(**unbounded)) == "rejected"
    filled = _bar(**unbounded, up_limit=Decimal("999999.999"), down_limit=Decimal("0.01"))
    assert _buy(filled) == "filled"
    assert _sell(filled) == "filled"


def test_the_published_band_also_governs_the_sell_side() -> None:
    """The lower half of the same `920924.BJ` row: the exchange published 5.13 and the derived
    rule gives `7.32 * 0.70 = 5.124 -> 5.12`, one fen *below*. A session locked at 5.13 is a
    limit-down bar the derived rule reads as an ordinary one."""
    locked = {
        "subject": "920924.BJ",
        "board": "bse",
        "previous_close": Decimal("7.32"),
        "open": Decimal("5.13"),
        "high": Decimal("5.13"),
        "low": Decimal("5.13"),
        "close": Decimal("5.13"),
    }

    assert _sell(_bar(**locked)) == "filled"
    assert _sell(_bar(**locked, up_limit=Decimal("9.51"), down_limit=Decimal("5.13"))) == "rejected"


def test_a_bar_that_only_touched_the_published_limit_still_fills() -> None:
    """The distinction `domain/price_limits.py::LimitTouch` draws in its two field pairs, made
    binding here: this policy rejects on the **one-price** shape (`low >= upper`, `high <=
    lower`), not on the touch. A bar that traded up to its published 12.03 and back down to
    10.10 had a counterparty on the other side all day, so the order fills; reading `high >=
    upper` instead would reject every session that so much as reached its limit -- 26 of every
    5,338 names on an ordinary day rather than the handful that lock."""
    touched = _bar(
        previous_close=Decimal("10.94"),
        open=Decimal("11.00"),
        high=Decimal("12.03"),
        low=Decimal("10.10"),
        close=Decimal("11.50"),
        up_limit=Decimal("12.03"),
        down_limit=Decimal("9.85"),
    )
    locked = _bar(
        previous_close=Decimal("10.94"),
        open=Decimal("12.03"),
        high=Decimal("12.03"),
        low=Decimal("12.03"),
        close=Decimal("12.03"),
        up_limit=Decimal("12.03"),
        down_limit=Decimal("9.85"),
    )

    assert _buy(touched) == "filled"
    assert _buy(locked) == "rejected"
    # The sell side is the mirror image and is asymmetric in the same direction.
    floored = _bar(
        previous_close=Decimal("10.94"),
        open=Decimal("10.50"),
        high=Decimal("11.00"),
        low=Decimal("9.85"),
        close=Decimal("10.00"),
        up_limit=Decimal("12.03"),
        down_limit=Decimal("9.85"),
    )
    assert _sell(floored) == "filled"


def test_a_beijing_listing_day_has_no_floor_and_both_sides_fill() -> None:
    """`920656.BJ` on 2024-02-02: the exchange published `(99999.99, 0.0)` and the bar ran
    18.00 / 19.91 / 17.55 / 19.90. A `down_limit` of zero is the published statement "no lower
    bound", so `high <= 0.0` is false and the sell fills -- no limit-free branch anywhere. The
    derived rule would put a 30% band on it and, on a listing day whose `pre_close` equals its
    close, get the wrong answer for a different reason."""
    listing_day = {
        "subject": "920656.BJ",
        "trade_date": date(2024, 2, 2),
        "board": "bse",
        "previous_close": Decimal("19.90"),
        "open": Decimal("18.00"),
        "high": Decimal("19.91"),
        "low": Decimal("17.55"),
        "close": Decimal("19.90"),
    }
    unbounded = _bar(**listing_day, up_limit=Decimal("99999.99"), down_limit=Decimal("0"))

    assert unbounded.has_published_limits is True
    assert unbounded.down_limit == Decimal("0")
    assert _buy(unbounded) == "filled"
    assert _sell(unbounded) == "filled"


def test_a_negative_published_floor_is_still_refused() -> None:
    """`ge=0`, not "any number": zero is a published encoding and everything under it is not."""
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _bar(up_limit=Decimal("12.03"), down_limit=Decimal("-0.01"))


def test_a_published_band_does_not_override_the_earlier_rejections() -> None:
    """The band is the last check, not the only one: a halt, a bad lot and T+1 still reject a
    bar that carries a perfectly ordinary published band."""
    ordinary = {"up_limit": Decimal("12.03"), "down_limit": Decimal("9.85")}

    assert _buy(_bar(suspended=True, **ordinary)) == "rejected"
    assert (
        AShareExecutionPolicy()
        .execute(ExecutionRequest(side="buy", quantity=150), _bar(**ordinary))
        .status
        == "rejected"
    )
    assert (
        AShareExecutionPolicy()
        .execute(
            ExecutionRequest(side="sell", quantity=100, position_open_date=SESSION),
            _bar(**ordinary),
        )
        .status
        == "rejected"
    )


# --- the contract on the two new fields -----------------------------------------------------


def test_one_published_side_without_the_other_is_refused() -> None:
    """Half a published band beside half a derived one is a band the exchange never set, and it
    would be silently asymmetric: a buy judged against reality and a sell against a rule."""
    with pytest.raises(ValidationError, match="supplied together or not at all"):
        _bar(up_limit=Decimal("12.03"))
    with pytest.raises(ValidationError, match="supplied together or not at all"):
        _bar(down_limit=Decimal("9.85"))


def test_an_inverted_published_band_is_refused() -> None:
    with pytest.raises(ValidationError, match="down_limit cannot be above up_limit"):
        _bar(up_limit=Decimal("9.85"), down_limit=Decimal("12.03"))


def test_the_conversion_from_a_stored_band_is_exact() -> None:
    """`Decimal(99999.999)` is `99999.998999999998022...` -- the binary double carried into the
    type whose point is that it does not do that. `published_limit_fields` goes through `str`,
    so the sentinel round-trips and so does an ordinary two-decimal band."""
    sentinel = PriceLimit(
        ts_code="603381.SH", trade_date=SESSION, up_limit=99999.999, down_limit=0.01
    )
    ordinary = PriceLimit(ts_code="000001.SZ", trade_date=SESSION, up_limit=12.03, down_limit=9.85)

    assert published_limit_fields(sentinel) == {
        "up_limit": Decimal("99999.999"),
        "down_limit": Decimal("0.01"),
    }
    assert published_limit_fields(ordinary) == {
        "up_limit": Decimal("12.03"),
        "down_limit": Decimal("9.85"),
    }
    # The trap itself, spelled without the literal `ruff`'s RUF032 (rightly) refuses to see in
    # source: this is what a caller who wrote `Decimal(limit.up_limit)` would have got.
    assert Decimal(sentinel.up_limit) != Decimal("99999.999")


def test_a_stored_band_splats_straight_into_a_bar() -> None:
    """The shape a caller that holds both a bar and a band actually writes."""
    limit = PriceLimit(ts_code="000001.SZ", trade_date=SESSION, up_limit=12.03, down_limit=9.85)
    bar = _bar(previous_close=Decimal("10.94"), **published_limit_fields(limit))

    assert bar.has_published_limits is True
    assert bar.up_limit == Decimal("12.03")
