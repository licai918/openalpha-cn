"""One rendering of the panel plane's three read-side answers, for all three faces (`V2-P1-016`).

`V2-P1-012` produces a `PanelHealthReport`, `V2-P1-013` a `DependencyClearance`, and
`panel/catalog.py` a `DatasetReadiness` per dataset. Three faces now hand those to callers --
`V2-P1-015`'s `openalpha panel doctor` / `data-check`, this issue's `GET /api/v1/panel/*`, and
`OpenAlphaSDK.panel_health` / `panel_clearance` / `panel_readiness` -- and every one of them
has to resolve the same request against the same store and render the same answer.

## Why the rendering is a module and not three functions in three places

`cli.py` wrote the first two of these serialisers and said why they were standalone functions:
"two renderings of one report that disagree about which fields exist is how a caller comes to
believe a severity is absent when it was merely dropped". That was a promise about this issue,
and keeping it means the CLI, the HTTP app and the SDK import one definition rather than three
that agree today. `counts_by_severity` is the concrete case: it is total over
`HEALTH_SEVERITIES` rather than built from the findings that happen to be present, so a
severity with no findings reads `0` instead of being missing -- and a second copy of this
function that built the mapping from the findings would make "no blocking findings" and "the
blocking key was never emitted" the same observation on one face and not the other.

## What is deliberately *not* here

HTTP status codes and exit codes. `cli.py` owns `PanelExit` and `api/app.py` owns
`PANEL_HTTP_STATUS`, because "what this channel does about a refusal" is a property of the
channel: a CI job has an exit code and three remedies, an HTTP client has a status class and a
body. What is shared is the *answer*; what is not is the envelope.

`PanelViewError.reason` is not an exception to that. It is the fault's *name*, not its
envelope -- both channels already had a row named `bad_request`, and naming the fault the same
thing is what lets each of them look its own envelope up rather than re-deriving the taxonomy
from `isinstance` checks that drift. A channel with no row for a name gets a `KeyError` at its
own boundary, which is the failure mode worth having.

## Reading a clearance without consuming it

`clearance_payload` reads `cleared_or_none` and never `cleared`, `bool(...)`, `len(...)` or
iteration. `DependencyClearance` raises for those three **even when it cleared** -- Task 36's
deliberate choice, because an accessor that answered on a healthy panel and raised on a sick
one would pass every test written against the first and fail only in production. Every caveat
on this payload is read off the `ClearedDataset` records this function already walks, rather
than through `DependencyClearance.caveat_codes()`, which goes through `cleared` and therefore
raises on a blocked clearance -- the one shape a refusal payload is always built from.

## Two errors, because a refusal and a malformed question are not the same fact

`PanelRequestError` is "this question cannot be put at all": a naive `as_of`, a request naming
no dataset, an exchange name no store could hold. `PanelUnreadableError` is "the panel cannot
answer it as asked": the exchange calendar this request wants to be judged against is not in
the store. The CLI already separates these (`bad_request` versus `unhealthy`) and the reason
is that no amount of re-fetching fixes the first. Neither is raised for a *sick* panel -- that
is a report with findings on it, or a clearance with blocks on it, because a caller has to be
able to read the reasons rather than a traceback.

Each carries a `disclosable` message beside `str(error)`, because one of the three faces is a
network boundary and the other two are not: the CLI and the SDK are inside the process that
owns the store, so a message naming it tells them nothing they did not configure, while a
response body hands that path to whoever could reach the port.

## Why this is a top-level module

`tests/unit/test_import_layering.py` pins `openalpha_cn.panel` as importing no sibling
subpackage at all, and this module must reach `panel_doctor`, `panel_gate`, `panel_ingest`
(for `load_trading_calendar`), `panel` and `domain`. `panel_ingest.py`, `panel_doctor.py` and
`panel_gate.py` are the precedents and the reasoning is theirs: the seam sits *above* the
package it must not be inside, the edges run one way only, and `openalpha_cn.panel`'s real
import closure is unchanged by this module's existence. It is the widest of the four because
it is the last one -- a face joins everything the plane produces -- and its dependency set is
pinned in `tests/unit/test_panel_ingest_import_isolation.py` for that reason.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import ClassVar, Final

from openalpha_cn.domain.trading_calendar import TradingCalendar, TradingCalendarError
from openalpha_cn.panel.catalog import (
    DEFAULT_DATE_TIMEZONE,
    DatasetReadiness,
    PanelStorageError,
    ReadinessIssue,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_doctor import (
    HEALTH_CATEGORIES,
    HEALTH_SEVERITIES,
    HealthFinding,
    PanelHealthReport,
    dataset_health,
)
from openalpha_cn.panel_gate import DependencyClearance, DependencyRequest
from openalpha_cn.panel_ingest import load_trading_calendar, stored_calendar_exchanges


class PanelViewError(RuntimeError):
    """Base for the two faults a panel face can report before any verdict exists.

    Carries two things every channel needs and neither can derive from the exception type
    without restating this module's taxonomy:

    - **`reason`** -- the fault's own name, in the vocabulary both channels are already keyed
      on (`cli.PanelExit.bad_request`, `api.app.PANEL_HTTP_STATUS["bad_request"]`). A channel
      looks the envelope up by this name instead of re-classifying by `isinstance`, so a fault
      added here without a row in a channel's table is a `KeyError` at that channel's boundary
      rather than a silently mis-enveloped refusal.
    - **`disclosable`** -- the message that may cross a process boundary. It differs from
      `str(error)` for one reason: an in-process caller (the SDK, the CLI) is already inside
      the process that owns the store, so naming the store's location tells it nothing it did
      not supply, while an HTTP response body hands that location to whoever asked. Nothing
      built here puts a filesystem path in `disclosable` -- `_without_store_path` takes the
      store's own location back out of a cause that interpolated it -- and
      `tests/integration/test_panel_interfaces.py` drives the shapes that could.
    """

    reason: ClassVar[str] = "panel_view_error"

    def __init__(self, message: str, *, disclosable: str | None = None) -> None:
        super().__init__(message)
        self.disclosable: str = message if disclosable is None else disclosable


class PanelRequestError(PanelViewError):
    """The question cannot be put at all, whatever is in the store.

    A naive `as_of`, a request naming no dataset, an exchange name the store could never hold.
    Distinct from `PanelUnreadableError` because the remedy is to edit the request, not to
    fetch anything.
    """

    reason: ClassVar[str] = "bad_request"


class PanelUnreadableError(PanelViewError):
    """The panel cannot answer the question as asked: the calendar it names is not stored.

    Not a finding, because there is no report to put one on -- the calendar is what the report
    would have been derived against.
    """

    reason: ClassVar[str] = "panel_unreadable"


PANEL_SUBDIRECTORY: Final[str] = "panel"
"""Where the panel plane lives inside a runtime directory, stated once for all three faces.

`runtime_dir/panel`, beside `runtime_dir/state.sqlite3`, so one `--runtime-dir` /
`OPENALPHA_RUNTIME_DIR` / `OpenAlphaSDK(runtime_dir=...)` names one panel. Three faces
disagreeing about where the store is would make every equivalence between them a coincidence.
"""

NO_CALENDAR_REMEDY: Final[str] = (
    "Build it first (`openalpha panel build --dataset trade_cal --year <year>`), or state on "
    "the record that this run has no calendar (`--no-calendar` on the command line, "
    "`calendar=false` over HTTP, `with_calendar=False` in the SDK)"
)
"""The two ways out of a missing calendar, spelled for each face.

Named rather than inlined because the message is the only thing a caller who gets this
refusal has to act on, and the flag is spelled differently on each of the three faces.
"""


PANEL_STORE_PLACEHOLDER: Final[str] = "this service's panel store"
"""What `disclosable` says where a local message says the store's absolute path.

The store's location is configuration of the process that holds it, not an answer about the
panel, and the two callers that can read the local message (`cli.py`, the SDK) are inside that
process already.
"""


def panel_store(runtime_dir: Path) -> PanelStore:
    """The panel plane inside `runtime_dir`."""
    return PanelStore(runtime_dir / PANEL_SUBDIRECTORY)


def _without_store_path(message: str, root: Path) -> str:
    """`message` with the store's own location replaced by a name for it.

    Both spellings, longest first: `Path.resolve()` differs from the configured path wherever
    a component is a symlink (every macOS `/var/...` temporary directory, for one), and a cause
    raised from inside `panel/store.py` can carry either. Replacing the shorter first would
    leave the longer one's prefix behind.

    What is left after the replacement is spelled with `/` on every platform (`V2-P5-064`). It
    is no longer a location -- the location is what the placeholder just took away -- so what
    remains is the dataset, the year and the file, and this string is `disclosable`: it crosses
    a process boundary to a caller who is not on this machine and has no use for its separator.
    Windows produced `this service's panel store\\adj_factor\\2026\\data.parquet`, which is
    neither a path the reader can use nor an identifier two deployments would agree on.
    """
    for path in sorted({str(root), str(root.resolve())}, key=len, reverse=True):
        message = message.replace(path, PANEL_STORE_PLACEHOLDER)
    if os.sep != "/":
        message = message.replace(os.sep, "/")
    return message


def stored_calendar(
    store: PanelStore, *, exchange: str, years: Sequence[int], as_of: datetime
) -> TradingCalendar:
    """The stored exchange calendar, or a refusal naming both ways out.

    `load_trading_calendar` is fail-closed twice over -- a missing, damaged or stale partition
    is blocked by `read_if_ready`, and a non-contiguous set of years is refused afterwards --
    so neither failure can hand back a calendar that reads the missing stretch as a holiday.
    Both arrive here as a refusal rather than as a report about a panel nobody could read.

    The local message names the store; `disclosable` does not. The cause is carried into both,
    because "which of `partition_missing` / `subject_missing` / `field_missing` stood in the
    way" is the actionable half of this refusal -- but it is run through
    `_without_store_path` first, since a `PanelStorageError` about a registered partition whose
    Parquet file is gone interpolates that file's path into its own detail.
    """
    try:
        return load_trading_calendar(store, exchange=exchange, years=years, as_of=as_of)
    except (TradingCalendarError, PanelStorageError) as error:
        remedy = _calendar_remedy(store, exchange=exchange, years=years, cause=error)
        raise PanelUnreadableError(
            f"the {exchange} calendar could not be read out of {store.root}: {error}. {remedy}",
            disclosable=(
                f"the {exchange} calendar could not be read out of {PANEL_STORE_PLACEHOLDER}: "
                f"{_without_store_path(str(error), store.root)}. {remedy}"
            ),
        ) from error


def _calendar_remedy(
    store: PanelStore, *, exchange: str, years: Sequence[int], cause: Exception
) -> str:
    """`NO_CALENDAR_REMEDY`, or the narrower one when the store holds a *different* exchange.

    `V2-P5-046`. The measured refusal was `the SSE calendar cannot be read at ...:
    ['subject_missing']; 1 required subject(s) are absent from trade_cal`, offering exactly two
    ways out: rebuild `trade_cal`, or `--no-calendar`. **Both were wrong.** `trade_cal` was
    built and healthy -- it held `SZSE` -- so rebuilding would have gone to a paid, slow
    provider for a dataset that is already there, and `--no-calendar` discards the check
    instead of answering it. The fix was `--exchange SZSE`, which the same command accepts and
    which returns `rc=0 READY daily`; nothing in the refusal pointed at it.

    So the narrower remedy is offered exactly when the store can support it: the cause is a
    `subject_missing` (the partition is *there*, the exchange is not in it) **and** the census
    names at least one exchange that is not the one asked for. Anything else -- a partition
    genuinely absent, a damaged catalog, a store that holds only the exchange already
    requested -- keeps `NO_CALENDAR_REMEDY` unchanged, because in those cases building really
    is the answer and naming `--exchange` would send a caller to a flag with nothing to put
    after it.

    Keyed on the cause's `subject_missing` code rather than on its type, `_factor_fail`'s rule:
    `load_trading_calendar` folds every blocking issue into one message, and a remedy chosen by
    exception type would offer `--exchange` for a missing Parquet file too.

    The census comes across the seam as `panel_ingest.stored_calendar_exchanges` rather than
    being read here, so this module still **names no dataset of its own** -- the claim
    `RESEARCH_PLANE_DATASETS` records for it in
    `tests/unit/test_panel_ingest_import_isolation.py`, and one that a local
    `read_coverage(TRADING_CALENDAR_DATASET, ...)` would have made false to buy one sentence.
    """
    if "subject_missing" not in str(cause):
        return NO_CALENDAR_REMEDY
    held = tuple(name for name in stored_calendar_exchanges(store, years) if name != exchange)
    if not held:
        return NO_CALENDAR_REMEDY
    requested = ", ".join(str(year) for year in dict.fromkeys(years))
    return (
        f"The calendar dataset is built and holds {', '.join(held)} for {requested}, but not "
        f"{exchange}. Ask for one it holds (`--exchange {held[0]}` on the command line, "
        f"`exchange` over HTTP and in the SDK) -- or, if {exchange} is genuinely the one you "
        f"need, {NO_CALENDAR_REMEDY[0].lower()}{NO_CALENDAR_REMEDY[1:]}"
    )


def panel_request(
    store: PanelStore,
    *,
    datasets: Sequence[str],
    years: Sequence[int],
    sessions: Sequence[date],
    index_codes: Sequence[str],
    as_of: datetime,
    exchange: str,
    with_calendar: bool,
) -> DependencyRequest:
    """Resolve one face's parameters into the stated request all three of them ask.

    `with_calendar` has no default here and none on any face that calls it, which is
    `DependencyRequest`'s own rule: every field that decides how hard the panel is examined is
    mandatory, because the most permissive request must not also be the easiest one to build.
    `calendar=None` switches off every session-scoped cross-check, after which the gate refuses
    a daily-cadence dataset nothing corroborated -- that is a decision, not a default.

    Refuses an empty dataset list here rather than letting it through to a report:
    `require_datasets` has this guard and `panel_health_report` does not, so a health request
    naming nothing would come back `is_clean=True` over zero datasets, which is the empty
    success this whole plane exists to make unavailable.

    Refuses a naive `as_of` by name. `PanelStore` enforces awareness too, but from inside a
    rule table whose job is to name a malformed *partition*; a caller who sent a bare
    `2026-01-17T04:00:00` should be told about that field.

    ## `exchange` when `with_calendar` is false

    `exchange` is mandatory on every face, and when `with_calendar=False` it reaches nothing:
    no calendar is loaded, so no verdict below can differ by it. Two well-formed but different
    exchange names then produce byte-identical answers, which is pinned rather than left to be
    discovered (`tests/integration/test_panel_interfaces.py::
    test_the_exchange_is_inert_when_the_caller_states_this_run_has_no_calendar`).

    That is a fact about `calendar=false`, not a licence to accept anything: the *well-formed*
    rule is applied here unconditionally, and it is `build_trading_calendar`'s own -- a
    non-empty name with no surrounding whitespace, because that is the only name the store can
    ever hold. What this cannot do is tell a typo apart from a real exchange the store has
    never held, because with the calendar switched off there is nothing to compare against; a
    caller who wants a misspelling caught has asked for `calendar=true`, and gets
    `PanelUnreadableError` naming the exchange.
    """
    if type(exchange) is not str or not exchange or exchange != exchange.strip():
        raise PanelRequestError(
            f"exchange must be a non-empty name with no surrounding whitespace; got "
            f"{exchange!r}. This is `domain.trading_calendar.build_trading_calendar`'s own "
            "rule on the name it stores, applied whatever `with_calendar` says, so a name no "
            "store could hold is refused rather than silently reaching nothing"
        )
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise PanelRequestError(
            f"as_of must be a timezone-aware datetime; got {as_of.isoformat()!r}. A naive "
            "instant has no point in time to be judged at"
        )
    requested = tuple(dict.fromkeys(datasets))
    if not requested:
        raise PanelRequestError(
            "this request named no dataset at all; a check that inspected nothing must not "
            "report a verdict"
        )
    resolved_years = tuple(dict.fromkeys(years))
    calendar = (
        stored_calendar(store, exchange=exchange, years=resolved_years, as_of=as_of)
        if with_calendar
        else None
    )
    return DependencyRequest(
        datasets=requested,
        as_of=as_of,
        years=resolved_years,
        sessions=tuple(dict.fromkeys(sessions)),
        calendar=calendar,
        index_codes=tuple(dict.fromkeys(index_codes)),
    )


def dataset_readiness(
    store: PanelStore, request: DependencyRequest
) -> tuple[DatasetReadiness, ...]:
    """Each named dataset's own readiness verdict, in request order.

    The narrowest of the three answers and the only one that runs no cross-dataset check at
    all: `DatasetReadiness` is decided from one dataset's catalog records against the
    requirement its own reader puts, so `DependencyRequest.sessions` is deliberately unused
    here. A caller who needs a session-scoped verdict is asking the health report's question,
    and a caller who needs permission to read is asking the gate's.

    Routed through `panel_doctor.dataset_health` rather than through `evaluate_readiness`
    directly, because `_requirement_for` is what makes the report ask each dataset the same
    question its own loader asks -- including the fallback that waives `required_dates` when no
    calendar reaches a requested year, which is exactly the case a face has to be able to
    report rather than drop.
    """
    return tuple(
        dataset_health(
            store,
            dataset=name,
            as_of=request.as_of,
            years=request.years,
            calendar=request.calendar,
            index_codes=request.index_codes,
            date_timezone=DEFAULT_DATE_TIMEZONE,
        ).readiness
        for name in request.datasets
    )


# --- serialisation ------------------------------------------------------------------------------


def _seconds(span: timedelta | None) -> float | None:
    return None if span is None else span.total_seconds()


def _instant(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _issue_payload(issue: ReadinessIssue) -> dict[str, object]:
    return {
        "code": issue.code,
        "dataset": issue.dataset,
        "detail": issue.detail,
        "year": issue.year,
        "missing_dates": [day.isoformat() for day in issue.missing_dates],
        "missing_items": list(issue.missing_items),
    }


def _finding_payload(finding: HealthFinding) -> dict[str, object]:
    return {
        "code": finding.code,
        "category": finding.category,
        "severity": finding.severity,
        "dataset": finding.dataset,
        "datasets": list(finding.datasets),
        "detail": finding.detail,
        "year": finding.year,
        "count": finding.count,
        "dates": [day.isoformat() for day in finding.dates],
        "items": list(finding.items),
        "related_limitations": list(finding.related_limitations),
    }


def readiness_payload(entries: Sequence[DatasetReadiness]) -> dict[str, object]:
    """Per-dataset readiness as JSON-ready data, losing nothing the verdict carries.

    Refuses an empty sequence. `all_ready` over no dataset is vacuously `True`, and a face that
    answered `{"all_ready": true, "datasets": []}` would be the empty success in its purest
    form -- `panel_request` already refuses the request that produces it, and this refuses the
    rendering, because the two guards protect different callers.

    `checks_waived` is carried per dataset and is not decoration: the empty tuple is the
    *stronger* claim ("every check ran"), and a caller drawing a conclusion from `state ==
    "ready"` has to be able to see which questions were never put.
    """
    if not entries:
        raise PanelRequestError(
            "a readiness report over no dataset at all is not a verdict; every dataset "
            "assessed must appear"
        )
    return {
        "as_of": entries[0].as_of.isoformat(),
        "all_ready": all(entry.is_ready for entry in entries),
        "blocked_datasets": [entry.dataset for entry in entries if not entry.is_ready],
        "datasets": [
            {
                "dataset": entry.dataset,
                "state": entry.state,
                "is_ready": entry.is_ready,
                "years_present": list(entry.years_present),
                "row_count": entry.row_count,
                "subject_count": entry.subject_count,
                "last_event_time": _instant(entry.last_event_time),
                "last_event_date": _day(entry.last_event_date),
                "checks_waived": list(entry.checks_waived),
                "issues": [_issue_payload(issue) for issue in entry.issues],
            }
            for entry in entries
        ],
    }


def health_report_payload(
    report: PanelHealthReport, *, limitation_detail: bool = True
) -> dict[str, object]:
    """A `PanelHealthReport` as JSON-ready data, losing nothing the report carries.

    `limitation_detail=False` drops each limitation's `detail` paragraph and keeps `code`,
    `datasets` and `dates` -- `V2-P4-110`. Measured on a generated panel asked about one dataset,
    the paragraphs were **14,359 of 16,936 bytes (84.8%)** of the answer, and they are static:
    byte-identical on a healthy panel and a broken one, on the first run and the thousandth,
    while the findings the caller asked for were 1,340 bytes. The *text* face has rendered them
    as a count since it was written, for the reason in `cli._echo_report` -- "a human report that
    buried its own findings under them would teach its readers to skim both" -- and this is the
    same choice made available to a machine reader.

    **The default is unchanged and that is a decision.** A registry only served when asked for is
    a registry that stops being read, and the codes are what these entries are *for*. What the
    parameter removes is the obligation to carry the prose on every poll, not the prose.

    **What it deliberately does not do is narrow the set.** The entries here are already scoped:
    `panel_doctor.known_limitations` selects on `wanted & set(item.datasets)`, and the rest are
    `storage_limitations()`, which name no dataset because they hold for every dataset alike.
    Asked about `index_daily`, four of the ten are that dataset's own and six are the storage
    plane's; asked about three datasets, twenty-three come back. The acceptance that raised this
    row read the size as "the whole ledger, unrelated to the dataset asked about", and
    `tests/integration/test_doctor_report_size.py::
    test_the_ledger_this_command_returns_is_already_scoped_to_the_datasets_asked_about` holds the
    measurement that says otherwise, so the narrowing is not attempted a second time.

    `counts_by_severity` is total over `panel_doctor.HEALTH_SEVERITIES` rather than built from
    the findings that happen to be present: a severity with no findings must read `0`, not be
    missing, or "no blocking findings" and "the blocking key was never emitted" become the same
    observation for a consumer.

    `limitations` stays a sibling of `findings` here as it is on the report itself. A
    structural boundary of a dataset and a defect of this fetch are different kinds of fact
    with different remedies, and a payload that merged them would teach its readers to skim
    both.
    """
    counts = dict.fromkeys(sorted(HEALTH_SEVERITIES), 0)
    for finding in report.findings:
        counts[finding.severity] += 1
    return {
        "as_of": report.as_of.isoformat(),
        "is_clean": report.is_clean,
        "counts_by_severity": counts,
        "blocked_datasets": list(report.blocked_datasets),
        "datasets": [
            {
                "dataset": health.dataset,
                "is_ready": health.is_ready,
                "state": health.readiness.state,
                "years_requested": list(health.years_requested),
                "years_present": list(health.readiness.years_present),
                "row_count": health.readiness.row_count,
                "subject_count": health.readiness.subject_count,
                "checks_waived": list(health.readiness.checks_waived),
                "cadence": health.freshness.cadence,
                "max_staleness_seconds": _seconds(health.freshness.max_staleness),
                "freshness_basis": health.freshness.basis,
                "event_age_seconds": _seconds(health.event_age),
                "fetch_age_seconds": _seconds(health.fetch_age),
                "revised_row_count": health.revised_row_count,
                "revision_labels": [[label, count] for label, count in health.revision_labels],
                "codes": [finding.code for finding in health.findings],
            }
            for health in report.datasets
        ],
        "findings": [_finding_payload(finding) for finding in report.findings],
        "cross_checks": [
            {
                "name": check.name,
                "datasets": list(check.datasets),
                "ran": check.ran,
                "skipped_reason": check.skipped_reason,
                "finding_count": check.finding_count,
            }
            for check in report.cross_checks
        ],
        "limitations": [
            {
                "code": limitation.code,
                "datasets": list(limitation.datasets),
                "dates": [day.isoformat() for day in limitation.dates],
                **({"detail": limitation.detail} if limitation_detail else {}),
            }
            for limitation in report.limitations
        ],
    }


def clearance_payload(clearance: DependencyClearance) -> dict[str, object]:
    """A `DependencyClearance` as JSON-ready data.

    Reads `cleared_or_none` and never `cleared`, `bool(...)`, `len(...)` or iteration.
    `DependencyClearance` raises for all three of those **even when it cleared** -- Task 36's
    deliberate choice, because an accessor that answered on a healthy panel and raised on a
    sick one would pass every test written against the first and fail only in production. The
    merged shape has a name that says what it is, and this is the one place in this repository
    that wants it.

    `cleared` entries carry their own width -- the years the year-scoped checks covered, the
    sessions a cross-check actually opened, and the caveats still open outside them -- because
    a bare dataset name is exactly as wide as its reader assumes, and that assumption is how
    `V2-P1-013`'s review found Task 29's wrong number reachable through a *cleared* gate.

    `blocks_by_category` is total over `panel_doctor.HEALTH_CATEGORIES` and is grouped off
    `GateBlock.category`, which the gate resolved through `GATE_CODE_CATEGORIES` -- the union
    of the health table with the gate's own. Grouping through `HEALTH_CODE_CATEGORY` instead
    would raise a `KeyError` on `unverified_daily_coverage`, which is not a health code at all
    because it is not a fault of the panel; a grouped view that swallowed the unmapped code
    would drop the only block on a refusal the gate exists to issue.
    """
    cleared = clearance.cleared_or_none
    return {
        "as_of": clearance.request.as_of.isoformat(),
        "is_blocked": clearance.is_blocked,
        "blocked_datasets": list(clearance.blocked_datasets),
        "blocks": [
            {
                "code": block.code,
                "category": block.category,
                "severity": block.severity,
                "dataset": block.dataset,
                "datasets": list(block.datasets),
                "detail": block.detail,
                "year": block.year,
            }
            for block in clearance.blocks
        ],
        "blocks_by_category": {
            category: [block.code for block in clearance.blocks if block.category == category]
            for category in sorted(HEALTH_CATEGORIES)
        },
        "cleared": (
            None
            if cleared is None
            else [
                {
                    "dataset": entry.dataset,
                    "years": list(entry.years),
                    "corroborated_sessions": [
                        day.isoformat() for day in entry.corroborated_sessions
                    ],
                    "caveats": list(entry.caveats),
                }
                for entry in cleared
            ]
        ),
        "notices": [_finding_payload(notice) for notice in clearance.notices],
        "unverified_checks": [
            {"dataset": name, "checks": list(checks)}
            for name, checks in clearance.unverified_checks
        ],
        "report": health_report_payload(clearance.report),
    }
