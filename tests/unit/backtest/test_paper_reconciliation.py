"""The account record: a paper book reconciled against what the user actually placed
(`V2-P7-006a`). The settlement is `test_paper_evening.py`'s own fixture on real files: S3 settles
S2's ten buys, so every attempt has a verdict and a fill price to compare against."""

from __future__ import annotations

import importlib
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import pytest

_HERE: Final[Path] = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
evenings: Any = importlib.import_module("test_paper_evening")

from openalpha_cn.backtest.paper_evening import attempt_outcomes  # noqa: E402
from openalpha_cn.backtest.paper_reconciliation import (  # noqa: E402
    AccountRecord,
    AccountRecordConflict,
    AccountRecordError,
    RecordedOrder,
    read_account_record,
    reconcile,
)


def _settlement(root: Path) -> Any:
    return evenings.tonight(evenings.through_s2(root)).settlement


def _fills(settlement: Any) -> dict[str, tuple[int, Decimal]]:
    verdicts = {item.order.order_id: item for item in settlement.runner_record.transitions}
    return {
        outcome.attempt.attempt_id: (
            verdicts[outcome.attempt.attempt_id].execution.quantity,
            verdicts[outcome.attempt.attempt_id].execution.filled_price,
        )
        for outcome in attempt_outcomes(settlement)
        if outcome.status == "filled"
    }


def _record(
    settlement: Any,
    *,
    not_placed: frozenset[str] = frozenset(),
    price_factor: Decimal = Decimal(1),
) -> AccountRecord:
    fills = _fills(settlement)
    orders = []
    for outcome in attempt_outcomes(settlement):
        order = outcome.attempt.order
        if order.order_id in not_placed:
            orders.append(
                RecordedOrder(
                    order_id=order.order_id,
                    ts_code=order.ts_code,
                    side=order.side,
                    placed=False,
                    filled_quantity=0,
                )
            )
        elif outcome.status == "filled":
            quantity, price = fills[outcome.attempt.attempt_id]
            orders.append(
                RecordedOrder(
                    order_id=order.order_id,
                    ts_code=order.ts_code,
                    side=order.side,
                    placed=True,
                    filled_quantity=quantity,
                    filled_price=(price * price_factor).quantize(Decimal("0.01")),
                )
            )
        else:
            orders.append(
                RecordedOrder(
                    order_id=order.order_id,
                    ts_code=order.ts_code,
                    side=order.side,
                    placed=True,
                    filled_quantity=0,
                )
            )
    return AccountRecord(
        schema_version="account-record/v1",
        session=settlement.session,
        orders=tuple(orders),
    )


def _reconciled(settlement: Any, record: AccountRecord) -> Any:
    return reconcile(record, settlement, outcomes=attempt_outcomes(settlement))


def _sale(settlement: Any) -> Any:
    """The fixture's one filled sale -- 600010.SH, 900 shares of the ledger's, at its 10.00 open."""
    return next(
        outcome
        for outcome in attempt_outcomes(settlement)
        if outcome.attempt.order.side == "sell" and outcome.status == "filled"
    )


def _with_line(record: AccountRecord, order_id: str, **changes: Any) -> AccountRecord:
    """`record` with the line for `order_id` carrying `changes` instead of its own fields."""
    return record.model_copy(
        update={
            "orders": tuple(
                line.model_copy(update=changes) if line.order_id == order_id else line
                for line in record.orders
            )
        }
    )


def test_a_record_that_matches_the_book_reconciles_with_no_divergence(tmp_path: Path) -> None:
    settlement = _settlement(tmp_path / "paper")
    record = _record(settlement)

    reconciliation = _reconciled(settlement, record)

    assert reconciliation.divergences == ()
    assert reconciliation.orders == len(record.orders) == len(settlement.attempts)
    assert reconciliation.reconciled == len(record.orders)
    document = reconciliation.document()
    assert document == {
        "session": settlement.session.isoformat(),
        "orders": len(record.orders),
        "reconciled": len(record.orders),
        "divergences": [],
    }


def test_an_order_the_user_did_not_place_is_named_and_not_absorbed(tmp_path: Path) -> None:
    settlement = _settlement(tmp_path / "paper")
    filled = next(outcome for outcome in attempt_outcomes(settlement) if outcome.status == "filled")
    record = _record(settlement, not_placed=frozenset({filled.attempt.order.order_id}))

    reconciliation = _reconciled(settlement, record)

    assert len(reconciliation.divergences) == 1
    divergence = reconciliation.divergences[0]
    assert (divergence.order_id, divergence.ts_code, divergence.side, divergence.kind) == (
        filled.attempt.order.order_id,
        filled.attempt.order.ts_code,
        filled.attempt.order.side,
        "not_placed",
    )
    assert divergence.book["status"] == "filled"
    assert divergence.record == {"placed": "false", "filled_quantity": "0", "filled_price": ""}
    assert reconciliation.reconciled == reconciliation.orders - 1
    assert filled.attempt.order.order_id in divergence.line()


def test_a_fill_at_another_price_is_named_rather_than_absorbed(tmp_path: Path) -> None:
    settlement = _settlement(tmp_path / "paper")
    record = _record(settlement, price_factor=Decimal("1.01"))

    reconciliation = _reconciled(settlement, record)

    kinds = {item.kind for item in reconciliation.divergences}
    assert kinds == {"price_differs"}
    # Both buys' fills diverge on price -- the sale's line also carries another price and agrees,
    # because a sale is reconciled by whether it filled, never by its price -- and the rejected
    # buy, which the record also leaves unfilled at zero shares, agrees. So two of the three
    # orders reconcile.
    assert reconciliation.reconciled == 2


@pytest.mark.parametrize(
    "missing",
    [
        "another session",
        "an order not due",
        "a due order",
        "a doubled line",
        "a quantity over the plan",
    ],
)
def test_an_irreconcilable_record_is_refused_by_name(tmp_path: Path, missing: str) -> None:
    settlement = _settlement(tmp_path / "paper")
    record = _record(settlement)
    first = record.orders[0]
    if missing == "another session":
        changed = record.model_copy(update={"session": date(2021, 1, 4)})
        said = "another session"
    elif missing == "an order not due":
        changed = record.model_copy(
            update={
                "orders": (
                    *record.orders,
                    first.model_copy(update={"order_id": "an_order_no_evening_planned"}),
                )
            }
        )
        said = "an_order_no_evening_planned"
    elif missing == "a due order":
        changed = record.model_copy(update={"orders": record.orders[1:]})
        said = first.order_id
    elif missing == "a doubled line":
        changed = record.model_copy(update={"orders": (*record.orders, first)})
        said = first.order_id
    else:
        changed = record.model_copy(
            update={
                "orders": (
                    first.model_copy(update={"filled_quantity": first.filled_quantity + 1}),
                    *record.orders[1:],
                )
            }
        )
        said = "more than the plan"

    with pytest.raises(AccountRecordConflict, match=said):
        _reconciled(settlement, changed)


def test_a_record_file_is_read_only_when_it_is_this_schema(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_text(
        json.dumps({"schema_version": "account-record/v2", "session": "2026-04-07", "orders": []}),
        encoding="utf-8",
    )
    with pytest.raises(AccountRecordError, match="account-record/v1"):
        read_account_record(path)

    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(AccountRecordError, match="not JSON"):
        read_account_record(path)

    path.write_text(
        json.dumps(
            {
                "schema_version": "account-record/v1",
                "session": "2026-04-07",
                "orders": [
                    {
                        "order_id": "x",
                        "ts_code": "600000.SH",
                        "side": "buy",
                        "placed": False,
                        "filled_quantity": 0,
                        "unexpected": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AccountRecordError, match="unexpected"):
        read_account_record(path)

    path.write_text(
        json.dumps(
            {
                "schema_version": "account-record/v1",
                "session": "2026-04-07",
                "orders": [
                    {
                        "order_id": "x",
                        "ts_code": "600000.SH",
                        "side": "buy",
                        "placed": True,
                        "filled_quantity": 0,
                        "filled_price": "10.20",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AccountRecordError, match="fill of zero shares"):
        read_account_record(path)


def test_a_record_that_agrees_on_no_fill_is_not_a_divergence(tmp_path: Path) -> None:
    """Where the record and the book agree that an order did not fill, there is no divergence to
    report: agreement is not something to report.

    The fixture's S3 settlement has three attempts -- two fills and one rejected at a limit-up
    open -- so the line the two sides agree did not fill is the rejected one; marking the fills
    "not placed" is the divergence `test_an_order_the_user_did_not_place_is_named_and_not_absorbed`
    pins, not agreement.
    """
    settlement = _settlement(tmp_path / "paper")
    record = _record(
        settlement,
        not_placed=frozenset(
            o.attempt.order.order_id for o in attempt_outcomes(settlement) if o.status != "filled"
        ),
    )

    reconciliation = _reconciled(settlement, record)

    assert reconciliation.divergences == ()


def test_a_record_without_its_schema_is_refused_by_name(tmp_path: Path) -> None:
    """The schema has no default: a file that does not name it is not this schema, and is refused
    rather than read as one."""
    path = tmp_path / "record.json"
    path.write_text(json.dumps({"session": "2026-04-07", "orders": []}), encoding="utf-8")

    with pytest.raises(AccountRecordError, match="schema_version"):
        read_account_record(path)


def test_a_record_that_is_not_utf8_is_refused_with_its_path(tmp_path: Path) -> None:
    """A file that cannot be decoded is unreadable, not empty: the refusal names the path and does
    not let a `UnicodeDecodeError` (a `ValueError`) escape unwrapped."""
    path = tmp_path / "record.json"
    path.write_bytes(b'{"schema_version": "\xff\xfe"}')

    with pytest.raises(AccountRecordError, match="cannot be read"):
        read_account_record(path)


# ---- a sale is reconciled by whether it filled, never by its shares or price -------------------


def test_a_sale_after_a_bonus_issue_is_reconciled_by_whether_it_filled(tmp_path: Path) -> None:
    """After a bonus issue the ledger's share count is not the account's
    (`strategy_survivor_summary`'s Units), so the account's 2000 real shares sold against the
    ledger's 900 is no conflict and no quantity divergence: a sale is reconciled by whether it
    filled, never by the ledger's shares.
    """
    settlement = _settlement(tmp_path / "paper")
    sale = _sale(settlement)
    order = sale.attempt.order
    record = _with_line(_record(settlement), order.order_id, filled_quantity=2000)

    reconciliation = _reconciled(settlement, record)

    assert order.order_id not in {item.order_id for item in reconciliation.divergences}
    assert reconciliation.divergences == ()


def test_a_sale_after_a_cash_dividend_is_reconciled_by_whether_it_filled(tmp_path: Path) -> None:
    """The ledger's sale price is `open x ratio` and its share count is the ledger's (`Units`), so
    after a cash dividend the account's real price is not the book's and the two differ by more
    than the fen: a sale both sides filled agrees, the price never compared."""
    settlement = _settlement(tmp_path / "paper")
    sale = _sale(settlement)
    order = sale.attempt.order
    book_price = _fills(settlement)[sale.attempt.attempt_id][1]
    record = _with_line(
        _record(settlement),
        order.order_id,
        filled_price=book_price - Decimal("0.20"),
    )

    reconciliation = _reconciled(settlement, record)

    assert reconciliation.divergences == ()


def test_a_sale_the_book_filled_and_the_account_placed_unfilled_still_diverges(
    tmp_path: Path,
) -> None:
    """The relaxation is only on shares and price: a sale the book filled that the account placed
    but did not fill is still a divergence, of the kind the book already names for it."""
    settlement = _settlement(tmp_path / "paper")
    order = _sale(settlement).attempt.order
    record = _with_line(_record(settlement), order.order_id, filled_quantity=0)

    reconciliation = _reconciled(settlement, record)

    (divergence,) = reconciliation.divergences
    assert divergence.order_id == order.order_id
    assert divergence.kind == "quantity_differs"


def test_a_buy_over_the_plan_is_still_a_conflict(tmp_path: Path) -> None:
    """A buy's quantity is real (its ratio is 1, `Units`), so the plan's bound still holds: a buy
    the account fills for more than the plan's quantity is refused, not reconciled."""
    settlement = _settlement(tmp_path / "paper")
    buy = next(
        outcome
        for outcome in attempt_outcomes(settlement)
        if outcome.attempt.order.side == "buy" and outcome.status == "filled"
    )
    order = buy.attempt.order
    record = _with_line(_record(settlement), order.order_id, filled_quantity=order.quantity + 1)

    with pytest.raises(AccountRecordConflict, match="more than the plan"):
        _reconciled(settlement, record)


def test_a_buy_at_another_price_is_still_a_price_divergence(tmp_path: Path) -> None:
    """A buy's price is real (its ratio is 1), so it is still compared to the fen: the relaxation
    touches sales only."""
    settlement = _settlement(tmp_path / "paper")
    buy = next(
        outcome
        for outcome in attempt_outcomes(settlement)
        if outcome.attempt.order.side == "buy" and outcome.status == "filled"
    )
    order = buy.attempt.order
    price = _fills(settlement)[buy.attempt.attempt_id][1]
    record = _with_line(_record(settlement), order.order_id, filled_price=price + Decimal("0.01"))

    reconciliation = _reconciled(settlement, record)

    (divergence,) = reconciliation.divergences
    assert (divergence.order_id, divergence.kind) == (order.order_id, "price_differs")
