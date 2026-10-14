"""The survivor day's morning re-check, its pure half (`V2-P7-008`, task D6).

`strategy_survivor_morning` compares the evening's plan with the facts day read again before the
next call auction: which of tonight's buys, holdings and watch-list names the overnight name corpus
has changed. It is advice only -- the paper book follows the evening's plan -- and pure: every input
is an argument, nothing is read, written, fetched or timed.

## Where the fixtures come from

Nothing here is a second frame. The facts days are `test_survivor_facts_panel.py`'s own,
imported, not copied: its `EXTENDED` panel read at the signal day's evening and read again later
-- the pair whose only difference is a code priced before its registry listing row, which the
adapter's docstring says "differ by that row and those two counts" -- and variations of the
evening's rows (a name turned special treatment, a composite sunk into the loser tail) screened by
the real kernel. The evening's journal document is hand-built to the keys the comparison reads
of `strategy_survivor_summary.evening_document`'s (`paper.book.holdings`, `paper.open_orders`,
`paper.decision.watch_list`, `paper.snapshot_id`, and the `facts` and `screen` sections, which the
real section functions give); the end-to-end file holds the real document to it.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Final

import pytest
from test_survivor_facts_panel import (
    BSE_BEFORE_LISTING,
    CONFIG,
    EXTENDED,
    LATER,
    SIGNAL,
    read_day,
    write_panel,
)

from openalpha_cn import strategy_survivor_morning as morning
from openalpha_cn import strategy_survivor_summary
from openalpha_cn.domain.survivor_book import ScreenResult, SecurityFacts
from openalpha_cn.domain.survivor_screen import screen
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.strategy_survivor_facts import FactsDay

FACTS_SECTION: Final = getattr(strategy_survivor_summary, "_facts_section")  # noqa: B009
SCREEN_SECTION: Final = getattr(strategy_survivor_summary, "_screen_section")  # noqa: B009
SNAPSHOT: Final[str] = "snapshot_0000000000000001"
NO_CHANGE: Final[str] = "no overnight change to tonight's plan"


@pytest.fixture(scope="module")
def extended_store(tmp_path_factory: pytest.TempPathFactory) -> PanelStore:
    return write_panel(tmp_path_factory.mktemp("morning-extended"), EXTENDED)


@dataclasses.dataclass(frozen=True)
class Day:
    """One facts day with its screen, as the daily command holds the pair."""

    facts: FactsDay
    screen: ScreenResult


def _day(facts: FactsDay) -> Day:
    return Day(facts=facts, screen=screen(facts.facts, CONFIG))


@pytest.fixture(scope="module")
def evening(extended_store: PanelStore) -> Day:
    """The signal day as the evening read it: a code priced before its listing row has a row aged
    0, and counts in `no_listing_row`."""
    return _day(read_day(extended_store))


@pytest.fixture(scope="module")
def later(extended_store: PanelStore) -> Day:
    """The same signal day read after the registry's row for that code is published: it has no
    row, and counts in `not_listed_with_bar`."""
    return _day(read_day(extended_store, as_of=LATER))


def _survivors(day: Day) -> list[str]:
    return [name.ts_code for name in day.screen.survivors]


def _turn(day: Day, code: str, **changes: Any) -> Day:
    """`day` with one row changed, screened again -- and, for a special-treatment flip, the
    notes' ST counts moved with it (they are checked to add up)."""
    rows = tuple(
        row.model_copy(update=changes) if row.ts_code == code else row for row in day.facts.facts
    )
    notes = day.facts.notes
    if changes.get("special_treatment") is True:
        notes = dataclasses.replace(notes, st=notes.st + 1, st_by_name=notes.st_by_name + 1)
    return _day(dataclasses.replace(day.facts, facts=rows, notes=notes))


def _sink(day: Day, code: str) -> Day:
    """`code`'s composite sunk below every other name's: it stays eligible and leaves the
    survivors, into the loser tail."""
    row = next(row for row in day.facts.facts if row.ts_code == code)
    return _turn(day, code, oriented_values=tuple(-9.0 for _ in row.oriented_values))


def _order(code: str, side: str, *, carried: bool = False) -> dict[str, Any]:
    return {"side": side, "ts_code": code, "carried": carried, "order_id": f"order_{code}"}


def _evening_document(
    day: Day,
    *,
    holdings: Sequence[str] = (),
    sells: Sequence[str] = (),
    buys: Sequence[str] = (),
    carried: Sequence[str] = (),
    watch: Sequence[str] = (),
) -> dict[str, Any]:
    """The keys of an evening's `result` the comparison reads, with the real `facts` and `screen`
    sections of `day`."""
    return {
        "kind": "survivor_book/v1",
        "session": day.facts.session.isoformat(),
        "facts": FACTS_SECTION(day.facts, day.facts.session),
        "screen": SCREEN_SECTION(day.screen),
        "paper": {
            "snapshot_id": SNAPSHOT,
            "book": {"holdings": [{"ts_code": code, "flags": []} for code in holdings]},
            "open_orders": [
                *(_order(code, "sell") for code in sells),
                *(_order(code, "buy") for code in buys),
                *(_order(code, "buy", carried=True) for code in carried),
            ],
            "decision": {"watch_list": list(watch)},
        },
    }


def _compare(
    evening_day: Day, document: Mapping[str, Any], morning_day: Day
) -> morning.MorningAdvice:
    names = morning.plan_names(document)
    return morning.compare_morning(
        evening=document,
        before=morning.read_facts(evening_day.facts, evening_day.screen, names.watched()),
        facts=morning_day.facts,
        screen=morning_day.screen,
    )


def _texts(advice: morning.MorningAdvice) -> list[str]:
    return [item.text for item in advice.differences]


def test_a_plan_the_overnight_names_did_not_touch_has_no_difference(evening: Day) -> None:
    top = _survivors(evening)
    document = _evening_document(
        evening, holdings=top[:3], buys=top[3:5], carried=top[5:6], watch=top[6:8]
    )

    advice = _compare(evening, document, evening)

    assert advice.differences == ()
    assert advice.tolerated == ()
    lines = morning.morning_lines(
        morning.morning_document(_pin(), advice),
        this_run={"requests": {"namechange": 1}, "request_count": 1, "wrote": True},
    )
    assert any(NO_CHANGE in line for line in lines)


def _pin() -> dict[str, Any]:
    return {
        "schema": morning.MORNING_SCHEMA,
        "session": SIGNAL.isoformat(),
        "as_of": "2026-04-13T08:30:00+08:00",
        "held": [],
        "evening_snapshot_id": SNAPSHOT,
        "requests": {"namechange": 1},
    }


def test_a_planned_buy_that_turned_special_treatment_is_not_to_be_placed(evening: Day) -> None:
    top = _survivors(evening)
    bought = top[3]
    document = _evening_document(evening, buys=[bought, top[4]])

    advice = _compare(evening, document, _turn(evening, bought, special_treatment=True))

    assert [item.kind for item in advice.differences if item.ts_codes == (bought,)] == [
        "do_not_place_buy"
    ]
    assert f"do not place BUY {bought}: special_treatment" in _texts(advice)
    # The other buy is untouched.
    assert not any(top[4] in text for text in _texts(advice) if text.startswith("do not place"))


def test_a_carried_buy_that_turned_special_treatment_is_not_to_be_placed_either(
    evening: Day,
) -> None:
    carried = _survivors(evening)[5]
    document = _evening_document(evening, carried=[carried])

    advice = _compare(evening, document, _turn(evening, carried, special_treatment=True))

    (item,) = (item for item in advice.differences if item.kind == "do_not_place_buy")
    assert item.ts_codes == (carried,)
    assert item.text.startswith(f"do not place BUY {carried}: special_treatment")
    assert "carried" in item.text


def test_a_buy_that_stays_eligible_and_drops_out_of_the_survivors_is_not_to_be_placed(
    evening: Day,
) -> None:
    bought = _survivors(evening)[2]
    document = _evening_document(evening, buys=[bought])

    advice = _compare(evening, document, _sink(evening, bought))

    assert f"do not place BUY {bought}: no longer a survivor" in _texts(advice)


def test_a_buy_that_was_already_ineligible_at_the_evening_is_no_overnight_change(
    evening: Day,
) -> None:
    """A carried buy whose name was halted already at the evening is not news this morning."""
    halted = _turn(evening, _survivors(evening)[4], suspended=True)
    code = _survivors(evening)[4]
    document = _evening_document(halted, carried=[code])

    advice = _compare(halted, document, _turn(halted, code, special_treatment=True))

    assert [item for item in advice.differences if item.kind == "do_not_place_buy"] == []


def test_a_holding_that_turned_special_treatment_is_sold_at_the_next_evening(
    evening: Day,
) -> None:
    held = _survivors(evening)[0]
    document = _evening_document(evening, holdings=[held])

    advice = _compare(evening, document, _turn(evening, held, special_treatment=True))

    assert (
        f"holding {held} turned special_treatment: the paper book sells it at the next evening"
        in _texts(advice)
    )
    assert [item.kind for item in advice.differences if item.ts_codes == (held,)] == [
        "holding_turned"
    ]


def test_a_holding_that_was_suspended_overnight_is_frozen_not_sold(evening: Day) -> None:
    held = _survivors(evening)[0]
    document = _evening_document(evening, holdings=[held])

    advice = _compare(evening, document, _turn(evening, held, suspended=True))

    (item,) = (item for item in advice.differences if item.kind == "holding_turned")
    assert item.text.startswith(f"holding {held} turned suspended")
    assert "freezes" in item.text and "sells" not in item.text


def test_a_holding_the_evening_already_sells_is_not_advised_about(evening: Day) -> None:
    held = _survivors(evening)[0]
    document = _evening_document(evening, holdings=[held], sells=[held])

    advice = _compare(evening, document, _turn(evening, held, special_treatment=True))

    assert [item for item in advice.differences if item.kind == "holding_turned"] == []


def test_a_holding_that_left_the_facts_is_told_as_not_assessed(evening: Day) -> None:
    held = _survivors(evening)[0]
    document = _evening_document(evening, holdings=[held])
    rows = tuple(row for row in evening.facts.facts if row.ts_code != held)
    on_boards = [row for row in rows if row.board in evening.facts.boards]
    complete = sum(1 for row in on_boards if None not in row.oriented_values)
    gone = _day(
        dataclasses.replace(
            evening.facts,
            facts=rows,
            universe_size=len(on_boards),
            scored_size=min(evening.facts.scored_size, complete),
        )
    )

    advice = _compare(evening, document, gone)

    assert f"holding {held} turned not_assessed" in " ".join(_texts(advice))


def test_a_watch_list_name_that_turned_special_treatment_changes_the_watch_list(
    evening: Day,
) -> None:
    top = _survivors(evening)
    watched = top[6]
    document = _evening_document(evening, watch=[top[6], top[7]])

    advice = _compare(evening, document, _turn(evening, watched, special_treatment=True))

    (item,) = (item for item in advice.differences if item.kind == "watch_list_changed")
    assert item.ts_codes == (watched,)
    assert item.text.startswith("watch list changed")
    assert watched in item.text and top[7] not in item.text


def test_a_name_outside_the_plan_that_turned_special_treatment_moves_only_the_counts(
    evening: Day,
) -> None:
    top = _survivors(evening)
    outsider = top[9]
    document = _evening_document(evening, holdings=top[:3], buys=top[3:5])

    advice = _compare(evening, document, _turn(evening, outsider, special_treatment=True))

    assert [item.kind for item in advice.differences] == ["counts_changed"]
    (item,) = advice.differences
    # The name turned ineligible, and the liquidity cut over the names that pass every static
    # reason moved with it, so the eligible count itself may or may not have changed; the ST count
    # of the name corpus certainly did.
    before, after = evening.facts.notes.st, evening.facts.notes.st + 1
    assert f"ST {before} -> {after}" in item.text


def test_a_code_priced_before_its_listing_row_is_tolerated_and_named(
    evening: Day, later: Day
) -> None:
    """The two reads of one signal day differ by the row of a code priced before its listing and by
    two counts, which add up: the code is listed under `tolerated`, never as advice."""
    top = _survivors(evening)
    document = _evening_document(evening, holdings=top[:3], buys=top[3:5], watch=top[5:7])
    assert BSE_BEFORE_LISTING in {row.ts_code for row in evening.facts.facts}
    assert BSE_BEFORE_LISTING not in {row.ts_code for row in later.facts.facts}

    advice = _compare(evening, document, later)

    assert advice.differences == ()
    assert advice.tolerated == (BSE_BEFORE_LISTING,)


def test_a_row_that_vanished_and_is_not_what_the_counts_say_is_advice_not_tolerance(
    evening: Day, later: Day
) -> None:
    """The same vanished row, with the counts not moving as the class says they do: nothing says
    it is the listing-row class, so it is not tolerated -- it is reported."""
    top = _survivors(evening)
    document = _evening_document(evening, holdings=top[:3])
    skewed = dataclasses.replace(
        later.facts,
        notes=dataclasses.replace(
            later.facts.notes, not_listed_with_bar=later.facts.notes.not_listed_with_bar + 1
        ),
    )

    advice = _compare(evening, document, Day(facts=skewed, screen=later.screen))

    assert advice.tolerated == ()
    (item,) = (item for item in advice.differences if item.kind == "row_gone")
    assert item.ts_codes == (BSE_BEFORE_LISTING,)


def test_the_advice_and_the_read_are_plain_json_and_frozen(evening: Day, later: Day) -> None:
    top = _survivors(evening)
    document = _evening_document(evening, holdings=top[:2], buys=top[2:4])
    advice = _compare(evening, document, _turn(later, top[2], special_treatment=True))
    read = morning.read_facts(evening.facts, evening.screen, morning.plan_names(document).watched())

    assert json.loads(json.dumps(advice.to_json())) == advice.to_json()
    assert morning.FactsRead.from_json(json.loads(json.dumps(read.to_json()))) == read
    with pytest.raises(dataclasses.FrozenInstanceError):
        advice.tolerated = ()  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        read.aged_zero = ()  # type: ignore[misc]


def test_the_read_names_the_codes_with_a_row_aged_zero(evening: Day) -> None:
    read = morning.read_facts(evening.facts, evening.screen, ())

    zero = {row.ts_code for row in evening.facts.facts if row.listed_sessions == 0}
    assert BSE_BEFORE_LISTING in zero
    assert read.aged_zero == tuple(sorted(zero))


def test_the_evening_read_is_checked_against_the_evenings_own_document(evening: Day) -> None:
    top = _survivors(evening)
    document = _evening_document(evening, holdings=top[:2])
    names = morning.plan_names(document).watched()

    morning.check_evening_read(morning.read_facts(evening.facts, evening.screen, names), document)

    drifted = _turn(evening, top[4], special_treatment=True)
    with pytest.raises(morning.EveningReadDriftError) as caught:
        morning.check_evening_read(
            morning.read_facts(drifted.facts, drifted.screen, names), document
        )
    assert "notes.st" in str(caught.value)


def test_a_document_with_no_evening_has_no_plan_to_check() -> None:
    with pytest.raises(morning.MorningCheckError, match="no evening"):
        morning.plan_names({"kind": "survivor_book/v1", "paper": None})
    with pytest.raises(morning.MorningCheckError, match="opens the paper book"):
        morning.plan_names({"kind": "survivor_book/v1", "opening": {"cash": "1"}})


def test_the_plan_is_read_off_the_evenings_orders_book_and_decision(evening: Day) -> None:
    top = _survivors(evening)
    document = _evening_document(
        evening, holdings=top[:3], sells=top[:1], buys=top[3:5], carried=top[5:6], watch=top[6:8]
    )

    names = morning.plan_names(document)

    assert names.holdings == tuple(sorted(top[:3]))
    assert names.selling == tuple(sorted(top[:1]))
    assert names.planned_buys == tuple(sorted(top[3:5]))
    assert names.carried_buys == tuple(sorted(top[5:6]))
    assert names.watch_list == tuple(sorted(top[6:8]))
    assert names.watched() == tuple(sorted(top[:8]))


@pytest.mark.parametrize(
    ("session", "as_of", "years"),
    [
        (date(2026, 4, 6), "2026-04-07T08:30:00+08:00", (2026,)),
        (date(2026, 12, 30), "2026-12-31T08:30:00+08:00", (2026,)),
        (date(2026, 12, 31), "2026-12-31T23:30:00+08:00", (2026,)),
        (date(2026, 12, 31), "2027-01-02T08:30:00+08:00", (2026, 2027)),
        (date(2026, 12, 31), "2027-01-01T00:10:00+08:00", (2026, 2027)),
        (date(2026, 12, 31), "2026-12-31T16:00:00+00:00", (2026, 2027)),
    ],
)
def test_the_morning_asks_the_sessions_year_and_the_mornings_when_it_differs(
    session: date, as_of: str, years: tuple[int, ...]
) -> None:
    """The name corpus is filed by announcement year: a morning in the new year asks the old
    year's too (an announcement of the evening) -- and the year is the Shanghai calendar's, which a
    16:00 UTC instant of 31 December is already past."""
    assert morning.fetch_years(session, datetime.fromisoformat(as_of)) == years


def test_the_document_carries_the_pin_the_advice_and_the_wording(evening: Day) -> None:
    top = _survivors(evening)
    document = _evening_document(evening, buys=top[:2])
    advice = _compare(evening, document, _turn(evening, top[0], special_treatment=True))

    filed = morning.morning_document(_pin(), advice)

    assert filed["schema"] == "openalpha-daily-survivor-morning/v1"
    assert {
        "as_of",
        "session",
        "held",
        "evening_snapshot_id",
        "differences",
        "tolerated",
        "requests",
    } <= set(filed)
    assert [item["text"] for item in filed["differences"]] == _texts(advice)
    assert "candidate" in filed["wording"] and "no order was placed" in filed["wording"]
    assert json.loads(json.dumps(filed)) == filed
    lines = morning.morning_lines(
        filed, this_run={"requests": {"namechange": 1}, "request_count": 1, "wrote": True}
    )
    assert any(f"do not place BUY {top[0]}" in line for line in lines)
    assert not any(NO_CHANGE in line for line in lines)
    assert any("tushare requests" in line and '"namechange": 1' in line for line in lines)


def test_the_listing_row_is_a_row_of_the_frame_the_adapter_says_it_is(evening: Day) -> None:
    fact: SecurityFacts = {row.ts_code: row for row in evening.facts.facts}[BSE_BEFORE_LISTING]
    assert (fact.board, fact.listed_sessions) == ("bse", 0)
    assert evening.facts.session == SIGNAL
