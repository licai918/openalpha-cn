"""A's veto and B's factors beside the daily list (`V2-P7-027`, `V2-P7-028`).

This is the command the human trader runs after an evening (or at any later time) to see, beside
the day's plan, the two readings the A/B work added: plan A's rule-based entry veto with the
arithmetic the rule was decided from, and plan B's two limit-event factors with their percentile
in the stored cross section. It prints; it does not decide, change, file or request anything.

## It reads two finished things and prints them

**The journal.** One finished survivor day's `openalpha-daily-survivor/v1` journal, read through
`daily_selection.read_survivor_journal` -- never P6's reader, which names another summary step.
The plan is `strategy_survivor_morning.plan_names`' own reading (holdings, the open orders, the
decision's watch list) plus the decision's `entries` and `exits` lists from
`result.paper.decision`; each name gets one role, `ENTRY`, `EXIT`, `HOLD` or `WATCH`, with the
decision winning over the book (a decided sale is still held until the open). A day that files
no plan is refused by name: the epoch day only opens the book, and a settle-only day ran no
evening, so neither has a list to annotate -- and the command refuses rather than printing an
empty one. A journal with no `result` is a day whose steps stopped before its paper step, and is
refused for the same reason.

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

Exit 1: no journal, an unfinished journal, the epoch or a settle-only day, no panel catalog, a
restated session inside the veto window, a panel whose calendar is missing or holds more than
one exchange, and no raw B build for the session. Exit 2: usage.

It writes nothing, creates nothing, sends no request and admits no registration -- a journal is
read, never validated against the registration that wrote it. `--registration` is accepted for
one purpose only, `--show-book`'s: to know whether the run belongs to a smoke registration, so
the smoke banner brackets the print (and lands under the JSON document's `smoke` key). Every
cell it prints is 候选参考 / candidate reference: not the list, not the paper book, not the
journal, and nothing here is evidence.
"""

from __future__ import annotations

import argparse
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
    """
    published = session_publication_instant(session)
    try:
        observations = load_factor_observations(
            store, definition, years=(session.year,), as_of=_read_instant(session)
        )
    except Exception as error:  # a blocked year is "no build for this session", not a crash
        if not _is_a_blocked_factor_read(error):
            raise
        raise MissingFactorBuild(definition=definition, session=session) from error
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


def _is_a_blocked_factor_read(error: Exception) -> bool:
    """Whether a factor read failed because the panel holds no build, and not for another reason.

    `load_factor_observations` raises one `FactorEngineError` for a blocked partition (including
    "the year is not registered at all", the ordinary no-build case) and the same type for a
    partition whose rows no longer match their manifest ("... are not the answers that build
    produced"). Only the first is a missing build, and the engine's blocked-read sentence is the
    one it writes from its readiness verdict -- "cannot be read at" -- so that is the phrase this
    recognises; anything else is re-raised and refused as a panel that cannot answer, not as a
    build a factor build command would produce.
    """
    return "cannot be read at" in str(error)


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
    entries = _decision_names(result, "entries")
    exits = _decision_names(result, "exits")
    details = _veto_details(store, session=session, calendar=calendar, exchange=exchange)
    cross_sections = {
        definition.key: raw_cross_section(store, definition, session=session)
        for definition in B_FACTORS
    }
    rows = [
        _row(role, code, details=details, cross_sections=cross_sections)
        for role, code in _roles(plan, entries=entries, exits=exits)
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
    """The veto's answer per name, refusing rather than reproducing a restated window (R-N3a)."""
    try:
        window = veto_window(store, session=session, calendar=calendar, exchange=exchange)
    except (LimitEventReadError, PanelStorageError) as error:
        raise AnnotationError(
            f"the veto's five-session window ending {session.isoformat()} cannot be read at each "
            f"session's own 16:30: {error}. A session inside that window was restated after its "
            "own publication, and the panel store keeps no earlier version of a restated row, so "
            "the vetoed set cannot be reproduced; showing the restated bytes would print a "
            "different set. It is refused by name, not substituted (R-N3a). Nothing was written "
            "and no request was sent"
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
    plan: PlanNames, *, entries: Sequence[str], exits: Sequence[str]
) -> tuple[tuple[str, str], ...]:
    """One `(role, ts_code)` per name, each name once, in `ROLE_ORDER` then by code.

    The decision wins over the book: a held name the evening decided to sell is an `EXIT` row,
    not a `HOLD` one, and a name the decision does not act on is a `HOLD` (still held) or a
    `WATCH` (the next the rule would draw).
    """
    role_of: dict[str, str] = {}
    for code in entries:
        role_of.setdefault(code, "ENTRY")
    for code in exits:
        role_of.setdefault(code, "EXIT")
    for code in plan.holdings:
        role_of.setdefault(code, "HOLD")
    for code in plan.watch_list:
        role_of.setdefault(code, "WATCH")
    return tuple(
        sorted(
            ((role, code) for code, role in role_of.items()),
            key=lambda item: (ROLE_ORDER.index(item[0]), item[1]),
        )
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
        panel_runtime_dir = (
            arguments.runtime_dir
            if arguments.panel_runtime_dir is None
            else arguments.panel_runtime_dir
        )
        _require_panel_catalog(panel_runtime_dir)
        store = panel_store(panel_runtime_dir)
        exchange, calendar = _load_calendar(store, session=session)
        document = annotate(journal=journal, store=store, calendar=calendar, exchange=exchange)
        banner = (
            None
            if arguments.registration is None
            else daily_selection.smoke_banner(arguments.registration)
        )
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
