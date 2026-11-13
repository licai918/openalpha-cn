"""The V2-P7-010 event-interface probe (plan C, task 2.2): the request plan, its hard budget, its
findings and its metadata-only report.

The five Tushare event interfaces a plan-C evidence producer might read -- `moneyflow`,
`limit_list_d`, `top_list`, `top_inst` and `anns_d` -- are candidates the decision chain says must
be verified before any design names them (`openalpha-cn-v2-p7-decision-chain.md` §8: 哪些可用、
权限与积分要求、单日请求量、历史深度、是否带发布时刻). This script is the tooling for that
verification and nothing else: it never writes a panel or a database, retains no row, and its one
report is metadata. Its only write is the cumulative budget file a `--live` invocation must
record (`--budget-file`), which is not research data.

**The cap is the budget.** `probe_plan` returns one `ProbeCall` for each interface's first
request: five interfaces times a schema probe, one whole-market day, a PIT-timestamp-field probe
and one whole-market request per requested depth year. `test_the_plan_never_exceeds_300_requests`
pins that its length never exceeds `PROBE_CAP`, and the function refuses a depth-year list that
would: 300 is a hypothesis the user can raise, and it is enforced twice -- the plan refuses to
exceed it, and `run_probe`'s counter refuses the request that would pass it. The plan is not the
whole spend: a whole-market day that fills a page buys follow-up `offset` requests, and those are
counted by the same counter, so one invocation still never forwards more than `PROBE_CAP`.

**One planned call, one finding; a full market day may take several requests.** Every sent
request yields exactly one `ProbeFinding` (a non-zero Tushare `code` and a transport exception are
findings too). A `full_day` page that reports Tushare's `has_more` flag -- only the literal
`False` means complete -- or fills `PAGE_ROW_LIMIT` rows with the flag absent buys one follow-up
request with `offset`/`limit`, and the finding reports every page and the total rows. `pages` is
the number of data pages received (0 for a refusal or a failed transport), and `truncated` says
whether `rows` is a lower bound: `possibly` when a page filled the documented limit and the probe
had no budget left to confirm it, `yes` when the endpoint's own flag said more rows exist (or a
follow-up page failed) and they were not fetched. `PAGE_ROW_LIMIT` is the vendor's documented
default, **to CONFIRM at the gated live step L1 -- it is not verified here**. The depth and
PIT-field probes are not paged: they answer one day or one security, so their row count is a
lower bound when a page fills the limit, and the question they answer -- does the interface answer
at all -- does not depend on the page total.

**The cap rides `p7_smoke_requests`' counter.** `p7_smoke_requests.CappedCounter` is imported and
incremented *before* the request is forwarded, so the request that would pass `PROBE_CAP` is never
built and never sent. It is constructed directly rather than through `capped_requests`, and that
is deliberate: the context manager patches `cli._panel_transport` and would take the transport out
of the caller's hands, while `run_probe` takes it as an argument so tests can pass a scripted one
and open a fake-transport seam with no process-global state.

**Answers are findings, never crashes.** A non-zero Tushare `code` (e.g. `40101`, a credential or
endpoint refusal) and a transport-level exception both become a `permitted=False` finding with an
`error_code` -- the Tushare code as text, or the exception's class name. Messages are never kept:
a provider message can carry a credential (see `cli._fetch_panel`'s reasoning), and they are not
metadata.

**The token is the environment's.** `TUSHARE_TOKEN` is read at send time and put in the request
envelope; it is never hardcoded, printed, returned in a finding or written anywhere. `run_probe`
is what reads it, so a scripted transport in a test receives only the test environment's value.

**The modes.** `main` defaults to `--dry-run`: prints the plan and its count, sends nothing,
writes nothing, and ignores `--budget-file` (it neither reads nor creates it). `--live` is the
only path that sends, and it refuses without `--i-have-the-users-go`, without a `TUSHARE_TOKEN`
read from the environment at send time, and without a `--budget-file`; even then it prints the
metadata report and writes only the budget file. `--live` is never run by this repository's tests
or by the batch that added this script: it is gated live step L1 and waits for the user's
explicit go.

**The budget is cumulative across invocations.** `--live` requires `--budget-file PATH`: a small
JSON file (`PROBE_BUDGET_SCHEMA`, one `spent` field, cumulative requests), read before the run and
rewritten atomically (a temporary file beside it, then `os.replace`). The invocation's allowance
is `PROBE_CAP - spent`, so a file at `PROBE_CAP` refuses before any request is sent, and the 300
is a ceiling on the live step rather than on one invocation. A live run that ends on a fault --
and a partial run the cap stopped -- still writes what it actually sent (attempts are counted
before the transport is entered, the conservative side). A process killed between its sends and
that write cannot record them: a `--live` run that does not end with its report must be accounted
for by hand before `--live` runs again. The file is a budget, not a lock: one live invocation
runs at a time (L1 is a single invocation).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final, Literal

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import p7_smoke_requests as budget  # noqa: E402

from openalpha_cn import cli  # noqa: E402
from openalpha_cn.providers.tushare import (  # noqa: E402
    TUSHARE_CREDENTIAL_CODE,
    TUSHARE_RATE_LIMIT_CODE,
    TUSHARE_RESPONSE_TRUNCATION_FLAG,
    TushareTransport,
)

PROBE_CAP: Final[int] = 300
"""The live probe's request ceiling: the plan refuses to plan past it and `run_probe` refuses to
send past it. A hypothesis (plan C's Task 2.2), adjustable only by asking the user."""

PAGE_ROW_LIMIT: Final[int] = 6000
"""The vendor's documented default rows per response for a Tushare Pro cross-section, and the
probe's hypothesis for the five event interfaces -- none of their own caps is measured. The
repository has measured exactly 6,000 rows on `daily`, `daily_basic` and `adj_factor`, each with
`has_more=True` (`providers/tushare.py`), and a live response carries its own `has_more` flag,
which the probe reads as the stronger witness. **To CONFIRM at the gated live step L1; not
verified here.** A `full_day` answer that fills this many rows without a flag buys an `offset`
follow-up request, and an answer at this count that the probe could not follow is flagged
`truncated="possibly"` rather than reported as a total."""

MAX_PAGES_PER_CALL: Final[int] = 10
"""How many pages one `full_day` call may take before the probe stops and flags the answer
truncated. 10 x `PAGE_ROW_LIMIT` = 60,000 rows is far above the whole-market day any of the five
candidate interfaces serves. The ceiling is there so a server that ignores `offset` cannot spend
the whole `PROBE_CAP` on one call; the cap is still the outer bound on requests."""

PROBE_BUDGET_SCHEMA: Final[str] = "openalpha-p7-event-probe-budget/v1"
"""What a `--budget-file` holds: one JSON object, `{"schema": PROBE_BUDGET_SCHEMA, "spent": N}`
with `N` the cumulative requests every earlier `--live` invocation sent. It is not research data;
it is what makes `PROBE_CAP` a ceiling on the live step instead of on one invocation."""

EVENT_INTERFACES: Final[tuple[str, ...]] = (
    "moneyflow",
    "limit_list_d",
    "top_list",
    "top_inst",
    "anns_d",
)
"""The five Tushare event interfaces the decision chain names as candidates (§8)."""

_SCHEMA_SUBJECT: Final[str] = "000001.SZ"
"""One representative security for the cheap schema and PIT-field probes: a valid `ts_code` turns
a whole-market endpoint into a one-stock answer without needing a second parameter set."""

_TUSHARE_TOKEN_ENV: Final[str] = "TUSHARE_TOKEN"
"""The credential's environment variable. The value is read at send time and never stored."""

_KNOWN_CODES: Final[Mapping[str, str]] = {
    str(TUSHARE_CREDENTIAL_CODE): "credential or endpoint refused",
    str(TUSHARE_RATE_LIMIT_CODE): "per-interface rate limit",
}
"""What the two measured Tushare rejection codes mean, so a report names them without reading a
message (the message can carry a credential, so it is never kept)."""

REFUSED_EXIT: Final[int] = 2
"""What a refusal of this script exits with (a usage error is 2 too): the invocation sent nothing
and recorded nothing. The one exception prints its report and says the budget file could not
record the requests that were sent."""


class ProbeBudgetError(ValueError):
    """A budget file the probe refuses to use; nothing was sent."""


Purpose = Literal["schema", "full_day", "depth", "pit_field"]

Truncation = Literal["no", "possibly", "yes"]
"""Whether a finding's `rows` is the total or a lower bound: `no` = the answer is complete,
`possibly` = a page filled the documented row limit and the probe could not confirm there is no
next page, `yes` = the server's own flag said more rows exist (or a follow-up page failed) and
they were not fetched. The two are kept apart because only the first is certain."""


@dataclass(frozen=True)
class ProbeCall:
    """One planned Tushare request: which interface, with which params, and why."""

    api_name: str
    params: Mapping[str, str]
    purpose: Purpose


@dataclass(frozen=True)
class ProbeFinding:
    """What one planned call answered: metadata only, never a row value.

    `rows` is the number of rows across every data page the call received, and `pages` is how
    many data pages that was: 0 when the answer was a refusal or a transport failure, and more
    than 1 when a `full_day` answer was paged. `truncated` says whether `rows` is a lower bound
    (`possibly`/`yes`) or the total (`no`). `error_code` is the first non-zero Tushare code or
    transport exception class name; when a follow-up page failed, it is that page's code beside
    `permitted=True`. `earliest_date_seen` is the earliest value of a `*_date` column parsed
    across the returned rows -- only the date is kept, never the cell -- and `timestamp_fields`
    names the columns whose names look like a publication instant, for the PIT decision the live
    step has to make.
    """

    api_name: str
    permitted: bool
    error_code: str | None
    rows: int
    pages: int
    fields: tuple[str, ...]
    earliest_date_seen: date | None
    timestamp_fields: tuple[str, ...]
    truncated: Truncation


# --- the plan -----------------------------------------------------------------------------------


_FIXED_PURPOSES: Final[tuple[Purpose, ...]] = ("schema", "full_day", "pit_field")
"""The probes of one interface that do not depend on `depth_years`; `depth` adds one per year."""


def probe_plan(*, today: date, depth_years: Sequence[int]) -> tuple[ProbeCall, ...]:
    """The whole probe: one request per call, at most `PROBE_CAP`.

    Parameters are the documented Tushare ones and are unverified until the gated live step L1
    runs -- the probe's job is to discover whether they answer. No trading calendar is available
    offline, so a weekday on or before the date is used as the guess at a session; a holiday shows
    up as `rows=0` in the report, which is not evidence that the interface is absent.

    `depth_years` are the whole years back to also probe, one whole-market request per interface
    each (e.g. `(1, 2, 3, 4, 5)`), and the plan refuses a list whose calls would pass
    `PROBE_CAP` rather than silently truncating the history it was asked to measure.
    """
    years = _depth_years(depth_years)
    calls = len(EVENT_INTERFACES) * (len(_FIXED_PURPOSES) + len(years))
    if calls > PROBE_CAP:
        raise ValueError(
            f"{len(years)} depth year(s) make {calls} probes, and PROBE_CAP is {PROBE_CAP}; "
            "not one of them was planned -- ask the user before the probe's budget is raised"
        )

    settled = _weekday(today - timedelta(days=1))
    earlier = _weekday(settled - timedelta(days=1))
    plan: list[ProbeCall] = []
    for api_name in EVENT_INTERFACES:
        plan.append(
            ProbeCall(
                api_name=api_name, params=_subject_params(api_name, settled), purpose="schema"
            )
        )
    for api_name in EVENT_INTERFACES:
        plan.append(
            ProbeCall(
                api_name=api_name, params=_market_params(api_name, settled), purpose="full_day"
            )
        )
    for api_name in EVENT_INTERFACES:
        plan.append(
            ProbeCall(
                api_name=api_name, params=_subject_params(api_name, earlier), purpose="pit_field"
            )
        )
    for years_back in years:
        day = _depth_day(settled, years_back)
        for api_name in EVENT_INTERFACES:
            plan.append(
                ProbeCall(api_name=api_name, params=_market_params(api_name, day), purpose="depth")
            )
    return tuple(plan)


def _depth_years(values: Sequence[int]) -> tuple[int, ...]:
    """The depth years as a sorted, de-duplicated tuple, or a refusal."""
    years: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(
                f"a depth year is a whole number of years back, 1 or more, not {value!r}: "
                "nothing was planned"
            )
        years.append(value)
    return tuple(sorted(set(years)))


def _weekday(day: date) -> date:
    """`day`, or the most recent Monday-Friday before it (the offline guess at a session)."""
    while day.weekday() > 4:
        day -= timedelta(days=1)
    return day


def _depth_day(settled: date, years_back: int) -> date:
    """The weekday probed `years_back` whole years before `settled` (Feb 29 becomes Feb 28)."""
    try:
        day = settled.replace(year=settled.year - years_back)
    except ValueError:
        day = settled.replace(year=settled.year - years_back, day=28)
    return _weekday(day)


def _stamp(day: date) -> str:
    return day.strftime("%Y%m%d")


def _subject_params(api_name: str, day: date) -> Mapping[str, str]:
    """The cheap one-subject request of an interface: the schema and PIT-field probes use it."""
    stamp = _stamp(day)
    if api_name == "anns_d":
        return {"ts_code": _SCHEMA_SUBJECT, "ann_date": stamp}
    if api_name in ("top_list", "top_inst"):
        return {"ts_code": _SCHEMA_SUBJECT, "trade_date": stamp}
    return {"ts_code": _SCHEMA_SUBJECT, "start_date": stamp, "end_date": stamp}


def _market_params(api_name: str, day: date) -> Mapping[str, str]:
    """The whole-market request of one day: the full-day and depth probes use it."""
    stamp = _stamp(day)
    if api_name == "anns_d":
        return {"ann_date": stamp}
    return {"trade_date": stamp}


# --- sending, under the cap ---------------------------------------------------------------------


def run_probe(
    plan: Sequence[ProbeCall], *, transport: TushareTransport, cap: int = PROBE_CAP
) -> tuple[ProbeFinding, ...]:
    """Send `plan` through `transport`, one request per call, and record one finding per request.

    The cap is `p7_smoke_requests.CappedCounter`: incremented before the transport is reached, so
    a call past `cap` is never sent and the probe stops there. The token comes from
    `TUSHARE_TOKEN` at send time and is never kept; `transport` is the caller's (tests pass a
    scripted one).
    """
    counter = budget.CappedCounter(cap)
    findings: list[ProbeFinding] = []
    for call in plan:
        finding = _probe_call(call, transport, counter)
        if finding is None:
            break
        findings.append(finding)
    return tuple(findings)


_FLAG_ABSENT: Final[object] = object()
"""A sentinel: the response carried no truncation flag at all (which is not the same as `null`)."""


@dataclass(frozen=True)
class _Page:
    """One response parsed into metadata only: a row value is read for the earliest date and then
    dropped, never stored."""

    permitted: bool
    error_code: str | None
    rows: int
    fields: tuple[str, ...]
    earliest_date_seen: date | None
    timestamp_fields: tuple[str, ...]
    more: bool | None
    """The server's `has_more`: `False` is the only complete answer, `None` is absent from the
    response -- the case the documented row limit has to judge."""


def _probe_call(
    call: ProbeCall, transport: TushareTransport, counter: budget.CappedCounter
) -> ProbeFinding | None:
    """One planned call's finding, or `None` when the cap stopped its first request unsent.

    A `full_day` answer is paged: while the last page says more rows exist (`has_more=True`, or
    the documented row limit with no flag) and the cap allows, one follow-up `offset` request is
    sent. The finding aggregates every page; `truncated` records a page the probe could not
    follow. Nothing else is paged.
    """
    if not _spend(counter, call.api_name):
        return None
    response, failure = _post(transport, _envelope(call))
    if response is None:
        return _refused(call.api_name, failure or "invalid_response")
    page = _parse_response(response)
    if not page.permitted:
        return _refused(call.api_name, page.error_code)
    if page.error_code is not None:  # permitted, but the body was not a data page
        return _invalid(call.api_name)

    pages = [page]
    truncated: Truncation = "no"
    follow_up_error: str | None = None
    if call.purpose == "full_day":
        while _wants_more(pages[-1]):
            if len(pages) >= MAX_PAGES_PER_CALL:
                truncated = _truncation_when_stopped(pages[-1])
                break
            if not _spend(counter, call.api_name):
                truncated = _truncation_when_stopped(pages[-1])
                break
            response, failure = _post(
                transport, _envelope(_offset_call(call, offset=sum(page.rows for page in pages)))
            )
            if response is None:
                truncated = _truncation_when_stopped(pages[-1])
                follow_up_error = failure
                break
            page = _parse_response(response)
            if not page.permitted or page.error_code is not None:
                truncated = _truncation_when_stopped(pages[-1])
                follow_up_error = page.error_code
                break
            pages.append(page)
    return _finding_of(call.api_name, pages, truncated=truncated, error_code=follow_up_error)


def _wants_more(page: _Page) -> bool:
    """Whether the page says, or the documented limit leaves it possible, that rows remain."""
    return page.more is True or (page.more is None and page.rows >= PAGE_ROW_LIMIT)


def _truncation_when_stopped(page: _Page) -> Truncation:
    """Why the page could not be followed: `yes` when the server said more, else `possibly`."""
    return "yes" if page.more is True else "possibly"


def _spend(counter: budget.CappedCounter, api_name: str) -> bool:
    """Take one request from the cap; `False` when it is spent and nothing was sent."""
    try:
        counter[api_name] += 1
    except budget.RequestCapReached:
        return False
    return True


def _envelope(call: ProbeCall) -> dict[str, Any]:
    """The Tushare request envelope: the credential is read at send time, never stored."""
    return {
        "api_name": call.api_name,
        "token": os.environ.get(_TUSHARE_TOKEN_ENV, ""),
        "params": dict(call.params),
        "fields": "",
    }


def _offset_call(call: ProbeCall, *, offset: int) -> ProbeCall:
    """The follow-up request for a whole-market answer: the same call plus `offset`/`limit`."""
    return ProbeCall(
        api_name=call.api_name,
        params={**call.params, "offset": str(offset), "limit": str(PAGE_ROW_LIMIT)},
        purpose=call.purpose,
    )


def _post(
    transport: TushareTransport, envelope: dict[str, Any]
) -> tuple[Mapping[str, Any] | None, str | None]:
    """The decoded response, or `None` and the transport exception's class name.

    The exception's message is never kept: it can carry the token.
    """
    try:
        return transport.post(envelope), None
    except Exception as error:  # a transport failure is a finding, not a crash
        return None, type(error).__name__


def _parse_response(response: Mapping[str, Any]) -> _Page:
    """One response as a `_Page`: a non-zero code is a refusal, a body without `data` is invalid,
    and a data page carries its own `has_more` when the server sends one."""
    code = response.get("code")
    if code != 0:
        return _refused_page(_error_code(code))
    data = response.get("data")
    if not isinstance(data, dict):
        return _invalid_page()
    raw_fields = data.get("fields")
    fields = (
        tuple(field for field in raw_fields if isinstance(field, str))
        if isinstance(raw_fields, list)
        else ()
    )
    raw_items = data.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    raw_more = data.get(TUSHARE_RESPONSE_TRUNCATION_FLAG, _FLAG_ABSENT)
    more = None if raw_more is _FLAG_ABSENT else raw_more is not False
    return _Page(
        permitted=True,
        error_code=None,
        rows=len(items),
        fields=fields,
        earliest_date_seen=_earliest_date(fields, items),
        timestamp_fields=tuple(field for field in fields if _looks_like_timestamp(field)),
        more=more,
    )


def _refused_page(error_code: str | None) -> _Page:
    return _Page(
        permitted=False,
        error_code=error_code,
        rows=0,
        fields=(),
        earliest_date_seen=None,
        timestamp_fields=(),
        more=None,
    )


def _invalid_page() -> _Page:
    return _Page(
        permitted=True,
        error_code="invalid_response",
        rows=0,
        fields=(),
        earliest_date_seen=None,
        timestamp_fields=(),
        more=None,
    )


def _finding_of(
    api_name: str,
    pages: Sequence[_Page],
    *,
    truncated: Truncation,
    error_code: str | None = None,
) -> ProbeFinding:
    """The aggregated finding of one planned call's pages: counts are total, dates the earliest."""
    dates = [page.earliest_date_seen for page in pages if page.earliest_date_seen is not None]
    return ProbeFinding(
        api_name=api_name,
        permitted=True,
        error_code=error_code,
        rows=sum(page.rows for page in pages),
        pages=len(pages),
        fields=_ordered_union(page.fields for page in pages),
        earliest_date_seen=min(dates) if dates else None,
        timestamp_fields=_ordered_union(page.timestamp_fields for page in pages),
        truncated=truncated,
    )


def _refused(api_name: str, error_code: str | None) -> ProbeFinding:
    """A first request the endpoint refused or the transport could not deliver."""
    return ProbeFinding(
        api_name=api_name,
        permitted=False,
        error_code=error_code,
        rows=0,
        pages=0,
        fields=(),
        earliest_date_seen=None,
        timestamp_fields=(),
        truncated="no",
    )


def _invalid(api_name: str) -> ProbeFinding:
    """A permitted answer whose body was not a data page: permitted, but no rows to count."""
    return ProbeFinding(
        api_name=api_name,
        permitted=True,
        error_code="invalid_response",
        rows=0,
        pages=0,
        fields=(),
        earliest_date_seen=None,
        timestamp_fields=(),
        truncated="no",
    )


def _error_code(code: Any) -> str:
    """A non-zero Tushare code as text, or `missing_code` when the envelope carried none."""
    if code is None:
        return "missing_code"
    return str(code)


def _earliest_date(fields: tuple[str, ...], items: list[Any]) -> date | None:
    """The earliest `*_date` cell across the returned rows -- the date only, nothing else."""
    dates: list[date] = []
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            continue
        for field, value in zip(fields, item, strict=True):
            if not field.lower().endswith("_date") or not isinstance(value, str):
                continue
            parsed = _parse_date(value)
            if parsed is not None:
                dates.append(parsed)
    return min(dates) if dates else None


def _parse_date(value: str) -> date | None:
    """A `YYYYMMDD` or `YYYY-MM-DD` cell as a `date`, or `None` when it is not a date."""
    text = value.strip()
    try:
        if len(text) == 8 and text.isdigit():
            return date(int(text[:4]), int(text[4:6]), int(text[6:]))
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return date(int(text[:4]), int(text[5:7]), int(text[8:]))
    except ValueError:
        return None
    return None


def _looks_like_timestamp(field: str) -> bool:
    """Whether a column name is a candidate publication instant (a name match, not proof)."""
    lowered = field.lower()
    return lowered.endswith("_date") or "time" in lowered or lowered.endswith("_at")


# --- the report ---------------------------------------------------------------------------------


def render_report(
    findings: Sequence[ProbeFinding], *, spent: int, cumulative: int | None = None
) -> str:
    """The metadata report: no row value, no message, no token, nothing persisted.

    One interface's findings are folded into one line: `permitted` is `yes` only when every call
    to it was permitted (`partial` when some were, `no` when none was), the row and page counts
    are the largest single answer -- a whole-market day, paged when it filled a page -- the
    earliest date is the earliest seen across the calls, and the field and timestamp names are
    the union in first-seen order. `truncated` is `yes` if any call's row count is a hard lower
    bound, else `possibly` if any is a possible one, else `no`; the paging note says which is
    which. `spent` is this invocation's requests; `cumulative`, when given, is the budget file's
    total after the run.
    """
    grouped: dict[str, list[ProbeFinding]] = {}
    for finding in findings:
        grouped.setdefault(finding.api_name, []).append(finding)

    lines = [
        "# Tushare event-interface probe (V2-P7-010) -- metadata report",
        "",
        "> Tooling output for the gated live step L1. Metadata only, not evidence; no row value is",
        "> printed or persisted. 候选 / candidate wording applies to anything drawn from it.",
        "",
        f"- Requests spent: {spent} of PROBE_CAP ({PROBE_CAP}).",
        f"- Interfaces reported: {len(grouped)}.",
    ]
    if cumulative is not None:
        lines.append(
            f"- Cumulative budget: {cumulative} of PROBE_CAP ({PROBE_CAP}) requests spent."
        )
    lines.extend(
        [
            "",
            f"- Full-day paging: a `full_day` answer with `has_more=true`, or {PAGE_ROW_LIMIT} "
            "rows and",
            "  no flag, buys one `offset` follow-up request from the same cap. The row limit is",
            "  the vendor's documented default, to CONFIRM at the gated live step L1 -- not "
            "verified here.",
            "- Truncation: `no` = complete; `possibly` = a page at the row limit with no flag and "
            "no",
            "  follow-up sent; `yes` = the endpoint's flag said more rows exist (or a follow-up "
            "page",
            "  failed) and they were not fetched. `rows` is then a lower bound, not the daily "
            "total.",
            "  An error code beside a permitted interface is a follow-up page's refusal.",
            "",
            "| interface | permitted | error code | rows | pages | truncated | earliest date seen "
            "| timestamp fields |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for api_name, group in grouped.items():
        permitted = _permitted_of(group)
        codes = _codes_of(group)
        rows = max(finding.rows for finding in group)
        pages = max(finding.pages for finding in group)
        truncated = _truncated_of(group)
        earliest = _earliest_of(group)
        timestamps = _ordered_union(finding.timestamp_fields for finding in group)
        lines.append(
            f"| `{api_name}` | {permitted} | {', '.join(codes) if codes else '-'} | {rows} "
            f"| {pages} | {truncated} | {earliest.isoformat() if earliest is not None else '-'} "
            f"| {_names(timestamps)} |"
        )

    lines.extend(["", "## Field names", ""])
    if not grouped:
        lines.append("- none: the probe produced no finding.")
    for api_name, group in grouped.items():
        fields = _ordered_union(finding.fields for finding in group)
        lines.append(f"- `{api_name}` ({len(fields)} field(s)): {_names(fields)}")

    lines.extend(["", "## Error codes", ""])
    codes = _codes_of([finding for group in grouped.values() for finding in group])
    if not codes:
        lines.append("- none: every finding was permitted.")
    for code in codes:
        meaning = _KNOWN_CODES.get(code)
        lines.append(
            f"- `{code}`: {meaning}" if meaning is not None else f"- `{code}`: unclassified"
        )

    return "\n".join(lines) + "\n"


def _permitted_of(group: Sequence[ProbeFinding]) -> str:
    if all(finding.permitted for finding in group):
        return "yes"
    if any(finding.permitted for finding in group):
        return "partial"
    return "no"


def _truncated_of(group: Sequence[ProbeFinding]) -> Truncation:
    """The worst truncation among a group's findings: a hard lower bound outranks a possible one."""
    found = {finding.truncated for finding in group}
    if "yes" in found:
        return "yes"
    if "possibly" in found:
        return "possibly"
    return "no"


def _codes_of(group: Sequence[ProbeFinding]) -> list[str]:
    codes: list[str] = []
    for finding in group:
        if finding.error_code is not None and finding.error_code not in codes:
            codes.append(finding.error_code)
    codes.sort()
    return codes


def _earliest_of(group: Sequence[ProbeFinding]) -> date | None:
    dates: list[date] = []
    for finding in group:
        if finding.earliest_date_seen is not None:
            dates.append(finding.earliest_date_seen)
    return min(dates) if dates else None


def _ordered_union(groups: Iterable[Sequence[str]]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for group in groups:
        for value in group:
            seen.setdefault(value, None)
    return tuple(seen)


def _names(values: tuple[str, ...]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "-"


def render_plan(plan: Sequence[ProbeCall], *, today: date) -> str:
    """The dry run's output: every planned request and the count, with nothing sent."""
    lines = [
        "# Tushare event-interface probe (V2-P7-010) -- plan (dry run)",
        "",
        f"- today: {today.isoformat()}",
        f"- planned calls: {len(plan)} of PROBE_CAP ({PROBE_CAP})",
        f"- full-day paging: a `full_day` page of {PAGE_ROW_LIMIT} rows (or `has_more=true`) buys",
        "  an `offset` follow-up request not listed above; every follow-up is inside the same cap.",
        "- dry run: nothing is sent and nothing is written.",
        "",
        "| # | interface | purpose | params |",
        "|---|---|---|---|",
    ]
    for index, call in enumerate(plan, start=1):
        params = json.dumps(dict(call.params), sort_keys=True, ensure_ascii=False)
        lines.append(f"| {index} | `{call.api_name}` | {call.purpose} | `{params}` |")
    return "\n".join(lines) + "\n"


# --- the cumulative budget across invocations ----------------------------------------------------


def read_probe_budget(path: Path) -> int:
    """The cumulative requests `path` records; 0 when the file does not exist yet.

    A file that exists but is not a `PROBE_BUDGET_SCHEMA` object with a non-negative whole
    `spent` is refused by name rather than read as zero: the file is the only guard against a
    later `--live` invocation spending the same 300 requests again.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    except OSError as error:
        raise ProbeBudgetError(
            f"{path} cannot be read ({type(error).__name__}): nothing was sent"
        ) from error
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise ProbeBudgetError(
            f"{path} is not JSON ({error}): nothing was sent -- it is the cumulative request "
            "budget, written by this script alone"
        ) from error
    if not isinstance(payload, dict) or payload.get("schema") != PROBE_BUDGET_SCHEMA:
        raise ProbeBudgetError(f"{path} is not a {PROBE_BUDGET_SCHEMA} file: nothing was sent")
    spent = payload.get("spent")
    if isinstance(spent, bool) or not isinstance(spent, int) or spent < 0:
        raise ProbeBudgetError(
            f"{path}'s spent is a whole number of requests, 0 or more, not {spent!r}: nothing "
            "was sent"
        )
    return spent


def _record_budget(path: Path, spent: int) -> None:
    """Record `spent` cumulative requests in `path`, atomically: a temporary file beside it then
    `os.replace`. The directory must exist; a failure is the caller's to report."""
    text = json.dumps({"schema": PROBE_BUDGET_SCHEMA, "spent": spent}, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    replaced = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
    finally:
        if not replaced:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


class _Tally:
    """Counts the requests forwarded to the caller's transport.

    The count is taken before the transport is entered -- the conservative side for a budget,
    since a request that fails in flight may still have reached the vendor. A request the cap
    stopped never reaches `post`, so it is not counted, and the finding records it as unsent.
    """

    def __init__(self, inner: TushareTransport) -> None:
        self.inner = inner
        self.attempts = 0

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.attempts += 1
        return self.inner.post(payload)


# --- the command --------------------------------------------------------------------------------


def _default_transport() -> TushareTransport:
    """The live mode's transport: the one HTTP seam the CLI's panel build also uses."""
    return cli._panel_transport()


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"a date is YYYY-MM-DD, not {value!r}") from error


def main(argv: Sequence[str] | None = None) -> int:
    """The probe's command: `--dry-run` (the default) or the gated, sending `--live`."""
    parser = argparse.ArgumentParser(
        prog="p7_event_probe.py",
        description=(
            "Probe the five Tushare event interfaces (V2-P7-010) under a hard 300-request cap, "
            "cumulative across --live invocations through --budget-file. The default is a dry "
            "run: nothing is sent and nothing is written."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned requests and their count, and send nothing (the default)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=("send the plan; requires --i-have-the-users-go, TUSHARE_TOKEN and --budget-file"),
    )
    parser.add_argument(
        "--i-have-the-users-go",
        dest="have_go",
        action="store_true",
        help="the user's explicit authorisation for the live Tushare requests",
    )
    parser.add_argument(
        "--budget-file",
        dest="budget_file",
        type=Path,
        default=None,
        help=(
            "the cumulative request-budget JSON file a --live run reads and rewrites (required "
            "by --live; ignored by --dry-run; the path as given, ~ is not expanded)"
        ),
    )
    parser.add_argument(
        "--today",
        type=_parse_day,
        default=None,
        help="the day the plan is built from (YYYY-MM-DD; default: today)",
    )
    parser.add_argument(
        "--depth-years",
        dest="depth_years",
        type=int,
        nargs="+",
        default=[1, 2, 3, 4, 5],
        metavar="N",
        help="whole years back to probe, one whole-market request per interface each",
    )
    args = parser.parse_args(argv)

    today = args.today if args.today is not None else date.today()
    try:
        plan = probe_plan(today=today, depth_years=args.depth_years)
    except ValueError as error:
        print(f"p7_event_probe: {error}", file=sys.stderr)
        return REFUSED_EXIT

    if not args.live:
        # The dry run ignores --budget-file entirely: it neither reads nor creates it.
        print(render_plan(plan, today=today), end="")
        return 0

    if args.dry_run:
        print(
            "p7_event_probe: --live and --dry-run are exclusive; nothing was sent",
            file=sys.stderr,
        )
        return REFUSED_EXIT
    if not args.have_go:
        print(
            "p7_event_probe: --live needs --i-have-the-users-go; nothing was sent",
            file=sys.stderr,
        )
        return REFUSED_EXIT
    if not os.environ.get(_TUSHARE_TOKEN_ENV):
        print(
            f"p7_event_probe: --live needs {_TUSHARE_TOKEN_ENV} in the environment; "
            "nothing was sent",
            file=sys.stderr,
        )
        return REFUSED_EXIT
    if args.budget_file is None:
        print(
            "p7_event_probe: --live needs --budget-file PATH, the cumulative request budget file "
            "-- without it two invocations could spend the 300 cap twice; nothing was sent",
            file=sys.stderr,
        )
        return REFUSED_EXIT

    budget_path: Path = args.budget_file
    try:
        spent = read_probe_budget(budget_path)
    except ProbeBudgetError as error:
        print(f"p7_event_probe: {error}", file=sys.stderr)
        return REFUSED_EXIT
    allowance = PROBE_CAP - spent
    if allowance <= 0:
        print(
            f"p7_event_probe: the cumulative request budget is spent: {spent} of PROBE_CAP "
            f"({PROBE_CAP}) requests recorded in {budget_path}; nothing was sent -- ask the user "
            "before the budget is raised",
            file=sys.stderr,
        )
        return REFUSED_EXIT

    tally = _Tally(_default_transport())
    try:
        findings = run_probe(plan, transport=tally, cap=allowance)
    except BaseException:
        # A partial or failed run still records what it actually sent. The write is best effort:
        # the exception in flight is the one to report.
        with contextlib.suppress(OSError, ValueError):
            _record_budget(budget_path, spent + tally.attempts)
        raise

    cumulative = spent + tally.attempts
    try:
        _record_budget(budget_path, cumulative)
    except (OSError, ValueError) as error:
        print(render_report(findings, spent=tally.attempts), end="")
        print(
            f"p7_event_probe: {tally.attempts} request(s) were sent and the budget file "
            f"{budget_path} could not be written ({type(error).__name__}: {error}); do not run "
            "--live again until it records them",
            file=sys.stderr,
        )
        return REFUSED_EXIT
    print(render_report(findings, spent=tally.attempts, cumulative=cumulative), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
