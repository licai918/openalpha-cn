"""The V2-P7-010 event-interface probe (plan C, task 2.2): the request plan, its hard budget, its
findings and its metadata-only report.

The five Tushare event interfaces a plan-C evidence producer might read -- `moneyflow`,
`limit_list_d`, `top_list`, `top_inst` and `anns_d` -- are candidates the decision chain says must
be verified before any design names them (`openalpha-cn-v2-p7-decision-chain.md` §8: 哪些可用、
权限与积分要求、单日请求量、历史深度、是否带发布时刻). This script is the tooling for that
verification and nothing else: it never writes a panel, a database or a file, retains no row, and
its one report is metadata.

**The plan is the budget.** `probe_plan` returns one `ProbeCall` per Tushare request: five
interfaces times a schema probe, one whole-market day, a PIT-timestamp-field probe and one
whole-market request per requested depth year. `test_the_plan_never_exceeds_300_requests` pins
that its length never exceeds `PROBE_CAP`, and the function refuses a depth-year list that would:
300 is a hypothesis the user can raise, and it is enforced twice -- the plan refuses to exceed it,
and `run_probe`'s counter refuses the request that would pass it.

**One call, one request, one finding.** Every planned call is exactly one POST, and every sent
request yields exactly one `ProbeFinding` (a non-zero Tushare `code` and a transport exception are
findings too). `pages` is the number of pages a finding consumed: 1 for a data page, 0 for a
refusal or a failed transport, because the probe never pages -- a page is a request, and the
plan's length is what says how many requests the probe may spend. A whole-market day that would
span more than one page therefore shows up as the row count the endpoint served, not as a count
of pages; 单日全市场请求数 for such an interface is a question for a later, uncapped probe, and the
report template says so.

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

**The modes.** `main` defaults to `--dry-run`: prints the plan and its count, sends nothing, writes
nothing. `--live` is the only path that sends, and it refuses without `--i-have-the-users-go` or a
`TUSHARE_TOKEN`; even then it prints the metadata report and writes nothing. `--live` is never run
by this repository's tests or by the batch that added this script: it is gated live step L1 and
waits for the user's explicit go.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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
    TushareTransport,
)

PROBE_CAP: Final[int] = 300
"""The live probe's request ceiling: the plan refuses to plan past it and `run_probe` refuses to
send past it. A hypothesis (plan C's Task 2.2), adjustable only by asking the user."""

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
"""What a refusal of this script exits with (a usage error is 2 too): nothing was sent and nothing
was written."""

Purpose = Literal["schema", "full_day", "depth", "pit_field"]


@dataclass(frozen=True)
class ProbeCall:
    """One planned Tushare request: which interface, with which params, and why."""

    api_name: str
    params: Mapping[str, str]
    purpose: Purpose


@dataclass(frozen=True)
class ProbeFinding:
    """What one sent request answered: metadata only, never a row value.

    `rows` is the number of rows in the one data page the request returned; `pages` is the number
    of pages that page represents (1), or 0 when the answer was a refusal or a transport failure.
    `earliest_date_seen` is the earliest value of a `*_date` column parsed across the returned
    rows -- only the date is kept, never the cell -- and `timestamp_fields` names the columns whose
    names look like a publication instant, for the PIT decision the live step has to make.
    """

    api_name: str
    permitted: bool
    error_code: str | None
    rows: int
    pages: int
    fields: tuple[str, ...]
    earliest_date_seen: date | None
    timestamp_fields: tuple[str, ...]


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
        try:
            counter[call.api_name] += 1
        except budget.RequestCapReached:
            break
        findings.append(_probe_call(call, transport))
    return tuple(findings)


def _probe_call(call: ProbeCall, transport: TushareTransport) -> ProbeFinding:
    envelope: dict[str, Any] = {
        "api_name": call.api_name,
        "token": os.environ.get(_TUSHARE_TOKEN_ENV, ""),
        "params": dict(call.params),
        "fields": "",
    }
    try:
        response = transport.post(envelope)
    except Exception as error:  # a transport failure is a finding, not a crash
        return ProbeFinding(
            api_name=call.api_name,
            permitted=False,
            error_code=type(error).__name__,
            rows=0,
            pages=0,
            fields=(),
            earliest_date_seen=None,
            timestamp_fields=(),
        )
    return _finding(call.api_name, response)


def _finding(api_name: str, response: Mapping[str, Any]) -> ProbeFinding:
    code = response.get("code")
    if code != 0:
        return ProbeFinding(
            api_name=api_name,
            permitted=False,
            error_code=_error_code(code),
            rows=0,
            pages=0,
            fields=(),
            earliest_date_seen=None,
            timestamp_fields=(),
        )
    data = response.get("data")
    if not isinstance(data, dict):
        return ProbeFinding(
            api_name=api_name,
            permitted=True,
            error_code="invalid_response",
            rows=0,
            pages=0,
            fields=(),
            earliest_date_seen=None,
            timestamp_fields=(),
        )
    raw_fields = data.get("fields")
    fields = (
        tuple(field for field in raw_fields if isinstance(field, str))
        if isinstance(raw_fields, list)
        else ()
    )
    raw_items = data.get("items")
    items = raw_items if isinstance(raw_items, list) else []
    return ProbeFinding(
        api_name=api_name,
        permitted=True,
        error_code=None,
        rows=len(items),
        pages=1,
        fields=fields,
        earliest_date_seen=_earliest_date(fields, items),
        timestamp_fields=tuple(field for field in fields if _looks_like_timestamp(field)),
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


def render_report(findings: Sequence[ProbeFinding], *, spent: int) -> str:
    """The metadata report: no row value, no message, no token, nothing persisted.

    One interface's findings are folded into one line: `permitted` is `yes` only when every call
    to it was permitted (`partial` when some were, `no` when none was), the row and page counts
    are the largest single answer -- the whole-market day -- the earliest date is the earliest
    seen across the calls, and the field and timestamp names are the union in first-seen order.
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
        "",
        "| interface | permitted | error code | rows | pages | earliest date seen "
        "| timestamp fields |",
        "|---|---|---|---|---|---|---|",
    ]
    for api_name, group in grouped.items():
        permitted = _permitted_of(group)
        codes = _codes_of(group)
        rows = max(finding.rows for finding in group)
        pages = max(finding.pages for finding in group)
        earliest = _earliest_of(group)
        timestamps = _ordered_union(finding.timestamp_fields for finding in group)
        lines.append(
            f"| `{api_name}` | {permitted} | {', '.join(codes) if codes else '-'} | {rows} "
            f"| {pages} | {earliest.isoformat() if earliest is not None else '-'} "
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
        "- dry run: nothing is sent and nothing is written.",
        "",
        "| # | interface | purpose | params |",
        "|---|---|---|---|",
    ]
    for index, call in enumerate(plan, start=1):
        params = json.dumps(dict(call.params), sort_keys=True, ensure_ascii=False)
        lines.append(f"| {index} | `{call.api_name}` | {call.purpose} | `{params}` |")
    return "\n".join(lines) + "\n"


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
            "Probe the five Tushare event interfaces (V2-P7-010) under a hard 300-request cap. "
            "The default is a dry run: nothing is sent and nothing is written."
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
        help="send the plan; requires --i-have-the-users-go and TUSHARE_TOKEN",
    )
    parser.add_argument(
        "--i-have-the-users-go",
        dest="have_go",
        action="store_true",
        help="the user's explicit authorisation for the live Tushare requests",
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

    findings = run_probe(plan, transport=_default_transport())
    print(render_report(findings, spent=len(findings)), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
