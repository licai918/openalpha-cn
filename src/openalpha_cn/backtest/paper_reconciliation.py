"""The account record: what the user actually placed, reconciled against the paper book
(`V2-P7-006a`).

## What a record is

The paper book assumes every order its evening printed was placed, and fills it at the open. The
user's real account may not: an order may never have been placed, may have filled at another
price, or for another quantity -- and a name the user traded may not be one the book planned to
trade at all. The account record is the user's own statement of the session's orders, one line
per order the book had due (`account-record/v1`), written by hand or by whatever fills it and
read from a JSON file (`read_account_record`).

## What reconciling does -- and does not

`reconcile` compares the record's every line with the settlement's attempt of the same order and
names each divergence by its kind: `not_placed` (the user says the order was not placed and the
book filled it), `filled_where_the_book_did_not` (the user filled an order the book did not),
`quantity_differs` and `price_differs`. A divergence is **recorded, never absorbed**: the book is
not rewritten from the account, and the account is not rewritten from the book. True account sync
-- making the book follow the real account -- is another issue.

A **sale** is reconciled only by whether it filled -- both sides filled, or neither, is agreement;
never by its shares or its price. Its quantity and price are the ledger's, in the ledger's units
(`strategy_survivor_summary`'s Units): after a share event (a bonus issue, a split) the ledger's
share count is not the account's, and after any adjustment -- a cash dividend too -- its prices
are not the real ones. So a sale is always "SELL ENTIRE POSITION", and the account's fill of the
real account's shares is the divergence-free answer to the same order. A **buy** is sized at its
real close with ratio 1, so its quantity and price are real, and both are compared.

Agreement is not a divergence, and neither is an order both sides agree did not fill.

## What a record cannot say

A record the settlement cannot be compared with is refused by name (`AccountRecordConflict`, a
`ValueError`) **before the book advances**: another session's record, a line for an order no
evening planned, a due order with no line (a missing line is a refusal, never agreement), the
same order twice, a `side` or `ts_code` that differs from the plan, or a **buy's** fill for more
than the plan's quantity. The caller reads the record before `paper_evening` commits the session,
so a refused record leaves the ledger and the snapshot untouched. A sale's fill over the ledger's
quantity is not a conflict: the ledger's share count is not the account's after a share event.

An **absent** record is not agreement: the record is optional, and a day without one says so --
the book's assumption that every printed order was placed stands unverified.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from openalpha_cn.backtest.paper_settlement import Settlement

if TYPE_CHECKING:  # the outcome type lives beside the settlement, which imports this module
    from openalpha_cn.backtest.paper_evening import AttemptOutcome

ACCOUNT_RECORD_SCHEMA: Final[str] = "account-record/v1"
"""The schema every record file names; any other is refused, not guessed at."""

NOT_PLACED: Final[str] = "not_placed"
FILLED_WHERE_THE_BOOK_DID_NOT: Final[str] = "filled_where_the_book_did_not"
QUANTITY_DIFFERS: Final[str] = "quantity_differs"
PRICE_DIFFERS: Final[str] = "price_differs"

_CENT: Final[Decimal] = Decimal("0.01")
"""The fen: an account's fill prices are quoted to it, and the book's are compared at it."""


class AccountRecordError(ValueError):
    """The account record cannot be read, is not this schema, or is not comparable with the
    settlement it is read against (`AccountRecordConflict`)."""


class AccountRecordConflict(AccountRecordError):
    """The record is this schema and readable, and it cannot be reconciled with the book's plan:
    another session, an order no evening planned, a due order with no line, the same order twice,
    a side or code that differs from the plan, or a buy's fill over the plan's quantity."""


class RecordedOrder(BaseModel):
    """One order the book had due on the session, as the user's account records it.

    A line the user says was not placed carries no fill; a placed line that did not fill carries
    zero shares and no price; a filled line carries the shares and the price it filled at. More
    than the plan's quantity is a conflict for a **buy** only: a sale's ledger quantity is not the
    account's after a share event, so its shares are not bounded by the plan's (`Units`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str = Field(min_length=1)
    ts_code: str = Field(min_length=1)
    side: Literal["buy", "sell"]
    placed: bool
    filled_quantity: int = Field(ge=0)
    filled_price: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _a_line_is_a_fill_or_nothing(self) -> RecordedOrder:
        if not self.placed and self.filled_quantity != 0:
            raise ValueError(
                "a fill requires placed: true -- an order the user did not place filled nothing"
            )
        if self.filled_quantity > 0 and self.filled_price is None:
            raise ValueError(
                "a filled quantity needs the price it filled at: the record says how many shares "
                "and at what price"
            )
        if self.filled_quantity == 0 and self.filled_price is not None:
            raise ValueError(
                "a price is given for a fill of zero shares: an order that filled nothing has no "
                "price"
            )
        return self


class AccountRecord(BaseModel):
    """The user's record of one session's orders, one line per due order: the file's body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["account-record/v1"]
    session: date
    orders: tuple[RecordedOrder, ...]

    @field_validator("orders")
    @classmethod
    def _one_line_per_order(cls, orders: tuple[RecordedOrder, ...]) -> tuple[RecordedOrder, ...]:
        counts = Counter(order.order_id for order in orders)
        doubled = sorted(order_id for order_id, count in counts.items() if count > 1)
        if doubled:
            raise ValueError(f"the account record names these orders twice: {doubled}")
        return tuple(sorted(orders, key=lambda order: order.order_id))


def read_account_record(path: Path) -> AccountRecord:
    """Read the account record at `path`, or refuse it by name.

    A file that cannot be read, is not JSON, is not an `account-record/v1` record (a missing or
    unknown schema, an unknown field, a malformed line) is an `AccountRecordError` naming the
    path and what is wrong; nothing is guessed and no line is dropped.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise AccountRecordError(
            f"the account record {path} cannot be read ({type(error).__name__}: {error})"
        ) from error
    try:
        body = json.loads(text)
    except ValueError as error:
        raise AccountRecordError(f"{path} is not JSON ({type(error).__name__}: {error})") from error
    try:
        return AccountRecord.model_validate(body)
    except ValidationError as error:
        raise AccountRecordError(f"{path} is not an account-record/v1 record: {error}") from error


@dataclass(frozen=True, slots=True, kw_only=True)
class Divergence:
    """One order on which the book and the account disagree, named by kind.

    `book` is the book's own verdict on the attempt and `record` the user's line, both as the
    printed document spells them -- so what was compared can be read back without the settlement.
    """

    order_id: str
    ts_code: str
    side: str
    kind: str
    book: dict[str, str]
    record: dict[str, str]

    def document(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "ts_code": self.ts_code,
            "side": self.side,
            "kind": self.kind,
            "book": dict(self.book),
            "record": dict(self.record),
        }

    def line(self) -> str:
        return (
            f"{self.order_id} {self.ts_code} {self.side}: {self.kind} "
            f"(book filled {self.book['filled_quantity']} "
            f"@ {self.book['filled_price'] or '-'}, record placed "
            f"{self.record['placed']} filled {self.record['filled_quantity']} "
            f"@ {self.record['filled_price'] or '-'})"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Reconciliation:
    """One session's comparison: every due order, the divergences between book and account, and
    how many orders agreed.

    `divergences` is empty when the two agree on every order -- including every order both say
    did not fill. `reconciled` is the count of due orders that did not diverge.
    """

    session: date
    orders: int
    divergences: tuple[Divergence, ...]

    @property
    def reconciled(self) -> int:
        return self.orders - len(self.divergences)

    def document(self) -> dict[str, Any]:
        return {
            "session": self.session.isoformat(),
            "orders": self.orders,
            "reconciled": self.reconciled,
            "divergences": [item.document() for item in self.divergences],
        }


def reconcile(
    record: AccountRecord,
    settlement: Settlement,
    *,
    outcomes: Sequence[AttemptOutcome],
) -> Reconciliation:
    """Compare `record` with `settlement`'s attempts (`outcomes`, `attempt_outcomes`' answer) and
    name every divergence.

    An irreconcilable record -- another session, a line no evening planned, a due order with no
    line, the same order twice, a side or code unlike the plan, a **buy's** fill over the plan's
    quantity -- is an `AccountRecordConflict` (a `ValueError`): the caller must refuse the day
    before its book advances, never reconcile a substituted record. A sale's shares and price are
    the ledger's, in the ledger's units, so a sale is compared only by whether it filled. Nothing
    here writes: the book keeps its settlement and the account keeps its record; the comparison is
    the whole of it.
    """
    _require_the_same_orders(record, settlement, outcomes)
    verdicts = {item.order.order_id: item for item in settlement.runner_record.transitions}
    lines = {line.order_id: line for line in record.orders}
    divergences = []
    for outcome in outcomes:
        divergence = _divergence(
            outcome, lines[outcome.attempt.order.order_id], verdicts.get(outcome.attempt.attempt_id)
        )
        if divergence is not None:
            divergences.append(divergence)
    return Reconciliation(
        session=settlement.session,
        orders=len(record.orders),
        divergences=tuple(divergences),
    )


def _require_the_same_orders(
    record: AccountRecord,
    settlement: Settlement,
    outcomes: Sequence[AttemptOutcome],
) -> None:
    """Refuse a record that does not describe exactly this settlement's due orders."""
    if record.session != settlement.session:
        raise AccountRecordConflict(
            f"the account record names another session ({record.session.isoformat()}), and this "
            f"settlement is {settlement.session.isoformat()}: one record is one session's"
        )
    counts = Counter(line.order_id for line in record.orders)
    doubled = sorted(order_id for order_id, count in counts.items() if count > 1)
    if doubled:
        raise AccountRecordConflict(
            f"the account record names these orders twice: {doubled}: one order is one line"
        )
    planned = {outcome.attempt.order.order_id: outcome.attempt.order for outcome in outcomes}
    stray = sorted(set(counts) - planned.keys())
    if stray:
        raise AccountRecordConflict(
            f"the account record names orders no evening of {settlement.session.isoformat()} "
            f"planned: {stray}"
        )
    missing = sorted(planned.keys() - counts.keys())
    if missing:
        raise AccountRecordConflict(
            f"the account record has no line for orders the evening planned: {missing}: a "
            "missing line is a refusal, never agreement"
        )
    for line in record.orders:
        order = planned[line.order_id]
        if line.side != order.side or line.ts_code != order.ts_code:
            raise AccountRecordConflict(
                f"the account record's line {line.order_id} says {line.side} {line.ts_code}, and "
                f"the plan's order is {order.side} {order.ts_code}"
            )
        if order.side == "buy" and line.filled_quantity > order.quantity:
            raise AccountRecordConflict(
                f"the account record fills {line.filled_quantity} shares of {line.order_id}, more "
                f"than the plan's {order.quantity}"
            )


def _divergence(
    outcome: AttemptOutcome,
    line: RecordedOrder,
    transition: Any,
) -> Divergence | None:
    """The divergence between one attempt and the account's line for it, or `None` when the two
    agree -- which includes both saying the order did not fill.

    A sell agrees whenever both sides filled it, whatever their shares and price: the ledger's
    are ledgers' units (`Units`), the account's are real, and the order is "SELL ENTIRE POSITION".
    A buy's shares and price are real, so both are compared.
    """
    book_filled, book_price = _book_fill(outcome, transition)
    order = outcome.attempt.order
    if not line.placed and book_filled == 0:
        return None
    if not line.placed:
        kind = NOT_PLACED
    elif book_filled == 0 and line.filled_quantity > 0:
        kind = FILLED_WHERE_THE_BOOK_DID_NOT
    elif order.side == "sell" and book_filled > 0 and line.filled_quantity > 0:
        return None
    elif book_filled != line.filled_quantity:
        kind = QUANTITY_DIFFERS
    elif _cents(line.filled_price) != _cents(book_price):
        kind = PRICE_DIFFERS
    else:
        return None
    return Divergence(
        order_id=order.order_id,
        ts_code=order.ts_code,
        side=order.side,
        kind=kind,
        book={
            "status": outcome.status,
            "filled_quantity": str(book_filled),
            "filled_price": "" if book_price is None else format(book_price, "f"),
        },
        record={
            "placed": "true" if line.placed else "false",
            "filled_quantity": str(line.filled_quantity),
            "filled_price": "" if line.filled_price is None else format(line.filled_price, "f"),
        },
    )


def _book_fill(outcome: AttemptOutcome, transition: Any) -> tuple[int, Decimal | None]:
    """The shares the book filled and at what price: the attempt's transition when the book
    filled it, and nothing otherwise (rejected, or not attempted for want of a bar)."""
    if outcome.status != "filled" or transition is None:
        return 0, None
    execution = transition.execution
    if execution is None:
        return 0, None
    return execution.quantity, execution.filled_price


def _cents(price: Decimal | None) -> Decimal | None:
    """A fill price at the fen, so a record's two-place price meets the book's own."""
    return None if price is None else price.quantize(_CENT)
