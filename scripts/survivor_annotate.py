"""A's veto and B's factors beside the daily list (`V2-P7-027`, `V2-P7-028`).

This is the command the human trader runs after an evening (or at any later time) to see, beside
the day's plan, the two readings the A/B work added: plan A's rule-based entry veto with the
arithmetic the rule was decided from, and plan B's two limit-event factors with their percentile
in the stored cross section. It prints; it does not decide, change, file or request anything.

## It reads two finished things and prints them

**The journal.** One finished survivor day's `openalpha-daily-survivor/v1` journal, read through
`daily_selection.read_survivor_journal` -- never P6's reader, which names another summary step.
The roles are the orders the user acts on, read through `strategy_survivor_morning.plan_names`:

- `ENTRY` -- a *planned buy* or a *carried buy*, the buys on `paper.open_orders`;
- `EXIT` -- a *live sell*, tonight's or one still live from an earlier evening (a sale never
  expires, and a carried sell a role read off the decision would mislabel as a holding);
- `HOLD` -- a holding with no live sell (a sale is still held until the open, so an exiting name
  is not also a holding row);
- `WATCH` -- the next names the rule would draw.

The decision's `entries`/`exits` from `result.paper.decision` are a **cross-check**, not the
source: a planned buy the decision does not name, or a decided exit with no sell order, is a
journal whose orders cannot be matched to the decision that made them and is refused by name. A
day that files no plan is refused too: the epoch day only opens the book, and a settle-only day
ran no evening, so neither has a list to annotate -- and the command refuses rather than
printing an empty one. A journal with no `result` is a day whose steps stopped before its paper
step, and is refused for the same reason.

**The panel.** A clone of the panel (an APFS copy in practice, named with
`--panel-runtime-dir`), opened only through `panel_view.panel_store`. The `panel/catalog.duckdb`
is checked **before** the store is constructed, because `PanelStore.__init__` mkdirs its root: a
runtime directory holding no panel would otherwise be given an empty one by a command whose
whole contract is that it writes nothing.

## A: the veto, read the way the evening read it (R-N3)

`panel_limit_events.veto_window` assembles the session and its four calendar predecessors and
reads each one **at its own 16:30**, so the set is the one the evening had. A session inside the
window that was restated after that instant cannot be answered from the panel any more -- the
store keeps no older version of a restated row -- so the read refuses. The annotation converts
that refusal into its own sentence and exits 1 (R-N3a): the guarantee is "never a different
set", not "always the same set", and printing the restated bytes would show a vetoed set the
evening did not have.

## B: the two factors, off the stored raw build

`raw_cross_section` is `scripts/research/p7_reading.py::_FactorReads._read_year`'s rule for one
session: only `TIER_ADMITTED_CODES["raw"]` values (`computed`) that are finite are read, the
build's own `as_of` decides which session it answers for, and among the builds stamped at or
after that session's 16:30 the **earliest** is used -- a later rebuild on the same evening must
not replace the build the day stood on. The read closes at the end of the session's own
Asia/Shanghai day, so a build published at 16:30, 17:00 or 21:00 that day is that session's and
one published on a later day cannot substitute for it. A panel with no such build is refused
with the exact `openalpha factor build ...` line that produces it (argument names as
`factor_view._unbuilt_factor_remedy` prints them) rather than a blank cell.

`percentile` is the mid-rank over the stored cross section: `(#below + #equal / 2) / N`, so a
name in a cross section that is entirely tied sits at 0.5 -- the rank a display must not read as
"worst" or "best".

## What it refuses by name, and what it never does (R-N2)

Exit 1: no journal, an unfinished journal, an order/decision disagreement, the epoch or a
settle-only day, no panel catalog, a restated session inside the veto window (or one whose
partition/catalog cannot be read), a window that reaches the start of the panel's calendar, a
panel whose calendar is missing or holds more than one exchange, and no raw B build for the
session. Exit 2: usage, and a `--registration` override that is not the journal's registration.

It writes nothing, creates nothing, sends no request and admits no registration -- a journal is
read, never validated against the registration that wrote it. **The smoke banner fails on:** it
is decided by the registration the journal records (resolved from the repository root) once its
sha256 equals the journal's, so a smoke day is bracketed without any flag; when no registration
can be resolved or verified the first and last lines say the smoke status is unknown.
`--registration` is the override for the case the recorded name does not resolve (a pinned
worktree's file), and it must be the same file the journal ran under. Every cell it prints is
候选参考 / candidate reference: not the list, not the paper book, not the journal, and nothing
here is evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import daily_selection  # noqa: E402

from openalpha_cn.backtest.factor_ic import TIER_ADMITTED_CODES  # noqa: E402
from openalpha_cn.domain.factor import FactorDefinition  # noqa: E402
from openalpha_cn.domain.survivor_veto import (  # noqa: E402
    VETO_RULE_ID,
    VetoDetail,
    veto_details,
)
from openalpha_cn.domain.trading_calendar import (  # noqa: E402
    TRADING_CALENDAR_DATASET,
    TradingCalendar,
)
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE, PanelStorageError  # noqa: E402
from openalpha_cn.panel.store import PanelStore  # noqa: E402
from openalpha_cn.panel_factors import (  # noqa: E402
    BROKEN_BOARD_COUNT_20,
    LIMIT_UP_COUNT_20,
    FactorEngineError,
    factor_observation_dataset,
    load_factor_observations,
)
from openalpha_cn.panel_ingest import (  # noqa: E402
    session_publication_instant,
    stored_calendar_exchanges,
)
from openalpha_cn.panel_limit_events import (  # noqa: E402
    LimitEventReadError,
    veto_window,
)
from openalpha_cn.panel_view import (  # noqa: E402
    PANEL_SUBDIRECTORY,
    PanelUnreadableError,
    panel_store,
    stored_calendar,
)
from openalpha_cn.strategy_survivor_morning import (  # noqa: E402
    MorningCheckError,
    PlanNames,
    plan_names,
)

ANNOTATION_KIND: Final[str] = "survivor-annotation/v1"
"""The `--json` document's schema: `session`, `config_id`, `rows`, `wording` (and `smoke`)."""

HEADER: Final[str] = (
    "候选参考 / candidate reference only: not part of the list, the paper book or the journal; "
    "nothing was written and no request was sent"
)
"""The first printed line: what this is, and what it did not touch (R-N2)."""

ROLE_ORDER: Final[tuple[str, ...]] = ("ENTRY", "EXIT", "HOLD", "WATCH")
"""The plan's four roles, in the order the printed rows are grouped."""

B_FACTORS: Final[tuple[FactorDefinition, ...]] = (LIMIT_UP_COUNT_20, BROKEN_BOARD_COUNT_20)
"""Plan B's two factors, in the order their columns are printed."""

_READ_ZONE: Final[ZoneInfo] = ZoneInfo(DEFAULT_DATE_TIMEZONE)
"""Asia/Shanghai: a session's day, and its 16:30, are the exchange's, not the host clock's."""

_DAY_JOURNAL: Final[re.Pattern[str]] = re.compile(r"\d{4}-\d{2}-\d{2}\.json")
"""`<session>.json`, and not the `<session>.morning...json` checks that share the directory."""

REPOSITORY: Final[Path] = _SCRIPTS.parent
"""The checkout this script lives in: a journal records its registration's name relative to it."""

UNVERIFIED_SMOKE_BANNER: Final[str] = (
    "登记未核对，是否冒烟未知 / registration not verified; smoke status unknown"
)
"""The first and last printed line when the run's registration cannot be resolved and checked.

The banner fails **on**, never off (`daily_selection.smoke_banner`'s house rule): a smoke day
whose registration cannot be verified must not lose its banner just because the recorded name
does not resolve from this runtime directory.
"""


class AnnotationError(RuntimeError):
    """The annotation refuses, by name, and writes nothing."""


class MissingFactorBuild(AnnotationError):
    """A session has no raw build of a factor, so the command names the build that makes one.

    `command` is the line the user runs first: the identical form
    `factor_view._unbuilt_factor_remedy` prints for a tier the panel holds no partition of, with
    this session's instant in place of the placeholder and the staleness left as a choice.
    """

    def __init__(self, *, definition: FactorDefinition, session: date) -> None:
        published = session_publication_instant(session)
        self.command: Final[str] = (
            f"openalpha factor build --factor {definition.qualified_key} --tier raw "
            f"--year {session.year} --as-of {published.isoformat()} "
            "--max-staleness-days <days>"
        )
        super().__init__(
            f"the panel holds no raw build of {definition.qualified_key} for "
            f"{session.isoformat()} at or after its 16:30 publication. The annotation prints B's "
            "two factors off the stored raw build and refuses rather than a blank cell"
        )


# --- the reading instant and one factor's cross section ------------------------------------------


def _read_instant(session: date) -> datetime:
    """The end of `session`'s own Asia/Shanghai day: the last instant a build of that day is read.

    A build published later on the session's own day is still that session's build, and a build
    published the next day is not -- so the day's close is the boundary, and it is derived from
    the session alone rather than from whatever clock this run happens to have.
    """
    return datetime.combine(session, time.max, tzinfo=_READ_ZONE)


def raw_cross_section(
    store: PanelStore, definition: FactorDefinition, *, session: date
) -> Mapping[str, float]:
    """One factor's stored raw values for `session`, from the earliest build it answers with.

    `_FactorReads._read_year`'s rule, for one session: the build's `as_of` dates it in
    Asia/Shanghai, a build stamped before the session's 16:30 is not an answer (it did not know
    the close), and the earliest build at or after that instant is the session's. Only
    `TIER_ADMITTED_CODES["raw"]` -- the `computed` code -- and finite values are read, so a name
    the build could not value is absent rather than a number nobody measured.

    A year the panel does not register for this factor is the ordinary "no build" case and is
    answered without reading anything; a year it does register but cannot read is a different
    fault -- a damaged or blocked partition -- and is refused as such, not as a build the
    `factor build` line would produce. That distinction is a catalog fact, not a phrase in the
    engine's exception text.
    """
    published = session_publication_instant(session)
    if session.year not in store.registered_years(factor_observation_dataset(definition)):
        raise MissingFactorBuild(definition=definition, session=session)
    try:
        observations = load_factor_observations(
            store, definition, years=(session.year,), as_of=_read_instant(session)
        )
    except FactorEngineError as error:
        raise AnnotationError(
            f"the panel holds {definition.qualified_key} observations for {session.year} and "
            f"cannot read them at {_read_instant(session).isoformat()}: {error}. The annotation "
            "refuses a partition it cannot read rather than reporting it as a missing build"
        ) from error
    admitted = TIER_ADMITTED_CODES["raw"]
    builds: dict[datetime, dict[str, float]] = {}
    for observation in observations:
        if observation.coverage not in admitted:
            continue
        value = observation.value
        if value is None or not math.isfinite(value):
            continue
        if observation.as_of.astimezone(_READ_ZONE).date() != session:
            continue
        if observation.as_of < published:
            continue
        builds.setdefault(observation.as_of, {})[observation.subject] = value
    if not builds:
        raise MissingFactorBuild(definition=definition, session=session)
    return dict(builds[min(builds)])


def percentile(value: float, cross_section: Sequence[float]) -> float:
    """`value`'s mid-rank in `cross_section`, divided by its length: a share in `[0, 1]`.

    Ties take their mid-rank, so a cross section entirely tied puts every name at 0.5 -- the
    honest reading of "this name is at the middle of what was measured", where a plain count of
    values below would put them all at 0.
    """
    if not cross_section:
        raise ValueError("percentile of an empty cross section is undefined")
    below = sum(1 for item in cross_section if item < value)
    equal = sum(1 for item in cross_section if item == value)
    return (below + equal / 2.0) / len(cross_section)


# --- the annotation document ---------------------------------------------------------------------


def annotate(
    *,
    journal: Mapping[str, Any],
    store: PanelStore,
    calendar: TradingCalendar,
    exchange: str,
) -> dict[str, Any]:
    """The `survivor-annotation/v1` document for one finished journal, read off the panel.

    A: the veto window read by `panel_limit_events.veto_window` (each session at its own 16:30)
    and reduced by `veto_details` to the count, mean and answer per name. B: one raw cross
    section per factor, read once, and each name's value with its mid-rank percentile and the
    cross section's size. Every number is a copy of a stored one; nothing is recomputed here.
    """
    result = journal.get("result")
    if not isinstance(result, Mapping):
        raise AnnotationError(
            "the journal holds no `result`: the day has not finished, and a day whose steps "
            "stopped before its paper step has no plan to annotate"
        )
    config_id = _config_id_of(journal)
    session = _session_of(journal)
    plan = _plan_of(result)
    decision_entries = _decision_names(result, "entries")
    decision_exits = _decision_names(result, "exits")
    details = _veto_details(store, session=session, calendar=calendar, exchange=exchange)
    cross_sections = {
        definition.key: raw_cross_section(store, definition, session=session)
        for definition in B_FACTORS
    }
    rows = [
        _row(role, code, details=details, cross_sections=cross_sections)
        for role, code in _roles(
            plan, decision_entries=decision_entries, decision_exits=decision_exits
        )
    ]
    wording = result.get("wording")
    if not isinstance(wording, str) or not wording:
        raise AnnotationError(
            f"the journal's result carries no survivor wording (got {wording!r}): the annotation "
            "prints the journal's own wording as its footer rather than inventing one"
        )
    return {
        "kind": ANNOTATION_KIND,
        "session": session.isoformat(),
        "config_id": config_id,
        "rows": rows,
        "wording": wording,
    }


def _veto_details(
    store: PanelStore, *, session: date, calendar: TradingCalendar, exchange: str
) -> Mapping[str, VetoDetail]:
    """The veto's answer per name, with one refusal sentence per cause of failure.

    The two failures this window can meet are different questions and are told apart by their
    exception, not by one message that blames a restatement for both:

    - `LimitEventReadError` -- the window's predecessor walk runs past the first session the
      calendar this panel holds knows (a January session whose prior year is not stored, or the
      calendar's own start). The rule needs five sessions and is not answered on a shorter one.
    - `PanelStorageError` -- one of the window's sessions cannot be read at its own 16:30: a row
      restated after that instant (the store keeps no earlier version, so the evening's set
      cannot be reproduced, R-N3a), or its partition/catalog cannot be read at all.
    """
    try:
        window = veto_window(store, session=session, calendar=calendar, exchange=exchange)
    except LimitEventReadError as error:
        prior = session.year - 1
        missing = prior not in set(store.registered_years(TRADING_CALENDAR_DATASET))
        note = (
            f" This panel holds no {prior} trade_cal, so the predecessors before "
            f"{session.year}-01-01 cannot be counted at all."
            if missing
            else ""
        )
        raise AnnotationError(
            f"the veto's five-session window ending {session.isoformat()} cannot be assembled: "
            f"{error}. The rule needs five sessions ending at the signal day, and the calendar "
            f"this panel holds does not reach far enough back from {session.isoformat()} for "
            "them; a shorter window would answer a different question and is not used."
            f"{note} Nothing was written and no request was sent"
        ) from error
    except PanelStorageError as error:
        raise AnnotationError(
            f"the veto's five-session window ending {session.isoformat()} cannot be read at each "
            f"session's own 16:30: {error}. A session inside that window was restated after its "
            "own publication -- the store keeps no earlier version of a restated row, so the "
            "evening's vetoed set cannot be reproduced (R-N3a) -- or its partition, or the "
            "catalog itself, cannot be read at that instant. The window is refused by name "
            "rather than substituted, and nothing was written and no request was sent"
        ) from error
    return veto_details(window)


def _plan_of(result: Mapping[str, Any]) -> PlanNames:
    """The day's plan, refusing the epoch and settle-only days by name."""
    session = result.get("session")
    label = session if isinstance(session, str) else "this day"
    try:
        return plan_names(result)
    except MorningCheckError as error:
        raise AnnotationError(f"{label}: {error}") from error


def _decision_names(result: Mapping[str, Any], key: str) -> tuple[str, ...]:
    """The decision's `entries`/`exits` codes, refused by name when the block is not that shape."""
    paper = result.get("paper")
    if not isinstance(paper, Mapping):
        raise AnnotationError(
            "the journal's result holds no paper block, so it has no decision to read entries "
            "and exits from"
        )
    decision = paper.get("decision")
    if not isinstance(decision, Mapping):
        raise AnnotationError("the journal's paper block holds no decision")
    items = decision.get(key)
    if not isinstance(items, list):
        raise AnnotationError(
            f"the journal's decision holds no list of {key}: the annotation reads one row per "
            f"name the day's decision names, and refuses a decision it cannot read"
        )
    codes: list[str] = []
    for item in items:
        code = item.get("ts_code") if isinstance(item, Mapping) else None
        if not isinstance(code, str):
            raise AnnotationError(
                f"the journal's decision lists a {key} item with no `ts_code` ({item!r})"
            )
        codes.append(code)
    return tuple(codes)


def _roles(
    plan: PlanNames, *, decision_entries: Sequence[str], decision_exits: Sequence[str]
) -> tuple[tuple[str, str], ...]:
    """One `(role, ts_code)` per name, each name once, in `ROLE_ORDER` then by code.

    The roles are the orders the user acts on, not the decision's lists: `ENTRY` is a planned or
    carried buy (a buy still live from an earlier evening is not in tonight's decision and would
    otherwise have no row), `EXIT` is a live sell -- **including a carried one**, which a role
    read off `decision.exits` would mislabel as `HOLD` -- `HOLD` is a holding with no live sell
    (a sale is still held until the open, so an exiting name is not also a holding row), and
    `WATCH` is the next names the rule would draw. The decision is the cross-check
    (`_refuse_orders_the_decision_does_not_name`), so a journal whose orders and decision
    disagree is refused rather than printed.
    """
    _refuse_orders_the_decision_does_not_name(
        plan, decision_entries=decision_entries, decision_exits=decision_exits
    )
    selling = set(plan.selling)
    role_of: dict[str, str] = {}
    for code in (*plan.planned_buys, *plan.carried_buys):
        role_of.setdefault(code, "ENTRY")
    for code in plan.selling:
        role_of.setdefault(code, "EXIT")
    for code in plan.holdings:
        if code in selling:
            continue
        role_of.setdefault(code, "HOLD")
    for code in plan.watch_list:
        role_of.setdefault(code, "WATCH")
    return tuple(
        sorted(
            ((role, code) for code, role in role_of.items()),
            key=lambda item: (ROLE_ORDER.index(item[0]), item[1]),
        )
    )


def _refuse_orders_the_decision_does_not_name(
    plan: PlanNames, *, decision_entries: Sequence[str], decision_exits: Sequence[str]
) -> None:
    """The evening's decision against the plan's orders: a disagreement refuses the annotation.

    Only the directions `build_trade_list` guarantees are checked. Tonight's buys are drawn from
    `decision.entries` (a decided entry that could not be funded produces no order, so a
    *planned buy* must be one of them), and every decided exit becomes a sell order (carried
    sells are extra, and carry no decision of tonight). A name that is both bought and sold is
    never a plan the book could have produced.
    """
    buying = {*plan.planned_buys, *plan.carried_buys}
    both = sorted(buying & set(plan.selling))
    if both:
        raise AnnotationError(
            f"the journal's plan lists {both} both as a buy and as a sell: one name cannot be "
            "both, so its role cannot be printed"
        )
    unplanned = sorted(set(plan.planned_buys) - set(decision_entries))
    if unplanned:
        raise AnnotationError(
            f"the journal's plan and its decision disagree: it holds planned buy(s) {unplanned} "
            f"that the decision's entries do not name ({sorted(decision_entries)}). The "
            "annotation refuses a day whose orders cannot be matched to the decision that made "
            "them"
        )
    unplaced = sorted(set(decision_exits) - set(plan.selling))
    if unplaced:
        raise AnnotationError(
            f"the journal's plan and its decision disagree: the decision's exits {unplaced} are "
            f"not on the plan's sell orders ({sorted(plan.selling)}). The annotation refuses a "
            "day whose orders cannot be matched to the decision that made them"
        )


def _row(
    role: str,
    code: str,
    *,
    details: Mapping[str, VetoDetail],
    cross_sections: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    """One name's row: its role, A's veto (or `null`) and B's two factor cells (or `null`)."""
    detail = details.get(code)
    row: dict[str, Any] = {
        "role": role,
        "ts_code": code,
        "veto": None
        if detail is None
        else {
            "rule": VETO_RULE_ID,
            "vetoed": detail.vetoed,
            "event_count": detail.event_count,
            "mean_weight": detail.mean_weight,
        },
    }
    for definition in B_FACTORS:
        row[definition.key] = _factor_cell(cross_sections[definition.key], code)
    return row


def _factor_cell(cross_section: Mapping[str, float], code: str) -> dict[str, Any] | None:
    """`{value, pct, n}` for a name in the build, else `None` -- never a made-up zero."""
    value = cross_section.get(code)
    if value is None:
        return None
    return {
        "value": value,
        "pct": percentile(value, tuple(cross_section.values())),
        "n": len(cross_section),
    }


# --- the printed form ----------------------------------------------------------------------------


def annotation_lines(document: Mapping[str, Any]) -> list[str]:
    """The printed annotation: the header, one line per row, the journal's wording as footer.

    A smoke registration's banner brackets the whole thing, first and last, read off the
    document's `smoke` key that `daily_selection.with_smoke` wrote there.
    """
    lines = [HEADER]
    for row in document["rows"]:
        lines.append(_row_line(row))
    lines.append(str(document["wording"]))
    banner = document.get(daily_selection.SMOKE_DOCUMENT_KEY)
    if not isinstance(banner, str):
        return lines
    return [banner, *lines, banner]


def _row_line(row: Mapping[str, Any]) -> str:
    """One printed row: role, code, A's veto, then B's two factors, each with its percentile."""
    veto = row["veto"]
    if veto is None:
        veto_text = "none"
    else:
        answer = "vetoed" if veto["vetoed"] else "not vetoed"
        veto_text = (
            f"{veto['rule']}, {answer}, events {veto['event_count']}, "
            f"mean {veto['mean_weight']:.4f}"
        )
    factors = " | ".join(
        f"{definition.key}: {_cell_text(row[definition.key])}" for definition in B_FACTORS
    )
    return f"{row['role']} {row['ts_code']} | veto: {veto_text} | {factors}"


def _cell_text(cell: Mapping[str, Any] | None) -> str:
    if cell is None:
        return "not in the build"
    return f"{cell['value']:.4f} (pct {cell['pct']:.3f} of {cell['n']})"


# --- the journal and the panel -------------------------------------------------------------------


def _config_id_of(journal: Mapping[str, Any]) -> str:
    registration = journal.get("registration")
    config_id = registration.get("config_id") if isinstance(registration, Mapping) else None
    if not isinstance(config_id, str) or not config_id:
        raise AnnotationError(
            f"the journal's registration block carries no config_id (got {config_id!r})"
        )
    return config_id


def _session_of(journal: Mapping[str, Any]) -> date:
    raw = journal.get("session")
    if not isinstance(raw, str):
        raise AnnotationError(f"the journal carries no ISO session date (got {raw!r})")
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise AnnotationError(
            f"the journal's session {raw!r} is not an ISO date: {error}"
        ) from error


def _journal_for(
    runtime_dir: Path, config_id: str, requested: date | None
) -> tuple[date, dict[str, Any]]:
    """`requested`'s journal, or the newest complete one; refused by name when there is none."""
    directory = daily_selection.survivor_journal_directory(runtime_dir, config_id)
    if requested is not None:
        path = directory / f"{requested.isoformat()}.json"
        journal = daily_selection.read_survivor_journal(path)
        if journal is None:
            raise AnnotationError(
                f"no survivor journal for {requested.isoformat()} of configuration {config_id} "
                f"in {directory}: the annotation reads a finished day's journal and has no list "
                "to print without one (run the day first)"
            )
        if not daily_selection.survivor_journal_complete(journal):
            raise AnnotationError(
                f"{path} is not finished: it holds no `result`, so the day stopped before its "
                "paper step. The annotation reads a finished day's plan and refuses to print a "
                "partial one"
            )
        return requested, journal
    newest = _newest_complete(directory)
    if newest is None:
        raise AnnotationError(
            f"no complete survivor journal of configuration {config_id} is filed under "
            f"{directory}: the annotation reads a finished day's journal and has no list to "
            "print without one"
        )
    return newest


def _newest_complete(directory: Path) -> tuple[date, dict[str, Any]] | None:
    """The newest complete `<session>.json`, skipping morning checks and partial files."""
    if not directory.is_dir():
        return None
    for path in sorted(directory.iterdir(), reverse=True):
        if _DAY_JOURNAL.fullmatch(path.name) is None:
            continue
        journal = daily_selection.read_survivor_journal(path)
        if journal is None:
            continue
        if daily_selection.survivor_journal_complete(journal):
            return _session_of(journal), journal
    return None


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _recorded_registration(journal: Mapping[str, Any]) -> tuple[Path | None, str | None]:
    """The registration path the journal records, resolved from the repository root, and its sha256.

    `daily_selection` records the registration's bare file name, so resolving it from a runtime
    directory usually finds nothing; that is the case the caller reports as "not verified"
    rather than silently deciding the run is not a smoke one. An absolute recorded path is used
    as it is.
    """
    registration = journal.get("registration")
    if not isinstance(registration, Mapping):
        return None, None
    recorded = registration.get("path")
    digest = registration.get("sha256")
    path = None
    if isinstance(recorded, str) and recorded:
        candidate = Path(recorded)
        path = candidate if candidate.is_absolute() else REPOSITORY / candidate
    return path, digest if isinstance(digest, str) and digest else None


def _smoke_banner(
    journal: Mapping[str, Any], override: Path | None, *, parser: argparse.ArgumentParser
) -> str | None:
    """The banner the journal's registration says to print, verified against the journal's sha256.

    A **verified** registration decides: `SMOKE_BANNER` when its settings mark a smoke run, else
    no banner. When it cannot be resolved or its sha256 does not equal the journal's, the answer
    is `UNVERIFIED_SMOKE_BANNER` -- fail on -- unless `--registration` supplied the file, in
    which case the override must match the journal exactly: a different file is a usage error
    (exit 2) rather than a banner over the wrong day.
    """
    recorded, digest = _recorded_registration(journal)
    if override is not None:
        given = _sha256(override)
        if given is None:
            parser.error(
                f"--registration {override} cannot be read, so whether this run is a smoke one "
                f"cannot be verified against the journal's recorded sha256 {digest!r}. Nothing "
                "was printed"
            )
        if digest is None or given != digest:
            parser.error(
                f"--registration {override} has sha256 {given}, and the journal records "
                f"{digest!r}: they are different registration files, so the annotation cannot "
                "tell which one this day ran under. Nothing was printed"
            )
        return daily_selection.smoke_banner(override)
    if recorded is None or digest is None or _sha256(recorded) != digest:
        return UNVERIFIED_SMOKE_BANNER
    return daily_selection.smoke_banner(recorded)


def _require_panel_catalog(panel_runtime_dir: Path) -> None:
    """Refuse a runtime directory whose panel would be created by opening it."""
    catalog = panel_runtime_dir / PANEL_SUBDIRECTORY / "catalog.duckdb"
    if catalog.is_file():
        return
    raise AnnotationError(
        f"{catalog} is not there: the annotation reads one panel clone's stored builds and "
        "writes nothing, while `PanelStore` would create the missing root when constructed. "
        "Clone a panel that holds the session's B raw build (an APFS clone: `cp -c`) and name "
        "its runtime directory with --panel-runtime-dir"
    )


def _load_calendar(store: PanelStore, *, session: date) -> tuple[str, TradingCalendar]:
    """The panel's one exchange and the stored calendar the veto window is counted on.

    The exchange is the panel's own: `trade_cal` carries one or more subjects and the annotation
    counts one book's sessions, so it refuses a clone holding more than one exchange's calendar
    rather than guessing. The years are the session's and -- when the panel stores it -- the one
    before, since a January window's four predecessors are in the prior year.
    """
    registered = set(store.registered_years(TRADING_CALENDAR_DATASET))
    if session.year not in registered:
        raise AnnotationError(
            f"the panel holds no {session.year} trade_cal, the calendar the veto's five-session "
            f"window ending {session.isoformat()} is counted on. Clone a panel that covers it"
        )
    years = (session.year - 1, session.year) if session.year - 1 in registered else (session.year,)
    exchanges = stored_calendar_exchanges(store, years)
    if not exchanges:
        raise AnnotationError(
            f"the panel's trade_cal for {', '.join(map(str, years))} holds no exchange's "
            "calendar, so the veto's sessions cannot be counted"
        )
    if len(exchanges) > 1:
        raise AnnotationError(
            f"the panel holds more than one exchange's calendar for {', '.join(map(str, years))} "
            f"({', '.join(exchanges)}): the annotation counts one book's sessions on one "
            "exchange's calendar and cannot tell which. Clone a panel holding the book's exchange"
        )
    exchange = exchanges[0]
    try:
        calendar = stored_calendar(
            store, exchange=exchange, years=years, as_of=_read_instant(session)
        )
    except PanelUnreadableError as error:
        raise AnnotationError(
            f"the {exchange} calendar of {', '.join(map(str, years))} cannot be read at "
            f"{_read_instant(session).isoformat()}: {error}"
        ) from error
    return exchange, calendar


# --- the command ---------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        required=True,
        help="The runtime directory holding the configuration's survivor journal.",
    )
    parser.add_argument(
        "--panel-runtime-dir",
        type=Path,
        default=None,
        help="The runtime directory whose panel/ is read (default: --runtime-dir); in practice "
        "an APFS clone of the panel that holds the session's B raw build.",
    )
    parser.add_argument(
        "--config-id",
        required=True,
        help="The book's configuration id, whose journals are under daily_survivor/<first 16>.",
    )
    parser.add_argument(
        "--session",
        type=date.fromisoformat,
        default=None,
        help="The session to annotate (default: the newest complete journal).",
    )
    parser.add_argument("--json", action="store_true", help="Print the annotation as JSON.")
    parser.add_argument(
        "--registration",
        type=Path,
        default=None,
        help="Read only to decide whether the run belongs to a smoke registration, so the smoke "
        "banner brackets the print (as --show-book does). The annotation admits no registration.",
    )
    arguments = parser.parse_args(argv)
    try:
        session, journal = _journal_for(
            arguments.runtime_dir, arguments.config_id, arguments.session
        )
        banner = _smoke_banner(journal, arguments.registration, parser=parser)
        panel_runtime_dir = (
            arguments.runtime_dir
            if arguments.panel_runtime_dir is None
            else arguments.panel_runtime_dir
        )
        _require_panel_catalog(panel_runtime_dir)
        store = panel_store(panel_runtime_dir)
        exchange, calendar = _load_calendar(store, session=session)
        document = annotate(journal=journal, store=store, calendar=calendar, exchange=exchange)
        document = dict(daily_selection.with_smoke(document, banner))
        if arguments.json:
            print(json.dumps(document, sort_keys=True, ensure_ascii=False))
        else:
            print("\n".join(annotation_lines(document)))
        return int(daily_selection.DailyExit.done)
    except MissingFactorBuild as error:
        print(f"survivor annotation: {error}", file=sys.stderr)
        print(error.command, file=sys.stderr)
        return int(daily_selection.DailyExit.step_failed)
    except AnnotationError as error:
        print(f"survivor annotation: {error}", file=sys.stderr)
        return int(daily_selection.DailyExit.step_failed)
    except daily_selection.StepFailedError as error:
        print(str(error), file=sys.stderr)
        return int(daily_selection.DailyExit.step_failed)
    except (FactorEngineError, PanelStorageError) as error:
        print(
            "survivor annotation: the panel cannot answer this read: "
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return int(daily_selection.DailyExit.step_failed)


if __name__ == "__main__":
    raise SystemExit(main())
