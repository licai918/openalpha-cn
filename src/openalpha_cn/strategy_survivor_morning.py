"""The survivor day's morning re-check, its pure half (`V2-P7-008`, task D6).

`python scripts/daily_selection.py --runtime-dir RT --morning-recheck` reads again, before the next
call auction, the one thing that can change overnight and matters for tomorrow's open: the name
corpus -- a new ST or delisting-risk announcement published after the evening's read. The script
fetches it (`namechange`, nothing else), reads the evening's facts day again and the morning's, and
hands the two to `compare_morning`; this module is that comparison and the document and the lines
built from it. It is pure: every input is an argument, nothing is read, written, fetched or timed.

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
- nothing at all is `no overnight change to tonight's plan`.

A difference is a **change**: a name the evening already found ineligible and still is, is not news.

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
from zoneinfo import ZoneInfo

from openalpha_cn.domain.survivor_book import ELIGIBILITY_REASON_ORDER, ScreenResult
from openalpha_cn.domain.survivor_policy import FREEZE_COMPATIBLE_REASONS

if TYPE_CHECKING:
    from openalpha_cn.strategy_survivor_facts import FactsDay

__all__ = [
    "MORNING_FILE_SUFFIX",
    "MORNING_SCHEMA",
    "MORNING_WORDING",
    "NO_OVERNIGHT_CHANGE",
    "Difference",
    "EveningReadDriftError",
    "FactsRead",
    "MorningAdvice",
    "MorningCheckError",
    "PlanNames",
    "check_evening_read",
    "check_morning_file",
    "compare_morning",
    "fetch_years",
    "morning_complete",
    "morning_document",
    "morning_lines",
    "plan_names",
    "read_facts",
]

MORNING_SCHEMA: Final[str] = "openalpha-daily-survivor-morning/v1"
MORNING_FILE_SUFFIX: Final[str] = ".morning.json"
"""The morning file is `<session><suffix>` beside the evening's journal `<session>.json`: a name
no reader of the journals' `????-??-??.json` finds."""
NO_OVERNIGHT_CHANGE: Final[str] = "no overnight change to tonight's plan"
MORNING_WORDING: Final[str] = (
    "advice only: candidates of a registered research configuration; no order was placed and the "
    "paper book was not changed -- it follows the evening's plan, and a difference is recorded "
    "here, never committed to it"
)

_SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
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
    """What the comparison found: the differences, in the order they are printed, and the codes
    tolerated as the listing-row class (never advice)."""

    differences: tuple[Difference, ...]
    tolerated: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "differences": [item.to_json() for item in self.differences],
            "tolerated": list(self.tolerated),
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


# --- the fetch, the file and what is printed ------------------------------------------------------


def fetch_years(session: date, as_of: datetime) -> tuple[int, ...]:
    """The announcement years the morning asks the name corpus for: the evening's session's, and
    the morning's own when it is another (the corpus is filed by announcement year, and an
    announcement of the evening is in the old year while the morning is already in the new one --
    one more request, at New Year). The year is the Shanghai calendar's."""
    morning_year = as_of.astimezone(_SHANGHAI).year
    return (session.year,) if morning_year == session.year else (session.year, morning_year)


def morning_complete(document: Mapping[str, Any]) -> bool:
    """Whether a morning file is a finished check's (it holds its `differences`), which a rerun
    prints again with no request and no write; otherwise it is a pin a rerun resumes from."""
    return "differences" in document


def check_morning_file(
    document: Any, *, session: date, evening_snapshot_id: str, registration_sha256: str
) -> None:
    """Refuse, by name, a morning file that is not this evening's: not a mapping of this schema,
    of another session, another evening's snapshot or another registration."""
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
    if not isinstance(document.get("as_of"), str) or not isinstance(document.get("held"), list):
        problems.append("it holds no pinned `as_of` and `held`")
    if problems:
        raise MorningCheckError("; ".join(problems))


def morning_document(pin: Mapping[str, Any], advice: MorningAdvice) -> dict[str, Any]:
    """The finished morning file: the pin (`schema`, `session`, `as_of`, `held`,
    `evening_snapshot_id`, `registration`, the filed evening read and `requests`) with the advice
    and the wording."""
    return {**pin, **advice.to_json(), "wording": MORNING_WORDING}


def _row(label: str, text: str) -> str:
    return f"{label:<{_LABEL_WIDTH}} {text}"


def morning_lines(document: Mapping[str, Any], *, this_run: Mapping[str, Any]) -> list[str]:
    """What the command prints for a finished morning file: the advice block, the requests this run
    made (`this_run`: `requests`, `request_count`, `wrote`) and the wording."""
    differences = list(document["differences"])
    lines = [
        _row(
            "morning re-check",
            f"the plan of {document['session']}, read again as of {document['as_of']}, before the "
            "next session's call auction -- advice only: the paper book follows the evening's "
            "plan and was not changed",
        )
    ]
    if differences:
        lines.append(_row("advice", f"{len(differences)} difference(s) from tonight's plan"))
        lines += [f"  {item['text']}" for item in differences]
    else:
        lines.append(_row("advice", NO_OVERNIGHT_CHANGE))
    tolerated = list(document["tolerated"])
    if tolerated:
        lines.append(
            _row(
                "tolerated",
                f"{', '.join(tolerated)} (priced before its registry listing row: a row aged 0 "
                "tonight, none this morning, the counts account for it -- not advice)",
            )
        )
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
