"""The survivor day's morning re-check, its pure half (`V2-P7-008`, task D6).

`python scripts/daily_selection.py --runtime-dir RT --morning-recheck` reads again, before the next
call auction, the one thing that can change overnight and matters for tomorrow's open: the name
corpus -- a new ST or delisting-risk announcement published after the evening's read. The script
fetches it (`namechange` of the session's year: one request, nothing else), reads the evening's
facts day again and the morning's, and hands the two to `compare_morning`; this module is that
comparison, the notices (`overnight_notices`), and the document and the lines built from them. It
is pure: every input is an argument, nothing is read, written, fetched or timed.

## Advice only

The paper book follows the evening's plan (decision, user-adopted): a decision cannot be committed
twice, and the book's record is the evening's. So nothing here changes it -- a divergence is
**recorded** in the morning file and **printed**, in the form the user acts on, and that is all:

- `do not place BUY <code>: <reason today>` -- a buy of tonight's plan, or one carried from an
  earlier session, whose name is no longer eligible, or no longer a survivor, this morning;
- `holding <code> turned <reason>: the paper book sells it at the next evening` -- a holding the
  evening kept that has a reason it had not (a suspension alone is *frozen*, not sold: the policy's
  `FREEZE_COMPATIBLE_REASONS`); a holding the evening already sells is not told about again;
- `watch list changed: ...` -- names of the evening's watch list that are no longer survivors;
- `counts changed: ...` -- the eligible and survivor counts, and the name corpus' ST and delisting
  counts, when they moved (a name outside the plan turning ST moves only these);
- nothing at all is `no change to tonight's plan among announcements dated through <session>`: the
  advice line carries its own scope, and never says "no overnight change" alone.

A difference is a **change**: a name the evening already found ineligible and still is, is not news.

## What it can see, and the notices

The morning's facts day is the **evening's session's**, read at the morning's clock, and the facts
adapter reads the name corpus through the session's year and cuts it to what had been announced by
the signal day (`announced_by`): a rename announced after the session -- published at 07:00 of the
morning itself, or over a weekend -- is invisible to the re-read whatever it is told. What the
check screens is the announcement dated the evening's own session but published after the
evening's read (T3: about half of the ST names are announced the evening before). It is a partial
guard, not the last word on tomorrow's open, and an empty advice is not "no ST announcement
exists".

What the one request brings that is dated **after** the session is not thrown away: every rename of
a name of the plan (a holding, a planned or carried buy, the watch list) whose announcement date is
after the session, in the stored corpus as the morning reads it, is listed as a **notice** --
"announced after the session, not screened": `<code> <name> <ann_date> <change>`. A notice is not
advice and says nothing the user is to do; the next evening screens it. At New Year the morning
still asks the session's year alone (the facts adapter reads no later year), so an announcement
dated in the new year is neither screened nor a notice.

## The file

`<session>.morning.json` beside the evening's journal: the pin (`schema`, `session`, `as_of`,
`held`, `evening_snapshot_id`, `registration`, the filed evening read), then `requests`, then the
advice, the notices and the wording. Its `held` is the evening's **pinned** holdings -- the head's,
before the evening's own fills and settlement (`[]` for the first evening, which then holds what
its buys fill) -- with which both facts days are read; the holdings the comparison speaks of are
the book **after** the evening's settlement, filed as `holdings_compared` and printed as such. A
finished file is printed again with no request and no write, but only before the next call
auction: the script checks the cutoff first, and a finished file after it is refused like any
other morning (the brief's order).

## Two reads, one before the fetch

The comparison needs what the evening saw per code -- which a journal's counts cannot say -- so the
script reads the evening's facts day again **before** it fetches (`read_facts`), at the evening's
own pinned clock and with its pinned `held`, and files the result in the morning file with the
morning's own clock. It is checked against the evening's document (`check_evening_read`): a store
that no longer gives the evening's facts has been changed by something other than the morning's name
fetch, and advice made over it would compare unlike days. Read after the fetch it could not be told
from the morning's: the new row is announced on the evening's own date and so is visible at the
evening's clock too -- which is why a rerun that stopped after the fetch must read the file's copy,
never the store.

## The one benign difference class (tolerated)

A code priced before its registry listing row appears gets a row aged 0 from an evening read (the
registry has never heard of it) and no row from a later one (the registry knows it, and does not
list it that day), where it is counted in `not_listed_with_bar` instead of `no_listing_row`
(`strategy_survivor_facts` module docstring): two reads of one signal day differ by that row and
those two counts. A code with a row aged 0 at the evening and none this morning is **tolerated**
-- listed under `tolerated`, never as advice -- when the counts say so: the codes that vanished are
as many as the fall in `no_listing_row` and as the rise in `not_listed_with_bar`. When they do not
add up nothing says the vanished row is that class, and each such code is reported (`row_gone`):
fail loud, never tolerate on a guess. An aged-0 row is ineligible (`listing_too_recent`) at both
reads, so tolerating it removes no advice a plan needed; what it removes is a false one.

Research honesty: the lines say "candidate" and "no order was placed", and nothing here is a
recommendation or a forecast.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Final

from openalpha_cn.domain.survivor_book import ELIGIBILITY_REASON_ORDER, ScreenResult
from openalpha_cn.domain.survivor_policy import FREEZE_COMPATIBLE_REASONS

if TYPE_CHECKING:
    from openalpha_cn.domain.name_history import NameHistory
    from openalpha_cn.strategy_survivor_facts import FactsDay

__all__ = [
    "MORNING_FILE_SUFFIX",
    "MORNING_SCHEMA",
    "MORNING_WORDING",
    "NO_CHANGE",
    "Difference",
    "EveningReadDriftError",
    "FactsRead",
    "MorningAdvice",
    "MorningCheckError",
    "Notice",
    "PlanNames",
    "check_evening_read",
    "check_morning_file",
    "compare_morning",
    "morning_complete",
    "morning_document",
    "morning_lines",
    "overnight_notices",
    "plan_names",
    "read_facts",
]

MORNING_SCHEMA: Final[str] = "openalpha-daily-survivor-morning/v1"
MORNING_FILE_SUFFIX: Final[str] = ".morning.json"
"""The morning file is `<session><suffix>` beside the evening's journal `<session>.json`: a name
no reader of the journals' `????-??-??.json` finds."""
NO_CHANGE: Final[str] = "no change to tonight's plan among announcements dated through"
"""The advice line of a morning with no difference, followed by the session: the scope of what was
screened is in the line itself, never "no overnight change" alone."""
MORNING_WORDING: Final[str] = (
    "advice only: candidates of a registered research configuration; no order was placed and the "
    "paper book was not changed -- it follows the evening's plan, and a difference is recorded "
    "here, never committed to it; the check screens announcements dated through the evening's "
    "session only -- one dated after it is a notice, not screened -- so no advice is not proof "
    "that no ST announcement exists"
)

_NOT_ASSESSED: Final[str] = "not_assessed"
"""The reason of a code the screen holds no verdict for -- it has no facts row -- as the evening's
summary names it: the kernel sells what it never assessed."""
_NO_LONGER_A_SURVIVOR: Final[str] = "no longer a survivor"
_LABEL_WIDTH: Final[int] = 18
_COUNTED: Final[tuple[tuple[str, str], ...]] = (
    ("eligible", "eligible"),
    ("survivors", "survivors"),
)
_COUNTED_NOTES: Final[tuple[tuple[str, str], ...]] = (("st", "ST"), ("delisting", "delisting"))
_ORDER: Final[Mapping[str, int]] = {
    reason: position for position, reason in enumerate(ELIGIBILITY_REASON_ORDER)
}


class MorningCheckError(ValueError):
    """The morning check cannot be made over what it was given, by name."""


class EveningReadDriftError(MorningCheckError):
    """The evening's facts read again do not equal the evening's own document: the store was
    changed since the evening by something other than the morning's name fetch."""


# --- the plan the evening made --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanNames:
    """The names of the evening's plan that the comparison reads, each tuple ascending."""

    holdings: tuple[str, ...]
    """The book after the evening's settlement: what the plan acts on."""
    selling: tuple[str, ...]
    """The holdings the evening itself sells at the next open."""
    planned_buys: tuple[str, ...]
    carried_buys: tuple[str, ...]
    """Buys planned on an earlier session and still live."""
    watch_list: tuple[str, ...]

    def watched(self) -> tuple[str, ...]:
        """Every name whose verdict the comparison reads, ascending, each once."""
        return tuple(
            sorted(
                {
                    *self.holdings,
                    *self.planned_buys,
                    *self.carried_buys,
                    *self.watch_list,
                }
            )
        )


def plan_names(evening: Mapping[str, Any]) -> PlanNames:
    """The plan read off an evening's `result` document (`strategy_survivor_summary`): the
    holdings its book ended with, its open orders and its decision's watch list.

    Refused by name for a document with no plan: the epoch day only opens the book, and a day that
    settled only (or was refused) ran no evening."""
    if "opening" in evening:
        raise MorningCheckError(
            "the day is the epoch session, which only opens the paper book: there is no plan to "
            "re-check"
        )
    paper = evening.get("paper")
    if paper is None:
        raise MorningCheckError(
            "the day ran no evening (it settled only): there is no plan to re-check"
        )
    try:
        orders = list(paper["open_orders"])
        holdings = [str(item["ts_code"]) for item in paper["book"]["holdings"]]
        watch = [str(code) for code in paper["decision"]["watch_list"]]
        return PlanNames(
            holdings=tuple(sorted(holdings)),
            selling=tuple(sorted(str(o["ts_code"]) for o in orders if o["side"] == "sell")),
            planned_buys=tuple(
                sorted(str(o["ts_code"]) for o in orders if o["side"] == "buy" and not o["carried"])
            ),
            carried_buys=tuple(
                sorted(str(o["ts_code"]) for o in orders if o["side"] == "buy" and o["carried"])
            ),
            watch_list=tuple(sorted(watch)),
        )
    except (KeyError, TypeError) as error:
        raise MorningCheckError(
            f"the evening's document has not the shape the comparison reads ({type(error).__name__}"
            f": {error}): its paper book's holdings, open orders and decision's watch list"
        ) from error


# --- one facts day, as far as the comparison reads it ---------------------------------------------


def _plain(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def _summary(facts: FactsDay, screened: ScreenResult) -> dict[str, Any]:
    named = Counter(reason for verdict in screened.verdicts for reason in verdict.reasons)
    return {
        "rows": len(facts.facts),
        "universe_size": facts.universe_size,
        "scored_size": facts.scored_size,
        "assessed": len(screened.verdicts),
        "eligible": sum(1 for verdict in screened.verdicts if verdict.eligible),
        "survivors": len(screened.survivors),
        "reasons": {reason: named[reason] for reason in sorted(ELIGIBILITY_REASON_ORDER)},
        "notes": {
            item.name: _plain(getattr(facts.notes, item.name))
            for item in sorted(fields(facts.notes), key=lambda item: item.name)
        },
    }


def _summary_of_document(evening: Mapping[str, Any]) -> dict[str, Any]:
    """The same summary, read off an evening document's `facts` and `screen` sections."""
    try:
        facts, screened = evening["facts"], evening["screen"]
        return {
            "rows": facts["rows"],
            "universe_size": facts["universe_size"],
            "scored_size": facts["scored_size"],
            "assessed": screened["assessed"],
            "eligible": screened["eligible"],
            "survivors": screened["survivors"],
            "reasons": dict(screened["reasons"]),
            "notes": dict(facts["notes"]),
        }
    except (KeyError, TypeError) as error:
        raise MorningCheckError(
            f"the evening's document has no readable facts and screen sections "
            f"({type(error).__name__}: {error})"
        ) from error


@dataclass(frozen=True, slots=True)
class FactsRead:
    """What the comparison reads of one facts day and its screen -- plain, and filed as JSON.

    `summary` counts the day (the evening document's own sections, read the same way), `aged_zero`
    names every code with a row aged 0 (the registry has no listing row for it), and `verdicts`
    holds the screen's answer for the names of the plan alone: its reasons and whether it is among
    the survivors. A name with no verdict has no facts row.
    """

    summary: Mapping[str, Any]
    aged_zero: tuple[str, ...]
    verdicts: Mapping[str, Mapping[str, Any]]

    def to_json(self) -> dict[str, Any]:
        return {
            "summary": dict(self.summary),
            "aged_zero": list(self.aged_zero),
            "verdicts": {code: dict(self.verdicts[code]) for code in sorted(self.verdicts)},
        }

    @classmethod
    def from_json(cls, document: Mapping[str, Any]) -> FactsRead:
        try:
            verdicts = {
                str(code): {
                    "reasons": [str(r) for r in item["reasons"]],
                    "survivor": item["survivor"],
                }
                for code, item in document["verdicts"].items()
            }
            if not all(isinstance(item["survivor"], bool) for item in verdicts.values()):
                raise TypeError("a verdict's `survivor` is not a boolean")
            return cls(
                summary=dict(document["summary"]),
                aged_zero=tuple(str(code) for code in document["aged_zero"]),
                verdicts=verdicts,
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise MorningCheckError(
                f"the filed evening read is not a facts read ({type(error).__name__}: {error})"
            ) from error

    def reasons_of(self, code: str) -> tuple[str, ...]:
        """`code`'s reasons, or `not_assessed` when it has no verdict (no facts row)."""
        item = self.verdicts.get(code)
        return (_NOT_ASSESSED,) if item is None else tuple(item["reasons"])

    def eligible(self, code: str) -> bool:
        return code in self.verdicts and not self.verdicts[code]["reasons"]

    def survivor(self, code: str) -> bool:
        return code in self.verdicts and bool(self.verdicts[code]["survivor"])


def read_facts(facts: FactsDay, screened: ScreenResult, names: Collection[str]) -> FactsRead:
    """One facts day and its screen as the comparison reads them; `names` are the plan's
    (`PlanNames.watched`)."""
    wanted = set(names)
    survivors = {name.ts_code for name in screened.survivors}
    return FactsRead(
        summary=_summary(facts, screened),
        aged_zero=tuple(sorted(row.ts_code for row in facts.facts if row.listed_sessions == 0)),
        verdicts={
            verdict.ts_code: {
                "reasons": list(verdict.reasons),
                "survivor": verdict.ts_code in survivors,
            }
            for verdict in screened.verdicts
            if verdict.ts_code in wanted
        },
    )


def check_evening_read(read: FactsRead, evening: Mapping[str, Any]) -> None:
    """Refuse an evening read that is not the evening's own: its summary must equal the one the
    evening's document holds. Every field that differs is named."""
    expected = _summary_of_document(evening)
    found = dict(read.summary)
    differing = sorted(
        key for key in {*expected, *found} if key != "notes" and expected.get(key) != found.get(key)
    )
    notes_there, notes_here = dict(expected.get("notes", {})), dict(found.get("notes", {}))
    differing += sorted(
        f"notes.{name}"
        for name in {*notes_there, *notes_here}
        if notes_there.get(name) != notes_here.get(name)
    )
    if differing:
        raise EveningReadDriftError(
            "the panel read at the evening's own clock no longer gives the evening's facts: "
            f"{', '.join(differing)} differ from the evening's document. Something other than the "
            "morning's name fetch changed the store since the evening, and advice made over it "
            "would compare unlike days. Nothing was fetched"
        )


# --- the comparison -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Difference:
    """One thing the morning says otherwise: its kind, the codes it concerns, the reasons, and the
    line the user reads."""

    kind: str
    ts_codes: tuple[str, ...]
    reasons: tuple[str, ...]
    text: str

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ts_codes": list(self.ts_codes),
            "reasons": list(self.reasons),
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class MorningAdvice:
    """What the comparison found: the differences, in the order they are printed, the codes
    tolerated as the listing-row class (never advice), and the holdings it compared -- the book
    after the evening's settlement, which is not the file's `held` (module docstring)."""

    differences: tuple[Difference, ...]
    tolerated: tuple[str, ...]
    holdings_compared: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "differences": [item.to_json() for item in self.differences],
            "tolerated": list(self.tolerated),
            "holdings_compared": list(self.holdings_compared),
        }


@dataclass(frozen=True, slots=True)
class Notice:
    """One rename of a name of the plan announced after the session: in the stored corpus, never
    screened -- the facts adapter reads what was announced by the session -- and not advice."""

    ts_code: str
    name: str
    ann_date: date
    change_reason: str

    @property
    def text(self) -> str:
        return f"{self.ts_code} {self.name} {self.ann_date.isoformat()} {self.change_reason}"

    def to_json(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "name": self.name,
            "ann_date": self.ann_date.isoformat(),
            "change_reason": self.change_reason,
            "text": self.text,
        }


def _ordered(reasons: Iterable[str]) -> tuple[str, ...]:
    """Reasons in the kernel's fixed order, `not_assessed` last."""
    return tuple(sorted(set(reasons), key=lambda reason: _ORDER.get(reason, len(_ORDER))))


def compare_morning(
    *,
    evening: Mapping[str, Any],
    before: FactsRead,
    facts: FactsDay,
    screen: ScreenResult,
) -> MorningAdvice:
    """The evening's plan against this morning's facts day: advice, never a change of the book.

    `evening` is the evening's `result` document (its plan), `before` the evening's facts day read
    again before the morning's fetch (`read_facts`), and `facts` and `screen` the morning's.
    See the module docstring for the differences and the tolerated class."""
    names = plan_names(evening)
    now = read_facts(facts, screen, names.watched())
    gone, tolerated = _vanished_rows(before, now, {row.ts_code for row in facts.facts})
    return MorningAdvice(
        differences=(
            *_buys(names, before, now),
            *_holdings(names, before, now),
            *_watch_list(names, before, now),
            *gone,
            *_counts(before, now),
        ),
        tolerated=tolerated,
        holdings_compared=names.holdings,
    )


def _buys(names: PlanNames, before: FactsRead, now: FactsRead) -> list[Difference]:
    found = []
    for code in sorted({*names.planned_buys, *names.carried_buys}):
        if before.eligible(code) and not now.eligible(code):
            reasons = now.reasons_of(code)
        elif before.survivor(code) and now.eligible(code) and not now.survivor(code):
            reasons = (_NO_LONGER_A_SURVIVOR,)
        else:
            continue
        carried = " (a buy carried from an earlier session)" if code in names.carried_buys else ""
        found.append(
            Difference(
                kind="do_not_place_buy",
                ts_codes=(code,),
                reasons=reasons,
                text=f"do not place BUY {code}: {', '.join(reasons)}{carried}",
            )
        )
    return found


def _holdings(names: PlanNames, before: FactsRead, now: FactsRead) -> list[Difference]:
    found = []
    for code in names.holdings:
        if code in names.selling:
            continue
        had, has = before.reasons_of(code), now.reasons_of(code)
        added = _ordered(reason for reason in has if reason not in had)
        if not added:
            continue
        frozen = "suspended" in has and set(has) <= FREEZE_COMPATIBLE_REASONS
        effect = (
            "the paper book freezes it at the next evening (it cannot trade while suspended; it "
            "is not sold)"
            if frozen
            else "the paper book sells it at the next evening"
        )
        found.append(
            Difference(
                kind="holding_turned",
                ts_codes=(code,),
                reasons=added,
                text=f"holding {code} turned {', '.join(added)}: {effect}",
            )
        )
    return found


def _watch_list(names: PlanNames, before: FactsRead, now: FactsRead) -> list[Difference]:
    dropped = {
        code: now.reasons_of(code) if not now.eligible(code) else (_NO_LONGER_A_SURVIVOR,)
        for code in names.watch_list
        if before.survivor(code) and not now.survivor(code)
    }
    if not dropped:
        return []
    told = ", ".join(f"{code} ({', '.join(reasons)})" for code, reasons in dropped.items())
    return [
        Difference(
            kind="watch_list_changed",
            ts_codes=tuple(dropped),
            reasons=_ordered({reason for reasons in dropped.values() for reason in reasons}),
            text=f"watch list changed: {told} (today's draw, not a promise for tomorrow)",
        )
    ]


def _vanished_rows(
    before: FactsRead, now: FactsRead, rows_now: Collection[str]
) -> tuple[list[Difference], tuple[str, ...]]:
    """The codes with a row aged 0 at the evening and none this morning, split into the tolerated
    class and the rest (`row_gone`): tolerated when the codes are as many as the fall in
    `no_listing_row` and as the rise in `not_listed_with_bar`."""
    vanished = tuple(code for code in before.aged_zero if code not in rows_now)
    if not vanished:
        return [], ()
    then, here = before.summary["notes"], now.summary["notes"]
    fall = then["no_listing_row"] - here["no_listing_row"]
    rise = here["not_listed_with_bar"] - then["not_listed_with_bar"]
    if len(vanished) == fall == rise:
        return [], vanished
    return [
        Difference(
            kind="row_gone",
            ts_codes=(code,),
            reasons=(),
            text=(
                f"{code} had a facts row aged 0 tonight and has none this morning, and the counts "
                f"do not say it is a code priced before its registry listing row (no_listing_row "
                f"-{fall}, not_listed_with_bar +{rise}, {len(vanished)} such code(s)): not "
                "tolerated"
            ),
        )
        for code in vanished
    ], ()


def _counts(before: FactsRead, now: FactsRead) -> list[Difference]:
    moved = [
        f"{label} {before.summary[key]} -> {now.summary[key]}"
        for key, label in _COUNTED
        if before.summary[key] != now.summary[key]
    ] + [
        f"{label} {before.summary['notes'][key]} -> {now.summary['notes'][key]}"
        for key, label in _COUNTED_NOTES
        if before.summary["notes"][key] != now.summary["notes"][key]
    ]
    if not moved:
        return []
    return [
        Difference(
            kind="counts_changed",
            ts_codes=(),
            reasons=(),
            text=f"counts changed: {', '.join(moved)}",
        )
    ]


# --- the notices, the file and what is printed ----------------------------------------------------


def overnight_notices(
    histories: Mapping[str, NameHistory], *, names: Collection[str], session: date
) -> tuple[Notice, ...]:
    """Every rename of a name in `names` -- the plan's (`PlanNames.watched`) -- whose announcement
    is dated after `session`, in `histories` (the stored name corpus as the morning reads it): what
    the morning's fetch brought that its facts read cannot screen. By code, then announcement."""
    found = [
        Notice(
            ts_code=code,
            name=record.name,
            ann_date=record.announced_on,
            change_reason=record.change_reason,
        )
        for code in set(names)
        if (history := histories.get(code)) is not None
        for record in history.records
        if record.announced_on > session
    ]
    return tuple(sorted(found, key=lambda item: (item.ts_code, item.ann_date, item.name)))


_FINISHED: Final[tuple[str, ...]] = (
    "differences",
    "tolerated",
    "notices",
    "holdings_compared",
    "wording",
)
"""The keys the advice stage files, all at once: a file holding none is a pin, one holding all is a
finished check, and one holding some is neither."""


def _texts_of(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _items_with_text(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict) and isinstance(item.get("text"), str) for item in value
    )


def morning_complete(document: Mapping[str, Any]) -> bool:
    """Whether a morning file is a finished check's, which a rerun prints again with no request and
    no write (`True`), or a pin a rerun resumes from (`False`: it holds none of the advice stage's
    keys). A file holding some of them, or any of them not of the shape the printing reads, is
    neither and is refused by name (`MorningCheckError`): it was edited, and printing it would
    break half way."""
    present = [key for key in _FINISHED if key in document]
    if not present:
        return False
    problems = [f"no `{key}`" for key in _FINISHED if key not in document]
    shapes = (
        ("differences", _items_with_text, "a list of differences, each with its `text`"),
        ("tolerated", _texts_of, "a list of codes"),
        ("notices", _items_with_text, "a list of notices, each with its `text`"),
        ("holdings_compared", _texts_of, "a list of codes"),
        ("wording", lambda value: isinstance(value, str), "a string"),
    )
    problems += [
        f"its `{key}` is not {what}"
        for key, holds, what in shapes
        if key in document and not holds(document[key])
    ]
    requests = document.get("requests")
    if not (
        isinstance(requests, dict)
        and all(
            isinstance(api, str) and isinstance(count, int) and not isinstance(count, bool)
            for api, count in requests.items()
        )
    ):
        problems.append("its `requests` is not the count of requests made by `api_name`")
    if problems:
        raise MorningCheckError(
            "it holds the finished check's "
            f"{', '.join(f'`{key}`' for key in present)} but is not a finished check: "
            f"{'; '.join(problems)}"
        )
    return True


def _instant(value: Any) -> datetime | None:
    """`value` as an instant carrying its UTC offset, or `None` when it is not one."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def check_morning_file(
    document: Any, *, session: date, evening_snapshot_id: str, registration_sha256: str
) -> None:
    """Refuse, by name, a morning file that is not this evening's: not a mapping of this schema,
    of another session, another evening's snapshot or another registration, or holding some of a
    finished check's keys and not the rest, or not of their shape (`morning_complete`)."""
    if not isinstance(document, dict) or document.get("schema") != MORNING_SCHEMA:
        raise MorningCheckError(f"it is not a {MORNING_SCHEMA} document")
    problems = []
    if document.get("session") != session.isoformat():
        problems.append(f"it is of session {document.get('session')!r}, not {session.isoformat()}")
    if document.get("evening_snapshot_id") != evening_snapshot_id:
        problems.append(
            f"it re-checks the evening snapshot {document.get('evening_snapshot_id')!r}, not "
            f"{evening_snapshot_id!r}"
        )
    registration = document.get("registration")
    if not isinstance(registration, dict) or registration.get("sha256") != registration_sha256:
        problems.append("it was written under another registration")
    if _instant(document.get("as_of")) is None or not isinstance(document.get("held"), list):
        problems.append("it holds no pinned `as_of` (an instant with its offset) and `held`")
    if not isinstance(document.get("evening_read"), dict):
        problems.append("it holds no filed evening read")
    try:
        morning_complete(document)
    except MorningCheckError as error:
        problems.append(str(error))
    if problems:
        raise MorningCheckError("; ".join(problems))


def morning_document(
    pin: Mapping[str, Any], advice: MorningAdvice, *, notices: Iterable[Notice]
) -> dict[str, Any]:
    """The finished morning file: the pin (`schema`, `session`, `as_of`, `held`,
    `evening_snapshot_id`, `registration`, the filed evening read and `requests`) with the advice,
    the notices and the wording."""
    return {
        **pin,
        **advice.to_json(),
        "notices": [item.to_json() for item in notices],
        "wording": MORNING_WORDING,
    }


def _row(label: str, text: str) -> str:
    return f"{label:<{_LABEL_WIDTH}} {text}"


def morning_lines(document: Mapping[str, Any], *, this_run: Mapping[str, Any]) -> list[str]:
    """What the command prints for a finished morning file: the holdings compared, the advice
    block (its scope in its own line), the tolerated codes, the notices, the requests this run made
    (`this_run`: `requests`, `request_count`, `wrote`) and the wording."""
    session = document["session"]
    differences = list(document["differences"])
    holdings = list(document["holdings_compared"])
    scope = f"among announcements dated through {session}"
    lines = [
        _row(
            "morning re-check",
            f"the plan of {session}, read again as of {document['as_of']}, before the next "
            "session's call auction -- advice only: the paper book follows the evening's plan "
            "and was not changed",
        ),
        _row(
            "holdings compared",
            f"{', '.join(holdings) if holdings else 'none'} (the book after the evening's "
            "settlement, which its plan acts on; the file's `held` is the evening's pinned "
            "holdings before it, with which both days are read)",
        ),
    ]
    if differences:
        lines.append(
            _row("advice", f"{len(differences)} difference(s) from tonight's plan {scope}")
        )
        lines += [f"  {item['text']}" for item in differences]
    else:
        lines.append(_row("advice", f"{NO_CHANGE} {session}"))
    tolerated = list(document["tolerated"])
    if tolerated:
        lines.append(
            _row(
                "tolerated",
                f"{', '.join(tolerated)} (priced before its registry listing row: a row aged 0 "
                "tonight, none this morning, the counts account for it -- not advice)",
            )
        )
    notices = list(document["notices"])
    lines.append(
        _row(
            "notices",
            f"announced after {session}, not screened -- a notice, not advice: "
            + (
                f"{len(notices)} rename(s) of the plan's names in the stored name corpus"
                if notices
                else "none for the plan's names in the stored name corpus"
            ),
        )
    )
    lines += [f"  {item['text']}" for item in notices]
    run = this_run
    lines.append(
        _row(
            "tushare requests",
            f"{run['request_count']} this run {json.dumps(run['requests'], sort_keys=True)}"
            + (
                ""
                if run["wrote"]
                else " -- the morning check was already complete; nothing was run"
            ),
        )
    )
    lines.append(_row("wording", document["wording"]))
    return lines
