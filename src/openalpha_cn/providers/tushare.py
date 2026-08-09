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
`trade_cal`, `stock_basic` and `namechange` today. A dataset without one is refused by name
rather than silently handed an empty batch, because "this dataset is not wired to the panel
plane" and "this dataset has no rows" are different facts. Both methods decode the same
response through the same clock table; only the assembly differs.

## Some datasets serve only the panel plane (`V2-P1-005`)

The refusal above now has a mirror image: `serves_evidence_plane=False` refuses `fetch()` for
a dataset by name. Two rows use it, for one reason.

`fetch()` is a payload-passthrough contract -- a `ProviderRecord`'s payload is the decoded
response row verbatim, which is what makes an evidence citation re-provable -- and a record
carries exactly one `available_time` for that whole payload. `stock_basic` and `namechange`
both put facts that became knowable at *different* instants into one response row:
`delist_date` sits beside `list_date` (a 2024 termination beside a 1990 listing), and
`end_date` sits on the name that is currently in effect, naming a rename that may not have
been announced yet. There is no single honest availability instant for such a row, so instead
of choosing one and documenting the leak, the evidence path refuses. The panel path splits
the row instead -- see `_stock_lifecycle_panel_rows`.

This is a stronger response than the one `is_open` gets below, and deliberately so: an
unparsed `is_open` is a type hazard whose blast radius ends at the caller, while a look-ahead
that reaches a stored record is the failure `V2-P1-005` exists to prevent.

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

## A truncated response is refused, never stored (`V2-P1-006`)

Every endpoint here serves at most some number of rows per response and **drops the oldest
ones** past that, with no error code. Measured on 2026-08-08: a bare
`adj_factor(ts_code=000001.SZ)` returns 6,000 rows beginning 2001-11-14, while that security's
real history is 8,627 rows beginning 1991-04-03 -- a whole decade of adjustment factors gone,
and every return computed across the boundary silently wrong.

`_check_response_completeness` is the guard, and it is deliberately two independent witnesses
rather than one:

- **`data.has_more`**, which every live response carries. It is *not* a genuine "more exists"
  flag: the window `20011114..20260808` holds exactly 6,000 rows and nothing older, and still
  reports `True`, while the same window minus one row reports `False`. So it is the server's
  own `len(rows) == limit` heuristic, which makes it over-report and never under-report --
  the fail-closed direction. Only the literal boolean `False` is read as complete, because
  `"False"` and `"0"` are truthy while `0` and `""` are falsy and neither truthiness is a fact
  about the data. That is the same rule, for the same reason, that `_open_flag` applies to
  `is_open`.
- **`max_rows_per_response`**, a row count measured per descriptor against the live endpoint.
  Per descriptor because the caps genuinely differ: `adj_factor` and `daily` cap at 6,000,
  `namechange` at 10,000, and `trade_cal` returned all 13,162 published SSE rows in one
  response with `has_more=False`. A single global constant would refuse that complete calendar
  while passing a truncated `namechange`. `None` means "not measured", not "no cap".

`data.count` is **not** a third witness: it reads `0` on every response measured, truncated or
not.

`requires_truncation_flag` turns an *absent* `has_more` into a failure rather than a skipped
check. It is on `adj_factor` alone today, and stating it per descriptor is what forces
`V2-P1-007`/`008` to decide it rather than inherit it.

**Where this misses.** The guard judges one response in isolation and cannot see a cap that
the endpoint applies *below* its measured value, nor one that changes. It also cannot see the
case where both witnesses are wrong together -- a server that truncates while reporting
`has_more=False` at a row count under the declared cap. What it does close is the entire
measured failure mode, at both the flag and the count. A calendar-derived expected row count
would be a third witness and is deliberately not used: `000001.SZ` has factor rows on 64 dates
in 1991 that the stored SZSE calendar marks closed, so the expectation would be wrong in the
direction that manufactures false alarms.
"""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from math import isfinite
from typing import Any, Final, Protocol, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from openalpha_cn.domain.adjustment import (
    ADJ_FACTOR_DATASET,
    ADJUSTMENT_DATE_COLUMN,
    ADJUSTMENT_FACTOR_COLUMN,
)
from openalpha_cn.domain.name_history import (
    NAME_ANNOUNCEMENT_COLUMN,
    NAME_COLUMN,
    NAME_EFFECTIVE_COLUMN,
    NAME_REASON_COLUMN,
    NAMECHANGE_DATASET,
)
from openalpha_cn.domain.panel_batch import (
    ColumnarPanelBatch,
    PanelColumn,
    PanelColumnKind,
    TimelineColumns,
)
from openalpha_cn.domain.stock_universe import (
    DELISTING_EVENT,
    LIFECYCLE_DATE_COLUMN,
    LIFECYCLE_EVENT_COLUMN,
    LISTING_EVENT,
    STOCK_BASIC_DATASET,
    UNIVERSE_EXCHANGE_COLUMN,
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

    ``daily_close`` (the ``daily`` dataset), ``calendar_publication`` (``trade_cal``) and
    ``calendar_static`` (both of ``V2-P1-005``'s registry datasets) have real production
    consumers. ``announcement`` is defined and independently tested against synthetic data so
    P1 can wire real financial datasets onto it without changing this shape.
    """

    daily_close = "daily_close"
    """Trading-day data: event_time=15:00, available_time=16:30, both Asia/Shanghai."""

    announcement = "announcement"
    """Financial-statement data keyed off ``ann_date``/``f_ann_date`` (original vs. revised)."""

    calendar_static = "calendar_static"
    """Static reference data: event_time == available_time == that day's 00:00.

    ``trade_cal`` deliberately does **not** use it -- see ``calendar_publication`` for why a
    calendar is the one dataset this rule is wrong for. Both of ``V2-P1-005``'s datasets do,
    for the same underlying reason: each of their panel rows records one thing that happened
    on one day and became knowable that day.

    - ``stock_basic``'s lifecycle rows are dated at the listing or the delisting. That is only
      not a look-ahead because the response row is *split* first, so no row carries a fact
      from a different instant -- see ``_stock_lifecycle_panel_rows``.
    - ``namechange``'s rows are dated at ``ann_date``, the announcement. The date the new name
      takes effect is published *in* that announcement, so it is knowable at the same instant
      and rides along as an ordinary column. This is exactly the asymmetry that forces the
      registry to split and lets the rename corpus stay one row: a rename's effective date is
      announced with it, a security's delisting date is not announced with its listing.
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
    """Column that dates a row **after** ``panel_rows`` has run.

    For a dataset without an expansion that is a response column (``trade_date`` /
    ``cal_date``). For one with an expansion it may name a column the expansion synthesises
    (``lifecycle_date``), in which case the raw response is checked against
    ``required_response_fields`` instead -- and such a dataset is panel-only, because there is
    no verbatim response row for ``fetch()`` to hand back under a single clock anyway.
    """
    clock: ClockStrategy
    params_builder: Callable[[ProviderRequest], dict[str, str]]
    """Builds the Tushare ``params`` object from a ``ProviderRequest``."""
    response_fields: str = Field(default="", max_length=2048)
    """The ``fields`` string sent to Tushare; ``""`` asks for that endpoint's defaults.

    Not decoration for ``stock_basic``: its default field set is ``ts_code, symbol, name,
    area, industry, cnspell, market, list_date, act_name, act_ent_type`` and contains **no
    ``delist_date``**, so a request that leaves this empty cannot see the column the whole
    survivorship question turns on.
    """
    required_response_fields: tuple[str, ...] = ()
    """Response columns that must be present; ``()`` means ``(date_field,)``.

    Declared per dataset so the schema check is about what this descriptor actually reads,
    not only about the one column that happens to date a row.
    """
    source_uri_template: str = Field(min_length=1, max_length=2048)
    """``str.format`` template with ``{dataset}``, ``{subject}``, and ``{date}`` placeholders."""
    panel_columns: tuple[TusharePanelColumn, ...] = ()
    """Panel-plane projection, or ``()`` for a dataset ``fetch_panel()`` does not serve yet."""
    panel_rows: Callable[[dict[str, Any]], tuple[dict[str, Any], ...]] | None = None
    """Expands one response row into the panel rows it carries; ``None`` means one-to-one.

    Exists because a response row is not always a unit of knowability. A ``stock_basic`` row
    carries a listing and, sometimes, a delisting, and no single ``available_time`` is right
    for both -- see ``_stock_lifecycle_panel_rows``.
    """
    serves_evidence_plane: bool = True
    """Whether ``fetch()`` serves this dataset; see this module's docstring for the two that
    do not."""
    max_rows_per_response: int | None = Field(default=None, ge=1)
    """The row cap this endpoint was **measured** to apply, or ``None`` for "not measured".

    Not a global constant, because the caps differ per endpoint -- see this module's
    docstring. ``None`` deliberately does not mean "no cap": it means nobody has established
    one, and the ``has_more`` witness is the only guard such a dataset has.
    """
    requires_truncation_flag: bool = False
    """Whether a response that omits ``has_more`` entirely is refused.

    Separate from ``max_rows_per_response`` because the two witnesses fail independently, and
    a descriptor should be able to demand the stronger one without also having to claim a
    measured cap (or the other way round).
    """

    @property
    def checked_response_fields(self) -> tuple[str, ...]:
        """The response columns ``_response_rows`` verifies are present."""
        return self.required_response_fields or (self.date_field,)


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


STOCK_BASIC_LIST_STATUS: Final[str] = "L,D"
"""Both halves of the registry in one request, measured rather than assumed.

A live probe on 2026-08-08: `list_status="L"` (and the bare request, and `list_status=""`)
returns **5,539** rows, all listed; `list_status="D"` returns **339**, all with a
`delist_date`; `list_status="P"` returns **0**. `"L,D"` returns **5,878** -- the union, in one
call, with the two halves distinguishable by whether `delist_date` is populated. `"LD"` and
`"L|D"` both return zero rows, so the comma is the separator the endpoint accepts and not a
lucky coincidence of parsing.
"""

STOCK_BASIC_FIELDS: Final[str] = "ts_code,name,exchange,market,list_status,list_date,delist_date"
"""What this descriptor asks for. `delist_date` is absent from the endpoint's defaults.

`name` and `market` are requested but deliberately **not** projected onto the panel: they are
attributes of the snapshot with no history and no clock (the registry calls `000005.SZ`
`ST星源(退)` today, and stamping that onto its 1990-12-10 listing row would be a 34-year
look-ahead). They are asked for so that a schema drift in the registry's shape is visible in
the raw response rather than only in the three columns that survive projection.
"""

NAMECHANGE_FIELDS: Final[str] = "ts_code,name,start_date,end_date,ann_date,change_reason"
"""The full `namechange` field set. `end_date` is requested and never stored -- it is the
witness `domain/name_history.py` cross-checks the derived intervals against."""


def _stock_basic_params(request: ProviderRequest) -> dict[str, str]:
    """Ask for the listed **and** the delisted registry in one call.

    Subjects are refused rather than passed through as `ts_code`. The endpoint accepts that
    filter, and this dataset must not: a `stock_basic` partition is the universe, a filtered
    universe is not one, and `StockUniverse.completeness()` would report a security count that
    described the filter rather than the market. `PanelStore` replaces a partition whole, so a
    filtered write would also silently destroy a full one.
    """
    if request.subjects:
        raise ProviderFailure(
            provider_id=_PROVIDER_ID,
            category="configuration",
            message=(
                f"{STOCK_BASIC_DATASET} serves the whole registry in one partition; got "
                f"subjects {list(request.subjects)}, and a filtered universe is not a universe"
            ),
            retryable=False,
        )
    return {"list_status": STOCK_BASIC_LIST_STATUS}


def _namechange_params(request: ProviderRequest) -> dict[str, str]:
    """Request one whole **announcement** year: `{start_date, end_date}`.

    The window filters `ann_date`, not `start_date` -- measured: the 2012 window returns 320
    rows whose announcement dates span 2012-01-05..2012-12-31 while their effective dates run
    to 2014-08-08. That is the right key for this dataset, because `ann_date` is what
    `available_time` is derived from, so one request is one partition is one availability
    year.

    The alternative whole-market pull, `offset`/`limit` paging, is **unsound** and was
    measured to be: two pages of 10,000 returned 14,166 rows containing 380 exact duplicates
    and 13,786 distinct ones, and among the missing was a genuine record the per-`ts_code`
    query returns (`000001.SZ` / `S深发展A`, effective 2006-10-09). Re-assembling the corpus
    from 37 announcement-year windows instead yields 14,166 *distinct* rows and recovers it.

    The year is `as_of`'s year *in Asia/Shanghai*: 2024-12-31 17:00Z is already 2025 in
    Shanghai, and asking UTC would fetch the wrong year for every late-evening request on the
    last day of a year. `_trade_cal_params` takes the same care for the same reason.
    """
    if request.subjects:
        raise ProviderFailure(
            provider_id=_PROVIDER_ID,
            category="configuration",
            message=(
                f"{NAMECHANGE_DATASET} serves one announcement year of the whole market per "
                f"request; got subjects {list(request.subjects)}, and a partition holding one "
                "name would replace the year's partition rather than add to it"
            ),
            retryable=False,
        )
    year = request.as_of.astimezone(_CHINA_TZ).year
    return {"start_date": f"{year}0101", "end_date": f"{year}1231"}


def _stock_lifecycle_panel_rows(row: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Split one registry row into its lifecycle events: a listing, and maybe a delisting.

    This is the expansion `V2-P1-005` turns on, and the argument for it is a dilemma rather
    than a preference. A panel row carries one `available_time` for everything in it. Give a
    whole registry row the listing's instant and a 2024 termination becomes visible to a 2019
    reader -- a look-ahead. Give it the termination's instant and the security disappears from
    every `as_of` before it died -- survivorship bias, the exact defect this issue names. The
    two facts therefore cannot share a row, and the point-in-time filter then does the right
    thing at both ends without any dataset-specific rule downstream.

    An absent `delist_date` is `None` or `""` depending on how the response was serialised;
    both mean "still listed" and neither invents a delisting row.
    """
    ts_code = row["ts_code"]
    exchange = row[UNIVERSE_EXCHANGE_COLUMN]
    listing = {
        "ts_code": ts_code,
        UNIVERSE_EXCHANGE_COLUMN: exchange,
        LIFECYCLE_EVENT_COLUMN: LISTING_EVENT,
        LIFECYCLE_DATE_COLUMN: row["list_date"],
    }
    delisted_on = row["delist_date"]
    if delisted_on is None or delisted_on == "":
        return (listing,)
    return (
        listing,
        {
            "ts_code": ts_code,
            UNIVERSE_EXCHANGE_COLUMN: exchange,
            LIFECYCLE_EVENT_COLUMN: DELISTING_EVENT,
            LIFECYCLE_DATE_COLUMN: delisted_on,
        },
    )


def _lifecycle_event_name(value: object) -> object:
    """Pass a lifecycle event through, refusing anything the domain contract does not know."""
    if value not in (LISTING_EVENT, DELISTING_EVENT):
        raise ValueError(
            f"lifecycle_event must be {LISTING_EVENT!r} or {DELISTING_EVENT!r}, got {value!r}"
        )
    return value


def _required_text(value: object) -> object:
    """Pass a non-empty string through; refuse anything else.

    A `None` in a projected string column is a legal `PanelColumn` value (panel data is
    sparse), so nothing downstream would object to a missing exchange or a missing name --
    it would simply become a row that groups under `None`. These columns are keys, not
    observations, so a missing one is a malformed response.
    """
    if type(value) is not str or not value:
        raise ValueError(f"expected a non-empty string, got {type(value).__name__} {value!r}")
    return value


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


def _adjustment_factor(value: object) -> object:
    """Parse Tushare's ``adj_factor`` into a real, usable ``float``.

    Three refusals, each of which would otherwise be silent:

    - **``bool``.** ``True`` is an ``int``, so any check written against the numeric tower
      admits it, and it would then scale every price by 1.0 -- an unadjusted series wearing an
      adjusted series' name, which is the exact failure this dataset exists to prevent.
    - **Zero, the negatives, and the non-finites.** None of them can scale a price, and the
      forward convention divides by one.
    - **Strings.** ``"1.0"`` would land in a ``float`` column and be rejected downstream by
      ``PanelColumn`` anyway; failing here names the column and the value.

    An ``int`` is accepted and widened, because JSON has one number type: the listing-day
    factor of exactly 1 can arrive as ``1`` rather than ``1.0``, and ``PanelColumn`` refuses an
    ``int`` in a ``float`` column (``bool`` is filtered out first, above).
    """
    if type(value) is float or type(value) is int:
        number = float(value)
        if isfinite(number) and number > 0.0:
            return number
    raise ValueError(
        f"adj_factor must be a finite positive number, got {type(value).__name__} {value!r}"
    )


def _optional_calendar_date_text(value: object) -> object:
    """Same as `_calendar_date_text`, but a missing value stays missing.

    `pretrade_date` was populated on every one of the 13,162 rows the live probe returned, so
    the null branch is defence rather than an observed case -- and a `None` here is honest
    where a fabricated date would not be.
    """
    if value is None or value == "":
        return None
    return _calendar_date_text(value)


TUSHARE_RESPONSE_TRUNCATION_FLAG: Final[str] = "has_more"
"""The response field that says the endpoint had more rows than it served.

Named as a constant because three places read it: the guard, the tests that build responses
without it, and `V2-P1-007`/`008` when they wire the next capped dataset.
"""

TUSHARE_PRICE_ROW_CAP: Final[int] = 6000
"""Rows per response for `adj_factor` and `daily`, measured against the live endpoints.

`adj_factor(ts_code=000001.SZ)` and `daily(ts_code=000001.SZ)` each return exactly 6,000 rows
with `has_more=True`, and `limit=8000` / `limit=10000` do not raise it. The cap drops the
*oldest* rows: `adj_factor`'s capped response starts 2001-11-14 against a true history
starting 1991-04-03.
"""

TUSHARE_NAMECHANGE_ROW_CAP: Final[int] = 10000
"""Rows per response for `namechange`, measured: the whole-corpus window returns exactly
10,000 rows with `has_more=True`, which is also why `domain/name_history.py` assembles the
corpus from 37 announcement-year windows instead."""

TUSHARE_DATASETS: tuple[TushareDatasetDescriptor, ...] = (
    TushareDatasetDescriptor(
        dataset="daily",
        kind="daily",
        subject_field="ts_code",
        date_field="trade_date",
        clock=ClockStrategy.daily_close,
        params_builder=_trade_date_params,
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        # Measured, and carried now rather than with `V2-P1-007`: the cap is a property of
        # the endpoint, not of the task that first stores its rows, and a 6,000-row `daily`
        # response is already reachable through `fetch()` today.
        max_rows_per_response=TUSHARE_PRICE_ROW_CAP,
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
        # No measured cap, and 6,000 would be actively wrong here: the whole published SSE
        # calendar -- 13,162 rows -- comes back in one response with `has_more=False`. This
        # is the descriptor that proves the cap has to be per endpoint.
        max_rows_per_response=None,
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
    TushareDatasetDescriptor(
        dataset=STOCK_BASIC_DATASET,
        kind=STOCK_BASIC_DATASET,
        subject_field="ts_code",
        # Synthesised by `_stock_lifecycle_panel_rows`, not a response column: the response
        # has two date columns and the expansion is what picks one per row.
        date_field=LIFECYCLE_DATE_COLUMN,
        clock=ClockStrategy.calendar_static,
        params_builder=_stock_basic_params,
        response_fields=STOCK_BASIC_FIELDS,
        required_response_fields=("ts_code", "exchange", "list_date", "delist_date"),
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        panel_rows=_stock_lifecycle_panel_rows,
        serves_evidence_plane=False,
        # No measured cap: the whole `L,D` registry -- 5,878 rows on 2026-08-08 -- comes back
        # in one response with `has_more=False`, which places this endpoint's cap somewhere
        # above 5,878 without establishing where. Declaring 6,000 here would be a guess, and
        # the guess is load-bearing: too low refuses a complete registry, too high passes a
        # truncated one. The flag witness guards it in the meantime, and the margin is thin
        # enough to be worth watching -- 122 rows.
        max_rows_per_response=None,
        panel_columns=(
            TusharePanelColumn(
                name=LIFECYCLE_EVENT_COLUMN,
                kind="string",
                source_field=LIFECYCLE_EVENT_COLUMN,
                parse=_lifecycle_event_name,
            ),
            TusharePanelColumn(
                name=LIFECYCLE_DATE_COLUMN,
                kind="string",
                source_field=LIFECYCLE_DATE_COLUMN,
                parse=_calendar_date_text,
            ),
            TusharePanelColumn(
                name=UNIVERSE_EXCHANGE_COLUMN,
                kind="string",
                source_field=UNIVERSE_EXCHANGE_COLUMN,
                parse=_required_text,
            ),
        ),
    ),
    TushareDatasetDescriptor(
        dataset=NAMECHANGE_DATASET,
        kind=NAMECHANGE_DATASET,
        subject_field="ts_code",
        # The announcement, not the effect. A rename's effective date is published *in* the
        # announcement, so it is knowable at `ann_date` and rides along as a column; dating
        # the row at `start_date` instead would put a 2012 partition's rows into 2014, which
        # `PanelStore.record_coverage` refuses outright and rightly so -- a request window
        # here is an *announcement* window, so one fetch would straddle three partitions and
        # three fetches would contend for one.
        date_field="ann_date",
        clock=ClockStrategy.calendar_static,
        params_builder=_namechange_params,
        response_fields=NAMECHANGE_FIELDS,
        required_response_fields=("ts_code", "name", "start_date", "ann_date"),
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        serves_evidence_plane=False,
        max_rows_per_response=TUSHARE_NAMECHANGE_ROW_CAP,
        # `end_date` is fetched and not projected: it is derivable from the successor's
        # `start_date` and, unlike the successor, is gated by no announcement, so storing it
        # would put an unannounced future rename on the record currently in effect.
        panel_columns=(
            TusharePanelColumn(
                name=NAME_COLUMN,
                kind="string",
                source_field="name",
                parse=_required_text,
            ),
            TusharePanelColumn(
                name=NAME_EFFECTIVE_COLUMN,
                kind="string",
                source_field="start_date",
                parse=_calendar_date_text,
            ),
            TusharePanelColumn(
                name=NAME_ANNOUNCEMENT_COLUMN,
                kind="string",
                source_field="ann_date",
                parse=_calendar_date_text,
            ),
            TusharePanelColumn(
                name=NAME_REASON_COLUMN,
                kind="string",
                source_field="change_reason",
                parse=_required_text,
            ),
        ),
    ),
    TushareDatasetDescriptor(
        dataset=ADJ_FACTOR_DATASET,
        kind=ADJ_FACTOR_DATASET,
        subject_field="ts_code",
        date_field="trade_date",
        # The factor for session D is knowable once D has closed. Dating it at D's midnight
        # instead would be a look-ahead of one session on every ex-dividend date, which is
        # precisely the day the number matters.
        clock=ClockStrategy.daily_close,
        # One trading day of the whole market, the same builder `daily` uses. Measured:
        # 5,387 rows on 2024-06-28 and 5,553 on 2026-08-07, against a 6,000-row cap -- so a
        # cross section fits and one security's whole history (8,627 rows) does not. When the
        # market outgrows the cap this refuses rather than truncating, and `subjects` is the
        # escape route: it splits the day instead of losing the oldest codes.
        params_builder=_trade_date_params,
        # `""` asks for the endpoint's defaults, which are exactly the three columns below --
        # measured, not assumed. Naming them in `fields` instead would hide a schema drift
        # that added a fourth; `required_response_fields` pins what this descriptor reads.
        response_fields="",
        required_response_fields=("ts_code", "trade_date", ADJUSTMENT_FACTOR_COLUMN),
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        max_rows_per_response=TUSHARE_PRICE_ROW_CAP,
        requires_truncation_flag=True,
        panel_columns=(
            TusharePanelColumn(
                name=ADJUSTMENT_DATE_COLUMN,
                kind="string",
                source_field="trade_date",
                parse=_calendar_date_text,
            ),
            TusharePanelColumn(
                name=ADJUSTMENT_FACTOR_COLUMN,
                kind="float",
                source_field=ADJUSTMENT_FACTOR_COLUMN,
                parse=_adjustment_factor,
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
    """Static metadata: event time and availability time are the same midnight.

    ``revision_time`` equals ``available_time`` for the reason ``trade_cal``'s does: the
    response carries no revision instant, and every alternative fabricates one. Read a
    ``revised_row_count == 0`` on either of these datasets as "unmeasured", not "none".

    ## Why ``ingested_time`` is raised rather than ``available_time`` lowered

    Tushare sometimes serves a row whose own date is in the future of the fetch. One live case
    on 2026-08-08: the ``namechange`` corpus already carried ``920165.BJ`` / 珈凯生物,
    announced and effective 2026-08-11. ``Timeline`` forbids
    ``available_time > ingested_time``, so such a row cannot be represented as it stands, and
    exactly two repairs exist.

    Lowering ``available_time`` to the fetch instant is what ``_calendar_publication_timeline``
    does for ``trade_cal``, and it is right *there* because a published calendar we
    demonstrably hold was demonstrably published. It is wrong here: a rename we hold has not
    necessarily been announced, and lowering the instant would make an unannounced rename
    readable -- the dangerous direction, and an invented fact.

    Raising ``ingested_time`` to the availability instant overstates only the one clock no
    point-in-time filter consults (``is_visible_at`` reads ``available_time``, and
    ``PartitionCoverage`` summarises event, availability and revision). That is why the repair
    is on this side. It is still an overstatement, so it is not allowed to survive: the raise
    exists **only so the row can be represented long enough to be discarded**.
    ``TushareProvider._decode_panel_rows`` bounds its point-in-time filter at
    ``min(as_of, ingested_at)``, so any row whose availability runs past the fetch instant is
    dropped there, and the raised clock never reaches a stored partition -- not for an
    ``as_of`` in the future, not for one a caller set to the end of the current year, not at
    all. An earlier version of this docstring said the raised value "reaches a stored
    partition only for a caller who has arranged for it to", which described a filter bounded
    at ``as_of`` alone; ``ProviderRequest`` accepts any ``as_of``, and dating the fetch at the
    end of the calendar year is a shape this repository's own fixtures use.
    ``tests/contract/providers/test_tushare_registry_datasets.py`` pins both branches, and
    ``tests/integration/panel/test_registry_ingest.py`` pins the drop.
    """
    moment = datetime.combine(_parse_tushare_date(row[date_field]), time(0, 0), tzinfo=_CHINA_TZ)
    return Timeline(
        event_time=moment,
        available_time=moment,
        ingested_time=max(ingested_at, moment),
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


_TRUNCATION_FLAG_ABSENT: Final[object] = object()


def _check_response_completeness(
    descriptor: TushareDatasetDescriptor,
    data: dict[str, Any],
    items: list[Any],
    provider_id: str,
) -> None:
    """Refuse a response that may have had rows withheld. See this module's docstring.

    Runs before the schema check and before any row is decoded, on both output paths, because
    a truncated response is well formed: it decodes cleanly, validates cleanly, stores
    cleanly, and is missing a decade of data. There is nothing later in the pipeline that
    could notice.

    ``retryable=False`` on every branch: the same request returns the same truncated answer,
    so a retry loop would spin. The remedy is a narrower window (or `subjects`), which is a
    different request.
    """
    flag = data.get(TUSHARE_RESPONSE_TRUNCATION_FLAG, _TRUNCATION_FLAG_ABSENT)
    if flag is _TRUNCATION_FLAG_ABSENT:
        if descriptor.requires_truncation_flag:
            raise ProviderFailure(
                provider_id=provider_id,
                category="upstream",
                message=(
                    f"Tushare's {descriptor.dataset} response carries no "
                    f"{TUSHARE_RESPONSE_TRUNCATION_FLAG} flag; every live response for this "
                    "dataset does, so this one cannot be shown to be complete and a "
                    "truncated factor series is silently wrong rather than short"
                ),
                retryable=False,
            )
    elif flag is not False:
        # `is not False`, not `if flag:`. `"False"` and `"0"` are truthy and `0` and `""` are
        # falsy, so either coercion turns a schema change into a wrong answer rather than an
        # error -- the same trap `_open_flag` exists for.
        raise ProviderFailure(
            provider_id=provider_id,
            category="upstream",
            message=(
                f"Tushare's {descriptor.dataset} response reports that it has more rows to "
                f"give ({TUSHARE_RESPONSE_TRUNCATION_FLAG}={flag!r}); it serves at most one "
                "page and drops the oldest rows, so this answer is a suffix of the truth. "
                f"{TUSHARE_RESPONSE_TRUNCATION_FLAG} must be exactly the boolean False for a "
                "response to count as complete. Narrow the request window or split it by "
                "subject"
            ),
            retryable=False,
        )
    cap = descriptor.max_rows_per_response
    if cap is not None and len(items) >= cap:
        raise ProviderFailure(
            provider_id=provider_id,
            category="upstream",
            message=(
                f"Tushare served {len(items)} {descriptor.dataset} row(s), which is its "
                f"measured per-response cap of {cap}; a response at the cap cannot be "
                "distinguished from one the cap truncated, and the rows it drops are the "
                "oldest. Narrow the request window or split it by subject"
            ),
            retryable=False,
        )


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
    _check_response_completeness(descriptor, data, items, provider_id)
    for required in descriptor.checked_response_fields:
        if required not in fields:
            raise ValueError(f"Tushare response for {descriptor.dataset} has no {required} column")
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            raise ValueError("Tushare item does not match fields")
        rows.append(dict(zip(fields, item, strict=True)))
    return rows


def _expand_panel_rows(
    descriptor: TushareDatasetDescriptor, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply the descriptor's `panel_rows` expansion, or hand the rows back unchanged.

    Panel-path only. `fetch()` never runs this, because an expansion is precisely the
    admission that the response row is not a unit of knowability, and a descriptor that
    declares one is refused on the evidence plane for that reason.
    """
    if descriptor.panel_rows is None:
        return rows
    expand = descriptor.panel_rows
    return [expanded for row in rows for expanded in expand(row)]


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
        f"at as_of {request.as_of.isoformat()} or at the instant of the fetch: not yet "
        "knowable is not the same as absent"
    )


MAX_PANEL_SOURCE_URI_LENGTH: Final[int] = 2048
"""`ColumnarPanelBatch`'s own limit on `source_uri`, restated here so this module can stay
under it rather than trip over it. The two must agree; if the contract's limit moves, this
constant is what a reviewer greps for."""


def _panel_source_uri(
    descriptor: TushareDatasetDescriptor,
    subjects: tuple[str, ...],
    rows: Sequence[dict[str, Any]],
) -> str:
    """One provenance URI for a whole partition-shaped batch.

    `ProviderRecord` carries one per row; a columnar batch has one field for all of them, so
    the `{date}` slot holds the closed range the batch actually covers rather than a single
    day. Rows are already ascending by the time this is called.

    ## Why the subject list is summarised past a point

    Joining every subject is right for a two-exchange calendar partition and impossible for a
    market-wide one. A `stock_basic` fetch carries 5,878 subjects and an `adj_factor` cross
    section 5,387, which join into ~60,000 characters -- and `ColumnarPanelBatch` refuses a
    `source_uri` over 2,048, *outside* `fetch_panel`'s decode `try`, so the whole fetch died
    with a contract error and no whole-market panel fetch could complete at all (reproduced
    against `2b06c4c` with 400 synthetic registry rows).

    So the join is kept while it fits and replaced by `"{n}-subjects"` when it does not. The
    count is not a substitute for the set and is not offered as one: the exact subjects live
    in the partition's own `subject` column and are covered by `ColumnarPanelBatch.
    content_digest`, which is what a later reader re-proves the partition against.
    """
    first = _parse_tushare_date(rows[0][descriptor.date_field])
    last = _parse_tushare_date(rows[-1][descriptor.date_field])
    unique_subjects = sorted(set(subjects))
    span = f"{first:%Y%m%d}-{last:%Y%m%d}"
    subject = unique_subjects[0] if len(unique_subjects) == 1 else ",".join(unique_subjects)
    uri = descriptor.source_uri_template.format(
        dataset=descriptor.dataset, subject=subject, date=span
    )
    if len(uri) <= MAX_PANEL_SOURCE_URI_LENGTH:
        return uri
    return descriptor.source_uri_template.format(
        dataset=descriptor.dataset, subject=f"{len(unique_subjects)}-subjects", date=span
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
        """Fetch a descriptor-table dataset or raise a structured failure.

        Refuses a descriptor that declares ``serves_evidence_plane=False``. See this module's
        docstring: an evidence record's payload is the response row verbatim under one
        ``available_time``, and two of this table's datasets put facts with different
        availability instants into one row.
        """
        descriptor = self._descriptor(request)
        if not descriptor.serves_evidence_plane:
            raise ProviderFailure(
                provider_id=self.metadata.provider_id,
                category="configuration",
                message=(
                    f"Tushare dataset {request.dataset} puts facts that became knowable at "
                    "different instants into one response row, so a verbatim evidence record "
                    "has no single available_time; it is served only on the panel plane, via "
                    "fetch_panel()"
                ),
                retryable=False,
            )
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
                "fields": descriptor.response_fields,
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
        could not otherwise tell apart. It counts **panel** rows, after any expansion, because
        that is the population the filter runs over; for a one-to-one descriptor the two are
        the same number.

        ## The filter is bounded at the fetch instant as well as at ``as_of``

        ``ProviderRequest`` accepts any ``as_of``, including one in the future, and a batch
        dated at the end of the current calendar year is a shape this repository's own
        fixtures use. Filtering on ``as_of`` alone therefore lets through a row that was not
        knowable when the fetch *ran* -- Tushare does serve those, e.g. a ``namechange``
        record announced 2026-08-11 that is already in the corpus on 2026-08-08 -- and
        ``_calendar_static_timeline`` can only represent such a row by raising its
        ``ingested_time`` above the instant this process actually observed it.

        So the bound is the **earlier** of the two instants, and the second half is exactly
        the same statement as the first: a row is kept only if it was knowable both by the
        requested ``as_of`` and by the moment this fetch happened. Nothing honest is lost --
        the dropped row was, by its own clock, not yet knowable -- and neither clock has to
        overstate to make it fit. A response whose rows are all dropped this way is
        ``no_data`` with ``served`` non-zero, which already says "served but not yet
        knowable".
        """
        items = _expand_panel_rows(
            descriptor, _response_rows(descriptor, response, self.metadata.provider_id)
        )
        ingested_at = self._clock()
        knowable_by = min(request.as_of, ingested_at)
        kept: list[tuple[date, dict[str, Any], Timeline]] = []
        for row in items:
            timeline = _CLOCK_BUILDERS[descriptor.clock](row, descriptor.date_field, ingested_at)
            if timeline.available_time > knowable_by:
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
