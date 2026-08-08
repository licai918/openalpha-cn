"""Tushare Pro BYOT adapter with explicit HTTP and point-in-time semantics.

Every Tushare Pro HTTP endpoint shares one request envelope and one response shape
(`code` / `data.fields` / `data.items`); only four things vary per dataset: how `params`
is built, which columns hold the subject and the date, how the four PIT clocks are
derived, and the `kind`/`source_uri` used for the resulting records. `TushareDatasetDescriptor`
makes those four things data instead of code, so adding a dataset is a new row in
`TUSHARE_DATASETS`, not a new adapter.

## Two output shapes, one descriptor table (`V2-P1-004`)

`fetch()` produces the row-wise `ProviderBatch` the evidence plane speaks. `fetch_panel()`
produces the columnar `ColumnarPanelBatch` ADR-0002's panel plane speaks, and is available
for exactly those datasets whose descriptor declares a `panel_columns` projection --
`trade_cal` today. A dataset without one is refused by name rather than silently handed an
empty batch, because "this dataset is not wired to the panel plane" and "this dataset has no
rows" are different facts. Both methods decode the same response through the same clock
table; only the assembly differs.

## `is_open` is parsed, never coerced -- on the panel path only

Tushare returns `is_open` as an integer `0`/`1`, and the obvious `bool(value)` is wrong in a
way that is invisible: `bool("0")` is `True`, so a schema change that turned the column into
strings would silently report every holiday as a trading day. `_open_flag` therefore accepts
only `0`/`1`/`"0"`/`"1"`/`bool` and raises on anything else.

`_open_flag` runs in the `panel_columns` projection, which only `fetch_panel()` applies.
`fetch()` is a payload-passthrough contract for every dataset in the table -- a
`ProviderRecord`'s payload is the decoded response row verbatim -- so `fetch("trade_cal")`
hands back `is_open` exactly as Tushare sent it, an `int` today and whatever a schema change
makes it tomorrow, with no parse in front of it. That is deliberate and not closed here:
making one
dataset's row-wise payload selectively typed would break the property that an evidence-plane
record is the upstream response, which is what makes it re-provable. The consequence is
bounded rather than trusted -- no calendar can be built from that path without going through
`domain/trading_calendar.py`, and `build_trading_calendar` refuses any `is_trading` that is
not exactly a `bool`, so a caller who reaches for `record.payload["is_open"]` and applies
`bool()` to it gets a wrong answer only inside their own code, never a wrong
`TradingCalendar`. `tests/contract/providers/test_tushare_trade_cal.py` pins both halves.
"""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any, Final, Protocol, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from openalpha_cn.domain.panel_batch import (
    ColumnarPanelBatch,
    PanelColumn,
    PanelColumnKind,
    TimelineColumns,
)
from openalpha_cn.domain.time import Timeline
from openalpha_cn.domain.trading_calendar import (
    CALENDAR_DATE_COLUMN,
    CALENDAR_OPEN_COLUMN,
    CALENDAR_PRETRADE_COLUMN,
    TRADING_CALENDAR_DATASET,
)
from openalpha_cn.providers.base import (
    ProviderBatch,
    ProviderFailure,
    ProviderFailureCategory,
    ProviderMetadata,
    ProviderRecord,
    ProviderRequest,
    utc_now,
)

_CHINA_TZ = ZoneInfo("Asia/Shanghai")

_PROVIDER_ID: Final[str] = "tushare.pro"

TRADING_CALENDAR_DEFAULT_EXCHANGE: Final[str] = "SSE"
"""What Tushare answers with when `exchange` is omitted, measured rather than assumed.

A live probe found the bare request and `exchange=SSE` return byte-identical rows, that SSE
carries 13,162 published days against SZSE's 12,966 (SZSE simply starts later), and that the
two disagree on `is_open` for **zero** of their shared dates. They are still kept as separate
subjects rather than merged: identical today is not the same fact as identical by
construction, and the subject column is where a future divergence would show up.
"""


class TushareTransport(Protocol):
    """Injectable transport boundary for Tushare Pro."""

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a Tushare request and return its decoded object."""


class UrllibTushareTransport:
    """Minimal standard-library HTTP transport for Tushare Pro."""

    def __init__(self, endpoint: str = "https://api.tushare.pro") -> None:
        self.endpoint = endpoint

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON without logging or persisting the user token."""
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            decoded = json.loads(response.read().decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Tushare response must be a JSON object")
        return cast(dict[str, Any], decoded)


class ClockStrategy(StrEnum):
    """How a dataset's four PIT clocks (event/available/ingested/revision) are derived.

    ``daily_close`` (the ``daily`` dataset) and ``calendar_publication`` (``trade_cal``) have
    real production consumers. ``announcement`` and ``calendar_static`` are defined and
    independently tested against synthetic data so P1 can wire real datasets onto them
    without changing this shape.
    """

    daily_close = "daily_close"
    """Trading-day data: event_time=15:00, available_time=16:30, both Asia/Shanghai."""

    announcement = "announcement"
    """Financial-statement data keyed off ``ann_date``/``f_ann_date`` (original vs. revised)."""

    calendar_static = "calendar_static"
    """Static reference data: event_time == available_time == that day's 00:00.

    Still without a production consumer, and ``trade_cal`` deliberately does **not** use it --
    see ``calendar_publication`` for why a calendar is the one dataset this rule is wrong for.
    """

    calendar_publication = "calendar_publication"
    """Forward-looking reference data: the event is a future day, availability is its year.

    Every other clock here describes data about the past, so ``available_time`` lands at or
    after ``event_time``. A calendar is the opposite: on 2026-08-08 the exchange has already
    told us that 2026-12-31 is a session, and the entire reason the dataset exists is to
    answer questions about days that have not happened. See ``_calendar_publication_timeline``
    for the two instants the availability time is derived from **and for the look-ahead this
    rule is known to leak** when the holiday schedule is amended mid-year.
    """


class TusharePanelColumn(BaseModel):
    """One response field projected onto one panel-plane column (``V2-P1-004``).

    ``parse`` turns Tushare's own representation into the logical ``kind``'s Python type and
    raises ``ValueError`` for anything it does not recognise. It is a function rather than a
    kind-keyed lookup because the interesting cases are dataset-specific: ``is_open``'s 0/1
    flag and a ``YYYYMMDD`` date string both arrive as ``str`` or ``int`` and both need a
    different rule.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=63)
    """Panel column name; revalidated by ``PanelColumn`` at the contract boundary."""
    kind: PanelColumnKind
    source_field: str = Field(min_length=1, max_length=128)
    """Response field this column is projected from."""
    parse: Callable[[object], object]


class TushareDatasetDescriptor(BaseModel):
    """Everything dataset-specific about one Tushare Pro endpoint.

    Frozen and ``extra="forbid"`` to match this repo's domain-model style: a descriptor
    is a value, not a place to grow ad hoc behavior.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset: str = Field(min_length=1, max_length=128)
    """Tushare ``api_name``; also the value ``ProviderRequest.dataset`` must match."""
    kind: str = Field(min_length=1, max_length=64)
    """Value written to ``ProviderRecord.kind``."""
    subject_field: str | None = Field(default=None)
    """Column holding the subject (e.g. ``ts_code``); ``None`` for calendar-style datasets."""
    date_field: str = Field(min_length=1, max_length=128)
    """Column holding the record's date (e.g. ``trade_date`` / ``cal_date`` / ``end_date``)."""
    clock: ClockStrategy
    params_builder: Callable[[ProviderRequest], dict[str, str]]
    """Builds the Tushare ``params`` object from a ``ProviderRequest``."""
    source_uri_template: str = Field(min_length=1, max_length=2048)
    """``str.format`` template with ``{dataset}``, ``{subject}``, and ``{date}`` placeholders."""
    panel_columns: tuple[TusharePanelColumn, ...] = ()
    """Panel-plane projection, or ``()`` for a dataset ``fetch_panel()`` does not serve yet."""


def _trade_date_params(request: ProviderRequest) -> dict[str, str]:
    """Build ``{trade_date[, ts_code]}`` params for one-trading-day datasets."""
    local_date = request.as_of.astimezone(_CHINA_TZ).strftime("%Y%m%d")
    parameters = {"trade_date": local_date}
    if request.subjects:
        parameters["ts_code"] = ",".join(request.subjects)
    return parameters


def _trade_cal_params(request: ProviderRequest) -> dict[str, str]:
    """Request one whole calendar year: ``{exchange, start_date, end_date}``.

    The year is the ``as_of``'s year *in Asia/Shanghai*, not in UTC: 2024-12-31 17:00Z is
    already 2025-01-01 in Shanghai, and asking UTC would fetch the wrong year's calendar for
    every late-evening request on the last day of a year. One year per request is also one
    year per partition, which is the granularity `PanelStore` stores at.

    Tushare takes a single `exchange`, so more than one subject is a malformed request rather
    than something to silently truncate or comma-join into an unsupported filter.

    Recorded limitation: `ProviderRequest` carries no date range, so `as_of` doubles as "which
    year to fetch". Backfilling 2015..2026 therefore means one request per year, each with an
    `as_of` inside that year. `_trade_date_params` has the same shape for the same reason, and
    widening the request contract is a change to every provider rather than to this dataset.
    """
    if len(request.subjects) > 1:
        raise ProviderFailure(
            provider_id=_PROVIDER_ID,
            category="configuration",
            message=(
                f"{TRADING_CALENDAR_DATASET} serves one exchange per request; got "
                f"{list(request.subjects)}"
            ),
            retryable=False,
        )
    exchange = request.subjects[0] if request.subjects else TRADING_CALENDAR_DEFAULT_EXCHANGE
    year = request.as_of.astimezone(_CHINA_TZ).year
    return {"exchange": exchange, "start_date": f"{year}0101", "end_date": f"{year}1231"}


_OPEN_FLAGS: Final[dict[str, bool]] = {"0": False, "1": True}


def _open_flag(value: object) -> object:
    """Parse Tushare's ``is_open`` into a real ``bool``. See this module's docstring."""
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return value == 1
    if type(value) is str and value in _OPEN_FLAGS:
        return _OPEN_FLAGS[value]
    raise ValueError(f"is_open must be 0 or 1, got {type(value).__name__} {value!r}")


def _calendar_date_text(value: object) -> object:
    """Parse a ``YYYYMMDD`` column into an ISO ``YYYY-MM-DD`` string.

    Stored as text, not as a `TIMESTAMPTZ`: a calendar date is a date, and putting it through
    an instant would re-introduce the very timezone ambiguity `panel_ingest`'s
    `date_timezone` exists to record. The parse is not a reformat -- `_parse_tushare_date`
    rejects anything that is not a real date, so a malformed cell fails here rather than in a
    consumer.
    """
    return _parse_tushare_date(value).isoformat()


def _optional_calendar_date_text(value: object) -> object:
    """Same as `_calendar_date_text`, but a missing value stays missing.

    `pretrade_date` was populated on every one of the 13,162 rows the live probe returned, so
    the null branch is defence rather than an observed case -- and a `None` here is honest
    where a fabricated date would not be.
    """
    if value is None or value == "":
        return None
    return _calendar_date_text(value)


TUSHARE_DATASETS: tuple[TushareDatasetDescriptor, ...] = (
    TushareDatasetDescriptor(
        dataset="daily",
        kind="daily",
        subject_field="ts_code",
        date_field="trade_date",
        clock=ClockStrategy.daily_close,
        params_builder=_trade_date_params,
        source_uri_template="tushare://{dataset}/{subject}/{date}",
    ),
    TushareDatasetDescriptor(
        dataset=TRADING_CALENDAR_DATASET,
        kind=TRADING_CALENDAR_DATASET,
        # The response carries `exchange` on every row, so the subject is read from the data
        # rather than defaulted to the dataset name: SSE and SZSE are genuinely two series.
        subject_field="exchange",
        date_field=CALENDAR_DATE_COLUMN,
        clock=ClockStrategy.calendar_publication,
        params_builder=_trade_cal_params,
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        # `name` is the panel column, `source_field` is Tushare's response column. They
        # coincide today and are still written out separately: renaming one must not silently
        # rename the other.
        panel_columns=(
            TusharePanelColumn(
                name=CALENDAR_DATE_COLUMN,
                kind="string",
                source_field="cal_date",
                parse=_calendar_date_text,
            ),
            TusharePanelColumn(
                name=CALENDAR_OPEN_COLUMN,
                kind="boolean",
                source_field="is_open",
                parse=_open_flag,
            ),
            TusharePanelColumn(
                name=CALENDAR_PRETRADE_COLUMN,
                kind="string",
                source_field="pretrade_date",
                parse=_optional_calendar_date_text,
            ),
        ),
    ),
)


def _dataset_names(descriptors: tuple[TushareDatasetDescriptor, ...]) -> tuple[str, ...]:
    """Return each descriptor's dataset name, in table order."""
    return tuple(descriptor.dataset for descriptor in descriptors)


_TUSHARE_DATASETS_BY_NAME: dict[str, TushareDatasetDescriptor] = {
    descriptor.dataset: descriptor for descriptor in TUSHARE_DATASETS
}


def _parse_tushare_date(value: object) -> date:
    """Parse one of Tushare's ``YYYYMMDD`` date columns."""
    return datetime.strptime(str(value), "%Y%m%d").date()


def _daily_close_timeline(row: dict[str, Any], date_field: str, ingested_at: datetime) -> Timeline:
    """15:00 close / 16:30 availability, both Asia/Shanghai, for trading-day datasets."""
    trading_day = _parse_tushare_date(row[date_field])
    event_time = datetime.combine(trading_day, time(15, 0), tzinfo=_CHINA_TZ)
    available_time = datetime.combine(trading_day, time(16, 30), tzinfo=_CHINA_TZ)
    return Timeline(
        event_time=event_time,
        available_time=available_time,
        ingested_time=ingested_at,
        revision_time=available_time,
    )


def _announcement_timeline(row: dict[str, Any], date_field: str, ingested_at: datetime) -> Timeline:
    """``ann_date`` sets event/available time; a later ``f_ann_date`` is a revision.

    ``date_field`` is unused here: announcement data always keys its PIT clocks off the
    fixed ``ann_date``/``f_ann_date`` columns that every Tushare financial-statement
    endpoint shares, regardless of which column the descriptor uses for display purposes.

    Known gap (measured, not assumed): a live probe of the real Tushare ``balancesheet``
    endpoint (3 stocks, 2022-2025, 65 rows) confirmed ``f_ann_date >= ann_date`` holds with
    zero violations, so that ordering assumption is safe. But it also found that restatements
    are **not distinguishable from the original filing by ``ann_date``/``f_ann_date`` alone**:
    for ``000001.SZ``, ``end_date=20231231`` returns two rows with ``ann_date=20240315`` AND
    ``f_ann_date=20240315`` on both, differing only in ``update_flag`` (``0`` vs ``1``); the
    same shape recurs at ``end_date=20240331``. This function does not read ``update_flag``,
    so those two rows produce byte-equal ``Timeline`` objects today — see
    ``test_announcement_clock_cannot_yet_distinguish_restatement_via_update_flag`` in
    ``tests/contract/providers/test_tushare_dataset_descriptors.py``, which pins this exact
    gap. Deciding how to disambiguate (drop all but the highest ``update_flag``? keep and rank
    both?) is deferred to the phase that wires real financial datasets onto this clock; when
    that lands, this function gains ``update_flag`` handling and the pinned test above must be
    rewritten to assert the new behavior.
    """
    ann_moment = datetime.combine(
        _parse_tushare_date(row["ann_date"]), time(0, 0), tzinfo=_CHINA_TZ
    )
    f_ann_date_raw = row.get("f_ann_date")
    revision_moment = (
        datetime.combine(_parse_tushare_date(f_ann_date_raw), time(0, 0), tzinfo=_CHINA_TZ)
        if f_ann_date_raw
        else ann_moment
    )
    return Timeline(
        event_time=ann_moment,
        available_time=ann_moment,
        ingested_time=ingested_at,
        revision_time=revision_moment,
    )


def _calendar_static_timeline(
    row: dict[str, Any], date_field: str, ingested_at: datetime
) -> Timeline:
    """Static metadata: event time and availability time are the same midnight."""
    moment = datetime.combine(_parse_tushare_date(row[date_field]), time(0, 0), tzinfo=_CHINA_TZ)
    return Timeline(
        event_time=moment,
        available_time=moment,
        ingested_time=ingested_at,
        revision_time=moment,
    )


def _calendar_publication_timeline(
    row: dict[str, Any], date_field: str, ingested_at: datetime
) -> Timeline:
    """The exchange calendar's clocks: a future session, dated at the start of its own year.

    ``event_time`` is the session's own midnight in Asia/Shanghai. ``available_time`` is the
    **earlier** of two instants:

    - *The start of the calendar date's own year.* Not a guess about when the exchange
      published: by the time year Y has begun, *a* calendar for year Y exists. The real
      publication date -- some November of Y-1 -- is not in the response and would have to be
      invented.
    - *The moment the row was observed* (``ingested_at``), which is what keeps ``Timeline``'s
      ``available_time <= ingested_time`` invariant true for a row fetched before its own year
      began. With today's ``_trade_cal_params`` this bound never actually wins: the fetched
      year is derived from ``as_of``, so a row's ``cal_date`` year equals ``as_of``'s year,
      and any run whose clock is at or after its own ``as_of`` has
      ``ingested_at >= as_of >= 1 January of that year``. It is reachable only by calling this
      function directly (as its unit tests do) or by a caller whose ``as_of`` runs ahead of
      its clock, and it is kept because the invariant must hold for those too -- an earlier
      version of this docstring justified it with a December-fetches-next-year scenario the
      params builder cannot produce.

    The two properties ``V2-P1-004`` is judged on fall out of the first bound: at
    ``as_of=2026-08-08`` a 2026-12-31 session is visible (availability 2026-01-01) and a
    2027-01-04 session is not (availability 2027-01-01, after the ``as_of``).

    ## Known defect: this rule leaks look-ahead, and the leak is measured

    The first bound is *not* conservative, and an earlier version of this docstring claimed it
    was ("a statement that cannot be wrong in the dangerous direction"). It is wrong in that
    direction whenever the holiday schedule is amended after its year has begun, which in
    China is routine: ``trade_cal`` serves one snapshot with no revision history, so an
    amendment made in May of year Y is dated here as though it had been knowable on
    1 January of Y.

    Two instances are proven against this same endpoint and carried as data in
    ``domain/trading_calendar.py::KNOWN_CALENDAR_LOOKAHEAD``, with regression fixtures in
    ``tests/contract/providers/test_tushare_trade_cal.py``:

    - **2015-09-03 and 2015-09-04** (Victory Day parade recess, announced 2015-05-13): dated
      available from 2015-01-01, a **132-day** look-ahead.
    - **2020-01-31** (Spring Festival extension, announced 2020-01-27): dated available from
      2020-01-01, a **26-day** look-ahead -- and this one *inverts* the verdict, because the
      schedule published 2019-11-21 had 31 January as an open session. Any backtest crossing
      2020-01-20..01-27 "knows" the market is shut through 2 February before anyone did, and
      the session that reopening lands on is 2020-02-03.

    So the cost is two-directional and the docstring must not list only one side. This rule
    also blocks between the exchange's November announcement and 1 January, when next year's
    calendar was in fact already published -- but that is the *smaller* of the two errors and
    stating it alone was the misleading part. Fixing the leak needs either a revision history
    the endpoint does not serve or an availability model that can express "unknown between
    these two instants", which ``Timeline``'s four fixed clocks cannot; both are outside
    ``V2-P1-004``. ``TradingCalendar.known_lookahead()`` is the interface ``V2-P1-013``'s gate
    reads so the uncertainty is visible rather than only written down here.

    ``revision_time`` equals ``available_time``, and that is **not** a claim that the cell was
    never revised -- the three dates above are proof that some cells were. The response carries
    no revision instant, so every alternative fabricates one: ``ingested_at`` would mark every
    row of every partition as revised at fetch time, which is false for the ~13,000 rows that
    never changed and would make ``PartitionCoverage.revised_row_count`` equal the row count
    for a dataset whose real revision count is unmeasurable. Equality is the only value that
    invents nothing; read it as "no revision instant is known", and read a calendar
    partition's ``revised_row_count == 0`` as "unmeasured", not "none". A revised calendar
    still shows up as a changed row in a re-fetched partition, through the partition's content
    hash, and never through this clock.
    """
    calendar_day = _parse_tushare_date(row[date_field])
    event_time = datetime.combine(calendar_day, time(0, 0), tzinfo=_CHINA_TZ)
    published_from = datetime.combine(date(calendar_day.year, 1, 1), time(0, 0), tzinfo=_CHINA_TZ)
    available_time = min(published_from, ingested_at)
    return Timeline(
        event_time=event_time,
        available_time=available_time,
        ingested_time=ingested_at,
        revision_time=available_time,
    )


_ClockBuilder = Callable[[dict[str, Any], str, datetime], Timeline]

_CLOCK_BUILDERS: dict[ClockStrategy, _ClockBuilder] = {
    ClockStrategy.daily_close: _daily_close_timeline,
    ClockStrategy.announcement: _announcement_timeline,
    ClockStrategy.calendar_static: _calendar_static_timeline,
    ClockStrategy.calendar_publication: _calendar_publication_timeline,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class _DecodedPanelRows:
    """One response, decoded and projected: what `fetch_panel` needs to assemble a batch."""

    rows: list[dict[str, Any]]
    subjects: tuple[str, ...]
    timelines: list[Timeline]
    values: tuple[tuple[object, ...], ...]
    """Parsed column values, aligned with the descriptor's `panel_columns`, in order."""
    served: int
    """Rows Tushare returned before the point-in-time filter ran."""


def _resolve_subject(descriptor: TushareDatasetDescriptor, row: dict[str, Any]) -> str:
    """Return the record's subject, falling back to the dataset name when none is declared."""
    if descriptor.subject_field is None:
        return descriptor.dataset
    return str(row[descriptor.subject_field])


def _response_rows(
    descriptor: TushareDatasetDescriptor, response: dict[str, Any], provider_id: str
) -> list[dict[str, Any]]:
    """Turn one Tushare envelope into field-keyed rows, or raise.

    Shared by the row-wise and the columnar decode: the envelope (`code` / `data.fields` /
    `data.items`) is the one thing every endpoint has in common, and having two copies of its
    validation would let the two output shapes disagree about what a malformed response is.
    """
    code = response.get("code")
    if code != 0:
        category: ProviderFailureCategory = "authentication" if code == -2001 else "upstream"
        raise ProviderFailure(
            provider_id=provider_id,
            category=category,
            message=f"Tushare rejected the request: {response.get('msg') or 'unknown error'}",
            retryable=category == "upstream",
        )
    data = response.get("data")
    if not isinstance(data, dict):
        raise ValueError("Tushare data must be an object")
    fields = data.get("fields")
    items = data.get("items")
    if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
        raise ValueError("Tushare fields must be a string array")
    if not isinstance(items, list):
        raise ValueError("Tushare items must be an array")
    if descriptor.date_field not in fields:
        raise ValueError(
            f"Tushare response for {descriptor.dataset} has no {descriptor.date_field} column"
        )
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            raise ValueError("Tushare item does not match fields")
        rows.append(dict(zip(fields, item, strict=True)))
    return rows


def _panel_no_data_reason(
    descriptor: TushareDatasetDescriptor, request: ProviderRequest, served: int
) -> str:
    """Say which of the two empty results happened; see `TushareProvider.fetch_panel`."""
    if served == 0:
        return (
            f"Tushare served no {descriptor.dataset} rows for the requested range: the "
            "exchange has not published it, which is a horizon and not a closed period"
        )
    return (
        f"Tushare served {served} {descriptor.dataset} row(s), none of which was yet knowable "
        f"at as_of {request.as_of.isoformat()}: not yet knowable is not the same as absent"
    )


def _panel_source_uri(
    descriptor: TushareDatasetDescriptor,
    subjects: tuple[str, ...],
    rows: Sequence[dict[str, Any]],
) -> str:
    """One provenance URI for a whole partition-shaped batch.

    `ProviderRecord` carries one per row; a columnar batch has one field for all of them, so
    the `{date}` slot holds the closed range the batch actually covers rather than a single
    day. Rows are already ascending by the time this is called.
    """
    first = _parse_tushare_date(rows[0][descriptor.date_field])
    last = _parse_tushare_date(rows[-1][descriptor.date_field])
    unique_subjects = sorted(set(subjects))
    subject = unique_subjects[0] if len(unique_subjects) == 1 else ",".join(unique_subjects)
    return descriptor.source_uri_template.format(
        dataset=descriptor.dataset, subject=subject, date=f"{first:%Y%m%d}-{last:%Y%m%d}"
    )


class TushareProvider:
    """Fetch Tushare records for any dataset declared in ``TUSHARE_DATASETS``."""

    _metadata = ProviderMetadata(
        provider_id=_PROVIDER_ID,
        display_name="Tushare Pro BYOT",
        source_license="Tushare Pro service terms",
        redistribution="restricted",
        credential_env_vars=("TUSHARE_TOKEN",),
        supported_datasets=_dataset_names(TUSHARE_DATASETS),
        caching_policy="provider-defined",
        rate_limit="Depends on the user's Tushare Pro account and points.",
        freshness="Daily records use a conservative 16:30 Asia/Shanghai availability time.",
        failure_semantics=(
            "Authentication, quota, transport, and schema errors raise ProviderFailure."
        ),
    )

    def __init__(
        self,
        *,
        token: str | None = None,
        transport: TushareTransport | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._token = token if token is not None else os.getenv("TUSHARE_TOKEN", "")
        self._transport = transport or UrllibTushareTransport()
        self._clock = clock

    @property
    def metadata(self) -> ProviderMetadata:
        """Return Tushare licensing and credential policy."""
        return self._metadata

    def fetch(self, request: ProviderRequest) -> ProviderBatch:
        """Fetch a descriptor-table dataset or raise a structured failure."""
        descriptor = self._descriptor(request)
        try:
            response = self._post(descriptor, request)
            records = self._decode(descriptor=descriptor, response=response, request=request)
        except ProviderFailure:
            raise
        except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError) as error:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="invalid_response",
                message=f"Tushare response could not be decoded: {error}",
                retryable=False,
            ) from error

        if not records:
            return ProviderBatch(
                provider_id=self.metadata.provider_id,
                request=request,
                fetched_at=self._clock(),
                status="no_data",
                no_data_reason="Tushare returned no records visible at the request clock.",
            )
        return ProviderBatch(
            provider_id=self.metadata.provider_id,
            request=request,
            fetched_at=self._clock(),
            status="success",
            records=records,
        )

    def fetch_panel(self, request: ProviderRequest) -> ColumnarPanelBatch:
        """Fetch a panel-plane dataset as a columnar batch (``PanelDataProvider``).

        Available only for descriptors that declare a ``panel_columns`` projection; anything
        else is refused by name rather than answered with an empty batch, because "not wired
        to the panel plane" and "no rows" are different facts and only one of them is data.

        Two distinguishable no-data results come out of this method, and keeping them apart is
        the point of ``V2-P1-004``:

        - Tushare returned **no rows at all** -- the exchange has not published that range.
          This is the horizon, and it is what stops a whole unpublished year from being read
          as one continuous holiday.
        - Tushare returned rows, none of which was **knowable at the request's ``as_of``**.
          This is the point-in-time filter, the same one ``_decode`` applies row by row.

        Rows are sorted ascending by the descriptor's date field before assembly. Tushare
        returns ``trade_cal`` in descending order, and while neither the batch contract nor
        the store cares, a partition whose row order depends on an upstream response ordering
        is one whose content hash does too.
        """
        descriptor = self._descriptor(request)
        if not descriptor.panel_columns:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="configuration",
                message=(
                    f"Tushare dataset {request.dataset} declares no panel projection, so it "
                    "cannot be fetched onto the panel plane"
                ),
                retryable=False,
            )
        try:
            response = self._post(descriptor, request)
            decoded = self._decode_panel_rows(
                descriptor=descriptor, response=response, request=request
            )
        except ProviderFailure:
            raise
        except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError) as error:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="invalid_response",
                message=f"Tushare response could not be decoded: {error}",
                retryable=False,
            ) from error

        # Deliberately outside the `try`: everything above is *decoding*, and a failure there
        # is an upstream fact (`invalid_response`). Everything below is the panel contract
        # validating itself, and a failure there is this module's own bug. Wrapping the second
        # as the first would let a descriptor that projects a string into a boolean column
        # read as a Tushare outage.
        fetched_at = self._clock()
        if not decoded.rows:
            return ColumnarPanelBatch(
                provider_id=self.metadata.provider_id,
                dataset=descriptor.dataset,
                kind=descriptor.kind,
                as_of=request.as_of,
                fetched_at=fetched_at,
                status="no_data",
                no_data_reason=_panel_no_data_reason(descriptor, request, decoded.served),
            )
        return ColumnarPanelBatch(
            provider_id=self.metadata.provider_id,
            dataset=descriptor.dataset,
            kind=descriptor.kind,
            as_of=request.as_of,
            fetched_at=fetched_at,
            status="success",
            subjects=decoded.subjects,
            timeline=TimelineColumns(
                event_time=tuple(line.event_time for line in decoded.timelines),
                available_time=tuple(line.available_time for line in decoded.timelines),
                ingested_time=tuple(line.ingested_time for line in decoded.timelines),
                revision_time=tuple(line.revision_time for line in decoded.timelines),
            ),
            columns=tuple(
                PanelColumn(spec.name, spec.kind, values)
                for spec, values in zip(descriptor.panel_columns, decoded.values, strict=True)
            ),
            source_uri=_panel_source_uri(descriptor, decoded.subjects, decoded.rows),
        )

    def _descriptor(self, request: ProviderRequest) -> TushareDatasetDescriptor:
        """Resolve the request's descriptor, refusing a missing token or unknown dataset."""
        if not self._token:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="configuration",
                message="TUSHARE_TOKEN is required.",
                retryable=False,
            )
        descriptor = _TUSHARE_DATASETS_BY_NAME.get(request.dataset)
        if descriptor is None:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="configuration",
                message=f"Unsupported Tushare dataset: {request.dataset}",
                retryable=False,
            )
        return descriptor

    def _post(
        self, descriptor: TushareDatasetDescriptor, request: ProviderRequest
    ) -> dict[str, Any]:
        """Build the one shared request envelope and hand it to the injected transport."""
        return self._transport.post(
            {
                "api_name": descriptor.dataset,
                "token": self._token,
                "params": descriptor.params_builder(request),
                "fields": "",
            }
        )

    def _decode_panel_rows(
        self,
        *,
        descriptor: TushareDatasetDescriptor,
        response: dict[str, Any],
        request: ProviderRequest,
    ) -> _DecodedPanelRows:
        """Decode, point-in-time filter, sort and project a response into column values.

        `served` is the number of rows Tushare actually returned, before the clock filter. It
        is what separates "the exchange has not published this range" from "this was not yet
        knowable at the requested ``as_of``" -- two very different empty results the caller
        could not otherwise tell apart.
        """
        items = _response_rows(descriptor, response, self.metadata.provider_id)
        ingested_at = self._clock()
        kept: list[tuple[date, dict[str, Any], Timeline]] = []
        for row in items:
            timeline = _CLOCK_BUILDERS[descriptor.clock](row, descriptor.date_field, ingested_at)
            if timeline.available_time > request.as_of:
                continue
            kept.append((_parse_tushare_date(row[descriptor.date_field]), row, timeline))
        kept.sort(key=lambda entry: entry[0])
        rows = [row for _, row, _ in kept]
        return _DecodedPanelRows(
            rows=rows,
            subjects=tuple(_resolve_subject(descriptor, row) for row in rows),
            timelines=[line for _, _, line in kept],
            values=tuple(
                tuple(spec.parse(row[spec.source_field]) for row in rows)
                for spec in descriptor.panel_columns
            ),
            served=len(items),
        )

    def _decode(
        self,
        *,
        descriptor: TushareDatasetDescriptor,
        response: dict[str, Any],
        request: ProviderRequest,
    ) -> tuple[ProviderRecord, ...]:
        items = _response_rows(descriptor, response, self.metadata.provider_id)
        ingested_at = self._clock()
        records: list[ProviderRecord] = []
        for row in items:
            timeline = _CLOCK_BUILDERS[descriptor.clock](row, descriptor.date_field, ingested_at)
            if timeline.available_time > request.as_of:
                continue
            subject = _resolve_subject(descriptor, row)
            date_value = _parse_tushare_date(row[descriptor.date_field])
            source_uri = descriptor.source_uri_template.format(
                dataset=descriptor.dataset, subject=subject, date=f"{date_value:%Y%m%d}"
            )
            records.append(
                ProviderRecord(
                    subject=subject,
                    kind=descriptor.kind,
                    timeline=timeline,
                    source_uri=source_uri,
                    summary=(
                        f"Tushare {descriptor.kind} record for {subject} "
                        f"on {date_value.isoformat()}."
                    ),
                    payload=cast(JsonValue, row),
                )
            )
        return tuple(records)
