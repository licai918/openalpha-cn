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
import fnmatch
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
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
from openalpha_cn.domain.name_history import NameHistory, NameRecord, build_name_history
from openalpha_cn.domain.survivor_book import ScreenResult, SecurityFacts
from openalpha_cn.domain.survivor_screen import screen
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.strategy_survivor_facts import FactsDay

FACTS_SECTION: Final = getattr(strategy_survivor_summary, "_facts_section")  # noqa: B009
SCREEN_SECTION: Final = getattr(strategy_survivor_summary, "_screen_section")  # noqa: B009
SNAPSHOT: Final[str] = "snapshot_0000000000000001"
NO_CHANGE: Final[str] = (
    f"no change to the plan of {SIGNAL.isoformat()} among announcements dated through "
    f"{SIGNAL.isoformat()}"
)
"""The advice line of a morning with no difference: its scope is in the line itself (review
Important 2) -- what the facts adapter screens is the name corpus as announced by the session --
and the plan is named by its session, never "tonight's" at 08:30 (whole-branch review M4)."""
UNSCOPED: Final[str] = "no overnight change"
"""The old advice line, which a pre-open announcement dated after the session made false."""
TONIGHT: Final[str] = "tonight's plan"
"""The old name of the plan, false at 08:30 of the morning after (whole-branch review M4)."""
ADVICE: Final[str] = f"{'advice':<18} "
NOTICES: Final[str] = f"{'notices':<18} "
COUNTS: Final[str] = f"{'counts':<18} "
HEADER: Final[str] = f"{'morning re-check':<18} "
EARLIER: Final[str] = f"{'earlier check':<18} "
RUN: Final[dict[str, Any]] = {"requests": {"namechange": 1}, "request_count": 1, "wrote": True}


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
        morning.morning_document(_pin(), advice, notices=()), this_run=RUN
    )
    assert f"{ADVICE}{NO_CHANGE}" in lines
    assert not any(UNSCOPED in line or TONIGHT in line for line in lines)
    assert not any(line.startswith(COUNTS) for line in lines)


def _pin(**changes: Any) -> dict[str, Any]:
    return {
        "schema": morning.MORNING_SCHEMA,
        "session": SIGNAL.isoformat(),
        "as_of": "2026-04-13T08:30:00+08:00",
        "held": [],
        "evening_snapshot_id": SNAPSHOT,
        "carried_from": None,
        "requests": {"namechange": 1},
        **changes,
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


def test_a_holding_that_stays_eligible_and_leaves_the_survivors_is_sold_at_the_next_evening(
    evening: Day,
) -> None:
    """The policy sells a holding that drops out of the survivors (`EXIT_SURVIVOR_LOST`) -- the
    worst composite first, at most `max_replacements_per_session` a day -- so the morning says so,
    the same change a planned buy is told "no longer a survivor" for (review Minor 4)."""
    held = _survivors(evening)[0]
    document = _evening_document(evening, holdings=[held])

    advice = _compare(evening, document, _sink(evening, held))

    (item,) = (item for item in advice.differences if item.ts_codes == (held,))
    assert item.kind == "holding_left_survivors"
    assert item.reasons == ("no longer a survivor",)
    assert item.text == (
        f"holding {held} left the survivors (still eligible): the paper book sells it at the next "
        "evening, unless that evening's replacement cap defers it"
    )


def test_a_holding_the_evening_already_sells_is_not_told_it_left_the_survivors(
    evening: Day,
) -> None:
    held = _survivors(evening)[0]
    document = _evening_document(evening, holdings=[held], sells=[held])

    advice = _compare(evening, document, _sink(evening, held))

    assert [item for item in advice.differences if item.ts_codes == (held,)] == []


def test_a_holding_frozen_at_the_evening_and_still_frozen_this_morning_is_no_difference(
    evening: Day,
) -> None:
    """A difference is a change: a holding the evening found suspended -- and froze -- that is
    suspended this morning too has nothing new to say."""
    held = _survivors(evening)[0]
    halted = _turn(evening, held, suspended=True)
    document = _evening_document(halted, holdings=[held])

    advice = _compare(halted, document, halted)

    assert [item for item in advice.differences if item.ts_codes == (held,)] == []


def test_a_frozen_holding_that_turned_special_treatment_is_told_only_what_is_new(
    evening: Day,
) -> None:
    held = _survivors(evening)[0]
    halted = _turn(evening, held, suspended=True)
    document = _evening_document(halted, holdings=[held])

    advice = _compare(halted, document, _turn(halted, held, special_treatment=True))

    (item,) = (item for item in advice.differences if item.kind == "holding_turned")
    assert item.reasons == ("special_treatment",)
    assert item.text == (
        f"holding {held} turned special_treatment: the paper book sells it at the next evening"
    )


def test_a_holding_suspended_and_turned_special_treatment_overnight_is_sold_not_frozen(
    evening: Day,
) -> None:
    """The freeze is the policy's: every reason must follow from the suspension
    (`FREEZE_COMPATIBLE_REASONS`). Special treatment does not, so a halted ST holding is sold."""
    held = _survivors(evening)[0]
    document = _evening_document(evening, holdings=[held])
    turned = _turn(evening, held, suspended=True, special_treatment=True)

    advice = _compare(evening, document, turned)

    (item,) = (item for item in advice.differences if item.kind == "holding_turned")
    assert {"suspended", "special_treatment"} <= set(item.reasons)
    assert item.text.endswith(": the paper book sells it at the next evening")
    assert "freezes" not in item.text


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
    """The counts are information, not a difference (whole-branch review M4): a name outside the
    plan turned ST changes nothing the user is to do, so the advice is "no change" and the counts
    are a line of their own, never counted among the differences."""
    top = _survivors(evening)
    outsider = top[9]
    document = _evening_document(evening, holdings=top[:3], buys=top[3:5])

    advice = _compare(evening, document, _turn(evening, outsider, special_treatment=True))

    assert advice.differences == ()
    # The name turned ineligible, and the liquidity cut over the names that pass every static
    # reason moved with it, so the eligible count itself may or may not have changed; the ST count
    # of the name corpus certainly did.
    before, after = evening.facts.notes.st, evening.facts.notes.st + 1
    assert f"ST {before} -> {after}" in advice.counts
    filed = morning.morning_document(_pin(), advice, notices=())
    assert filed["differences"] == [] and filed["counts_changed"] == list(advice.counts)
    lines = morning.morning_lines(filed, this_run=RUN)
    assert f"{ADVICE}{NO_CHANGE}" in lines
    (counts,) = (line for line in lines if line.startswith(COUNTS))
    assert f"ST {before} -> {after}" in counts
    assert "information" in counts and "not advice" in counts
    assert not any("difference(s)" in line for line in lines)


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


def test_a_read_whose_planned_names_are_not_survivors_is_drift_though_every_count_agrees(
    evening: Day,
) -> None:
    """The counts cannot see a swap that keeps them all: a planned buy sunk out of the survivors
    and the next name moved in. Every planned buy, carried buy and watch-list name of the evening
    was a survivor of its screen -- the policy draws them from the survivors, and settlement
    cancels a live buy outside them -- so a read in which one is not is not the evening's
    (review Minor 11)."""
    top = _survivors(evening)
    swapped = _sink(evening, top[3])
    unplanned = _evening_document(evening, holdings=top[:2])
    names = morning.plan_names(unplanned).watched()
    # The counts agree: the aggregate check alone takes the swapped read for the evening's.
    morning.check_evening_read(morning.read_facts(swapped.facts, swapped.screen, names), unplanned)

    for planned in (
        _evening_document(evening, buys=[top[3], top[4]]),
        _evening_document(evening, carried=[top[3]]),
        _evening_document(evening, watch=[top[3], top[4]]),
    ):
        names = morning.plan_names(planned).watched()
        morning.check_evening_read(
            morning.read_facts(evening.facts, evening.screen, names), planned
        )
        with pytest.raises(morning.EveningReadDriftError) as caught:
            morning.check_evening_read(
                morning.read_facts(swapped.facts, swapped.screen, names), planned
            )
        assert top[3] in str(caught.value)
        assert top[4] not in str(caught.value)


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


def test_the_document_carries_the_pin_the_advice_and_the_wording(evening: Day) -> None:
    top = _survivors(evening)
    document = _evening_document(evening, buys=top[:2])
    advice = _compare(evening, document, _turn(evening, top[0], special_treatment=True))

    filed = morning.morning_document(_pin(), advice, notices=())

    assert filed["schema"] == "openalpha-daily-survivor-morning/v1"
    assert {
        "as_of",
        "session",
        "held",
        "evening_snapshot_id",
        "carried_from",
        "differences",
        "tolerated",
        "notices",
        "holdings_compared",
        "counts_changed",
        "requests",
    } <= set(filed)
    assert [item["text"] for item in filed["differences"]] == _texts(advice)
    assert "candidate" in filed["wording"] and "no order was placed" in filed["wording"]
    assert json.loads(json.dumps(filed)) == filed
    lines = morning.morning_lines(filed, this_run=RUN)
    assert any(f"do not place BUY {top[0]}" in line for line in lines)
    assert (
        f"{ADVICE}{len(advice.differences)} difference(s) from the plan of {SIGNAL.isoformat()} "
        f"among announcements dated through {SIGNAL.isoformat()}"
    ) in lines
    assert not any(NO_CHANGE in line or UNSCOPED in line or TONIGHT in line for line in lines)
    assert any("tushare requests" in line and '"namechange": 1' in line for line in lines)


def test_the_header_names_the_plan_by_its_session_and_the_shanghai_date_the_check_was_read(
    evening: Day,
) -> None:
    """At 08:30 the plan is the evening of its session, named so; and since a check is one of its
    session's generations by the Shanghai calendar date it was read on (whole-branch review C1),
    the header says that date -- and the instant, in Shanghai time, however it was pinned."""
    document = _evening_document(evening)
    advice = _compare(evening, document, evening)
    pinned = morning.morning_document(_pin(as_of="2026-04-12T23:30:00+00:00"), advice, notices=())

    (header,) = (
        line for line in morning.morning_lines(pinned, this_run=RUN) if line.startswith(HEADER)
    )

    assert header.startswith(
        f"{HEADER}the plan of {SIGNAL.isoformat()}, read again on 2026-04-13 "
        "(2026-04-13T07:30:00+08:00), before the next session's call auction"
    )
    assert TONIGHT not in header
    assert not any(line.startswith(EARLIER) for line in morning.morning_lines(pinned, this_run=RUN))


def test_a_later_generation_names_the_earlier_check_whose_evening_read_it_compared_with(
    evening: Day,
) -> None:
    """A later generation is not read against the store at the evening's clock -- an earlier
    morning's fetch has changed what that clock reads -- but against the evening read the earlier
    check filed before its own fetch: its line names that file and when it was read."""
    document = _evening_document(evening)
    advice = _compare(evening, document, evening)
    earlier = {
        "file": f"{SIGNAL.isoformat()}.morning.json",
        "as_of": "2026-04-11T08:35:00+08:00",
    }
    filed = morning.morning_document(_pin(carried_from=earlier), advice, notices=())

    lines = morning.morning_lines(filed, this_run=RUN)

    (line,) = (line for line in lines if line.startswith(EARLIER))
    assert line.startswith(f"{EARLIER}read on 2026-04-11 (2026-04-11T08:35:00+08:00)")
    assert f"{SIGNAL.isoformat()}.morning.json" in line and "kept as it is" in line
    assert "evening read" in line and "before its own fetch" in line
    assert lines.index(line) == 1


def test_the_first_generation_is_named_by_its_session_and_a_later_one_by_its_date_too() -> None:
    """One check per Shanghai calendar date (whole-branch review C1): the first is
    `<session>.morning.json`, each later one `<session>.morning.<date>.json` beside it -- and
    neither is a name the journals' readers take for a session's journal (`????-??-??.json`,
    `date.fromisoformat(stem)`)."""
    first = morning.morning_file_name(SIGNAL)
    later = morning.morning_file_name(SIGNAL, date(2026, 4, 13))

    assert first == f"{SIGNAL.isoformat()}{morning.MORNING_FILE_SUFFIX}"
    assert first == f"{SIGNAL.isoformat()}.morning.json"
    assert later == f"{SIGNAL.isoformat()}.morning.2026-04-13.json"
    for name in (first, later):
        assert not fnmatch.fnmatchcase(name, "????-??-??.json")
        with pytest.raises(ValueError):
            date.fromisoformat(name.removesuffix(".json"))


def test_the_generations_of_a_session_are_listed_oldest_first_and_nothing_else_is() -> None:
    session = SIGNAL.isoformat()
    names = [
        f"{session}.json",
        f"{session}.morning.2026-04-14.json",
        f"{session}.morning.json",
        f"{session}.morning.2026-04-13.json",
        f"{session}.morning.json.partial",
        f"{session}.morning.2026-04-13.json.partial",
        f"{session}.morning.tomorrow.json",
        f"{session}.morning.2026-02-30.json",
        "2026-04-09.morning.json",
        "2026-04-09.morning.2026-04-13.json",
    ]

    generations = morning.morning_generations(names, session=SIGNAL)

    assert generations == (
        (f"{session}.morning.json", None),
        (f"{session}.morning.2026-04-13.json", date(2026, 4, 13)),
        (f"{session}.morning.2026-04-14.json", date(2026, 4, 14)),
    )
    assert morning.morning_generations([f"{session}.json"], session=SIGNAL) == ()


def test_a_check_belongs_to_the_shanghai_calendar_date_of_its_instant() -> None:
    assert morning.morning_date(datetime.fromisoformat("2026-04-12T23:30:00+00:00")) == date(
        2026, 4, 13
    )
    assert morning.morning_date(datetime.fromisoformat("2026-04-13T08:30:00+08:00")) == date(
        2026, 4, 13
    )
    assert morning.morning_date(datetime.fromisoformat("2026-04-13T16:30:00+00:00")) == date(
        2026, 4, 14
    )
    with pytest.raises(TypeError):
        morning.morning_date(datetime(2026, 4, 13, 8, 30))


def test_the_holdings_compared_are_the_book_after_the_evening_not_the_files_held(
    evening: Day,
) -> None:
    """The file's `held` is the evening's pin, made before its settlement (`[]` for the first
    evening, which then holds what its buys fill); the holdings the morning compares are the book
    after it, and the file and the lines say which is which (review Minor 7)."""
    top = _survivors(evening)
    document = _evening_document(evening, holdings=top[:3], buys=top[3:5])

    filed = morning.morning_document(_pin(), _compare(evening, document, evening), notices=())

    assert filed["held"] == []
    assert filed["holdings_compared"] == sorted(top[:3])
    lines = morning.morning_lines(filed, this_run=RUN)
    (line,) = (line for line in lines if line.startswith("holdings compared"))
    assert ", ".join(sorted(top[:3])) in line
    assert "after the evening's settlement" in line and "`held`" in line


def _history(code: str, *records: tuple[str, date, date, str]) -> NameHistory:
    """`code`'s names, each `(name, effective from, announced on, change reason)`."""
    return build_name_history(
        code,
        [
            NameRecord(
                ts_code=code,
                name=name,
                effective_from=effective,
                announced_on=announced,
                change_reason=reason,
            )
            for name, effective, announced, reason in records
        ],
    )


def test_a_plan_names_announcement_dated_after_the_session_is_a_notice_never_advice(
    evening: Day,
) -> None:
    """The facts adapter screens the name corpus as announced by the session, so a rename the
    morning's fetch brought that is dated after it -- published before the open, or over a weekend
    -- changes no verdict. It is listed, for each name of the plan, as a notice: the code, the
    name, the announcement date and the change, and nothing that reads as an order (review
    Important 2). A name outside the plan, and an announcement dated on the session itself, are
    not notices."""
    top = _survivors(evening)
    held, bought, carried, watched, outsider = top[0], top[3], top[5], top[6], top[9]
    document = _evening_document(
        evening, holdings=[held], buys=[bought], carried=[carried], watch=[watched]
    )
    first, day = date(2020, 1, 2), SIGNAL

    def after(days: int) -> date:
        return day + timedelta(days=days)

    histories = {
        held: _history(held, ("甲", first, first, "新增"), ("乙", after(2), day, "改名")),
        bought: _history(
            bought, ("丙", first, first, "新增"), ("*ST丙", after(3), after(1), "*ST")
        ),
        carried: _history(carried, ("丁", first, first, "新增")),
        watched: _history(
            watched,
            ("戊", first, first, "新增"),
            ("ST戊", after(5), after(2), "ST"),
            ("己", after(9), after(3), "改名"),
        ),
        outsider: _history(
            outsider, ("庚", first, first, "新增"), ("ST庚", after(3), after(1), "ST")
        ),
    }

    notices = morning.overnight_notices(
        histories, names=morning.plan_names(document).watched(), session=day
    )

    expected = sorted(
        [
            (bought, "*ST丙", after(1), "*ST"),
            (watched, "ST戊", after(2), "ST"),
            (watched, "己", after(3), "改名"),
        ]
    )
    assert [notice.text for notice in notices] == [
        f"{code} {name} {announced.isoformat()} {change}"
        for code, name, announced, change in expected
    ]
    filed = morning.morning_document(_pin(), _compare(evening, document, evening), notices=notices)
    assert filed["differences"] == []
    assert filed["notices"] == [
        {
            "ts_code": code,
            "name": name,
            "ann_date": announced.isoformat(),
            "change_reason": change,
            "text": f"{code} {name} {announced.isoformat()} {change}",
        }
        for code, name, announced, change in expected
    ]
    assert json.loads(json.dumps(filed, ensure_ascii=False)) == filed
    lines = morning.morning_lines(filed, this_run=RUN)
    assert f"{ADVICE}{NO_CHANGE}" in lines
    start = next(index for index, line in enumerate(lines) if line.startswith(NOTICES))
    header, block = lines[start], lines[start + 1 : start + 1 + len(notices)]
    assert f"announced after {day.isoformat()}" in header
    assert "not screened" in header and "not advice" in header
    assert block == [f"  {notice.text}" for notice in notices]
    for line in (header, *block):
        assert not any(word in line for word in ("BUY", "SELL", "place", "order")), line


def test_a_morning_with_no_announcement_after_the_session_says_it_has_no_notice(
    evening: Day,
) -> None:
    top = _survivors(evening)
    document = _evening_document(evening, holdings=top[:2], buys=top[2:4])
    first = date(2020, 1, 2)
    histories = {code: _history(code, ("甲", first, first, "新增")) for code in top[:4]}

    notices = morning.overnight_notices(
        histories, names=morning.plan_names(document).watched(), session=SIGNAL
    )

    assert notices == ()
    filed = morning.morning_document(_pin(), _compare(evening, document, evening), notices=())
    lines = morning.morning_lines(filed, this_run=RUN)
    (header,) = (line for line in lines if line.startswith(NOTICES))
    assert "none" in header and f"announced after {SIGNAL.isoformat()}" in header


def test_the_listing_row_is_a_row_of_the_frame_the_adapter_says_it_is(evening: Day) -> None:
    fact: SecurityFacts = {row.ts_code: row for row in evening.facts.facts}[BSE_BEFORE_LISTING]
    assert (fact.board, fact.listed_sessions) == ("bse", 0)
    assert evening.facts.session == SIGNAL


SHA: Final[str] = "a" * 64


def _filed_pin(**changes: Any) -> dict[str, Any]:
    """A pin as the command files it before the fetch: the pin's keys, the registration it was
    written under and the evening's facts read."""
    return {**_pin(), "registration": {"sha256": SHA}, "evening_read": {}, **changes}


def _check(document: Any, *, read_on: date | None = None) -> None:
    morning.check_morning_file(
        document,
        session=SIGNAL,
        evening_snapshot_id=SNAPSHOT,
        registration_sha256=SHA,
        read_on=read_on,
    )


LATER_ON: Final[date] = date(2026, 4, 13)
"""The date `_pin()`'s instant falls on in Shanghai: a later generation's name."""
CARRIED: Final[dict[str, str]] = {
    "file": f"{SIGNAL.isoformat()}.morning.json",
    "as_of": "2026-04-11T08:35:00+08:00",
}


def test_a_later_generation_is_its_dates_and_carries_the_earlier_checks_read() -> None:
    """A later generation's file is named by its date: its instant must fall on it, and it names
    the earlier check whose evening read it carries; the first generation carries none."""
    _check(_filed_pin(carried_from=CARRIED), read_on=LATER_ON)

    with pytest.raises(morning.MorningCheckError, match="2026-04-14"):
        _check(_filed_pin(carried_from=CARRIED), read_on=date(2026, 4, 14))
    with pytest.raises(morning.MorningCheckError, match="carried_from"):
        _check(_filed_pin(), read_on=LATER_ON)
    with pytest.raises(morning.MorningCheckError, match="carried_from"):
        _check(_filed_pin(carried_from=CARRIED))


@pytest.mark.parametrize(
    "carried",
    [
        {"file": 7, "as_of": "2026-04-11T08:35:00+08:00"},
        {"file": "2026-04-10.morning.json", "as_of": "2026-04-11T08:35:00"},
        {"file": "2026-04-10.morning.json"},
        "2026-04-10.morning.json",
    ],
    ids=["file not a name", "as_of without an offset", "no as_of", "not a mapping"],
)
def test_a_carried_from_that_is_not_an_earlier_check_is_refused(carried: Any) -> None:
    with pytest.raises(morning.MorningCheckError, match="carried_from"):
        _check(_filed_pin(carried_from=carried), read_on=LATER_ON)


def test_a_morning_file_with_no_carried_from_is_refused() -> None:
    pin = _filed_pin()
    del pin["carried_from"]

    with pytest.raises(morning.MorningCheckError, match="carried_from"):
        _check(pin)


def test_an_evening_read_carried_from_an_earlier_check_is_checked_and_named_by_its_file(
    evening: Day,
) -> None:
    """The evening read a later generation carries is held to the evening's document as the
    first generation's own read is; one that differs is refused naming the file it came from --
    an edited file, never a store changed under the evening."""
    top = _survivors(evening)
    document = _evening_document(evening, holdings=top[:2])
    names = morning.plan_names(document).watched()
    drifted = _turn(evening, top[4], special_treatment=True)

    with pytest.raises(morning.EveningReadDriftError) as caught:
        morning.check_evening_read(
            morning.read_facts(drifted.facts, drifted.screen, names),
            document,
            filed_in="2026-04-10.morning.json",
        )

    message = str(caught.value)
    assert "2026-04-10.morning.json" in message and "notes.st" in message
    assert "Something other than the morning's name fetch" not in message


def test_a_pin_the_command_filed_is_this_evenings_and_a_finished_file_is_complete(
    evening: Day,
) -> None:
    pin = _filed_pin()

    _check(pin)

    assert not morning.morning_complete(pin)
    advice = _compare(evening, _evening_document(evening), evening)
    finished = morning.morning_document(pin, advice, notices=())
    _check(finished)
    assert morning.morning_complete(finished)


def _finished(evening: Day) -> dict[str, Any]:
    """A finished file with one difference and one notice, as the command files it."""
    top = _survivors(evening)
    document = _evening_document(evening, buys=top[:2])
    first = date(2020, 1, 2)
    notices = morning.overnight_notices(
        {top[1]: _history(top[1], ("甲", first, first, "新增"), ("ST甲", SIGNAL, SIGNAL, "ST"))},
        names=(top[1],),
        session=SIGNAL - timedelta(days=1),
    )
    advice = _compare(evening, document, _turn(evening, top[0], special_treatment=True))
    filed = morning.morning_document(_filed_pin(), advice, notices=notices)
    assert filed["differences"] and filed["notices"]
    body: dict[str, Any] = json.loads(json.dumps(filed))
    return body


def _edited(document: Mapping[str, Any], edit: str) -> dict[str, Any]:
    """`document` with `edit` made: `key` removed, `key.0.text` removed from its first item, or
    `key.<json>` set to that value."""
    edited: dict[str, Any] = json.loads(json.dumps(document))
    key, _, value = edit.partition(".")
    if not value:
        del edited[key]
    elif value == "0.text":
        del edited[key][0]["text"]
    else:
        edited[key] = json.loads(value)
    return edited


@pytest.mark.parametrize(
    ("edit", "named"),
    [
        ("tolerated", "tolerated"),
        ("wording", "wording"),
        ("notices", "notices"),
        ("holdings_compared", "holdings_compared"),
        ("counts_changed", "counts_changed"),
        ("requests", "requests"),
        ("differences.0.text", "differences"),
        ("notices.0.text", "notices"),
        ('differences."none"', "differences"),
        ("tolerated.[1]", "tolerated"),
        ("counts_changed.[1]", "counts_changed"),
        ("wording.3", "wording"),
        ('requests.{"namechange": "one"}', "requests"),
    ],
)
def test_a_finished_morning_file_not_of_the_finished_shape_is_refused_not_printed(
    evening: Day, edit: str, named: str
) -> None:
    """A finished file is printed again from its own keys, so one edited by hand -- a key gone,
    a difference with no line -- is refused by what is wrong with it instead of breaking the
    printing (review Minor 3); a pin, which holds none of the finished keys, is not finished."""
    edited = _edited(_finished(evening), edit)

    with pytest.raises(morning.MorningCheckError, match=named):
        morning.morning_complete(edited)
    with pytest.raises(morning.MorningCheckError, match=named):
        _check(edited)


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"schema": "openalpha-daily-survivor-morning/v0"}, "is not a"),
        ({"session": "2026-04-09"}, "2026-04-09"),
        ({"evening_snapshot_id": "snapshot_of_another_evening"}, "snapshot_of_another_evening"),
        ({"registration": {"sha256": "b" * 64}}, "another registration"),
        ({"registration": None}, "another registration"),
        ({"as_of": "2026-04-13T08:30:00"}, "pinned `as_of`"),
        ({"as_of": "tomorrow morning"}, "pinned `as_of`"),
        ({"as_of": None}, "pinned `as_of`"),
        ({"held": "none"}, "`held`"),
        ({"evening_read": None}, "evening read"),
    ],
)
def test_a_morning_file_that_is_not_this_evenings_is_refused_by_what_is_wrong_with_it(
    changes: dict[str, Any], named: str
) -> None:
    with pytest.raises(morning.MorningCheckError, match=named):
        _check(_filed_pin(**changes))


def test_a_morning_file_that_is_not_a_mapping_is_refused() -> None:
    with pytest.raises(morning.MorningCheckError, match="is not a"):
        _check(["not", "a", "mapping"])
