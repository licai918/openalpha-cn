"""The survivor evening's record and its human summary (`V2-P7-008`, task D4).

`evening_document` turns what one survivor evening produced -- the facts day, the kernel's screen
of it, the paper book's evening and the sessions it caught up -- into the JSON document the daily
journal files as its `result`; `summary_lines` turns that document, and nothing else, into the
lines the daily command prints. Both are pure: every input is an argument, nothing is read,
written, fetched or timed, and the same inputs give the same bytes (no set is iterated unsorted,
every mapping this module builds from something other than a fixed list of field names is built
in sorted order, the pydantic dumps keep their models' field order, which is fixed, and no clock
is asked).

## The document

`kind`, `session`, `session_index`, `as_of`; `facts`; `screen`; `paper`; `wording`. Decimals are
strings, dates ISO strings, tuples lists, and every mapping keyed by something other than a fixed
field name (reason counts, the notes, the orders a settlement names) has its keys sorted. The
document is read back from a journal by `summary_lines`, so it holds everything the summary
prints.

- **`facts`**: the day's sizes, the coverage as a ratio to six places (`None` for a day with no row
  on the traded boards, which the adapter refuses long before here), **every** count the adapter
  noted (`FactsNotes`, field for field, `unrecognised_board_codes` included) and what became of
  each held code. `st_by_band` carries its note: the 5% band is a witness of special treatment
  only before 2025-07-07, so a 0 after it is the band no longer answering, not the band agreeing
  with the names.
- **`screen`**: how many securities were assessed, how many are eligible and survive, and how many
  verdicts carry each reason -- all six, zeros included, so a reason that is missing from a
  document is never one that was not counted.
- **`paper`**: the evening's `trade_list` (the `TradeList` JSON: its id, `unfunded`, every order's
  id) and `decision` (the `BookDecision` JSON), the `snapshot_id`, what `settled` (the orders that
  filled, expired and were cancelled by id, every attempt with its verdict and reason, every
  write-off with the cost basis it wrote off), the `book` the session ended with and the
  `catch_ups`. Then what the summary needs and the journal's reader may want: `outcome`
  (`created`, or `unchanged` for a rerun that wrote nothing), the `open_session` the orders are
  for, the `open_orders` in the order they are printed (sales, then tonight's buys, then the buys
  carried from earlier sessions) and the `illiquid_exits`.
- **`paper: null`** is the session that ran no evening -- settled only, or refused before the paper
  step. The reason the caller gives, and the sessions it did settle, are `paper_skipped`.

**What the daily command adds (`V2-P7-008`, D5b).** It journals each session a catch-up settles as
that session commits, in this module's own form of a catch-up (`catch_up_entry`), so a run again
after a stop passes the ones an earlier run of the day settled as `filed_catch_ups`: they come
before this run's, and the two are checked as one list. A session an earlier run committed and
stopped before journalling is derived again by the run after it and filed then, `rederived`
true; the catch-up line names every such session, so its fills and write-offs are printed once,
under it. When the catch-up changed the holdings the
facts day was read with, the paper book reads the day again (`strategy_survivor_book.evening`); the
caller passes that second read as `read_again`, the paper section is computed on it and carries its
facts section as `facts_read_again`, and `facts` and `screen` stay what the prediction used (D3
review, rule 8). A halt's start comes from the documents of the sessions before (`carried_since`).

**The book's figures are the settlement's.** `Settlement.book` is the book after the close and
after the write-offs; `Settlement.runner_record` is the runner's trace from before both, and its
`equity` overstates a write-off day by the whole of what was written off. The document reads
`equity`, `cash`, `gross_exposure` and `carried_marks` from the settlement and never from the
runner's record (`CarriedMark.sessions_carried` is always 1 there -- each `advance` is a
one-session run -- so the document does not carry it: the length of a halt is the journal
history's, which the caller passes as `carried_since`).

**Units.** A holding's quantity and a sale's `reference_price` are the ledger's, in the ledger's
units (the real price times the adjustment ratio since the buy filled): after a share event (a
bonus issue, a split) the ledger's share count is not the account's, and after any adjustment --
a cash dividend too -- its prices are not the real ones. So a sale is always "SELL ENTIRE
POSITION", its ledger quantity printed beside the REAL close of the facts day and never as a
number of shares to sell. A buy is sized at its real close with ratio 1, so its quantity is real.
A holding whose ledger mark is not its real close also carries `ledger_ratio` (mark over close, to
four places), and the positions line prints it: the adjustment since the buy, **cash dividends
included** -- Tushare's `adj_factor` moves on a cash dividend as on a bonus issue -- and so no
share count. After a cash dividend the account holds the shares it bought and the cash (1000
shares, 0.50 paid out of a 25.00 close: ratio 1.0204, and still 1000 shares, never 1020.4); its
count differs from the ledger's only after a share event. No line derives a share count from the
ratio.

## The summary

Sections, in this order: session, book, facts, screen, settled, positions, the ORDERS FOR THE OPEN
OF D+1 (call auction 09:15) and the carried orders, what the draw left out (unfunded,
unaffordable, deferred exits, near exit, frozen, watch list), illiquid exits, catch-up, prediction,
Tushare requests, units, wording. A list with nothing in it prints as `none`, so a missing line
never reads as "nothing to do". A day with no evening prints `NONE PLANNED tonight -- no evening
was run`, the reason, whether tonight's session was settled (a settle-only day lists it among its
catch-ups; a day refused before its paper book settled nothing), and that the orders still live in
the book from earlier evenings are not listed here (a sale never expires), never an empty plan. A
carried order says the date it was planned on (`signal_on`). The wording is "candidates"; nothing
here is a recommendation, and the ledger's equity is never evidence of anything.

`prediction` and `this_run` are blocks the caller merges into the mapping it passes to
`summary_lines`; this module's document does not carry them, and whether the journal's `result`
also holds them is the caller's decision. The summary renders what is there. So are `panel` and
`factors`, the daily command's panel and factor steps: the sessions each caught up are printed
after the prediction, as the P6 day prints its factor catch-up, and nothing when there were none.

Real closes in the ORDERS block are references, not limit prices: the paper book fills at the
open, and the block's header says so once. Its header also carries the trade list's id and whether
the run committed the evening (`created`) or found it settled and derived it again
(`unchanged`), so a printed plan can be tied to its journal entry. When the caller's `this_run`
says the run printing the plan was made at or after the call auction of the session the orders
are for (`after_the_call_auction`: that session's date and the auction's instant -- a committed
evening completed, or a plan printed again, the next morning), the header is followed by a line
that says so: the paper book fills those orders at that open whether or not they were placed. It
is the printing run's fact, so the document never carries it.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import fields
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from openalpha_cn.backtest.paper_evening import (
    CatchUpResult,
    EveningResult,
    attempt_outcomes,
)
from openalpha_cn.backtest.paper_settlement import Settlement
from openalpha_cn.domain.survivor_book import (
    ELIGIBILITY_REASON_ORDER,
    EligibilityVerdict,
    ScreenResult,
    SecurityFacts,
    SurvivorBookConfig,
    floor_share,
)
from openalpha_cn.domain.survivor_orders import TradeOrder

if TYPE_CHECKING:
    from openalpha_cn.strategy_survivor_facts import FactsDay
    from openalpha_cn.strategy_survivor_market import SessionOrdinals

__all__ = [
    "BAND_WITNESS_ENDS",
    "SURVIVOR_DOCUMENT_KIND",
    "SURVIVOR_WORDING",
    "carried_since",
    "catch_up_entry",
    "evening_document",
    "illiquid_rank",
    "summary_lines",
]

SURVIVOR_DOCUMENT_KIND: Final[str] = "survivor_book/v1"

SURVIVOR_WORDING: Final[str] = (
    "candidates of a registered research configuration; no order was placed; ledger equity is "
    "not evidence; with N=10, single-year results are mostly luck"
)
"""What every document and every summary says it is: a candidate, not an order, and a ledger that
proves nothing."""

BAND_WITNESS_ENDS: Final[date] = date(2025, 7, 7)
"""The first session on which the 5% main-board band is no witness of special treatment: the main
board's risk-warning securities trade on a 10% band from it."""

_BAND_WITNESS_NOTE: Final[str] = (
    "the 5% main-board band is a witness of special treatment only before 2025-07-07; from that "
    "session the main board's risk-warning securities trade on a 10% band, so on or after it the "
    "band is no witness of special treatment and a count of 0 is the band no longer answering, "
    "not the band agreeing with the names"
)
_BAND_LINE: Final[str] = "band = 0: no band witness since 2025-07-07"
_CAVEAT: Final[str] = "today's draw, not a promise for tomorrow"
_UNITS: Final[str] = (
    "fees after an adjustment event are charged on the scaled notional; filled_price and marks "
    "are in ledger units (the real price times the adjustment ratio); transfer_fee is slippage"
)
_REASON_UNKNOWN: Final[str] = "not_assessed"
"""The reason a sale gives for a code the screen holds no verdict for: the facts day gave it no row,
and the kernel sells what it never assessed."""

_RATIO_PLACES: Final[Decimal] = Decimal("0.000001")
_RATIO_STEP: Final[Decimal] = Decimal("0.0001")
_LABEL_WIDTH: Final[int] = 18
_ORDERS_HEADER: Final[str] = "ORDERS FOR THE OPEN OF D+1 (call auction 09:15)"
_REFERENCE_NOTE: Final[str] = (
    "  real closes are references, not limit prices; the paper book fills at the open"
)
_EVENING_STATES: Final[Mapping[str, str]] = {
    "created": "evening created",
    "unchanged": "evening unchanged (settled earlier; derived again, nothing written)",
}
"""What the ORDERS header says of the run: it committed the evening, or found it settled and
derived it again, writing nothing -- so a printed plan can be told from the run that made it."""
_SUSPENDED: Final[str] = "(suspended: the close may be an earlier session's)"
_POSITIONS_NOTE: Final[str] = (
    "(ledger quantity in ledger units; real close from the facts; a ledger/real ratio is the "
    "adjustment since the buy, cash dividends included, and no share count: the account's share "
    "count differs from the ledger's only after a share event -- a bonus issue or a split -- and "
    "every sale is SELL ENTIRE POSITION)"
)
"""What the positions header says of the figures under it (D5 joint review, Important 2): Tushare's
`adj_factor` moves on a cash dividend as on a bonus issue, so no share count is derived from the
ratio -- after a cash dividend the account holds the shares it bought, plus the cash."""
_PRINTED_NOTES: Final[frozenset[str]] = frozenset(
    {
        "delisting",
        "name_unknown_assumed_ordinary",
        "name_unknown_before_first",
        "name_unknown_no_record",
        "no_bar_within_carry",
        "no_listing_row",
        "not_listed_with_bar",
        "st",
        "st_by_band",
        "st_by_name",
        "st_pending",
        "suspended_without_bar",
        "unrecognised_board",
        "unrecognised_board_codes",
    }
)
"""The notes the facts lines have a place for: the adapter's `FactsNotes` fields, one for one
(`tests/unit/test_strategy_survivor_summary.py` holds the two equal)."""


# --- the liquidity rank -------------------------------------------------------------------------


def illiquid_rank(facts: FactsDay, screen: ScreenResult, code: str) -> tuple[int, int]:
    """`code`'s place by traded amount in the population the liquidity cut was taken over, and that
    population's size.

    The population is the names that pass every static reason -- the verdicts with no reason but,
    perhaps, `illiquid`, which is the only reason that depends on the others
    (`domain.survivor_screen`). The rank counts from the thinnest: 1 is the thinnest, ties broken
    by `ts_code` as the screen breaks them, and the kernel drops the first
    `floor(population x liquidity_drop_fraction)`. A code outside the population has no rank, and
    neither does any code when the screen assessed a name the facts day has no row for (it is not
    this day's screen): both are refused by name.
    """
    population = [item.ts_code for item in screen.verdicts if set(item.reasons) <= {"illiquid"}]
    if code not in population:
        raise ValueError(
            f"{code} is not in the liquidity population: the screen holds no verdict for it, or "
            "it fails a static reason and was never ranked by amount"
        )
    amounts = {item.ts_code: item.avg_amount for item in facts.facts}
    unrowed = sorted(set(population) - amounts.keys())
    if unrowed:
        raise ValueError(
            f"the screen assessed {unrowed} and the facts day has no row for them: this is not "
            "the screen of this facts day, and no amount rank can be taken"
        )
    ordered = sorted(population, key=lambda name: (amounts[name], name))
    return ordered.index(code) + 1, len(ordered)


# --- the document -------------------------------------------------------------------------------


def evening_document(
    *,
    session: date,
    session_index: int,
    as_of: datetime,
    facts: FactsDay,
    screen: ScreenResult,
    evening: EveningResult | None,
    catch_ups: Sequence[CatchUpResult],
    ordinals: SessionOrdinals,
    book: SurvivorBookConfig,
    paper_skipped: str | None = None,
    carried_since: Mapping[str, date] | None = None,
    filed_catch_ups: Sequence[Mapping[str, Any]] = (),
    read_again: tuple[FactsDay, ScreenResult] | None = None,
) -> dict[str, Any]:
    """The `result` document of the survivor evening of `session` (ordinal `session_index`), read
    at `as_of`; see the module docstring.

    `evening` is the paper book's answer, or `None` for a session that ran no evening, in which
    case `paper_skipped` is the reason the caller gives, and is required. `catch_ups` are the
    sessions settled before it -- or, with no evening, the sessions settled instead of it -- oldest
    first; `filed_catch_ups` are more of them as an earlier run of the day filed them
    (`catch_up_entry`'s form), which come first, and the two are checked as one list.
    `ordinals` turns the orders' sessions into dates, and `book` is the configuration whose
    liquidity cut an illiquid exit is reported against. `carried_since` is the first session each
    carried holding had no bar, from the journal's history: an entry for a name that is not
    carried tonight is ignored, and no entry is a halt of unknown length, never of length 1.
    `read_again` is the facts day and screen the evening ran on when it read the day again: the
    paper section is theirs and carries the facts section as `facts_read_again`, while `facts`
    and `screen` are what the prediction used.

    A facts day or an evening that is not of `session`, a `paper_skipped` given beside an evening
    or missing without one, catch-ups out of order or not before the evening, a filed catch-up
    that is not `catch_up_entry`'s form, and a day read again without an evening or of another
    session are refused by name: the document would describe another day than the one it is filed
    under.
    """
    filed = [_filed_catch_up(entry) for entry in filed_catch_ups]
    _check(
        session=session,
        session_index=session_index,
        facts=facts,
        evening=evening,
        indices=[entry["session_index"] for entry in filed]
        + [item.settlement.session_index for item in catch_ups],
        paper_skipped=paper_skipped,
        read_again=read_again,
    )
    entries = [*filed, *(catch_up_entry(item) for item in catch_ups)]
    document: dict[str, Any] = {
        "kind": SURVIVOR_DOCUMENT_KIND,
        "session": session.isoformat(),
        "session_index": session_index,
        "as_of": as_of.isoformat(),
        "facts": _facts_section(facts, session),
        "screen": _screen_section(screen),
    }
    if evening is None:
        document["paper"] = None
        document["paper_skipped"] = {"reason": paper_skipped, "catch_ups": entries}
    else:
        paper_facts, paper_screen = (facts, screen) if read_again is None else read_again
        document["paper"] = _paper_section(
            session_index=session_index,
            facts=paper_facts,
            screen=paper_screen,
            evening=evening,
            catch_ups=entries,
            ordinals=ordinals,
            book=book,
            carried_since=carried_since or {},
        )
        if read_again is not None:
            document["paper"]["facts_read_again"] = _facts_section(paper_facts, session)
    document["wording"] = SURVIVOR_WORDING
    return document


def _filed_catch_up(entry: Mapping[str, Any]) -> dict[str, Any]:
    """A catch-up as the journal filed it, checked to be `catch_up_entry`'s form as far as the
    document's checks read it: a mapping with its session and its integer ordinal."""
    if (
        not isinstance(entry, Mapping)
        or not isinstance(entry.get("session"), str)
        or type(entry.get("session_index")) is not int
    ):
        raise ValueError(
            f"a filed catch-up must be a catch_up_entry -- a mapping naming its session and its "
            f"integer session_index -- not {entry!r}"
        )
    return dict(entry)


def _check(
    *,
    session: date,
    session_index: int,
    facts: FactsDay,
    evening: EveningResult | None,
    indices: Sequence[int],
    paper_skipped: str | None,
    read_again: tuple[FactsDay, ScreenResult] | None,
) -> None:
    """Refuse, by name, a document whose parts are not of the day it is filed under."""
    if facts.session != session:
        raise ValueError(
            f"the facts day is of session {facts.session.isoformat()}, not "
            f"{session.isoformat()}: the document would describe another day"
        )
    if read_again is not None:
        if evening is None:
            raise ValueError(
                "a facts day read again was given with no evening: only the paper book's evening "
                "reads the day again, with the holdings its catch-up left"
            )
        if read_again[0].session != session:
            raise ValueError(
                f"the facts day read again is of session {read_again[0].session.isoformat()}, "
                f"not {session.isoformat()}: the document would describe another day"
            )
    if evening is None and not paper_skipped:
        raise ValueError(
            "no evening was given and no paper_skipped reason: a day that ran no evening says "
            "why, or the journal holds a day it cannot explain"
        )
    if evening is not None:
        if paper_skipped is not None:
            raise ValueError(
                f"paper_skipped ({paper_skipped!r}) was given with an evening: a day that ran "
                "its evening skipped nothing"
            )
        held = evening.snapshot
        if held.session != session:
            raise ValueError(
                f"the evening is of session {held.session.isoformat()}, not "
                f"{session.isoformat()}: the document would describe another day"
            )
        if held.session_index != session_index:
            raise ValueError(
                f"the evening is of session index {held.session_index}, not {session_index}"
            )
    if list(indices) != sorted(set(indices)):
        raise ValueError(
            f"catch-ups must be given oldest first, each session once, not {list(indices)}"
        )
    for index in indices:
        if index > session_index:
            raise ValueError(
                f"a catch-up of session index {index} is after the session being documented "
                f"(index {session_index})"
            )
        if evening is not None and index == session_index:
            raise ValueError(
                f"a catch-up of session index {index} is the session the evening settles: a "
                "session is settled once"
            )


def _facts_section(facts: FactsDay, session: date) -> dict[str, Any]:
    notes = {
        item.name: _plain(getattr(facts.notes, item.name))
        for item in sorted(fields(facts.notes), key=lambda item: item.name)
    }
    return {
        "rows": len(facts.facts),
        "boards": list(facts.boards),
        "universe_size": facts.universe_size,
        "scored_size": facts.scored_size,
        "coverage": _coverage(facts.scored_size, facts.universe_size),
        "notes": notes,
        "band_witness": {
            "st_by_band": facts.notes.st_by_band,
            "inert_from": BAND_WITNESS_ENDS.isoformat(),
            "inert": session >= BAND_WITNESS_ENDS,
            "note": _BAND_WITNESS_NOTE,
        },
        "held": list(facts.held),
        "held_status": [
            {"ts_code": item.ts_code, "status": item.status} for item in facts.held_status
        ],
    }


def _coverage(scored: int, universe: int) -> str | None:
    """The scored share of the universe as a ratio to six places; `None` when there is no
    universe to be a share of."""
    if universe == 0:
        return None
    return _text((Decimal(scored) / Decimal(universe)).quantize(_RATIO_PLACES))


def _screen_section(screen: ScreenResult) -> dict[str, Any]:
    named = Counter(reason for verdict in screen.verdicts for reason in verdict.reasons)
    return {
        "assessed": len(screen.verdicts),
        "eligible": sum(1 for verdict in screen.verdicts if verdict.eligible),
        "survivors": len(screen.survivors),
        "reasons": {reason: named[reason] for reason in sorted(ELIGIBILITY_REASON_ORDER)},
    }


def _paper_section(
    *,
    session_index: int,
    facts: FactsDay,
    screen: ScreenResult,
    evening: EveningResult,
    catch_ups: Sequence[Mapping[str, Any]],
    ordinals: SessionOrdinals,
    book: SurvivorBookConfig,
    carried_since: Mapping[str, date],
) -> dict[str, Any]:
    settlement = evening.settlement
    rows = {item.ts_code: item for item in facts.facts}
    verdicts = {verdict.ts_code: verdict for verdict in screen.verdicts}
    tries = {item.order.order_id: item.attempts for item in settlement.live}
    plan = evening.trade_list
    opens = session_index + 1
    return {
        "trade_list": plan.model_dump(mode="json"),
        "decision": evening.decision.model_dump(mode="json"),
        "snapshot_id": evening.snapshot.snapshot_id,
        "settled": _settled(settlement),
        "book": _book(settlement, evening, rows, carried_since),
        "catch_ups": list(catch_ups),
        "outcome": evening.outcome,
        "open_session": {"index": opens, "date": _day(ordinals, opens)},
        "open_orders": [
            *(_open_order(order, rows, verdicts, ordinals, tries=None) for order in plan.orders),
            *(
                _open_order(order, rows, verdicts, ordinals, tries=_tries_of(order, tries))
                for order in plan.carried
            ),
        ],
        "illiquid_exits": _illiquid_exits(facts, screen, evening, book),
    }


def _tries_of(order: TradeOrder, tries: Mapping[str, int]) -> int:
    if order.order_id not in tries:
        raise ValueError(
            f"the trade list carries {order.order_id} ({order.side} {order.ts_code}) and the "
            "settlement does not hold it live"
        )
    return tries[order.order_id]


def _book(
    settlement: Settlement,
    evening: EveningResult,
    rows: Mapping[str, SecurityFacts],
    carried_since: Mapping[str, date],
) -> dict[str, Any]:
    """The session's figures, from the settlement -- never from `runner_record`."""
    carried = sorted(settlement.carried_marks, key=lambda mark: mark.subject)
    carried_codes = {mark.subject for mark in carried}
    decision = evening.decision
    holdings = []
    for position in sorted(settlement.book.positions, key=lambda item: item.subject):
        code = position.subject
        row = rows.get(code)
        flags = sorted(
            flag
            for flag, on in (
                ("carried", code in carried_codes),
                ("frozen", code in decision.frozen),
                ("near_exit", code in decision.near_exit),
            )
            if on
        )
        since = carried_since.get(code) if code in carried_codes else None
        mark = settlement.book.mark(code)
        holdings.append(
            {
                "ts_code": code,
                "ledger_quantity": position.quantity,
                "real_close": None if row is None else _text(row.close),
                "ledger_mark": None if mark is None else _text(mark),
                "ledger_ratio": _ratio(mark, row),
                "halted": row is not None and row.suspended,
                "flags": flags,
                "carried_since": None if since is None else since.isoformat(),
            }
        )
    return {
        "equity": _text(settlement.equity),
        "cash": _text(settlement.book.cash),
        "gross_exposure": _text(settlement.gross_exposure),
        "carried_marks": [
            {"ts_code": mark.subject, "trade_date": mark.trade_date, "price": _text(mark.price)}
            for mark in carried
        ],
        "holdings": holdings,
    }


def _ratio(mark: Decimal | None, row: SecurityFacts | None) -> str | None:
    """The ledger's mark over the facts day's real close, to four places, when they differ.

    The ledger keeps a holding's shares and mark in its own units, the real price times the
    adjustment ratio since the buy filled, so a mark that is not the real close says that ratio:
    the adjustment since the buy, cash dividends included -- Tushare's `adj_factor` moves on a
    cash dividend as on a bonus issue. It is no share count: after a cash dividend the account
    still holds the shares it bought (1000 shares bought at 25.00, 0.50 paid out, a real close of
    24.50: the ratio is 1.0204, and the account holds 1000 shares and the cash, never 1020.4), and
    its count differs from the ledger's only after a share event. `None` when they agree -- no
    event, or one that has not moved this holding -- and when either is missing.
    """
    if mark is None or row is None or mark == row.close:
        return None
    return _text((mark / row.close).quantize(_RATIO_STEP))


def _settled(settlement: Settlement) -> dict[str, Any]:
    """What a session settled: the orders that left the book, every attempt and every write-off."""
    named: dict[str, dict[str, Any]] = {}

    def note(order: TradeOrder) -> None:
        named[order.order_id] = {
            "ts_code": order.ts_code,
            "side": order.side,
            "quantity": order.quantity,
        }

    for order in (*settlement.filled, *settlement.expired, *settlement.cancelled):
        note(order)
    attempts = []
    for outcome in attempt_outcomes(settlement):
        order = outcome.attempt.order
        note(order)
        attempts.append(
            {
                "order_id": order.order_id,
                "attempt_id": outcome.attempt.attempt_id,
                "ts_code": order.ts_code,
                "side": order.side,
                "quantity": order.quantity,
                "status": outcome.status,
                "reason": outcome.reason,
            }
        )
    return {
        "filled": [order.order_id for order in settlement.filled],
        "expired": [order.order_id for order in settlement.expired],
        "cancelled": [order.order_id for order in settlement.cancelled],
        "orders": {order_id: named[order_id] for order_id in sorted(named)},
        "attempts": attempts,
        "written_off": [
            {
                "ts_code": item.ts_code,
                "order_id": item.transition.order.order_id,
                "ledger_quantity": item.transition.order.quantity,
                # The write-off recovers nothing, so what it realises is the whole cost basis.
                "cost_basis": _text(-item.transition.realized_pnl_delta),
            }
            for item in settlement.written_off
        ],
    }


def catch_up_entry(item: CatchUpResult, *, rederived: bool = False) -> dict[str, Any]:
    """One session a catch-up settled, as a document lists it: its session and ordinal, whether
    it was committed now or found settled, its snapshot, what it settled, the settlement's equity
    and cash (never the runner's record's), and whether it was `rederived` -- committed by an
    earlier run of the day, which stopped before journalling it, and derived again by this one
    (`strategy_survivor_book.catch_up_again`: `unchanged`, nothing written; D5 joint review,
    Important 3). Plain JSON, so the daily command files it in its journal as the session commits
    and hands it back as a filed catch-up."""
    settlement = item.settlement
    return {
        "session": settlement.session.isoformat(),
        "session_index": settlement.session_index,
        "outcome": item.outcome,
        "snapshot_id": item.snapshot.snapshot_id,
        "settled": _settled(settlement),
        "equity": _text(settlement.equity),
        "cash": _text(settlement.book.cash),
        "rederived": rederived,
    }


def _open_order(
    order: TradeOrder,
    rows: Mapping[str, SecurityFacts],
    verdicts: Mapping[str, EligibilityVerdict],
    ordinals: SessionOrdinals,
    *,
    tries: int | None,
) -> dict[str, Any]:
    """One order for tomorrow's open as it is printed. `tries` is how many sessions a carried order
    has been tried on, `None` for an order planned tonight."""
    reasons: list[str] = []
    if order.side == "sell":
        verdict = verdicts.get(order.ts_code)
        reasons = [_REASON_UNKNOWN] if verdict is None else list(verdict.reasons)
    row = rows.get(order.ts_code)
    return {
        "order_id": order.order_id,
        "side": order.side,
        "ts_code": order.ts_code,
        "quantity": order.quantity,
        "reference_price": _text(order.reference_price),
        "real_close": None if row is None else _text(row.close),
        "suspended": row is not None and row.suspended,
        "reason": order.reason,
        "reasons": reasons,
        "signal_session": order.signal_session,
        "signal_on": _day(ordinals, order.signal_session),
        "execute_session": order.execute_session,
        "execute_on": _day(ordinals, order.execute_session),
        "expires_session": order.expires_session,
        "expires_on": (
            None if order.expires_session is None else _day(ordinals, order.expires_session)
        ),
        "carried": tries is not None,
        "attempt": None if tries is None else tries + 1,
        "attempts_allowed": (
            None
            if order.expires_session is None
            else order.expires_session - order.execute_session + 1
        ),
    }


def _illiquid_exits(
    facts: FactsDay, screen: ScreenResult, evening: EveningResult, book: SurvivorBookConfig
) -> list[dict[str, Any]]:
    """Each exit whose verdict says `illiquid`, with its amount rank in the population the cut was
    taken over and how many of that population the cut drops."""
    illiquid = {item.ts_code for item in screen.verdicts if "illiquid" in item.reasons}
    found = []
    for exit_ in evening.decision.exits:
        if exit_.ts_code not in illiquid:
            continue
        rank, population = illiquid_rank(facts, screen, exit_.ts_code)
        found.append(
            {
                "ts_code": exit_.ts_code,
                "rank": rank,
                "population": population,
                "cut": floor_share(population, book.liquidity_drop_fraction),
            }
        )
    return found


def _day(ordinals: SessionOrdinals, index: int) -> str | None:
    """The date of session `index`, or `None` where the calendar has not reached it.

    `SessionOrdinals.day_of` refuses an ordinal past the calendar's last session with a
    `SessionOrdinalError`, a `ValueError`; it is caught as one so that this module imports nothing
    from the panel plane, and `day_of` raises nothing else for an ordinal of 1 or more.
    """
    try:
        return ordinals.day_of(index).isoformat()
    except ValueError:
        return None


def _text(value: Decimal) -> str:
    """A decimal as a plain string, never in exponent form."""
    return format(value, "f")


def _plain(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


# --- a halt's start, from the journal's history ---------------------------------------------------


def carried_since(
    carried: Collection[str],
    *,
    session: date,
    earlier: Iterable[tuple[date, Mapping[str, Any] | None]],
) -> dict[str, date]:
    """The first session of each carried holding's halt, read off the documents filed for the
    sessions before `session` -- the `carried_since` an evening's document is given.

    `carried` are the codes tonight's settlement carries (no bar tonight). `earlier` are the
    sessions before `session`, **newest first**, each with the `result` its journal filed, or
    `None` when there is none to read (missing, unreadable or incomplete). Walking back, a code's
    halt began on the session after the newest one whose document holds it with a bar, or holds
    nothing at all (the epoch's opening). A session whose document says nothing of its holdings
    -- `None`, no evening (`paper` null: settled only, or refused), or not this module's shape --
    leaves the start of every halt still open there unknown, and so does a history that ends
    before the halt does: such a code is left out, never dated by a guess (and never "1
    session"). `earlier` is read only as far back as some halt still reaches.
    """
    since = {code: session for code in carried}
    walking = set(since)
    if not walking:
        return {}
    for day, result in earlier:
        held = _carried_in(result)
        if held is None:
            for code in walking:
                del since[code]
            return dict(sorted(since.items()))
        for code in sorted(walking):
            if code in held:
                since[code] = day
            else:
                walking.discard(code)
        if not walking:
            return dict(sorted(since.items()))
    for code in walking:
        del since[code]
    return dict(sorted(since.items()))


def _carried_in(result: Mapping[str, Any] | None) -> frozenset[str] | None:
    """The codes `result`'s evening carried, an empty set for the epoch's opening (it holds
    nothing), or `None` when the document says nothing of what was held."""
    if not isinstance(result, Mapping):
        return None
    if "opening" in result:
        return frozenset()
    paper = result.get("paper")
    book = paper.get("book") if isinstance(paper, Mapping) else None
    holdings = book.get("holdings") if isinstance(book, Mapping) else None
    if not isinstance(holdings, list):
        return None
    carried: set[str] = set()
    for holding in holdings:
        code = holding.get("ts_code") if isinstance(holding, Mapping) else None
        flags = holding.get("flags") if isinstance(holding, Mapping) else None
        if not isinstance(code, str) or not isinstance(flags, list):
            return None
        if "carried" in flags:
            carried.add(code)
    return frozenset(carried)


# --- the summary --------------------------------------------------------------------------------


def summary_lines(result: Mapping[str, Any]) -> list[str]:
    """The lines the daily command prints for `result`, an `evening_document` (read back from the
    journal or not), with the caller's `prediction` and `this_run` blocks merged in if it has them,
    and its `panel` and `factors` blocks, whose catch-ups are printed after the prediction.

    Nothing is read but `result`: a document that went through JSON prints as the one that did
    not. See the module docstring for the sections and what each says.
    """
    facts = result["facts"]
    paper = result["paper"]
    skipped = result.get("paper_skipped") or {}
    lines = [
        _row(
            "session",
            f"{result['session']} (index {result['session_index']}; as of {result['as_of']})",
        ),
        _row("book", _book_text(paper, skipped)),
        *_facts_lines(facts, date.fromisoformat(result["session"])),
        *_screen_lines(result["screen"]),
    ]
    if paper is not None:
        lines += _settled_lines(paper["settled"])
        lines += _position_lines(paper["book"]["holdings"])
    lines += _orders_lines(
        paper,
        skipped,
        passed=(result.get("this_run") or {}).get("after_the_call_auction"),
        session_index=result["session_index"],
    )
    if paper is not None:
        lines += _carried_lines(paper)
        lines += _draw_lines(paper)
    days = (paper["catch_ups"] if paper is not None else skipped.get("catch_ups")) or []
    lines += _catch_up_lines(days)
    lines.append(_row("prediction", _prediction_text(result.get("prediction"))))
    lines += _built_lines(result)
    lines.append(_row("tushare requests", _requests_text(result.get("this_run"))))
    lines.append(_row("units", _UNITS))
    lines.append(_row("wording", result["wording"]))
    return lines


def _row(label: str, text: str) -> str:
    return f"{label:<{_LABEL_WIDTH}} {text}"


def _built_lines(result: Mapping[str, Any]) -> list[str]:
    """The sessions the caller's panel and factor steps caught up, when it merged them and there
    were any -- the P6 day's "factor catch-up" line, and the panel's beside it."""
    lines = []
    for label, key in (("panel catch-up", "panel"), ("factor catch-up", "factors")):
        block = result.get(key)
        caught = block.get("caught_up") if isinstance(block, Mapping) else None
        if caught:
            lines.append(_row(label, ", ".join(str(day) for day in caught)))
    return lines


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _book_text(paper: Mapping[str, Any] | None, skipped: Mapping[str, Any]) -> str:
    if paper is None:
        return f"no evening was run: {skipped['reason']}"
    book = paper["book"]
    return (
        f"equity {book['equity']}, cash {book['cash']}, gross exposure "
        f"{book['gross_exposure']}, {_plural(len(book['holdings']), 'holding')}; "
        "ledger equity is not evidence"
    )


def _facts_lines(facts: Mapping[str, Any], session: date) -> list[str]:
    notes = facts["notes"]
    coverage = facts["coverage"] if facts["coverage"] is not None else "n/a"
    boards = "/".join(facts["boards"])
    codes = notes["unrecognised_board_codes"]
    unrecognised = f"unrecognised_board {notes['unrecognised_board']}" + (
        f" [{', '.join(codes)}]" if codes else ""
    )
    lines = [
        _row(
            "facts",
            f"rows {facts['rows']}, universe on {boards} {facts['universe_size']}, scored "
            f"{facts['scored_size']} (coverage {coverage}); held {len(facts['held'])}",
        ),
        f"  no_bar_within_carry {notes['no_bar_within_carry']}, not_listed_with_bar "
        f"{notes['not_listed_with_bar']}, {unrecognised}, no_listing_row "
        f"{notes['no_listing_row']}, suspended_without_bar {notes['suspended_without_bar']}, "
        f"delisting {notes['delisting']}",
        f"  ST {notes['st']} (by name {notes['st_by_name']}, pending {notes['st_pending']}, "
        f"by band {notes['st_by_band']})",
    ]
    inert_from = date.fromisoformat(facts["band_witness"]["inert_from"])
    if notes["st_by_band"] == 0 and session >= inert_from:
        lines.append(
            f"  {_BAND_LINE} (a 0 here is the band no longer answering; the names alone say ST)"
        )
    lines.append(
        f"  name unknown -> ordinary {notes['name_unknown_assumed_ordinary']} (never renamed "
        f"{notes['name_unknown_no_record']}, before first record "
        f"{notes['name_unknown_before_first']})"
    )
    # The document carries every field of the adapter's notes; a later adapter may add one this
    # summary has no line for, and its number is printed rather than left out.
    other = sorted(name for name in notes if name not in _PRINTED_NOTES)
    if other:
        lines.append("  other notes " + ", ".join(f"{name} {notes[name]}" for name in other))
    told = [item for item in facts["held_status"] if item["status"] != "row"]
    if told:
        lines.append(
            "  held status " + ", ".join(f"{item['ts_code']} {item['status']}" for item in told)
        )
    return lines


def _screen_lines(screen: Mapping[str, Any]) -> list[str]:
    reasons = ", ".join(f"{name} {count}" for name, count in screen["reasons"].items())
    return [
        _row(
            "screen",
            f"assessed {screen['assessed']}, eligible {screen['eligible']}, survivors "
            f"{screen['survivors']}",
        ),
        f"  reasons {reasons}",
    ]


def _counts(settled: Mapping[str, Any]) -> str:
    verdicts = Counter(item["status"] for item in settled["attempts"])
    return (
        f"filled {len(settled['filled'])}, expired {len(settled['expired'])}, cancelled "
        f"{len(settled['cancelled'])}, rejected {verdicts['rejected']}, not attempted "
        f"{verdicts['not_attempted']}, written off {len(settled['written_off'])}"
    )


def _order_text(item: Mapping[str, Any]) -> str:
    """A settled order by name: a buy by its quantity, a sale by its ledger quantity."""
    if item["side"] == "sell":
        return f"{item['ts_code']} sell (ledger qty {item['quantity']})"
    return f"{item['ts_code']} buy {item['quantity']}"


def _settled_detail(settled: Mapping[str, Any], indent: str) -> list[str]:
    lines = []
    for item in settled["attempts"]:
        verdict = {
            "filled": "filled",
            "rejected": f"rejected -- {item['reason']}",
            "not_attempted": f"not attempted -- {item['reason']}",
        }[item["status"]]
        lines.append(f"{indent}{_order_text(item)}: {verdict}")
    for verb in ("expired", "cancelled"):
        lines += [f"{indent}{verb} {_order_text(settled['orders'][oid])}" for oid in settled[verb]]
    for item in settled["written_off"]:
        lines.append(
            f"{indent}{item['ts_code']} written off (ledger qty {item['ledger_quantity']}): "
            f"whole cost basis {item['cost_basis']}, no price"
        )
    return lines


def _settled_lines(settled: Mapping[str, Any]) -> list[str]:
    return [_row("settled", _counts(settled)), *_settled_detail(settled, "  ")]


def _position_lines(holdings: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        _row(
            "positions",
            f"{_plural(len(holdings), 'holding')} {_POSITIONS_NOTE}",
        )
    ]
    for item in holdings:
        close = item["real_close"] if item["real_close"] is not None else "n/a"
        text = f"  {item['ts_code']}  ledger qty {item['ledger_quantity']}  real close {close}"
        if item["ledger_ratio"] is not None:
            text += f"  ledger/real ratio {item['ledger_ratio']}"
        if item["halted"]:
            text += f" {_SUSPENDED}"
        status = []
        if "frozen" in item["flags"]:
            status.append("frozen")
        if "near_exit" in item["flags"]:
            status.append("near exit")
        if "carried" in item["flags"]:
            since = item["carried_since"]
            status.append("no bar tonight" if since is None else f"carried since {since}")
        lines.append(text + (f"  {'; '.join(status)}" if status else ""))
    return lines


def _live_through(item: Mapping[str, Any]) -> str:
    return (
        item["expires_on"]
        if item["expires_on"] is not None
        else f"session {item['expires_session']}"
    )


def _order_line(item: Mapping[str, Any]) -> str:
    """One order planned tonight. The real close is a reference, said once under the header: the
    paper book fills at the open, and a close is no limit price."""
    close = item["real_close"] if item["real_close"] is not None else "n/a"
    if item["side"] == "sell":
        why = f" ({', '.join(item['reasons'])})" if item["reasons"] else ""
        suspended = f" {_SUSPENDED}" if item["suspended"] else ""
        return (
            f"  SELL ENTIRE POSITION {item['ts_code']} (ledger qty {item['quantity']}; real "
            f"close {close}){suspended} {item['reason']}{why}"
        )
    return (
        f"  BUY {item['ts_code']} {item['quantity']} reference: real close {close} "
        f"{item['reason']}, live through {_live_through(item)}"
    )


def _planned_on(item: Mapping[str, Any]) -> str:
    """The session an order was planned on, by its date -- or its ordinal, for a document filed
    before the date was (D5 joint review, Minor 7)."""
    day = item.get("signal_on")
    return str(day) if day is not None else f"session {item['signal_session']}"


def _carried_line(item: Mapping[str, Any]) -> str:
    """One order planned on an earlier session and still live."""
    close = item["real_close"] if item["real_close"] is not None else "n/a"
    if item["side"] == "sell":
        return (
            f"  SELL ENTIRE POSITION {item['ts_code']} (ledger qty {item['quantity']}; real "
            f"close {close}) {item['reason']}, planned on {_planned_on(item)}, "
            f"attempt {item['attempt']}, a sale never expires"
        )
    return (
        f"  BUY {item['ts_code']} {item['quantity']} (planned on "
        f"{_planned_on(item)} at {item['reference_price']}; real close today {close}) "
        f"{item['reason']}, live through {_live_through(item)}, attempt {item['attempt']} of "
        f"{item['attempts_allowed']} (one per session; a session with no bar counts)"
    )


def _orders_lines(
    paper: Mapping[str, Any] | None,
    skipped: Mapping[str, Any],
    *,
    passed: Mapping[str, Any] | None,
    session_index: int,
) -> list[str]:
    """The ORDERS block. `passed` is the caller's note that the run printing the plan was made at
    or after the call auction of the session the orders are for (`this_run`'s
    `after_the_call_auction`): the header is then followed by the line that says so."""
    if paper is None:
        # "None planned tonight" is not "nothing is live": orders from earlier evenings -- a
        # carried sale above all, which never expires -- stay in the book and are retried at the
        # next open once the session is settled. This module is not told which are live. A day
        # that settled its own session alone (settle-only: it is among its catch-ups) did settle
        # tonight; a day refused before its paper book settled nothing (D5 joint review, Minor 6).
        settled = any(
            entry.get("session_index") == session_index for entry in skipped.get("catch_ups", ())
        )
        tonight = (
            "the session was settled tonight without a decision (catch-up below), and the orders "
            "from earlier evenings it left live are retried at the next open and are not listed "
            "here (a sale never expires)"
            if settled
            else "orders still live in the paper book from earlier evenings are not settled "
            "tonight and are not listed here (a sale never expires)"
        )
        return [
            f"{_ORDERS_HEADER}: NONE PLANNED tonight -- no evening was run ({skipped['reason']}); "
            + tonight
        ]
    opens = paper["open_session"]
    when = opens["date"] if opens["date"] is not None else f"session {opens['index']}"
    planned = [item for item in paper["open_orders"] if not item["carried"]]
    carried = len(paper["open_orders"]) - len(planned)
    nothing = "  no new order planned tonight" + (
        f"; {_plural(carried, 'carried order')} below" if carried else ""
    )
    state = _EVENING_STATES[paper["outcome"]]
    late = (
        []
        if not passed
        else [
            f"  the call auction of {passed['session']} has passed (it began "
            f"{passed['call_auction']}): these orders were for it, and this run was made after it "
            "-- the paper book fills them at that open whether or not they were placed"
        ]
    )
    return [
        f"{_ORDERS_HEADER}, {when} -- trade list {paper['trade_list']['trade_list_id']}, {state}",
        *late,
        _REFERENCE_NOTE,
        *(_order_line(item) for item in planned),
        *([nothing] if not planned else []),
    ]


def _carried_lines(paper: Mapping[str, Any]) -> list[str]:
    carried = [item for item in paper["open_orders"] if item["carried"]]
    if not carried:
        return [_row("carried orders", "none")]
    noun = "live order" if len(carried) == 1 else "live orders"
    return [
        _row("carried orders", f"{len(carried)} {noun} from earlier sessions"),
        *(_carried_line(item) for item in carried),
    ]


def _listed(label: str, codes: Sequence[str], *, caveat: bool = False) -> str:
    if not codes:
        return _row(label, "none")
    return _row(label, ", ".join(codes) + (f" ({_CAVEAT})" if caveat else ""))


def _draw_lines(paper: Mapping[str, Any]) -> list[str]:
    decision = paper["decision"]
    exits = paper["illiquid_exits"]
    lines = [
        _listed("unfunded", paper["trade_list"]["unfunded"], caveat=True),
        _listed("unaffordable", decision["unaffordable"], caveat=True),
        _listed("deferred exits", decision["deferred_exits"]),
        _listed("near exit", decision["near_exit"]),
        _listed("frozen", decision["frozen"]),
        _listed("watch list", decision["watch_list"], caveat=True),
    ]
    if not exits:
        return [*lines, _row("illiquid exits", "none")]
    return [
        *lines,
        _row("illiquid exits", f"{_plural(len(exits), 'exit')} cut for thin amount"),
        *(
            f"  {item['ts_code']}: amount rank {item['rank']} of population "
            f"{item['population']} (1 = thinnest; the bottom {item['cut']} are cut)"
            for item in exits
        ),
    ]


def _catch_up_lines(days: Sequence[Mapping[str, Any]]) -> list[str]:
    if not days:
        return [_row("catch-up", "none")]
    # A journal filed before the D5 fix wave has no `rederived`: none of its entries was.
    again = [str(day["session"]) for day in days if day.get("rederived")]
    named = (
        f"; re-derived after an interrupted journal: {', '.join(again)} (committed by an earlier "
        "run of the day, which stopped before journalling them; derived again now, nothing "
        "written)"
        if again
        else ""
    )
    lines = [
        _row(
            "catch-up",
            f"{_plural(len(days), 'session')} settled without a decision, oldest first{named}",
        )
    ]
    for day in days:
        lines.append(
            f"  {day['session']} (index {day['session_index']}): {_counts(day['settled'])}; "
            f"equity {day['equity']}"
        )
        lines += _settled_detail(day["settled"], "    ")
    return lines


def _prediction_text(block: Mapping[str, Any] | None) -> str:
    """What the caller filled: the record, or why there is none. Only what is there is printed."""
    if block is None:
        return "not in this document"
    if not block.get("registered", True):
        return f"none: {block.get('reason', 'no reason was given')}"
    head = " ".join(
        str(block[key]) for key in ("record_id", "family", "horizon") if block.get(key) is not None
    )
    parts = [head] if head else []
    if block.get("scored") is not None:
        parts.append(f"{block['scored']} scored")
    if block.get("standing") is not None:
        parts.append(str(block["standing"]))
    text = ", ".join(parts)
    detail = []
    if block.get("outcome") is not None:
        detail.append(str(block["outcome"]))
    if block.get("outcome_known_at") is not None:
        detail.append(f"outcome knowable {block['outcome_known_at']}")
    if detail:
        text += f" ({'; '.join(detail)})"
    return text


def _requests_text(run: Mapping[str, Any] | None) -> str:
    if run is None:
        return "not recorded in this document"
    return f"{run['request_count']} this run {json.dumps(run['requests'], sort_keys=True)}" + (
        "" if run["wrote"] else " -- the day was already complete; nothing was run"
    )
