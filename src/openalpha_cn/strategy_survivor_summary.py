"""The survivor evening's record and its human summary (`V2-P7-008`, task D4).

`evening_document` turns what one survivor evening produced -- the facts day, the kernel's screen
of it, the paper book's evening and the sessions it caught up -- into the JSON document the daily
journal files as its `result`; `summary_lines` turns that document, and nothing else, into the
lines the daily command prints. Both are pure: every input is an argument, nothing is read,
written, fetched or timed, and the same inputs give the same bytes (no set is iterated unsorted,
every mapping is built in sorted order, no clock is asked).

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

**The book's figures are the settlement's.** `Settlement.book` is the book after the close and
after the write-offs; `Settlement.runner_record` is the runner's trace from before both, and its
`equity` overstates a write-off day by the whole of what was written off. The document reads
`equity`, `cash`, `gross_exposure` and `carried_marks` from the settlement and never from the
runner's record (`CarriedMark.sessions_carried` is always 1 there -- each `advance` is a
one-session run -- so the document does not carry it: the length of a halt is the journal
history's, which the caller passes as `carried_since`).

**Units.** A holding's quantity and a sale's `reference_price` are the ledger's, in the ledger's
units (the real price times the adjustment ratio since the buy filled); after a bonus issue or a
cash dividend the ledger's share count is not the account's. So a sale is always "SELL ENTIRE
POSITION", its ledger quantity printed beside the REAL close of the facts day and never as a
number of shares to sell. A buy is at its real close with ratio 1, so its quantity is real.

## The summary

Sections, in this order: session, book, facts, screen, settled, positions, the ORDERS FOR THE OPEN
OF D+1 (call auction 09:15) and the carried orders, what the draw left out (unfunded,
unaffordable, deferred exits, near exit, frozen, watch list), illiquid exits, catch-up, prediction,
Tushare requests, units, wording. A list with nothing in it prints as `none`, so a missing line
never reads as "nothing to do". A day with no evening prints `NONE PLANNED -- no evening was run`
and the reason, never an empty plan. The wording is "candidates"; nothing here is a
recommendation, and the ledger's equity is never evidence of anything.

`prediction` and `this_run` are blocks the caller merges into the mapping it passes to
`summary_lines` (they are not part of the journal's `result`: the prediction is filed before the
paper step, and the requests are the run's own); the summary renders what is there.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
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
) -> dict[str, Any]:
    """The `result` document of the survivor evening of `session` (ordinal `session_index`), read
    at `as_of`; see the module docstring.

    `evening` is the paper book's answer, or `None` for a session that ran no evening, in which
    case `paper_skipped` is the reason the caller gives. `catch_ups` are the sessions settled
    before it -- or, with no evening, the sessions settled instead of it -- oldest first.
    `ordinals` turns the orders' sessions into dates, and `book` is the configuration whose
    liquidity cut an illiquid exit is reported against. `carried_since` is the first session each
    carried holding had no bar, from the journal's history: an entry for a name that is not
    carried tonight is ignored, and no entry is a halt of unknown length, never of length 1.

    A facts day or an evening that is not of `session`, a `paper_skipped` given beside an evening,
    and catch-ups out of order or not before the evening are refused by name: the document would
    describe another day than the one it is filed under.
    """
    _check(
        session=session,
        session_index=session_index,
        facts=facts,
        evening=evening,
        catch_ups=catch_ups,
        paper_skipped=paper_skipped,
    )
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
        document["paper_skipped"] = {
            "reason": paper_skipped,
            "catch_ups": [_catch_up(item) for item in catch_ups],
        }
    else:
        document["paper"] = _paper_section(
            session_index=session_index,
            facts=facts,
            screen=screen,
            evening=evening,
            catch_ups=catch_ups,
            ordinals=ordinals,
            book=book,
            carried_since=carried_since or {},
        )
    document["wording"] = SURVIVOR_WORDING
    return document


def _check(
    *,
    session: date,
    session_index: int,
    facts: FactsDay,
    evening: EveningResult | None,
    catch_ups: Sequence[CatchUpResult],
    paper_skipped: str | None,
) -> None:
    """Refuse, by name, a document whose parts are not of the day it is filed under."""
    if facts.session != session:
        raise ValueError(
            f"the facts day is of session {facts.session.isoformat()}, not "
            f"{session.isoformat()}: the document would describe another day"
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
    indices = [item.settlement.session_index for item in catch_ups]
    if indices != sorted(set(indices)):
        raise ValueError(f"catch-ups must be given oldest first, each session once, not {indices}")
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
    catch_ups: Sequence[CatchUpResult],
    ordinals: SessionOrdinals,
    book: SurvivorBookConfig,
    carried_since: Mapping[str, date],
) -> dict[str, Any]:
    settlement = evening.settlement
    rows = {item.ts_code: item for item in facts.facts}
    closes = {code: item.close for code, item in rows.items()}
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
        "catch_ups": [_catch_up(item) for item in catch_ups],
        "outcome": evening.outcome,
        "open_session": {"index": opens, "date": _day(ordinals, opens)},
        "open_orders": [
            *(_open_order(order, closes, verdicts, ordinals, tries=None) for order in plan.orders),
            *(
                _open_order(order, closes, verdicts, ordinals, tries=_tries_of(order, tries))
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
    adjustment ratio since the buy filled, so a mark that is not the real close says the ratio
    the account's shares are the ledger's quantity times. `None` when they agree -- no event, or
    one that has not moved this holding -- and when either is missing.
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


def _catch_up(item: CatchUpResult) -> dict[str, Any]:
    settlement = item.settlement
    return {
        "session": settlement.session.isoformat(),
        "session_index": settlement.session_index,
        "outcome": item.outcome,
        "snapshot_id": item.snapshot.snapshot_id,
        "settled": _settled(settlement),
        "equity": _text(settlement.equity),
        "cash": _text(settlement.book.cash),
    }


def _open_order(
    order: TradeOrder,
    closes: Mapping[str, Decimal],
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
    return {
        "order_id": order.order_id,
        "side": order.side,
        "ts_code": order.ts_code,
        "quantity": order.quantity,
        "reference_price": _text(order.reference_price),
        "real_close": _text(closes[order.ts_code]) if order.ts_code in closes else None,
        "reason": order.reason,
        "reasons": reasons,
        "signal_session": order.signal_session,
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


# --- the summary --------------------------------------------------------------------------------


def summary_lines(result: Mapping[str, Any]) -> list[str]:
    """The lines the daily command prints for `result`, an `evening_document` (read back from the
    journal or not), with the caller's `prediction` and `this_run` blocks merged in if it has them.

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
    lines += _orders_lines(paper, skipped)
    if paper is not None:
        lines += _carried_lines(paper)
        lines += _draw_lines(paper)
    days = (paper["catch_ups"] if paper is not None else skipped.get("catch_ups")) or []
    lines += _catch_up_lines(days)
    lines.append(_row("prediction", _prediction_text(result.get("prediction"))))
    lines.append(_row("tushare requests", _requests_text(result.get("this_run"))))
    lines.append(_row("units", _UNITS))
    lines.append(_row("wording", result["wording"]))
    return lines


def _row(label: str, text: str) -> str:
    return f"{label:<{_LABEL_WIDTH}} {text}"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _book_text(paper: Mapping[str, Any] | None, skipped: Mapping[str, Any]) -> str:
    if paper is None:
        reason = skipped.get("reason") or "no reason was given"
        return f"no evening was run: {reason}"
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
            f"{_plural(len(holdings), 'holding')} (ledger quantity in ledger units; real close "
            "from the facts; where a ratio is shown, account shares = ledger qty x ratio)",
        )
    ]
    for item in holdings:
        close = item["real_close"] if item["real_close"] is not None else "n/a"
        text = f"  {item['ts_code']}  ledger qty {item['ledger_quantity']}  real close {close}"
        if item["ledger_ratio"] is not None:
            text += f"  ledger/real ratio {item['ledger_ratio']}"
        if item["halted"]:
            text += " (suspended: the close may be an earlier session's)"
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
    """One order planned tonight, as it is to be placed at the open."""
    close = item["real_close"] if item["real_close"] is not None else "n/a"
    if item["side"] == "sell":
        why = f" ({', '.join(item['reasons'])})" if item["reasons"] else ""
        return (
            f"  SELL ENTIRE POSITION {item['ts_code']} (ledger qty {item['quantity']}; real "
            f"close {close}) {item['reason']}{why}"
        )
    return (
        f"  BUY {item['ts_code']} {item['quantity']} @ real close {close} {item['reason']}, "
        f"live through {_live_through(item)}"
    )


def _carried_line(item: Mapping[str, Any]) -> str:
    """One order planned on an earlier session and still live."""
    close = item["real_close"] if item["real_close"] is not None else "n/a"
    if item["side"] == "sell":
        return (
            f"  SELL ENTIRE POSITION {item['ts_code']} (ledger qty {item['quantity']}; real "
            f"close {close}) {item['reason']}, planned on session {item['signal_session']}, "
            f"attempt {item['attempt']}, a sale never expires"
        )
    return (
        f"  BUY {item['ts_code']} {item['quantity']} (planned on session "
        f"{item['signal_session']} at {item['reference_price']}; real close today {close}) "
        f"{item['reason']}, live through {_live_through(item)}, attempt {item['attempt']} of "
        f"{item['attempts_allowed']}"
    )


def _orders_lines(paper: Mapping[str, Any] | None, skipped: Mapping[str, Any]) -> list[str]:
    if paper is None:
        reason = skipped.get("reason") or "no reason was given"
        return [f"{_ORDERS_HEADER}: NONE PLANNED -- no evening was run ({reason})"]
    opens = paper["open_session"]
    when = opens["date"] if opens["date"] is not None else f"session {opens['index']}"
    planned = [item for item in paper["open_orders"] if not item["carried"]]
    return [
        f"{_ORDERS_HEADER}, {when}",
        *(_order_line(item) for item in planned),
        *(["  no new order planned tonight"] if not planned else []),
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
    lines = [
        _row(
            "catch-up",
            f"{_plural(len(days), 'session')} settled without a decision, oldest first",
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
