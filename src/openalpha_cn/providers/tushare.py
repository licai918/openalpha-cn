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
for exactly those datasets whose descriptor declares a `panel_columns` projection -- which,
since `V2-P1-007`, is every row of the table. A dataset without one is refused by name rather
than silently handed an empty batch, because "this dataset is not wired to the panel plane" and
"this dataset has no rows" are different facts. Both methods decode the same response through
the same clock table; only the assembly differs.

`daily` is the one dataset that is served on **both** planes and stored on only one, and that
is deliberate. Unlike `stock_basic` and `namechange` below, a daily bar has a single honest
`available_time`, so `fetch()` can hand back the response row verbatim and the record stays
re-provable. What moves the dataset onto the panel plane is scale -- ADR-0002 sizes the price
panel at ~1.35e7 rows per field, and an evidence record costs a pydantic rebuild and three
SHA-256 digests each -- and scale is a reason to add a second output shape, not to make the
first one refuse. `evidence/builder.py` still declines `kind="daily"`, and correctly: an
`EvidenceSnapshot` is a normalised citation (a limit-up, a disclosure, a theme) and a price bar
is none of those.

## Some datasets serve only the panel plane (`V2-P1-005`)

The refusal above now has a mirror image: `serves_evidence_plane=False` refuses `fetch()` for
a dataset by name. Four rows use it, for one reason.

`fetch()` is a payload-passthrough contract -- a `ProviderRecord`'s payload is the decoded
response row verbatim, which is what makes an evidence citation re-provable -- and a record
carries exactly one `available_time` for that whole payload. `stock_basic`, `namechange` and
`index_member_all` all put facts that became knowable at *different* instants into one
response row: `delist_date` sits beside `list_date` (a 2024 termination beside a 1990
listing), `end_date` sits on the name that is currently in effect, naming a rename that may
not have been announced yet, and `out_date` sits beside `in_date` on an industry assignment.
There is no single honest availability instant for such a row, so instead of choosing one and
documenting the leak, the evidence path refuses. The panel path splits the row instead -- see
`_stock_lifecycle_panel_rows` and `_industry_membership_panel_rows`. `index_classify` is the
fourth and is there for the neighbouring reason: its date column is synthesised, so there is
no verbatim response row to hand back under a single clock.

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
check. Two descriptors set it, for two different reasons. `adj_factor` has a measured cap and
demands the flag *as well*, because a factor series missing its first decade is silently wrong
rather than short. `stock_basic` sets it because the flag is its **only** witness: its cap is
not merely unmeasured but unmeasurable from outside (see that descriptor's comment), so
without this it had *neither* guard -- a `stock_basic` response omitting `has_more` was
accepted at 300 rows with nothing consulted at all. Stating it per descriptor is what forces
`V2-P1-007`/`008` to decide it rather than inherit it.

**Where this misses.** The guard judges one response in isolation, so the residue is a server
that truncates while reporting `has_more=False` at a row count under the declared cap. It is
*not* an endpoint that lowers its cap: raising `limit` above the measured value (6,001 /
8,000 / 10,000) still returns exactly 6,000 rows with `has_more=True`, which shows the server
computes the flag as `len(rows) == min(limit, cap)` -- so any truncation at any cap reports
`True`, and the flag witness covers a moved cap on its own.

A calendar-derived expected row count would be a third witness and is deliberately not used
*here*: `000001.SZ` has factor rows on 64 dates in 1991 that are not in the stored SZSE
calendar at all -- its series begins 1991-07-03 and the factor begins 1991-04-03 -- so the
expectation would be wrong in the direction that manufactures false alarms. The calendar
still earns its place one layer up, where the direction can be chosen:
`panel_ingest.write_adjustment_factors` refuses a *partition* that is missing a session the
calendar reports open, and tolerates one that carries extra days.

### `daily` and `daily_basic` take the cap and decline the flag (`V2-P1-007`)

`requires_truncation_flag` was stated per descriptor precisely so this issue would have to
decide it rather than inherit it, and the decision is **no**, with a reason rather than a
shrug. The flag governs only the case where `has_more` is *absent*; a flag that is present is
checked for every dataset, and both price endpoints carry a measured 6,000-row cap (a bare
`daily(ts_code=000001.SZ)` and a bare `daily_basic(ts_code=000001.SZ)` each return exactly
6,000 rows with `has_more=True`). `adj_factor` demands the flag as well because a truncated
factor series is silently *wrong* -- the step function answers the missing decade from an older
level -- while a truncated `daily` response is *short*: the bar is simply absent and nothing
downstream interpolates it. The residue is stated rather than argued away: a response that both
omits `has_more` and comes in under 6,000 rows is accepted, and
`tests/contract/providers/test_tushare_daily.py` pins both halves of that.

Two things about that decision are worth writing down rather than leaving to be rediscovered.

**It is not a free choice.** Setting the flag to `True` breaks two unrelated tests --
`test_the_flag_is_demanded_exactly_where_it_is_the_only_witness_or_the_stakes_are_highest` and
`test_tushare_byot_maps_daily_payload_without_exposing_token` -- so the enumerative assertion in
the first of those admits exactly one answer. The reasoning above stands on its own (the
cross-section path measures `has_more == (len(rows) == min(limit, cap))`, so any truncation at
any cap sets it, and reaching the residue needs an API schema change), but an assertion that
allows only one value is not independent evidence for that value. A later task that has a real
reason to demand the flag on a price dataset should change the test rather than read its
redness as a verdict.

**The headroom is not a schedule.** The cross section was 5,535 rows on 2026-08-07 against the
6,000 cap: 465 spare. Year-end counts measured on the live endpoint are 3,581 (2018), 3,796,
4,201, 4,738, 5,063, 5,329, 5,370, 5,458 -- so the last three years added +41, +88 and +77 and
2020..2022 added +405, +537 and +325. Extrapolating the recent rate gives five to eleven years
and extrapolating the earlier one gives about one, and nothing in the data says which regime the
next listing cycle is in. So this is a bound to *monitor*, not a date: when a cross section
approaches the cap the escape route is already in the descriptor (`subjects` splits one session
into several requests), and `_check_response_completeness` refuses at the cap rather than
storing a short session in the meantime.

## The panel projection is a schema contract of its own (`V2-P1-007`)

`_check_panel_projection` refuses a response whose rows lack a column the descriptor projects.
It exists because the two planes have genuinely different schema contracts and one field cannot
carry both: `required_response_fields` is the *evidence* plane's, and `daily`'s has to stay at
its default `(trade_date,)` because `fetch()` is a payload-passthrough whose oldest caller
drives it with a four-column response. The panel path reads nine columns for `daily` and
seventeen for `daily_basic`, and without this check a drifted response surfaced as
`Tushare response could not be decoded: 'pre_close'` -- a bare `KeyError` reason for a named
schema change. The check runs against the **expanded** rows rather than the response's own
`fields`, because `stock_basic` projects two columns its response does not carry.
"""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
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
from openalpha_cn.domain.daily_prices import (
    DAILY_AVAILABILITY_TIME,
    DAILY_BASIC_DATA_COLUMNS,
    DAILY_BASIC_DATASET,
    DAILY_BASIC_NULLABLE_COLUMNS,
    DAILY_DATA_COLUMNS,
    DAILY_DATASET,
    DAILY_PRICE_COLUMNS,
    PRICE_DATE_COLUMN,
    SESSION_CLOSE_TIME,
)
from openalpha_cn.domain.financial_statements import (
    ANNOUNCEMENT_DATE_COLUMN,
    BALANCE_SHEET_DATASET,
    DATASETS_WITH_REVISION_LABEL,
    FINANCIAL_INDICATOR_DATASET,
    FINANCIAL_STATEMENT_DATASETS,
    FIRST_ANNOUNCEMENT_COLUMN,
    REPORT_PERIOD_COLUMN,
    REVISION_LABEL_COLUMN,
    STATEMENT_DATA_COLUMNS,
)
from openalpha_cn.domain.index_membership import (
    INDEX_CONSTITUENT_COLUMN,
    INDEX_PUBLICATION_DATE_COLUMN,
    INDEX_WEIGHT_COLUMN,
    INDEX_WEIGHT_DATASET,
    TOTAL_PUBLISHED_WEIGHT,
)
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_CODE_COLUMN,
    INDUSTRY_FROM_COLUMN,
    INDUSTRY_L1_COLUMN,
    INDUSTRY_L2_COLUMN,
    INDUSTRY_L3_COLUMN,
    INDUSTRY_LEVEL_COLUMN,
    INDUSTRY_MEMBERSHIP_DATASET,
    INDUSTRY_MEMBERSHIP_TAXONOMY,
    INDUSTRY_NAME_COLUMN,
    INDUSTRY_PARENT_COLUMN,
    INDUSTRY_PUBLISHED_COLUMN,
    INDUSTRY_TAXONOMY_COLUMN,
    INDUSTRY_TAXONOMY_DATE_COLUMN,
    INDUSTRY_TAXONOMY_EFFECTIVE_FROM,
    INDUSTRY_THROUGH_COLUMN,
    INDUSTRY_TREE_DATASET,
)
from openalpha_cn.domain.name_history import (
    NAME_ANNOUNCEMENT_COLUMN,
    NAME_COLUMN,
    NAME_EFFECTIVE_COLUMN,
    NAME_REASON_COLUMN,
    NAMECHANGE_DATASET,
)
from openalpha_cn.domain.panel_batch import (
    MAX_SOURCE_URI_LENGTH,
    ColumnarPanelBatch,
    PanelColumn,
    PanelColumnKind,
    TimelineColumns,
)
from openalpha_cn.domain.price_limits import (
    DOWN_LIMIT_COLUMN,
    HALT_TYPE,
    PRICE_LIMIT_DATA_COLUMNS,
    PRICE_LIMIT_DATASET,
    RESUMPTION_TYPE,
    SUSPENSION_DATA_COLUMNS,
    SUSPENSION_DATASET,
    SUSPENSION_TIMING_COLUMN,
    SUSPENSION_TYPE_COLUMN,
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

    Every member now has real production consumers. ``announcement`` was the last one that did
    not: it was defined and tested against synthetic data so that ``V2-P1-011`` could wire the
    four financial-statement endpoints onto it without changing this shape, and that is what
    happened -- the shape held, and the only change the real data forced was inside
    ``_announcement_timeline`` itself (see its docstring for the 116 rows that raised).
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

    taxonomy_backfill = "taxonomy_backfill"
    """An interval expressed in a classification published after the interval began.

    ``index_member_all``'s clock: the row's own event, floored at the taxonomy's effective date.
    The floor is the part no column of the row can supply. See ``_taxonomy_backfill_timeline``
    for the look-ahead it closes and ``_industry_membership_panel_rows`` for why the row is split
    first, exactly as ``calendar_static`` needs ``stock_basic`` split first.
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


def _index_weight_params(request: ProviderRequest) -> dict[str, str]:
    """Request one index's publication for one calendar month: `{index_code, start_date,
    end_date}`.

    Two measurements shape this, both from a live probe on 2026-08-09.

    **One index per request, because the endpoint gives nothing else.**
    `index_code="000300.SH,000905.SH"` returns **zero** rows -- not the union, and not an error.
    Comma-joining would therefore turn a two-index request into an empty `no_data` batch that
    reads as "the publisher has not published this month". `_trade_cal_params` refuses more than
    one subject for the same reason; unlike that one, this descriptor has no default to fall
    back to, because a bare `index_weight` request is not "the market" and there is nothing to
    measure a default against. A missing subject is refused too.

    **One calendar month per request, because that is exactly one publication.** The endpoint
    publishes on the last open session of each month: 633 publications across the three indices
    of `V2-P1-009` fall on 633 distinct months, no month inside an index's life carries none and
    no month carries two, and all 633 are the last open SSE session of their month. So a month
    window maps one-to-one onto a publication without the caller having to derive the month-end
    session from a calendar first. It is also comfortably inside the 7,000-row cap: the largest
    publication is 1,000 rows.

    The month is `as_of`'s month *in Asia/Shanghai*: 2024-12-31 17:00Z is already January in
    Shanghai, and asking UTC would fetch the wrong month for every late-evening request on a
    month boundary. `_trade_cal_params` and `_namechange_params` take the same care.
    """
    if len(request.subjects) != 1:
        raise ProviderFailure(
            provider_id=_PROVIDER_ID,
            category="configuration",
            message=(
                f"{INDEX_WEIGHT_DATASET} serves one index per request and the endpoint returns "
                f"zero rows for a comma-joined index_code; got {list(request.subjects)}"
            ),
            retryable=False,
        )
    local_date = request.as_of.astimezone(_CHINA_TZ).date()
    first = local_date.replace(day=1)
    # The 28th of any month plus four days always lands in the next month; taking that month's
    # first day back one day is the current month's last, without a table of month lengths and
    # without a leap-year branch.
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return {
        "index_code": request.subjects[0],
        "start_date": f"{first:%Y%m%d}",
        "end_date": f"{last:%Y%m%d}",
    }


def _financial_statement_params(request: ProviderRequest) -> dict[str, str]:
    """Request one security's filings for one calendar year: `{ts_code, start_date, end_date}`.

    Three measurements shape this, all from a live probe on 2026-08-09.

    **`ts_code` is required, and a comma-joined list is worse than an error.** A request without
    it fails with `code=50101, 必填参数, ts_code` on all four endpoints -- there is no
    cross-section fetch here, unlike every other dataset in this table. Worse,
    `ts_code="000001.SZ,600000.SH"` returns **zero rows with `code=0` and `has_more=False`** on
    all three of `income`, `balancesheet` and `cashflow` -- the three this builder serves --
    which is indistinguishable from "this security has never filed"; `fina_indicator` is the
    one endpoint where the same request *does* return both securities' rows (4 / 6 / 5 rows for
    `000001.SZ` alone in 2024 against 0 / 0 / 0 for the pair, and 5 against 9 on
    `fina_indicator`). So one subject per request, refused rather than defaulted, exactly as
    `_index_weight_params` refuses a comma-joined `index_code`.

    **The window is what keeps a 100-row cap unreachable.** `balancesheet` and `fina_indicator`
    cap at exactly 100 rows per response (`limit=500` and `limit=5000` return the same 100 with
    `has_more=True`), and `000001.SZ` alone has 162 `balancesheet` rows and 232
    `fina_indicator` rows -- one security's history does not fit in one response. Over the
    53-security sample, paged to exhaustion, the largest single (security, year) window is
    **15 rows** for `income`, `balancesheet` and `cashflow` (`301109.SZ` 2022, `300383.SZ`
    2014), with a p99 of 11 and a median of 4 to 6; for `fina_indicator` it is **8**, which is
    also its structural maximum today -- four fiscal periods times the largest multiplicity the
    probe found. So the window sits 6.7x under the tightest measured cap at its worst, and
    `_check_response_completeness` refuses at the cap rather than storing a suffix if a future
    year ever climbs.

    `offset` does work on these endpoints and is deliberately not used, for
    `_index_member_all_params`' reason: the descriptor's guarantee is about one response, a
    paging loop's is about the caller.

    ## Why `fina_indicator` needs its own builder

    On `income`, `balancesheet` and `cashflow`, `start_date`/`end_date` filter **`ann_date`**:
    `000001.SZ` with `20240101..20241231` returns the four filings announced in 2024, one of
    which is the *2023* annual. On `fina_indicator` the same parameters filter **`end_date`**:
    the same window returns the 2024 periods, including the 2024 annual announced 2025-03-15,
    and excludes the 2023 annual announced 2024-03-15. Nothing in the request says which, so the
    asymmetry is written down here and split into two builders -- see
    `_financial_indicator_params` for the completeness hole a shared one leaves.

    A statement window is exactly one **availability** year, so one request is one partition,
    which is `_namechange_params`' property.

    The year is `as_of`'s year *in Asia/Shanghai*: 2024-12-31 17:00Z is already 2025 in
    Shanghai. `_trade_cal_params`, `_namechange_params` and `_index_weight_params` take the same
    care for the same reason.
    """
    year = _require_one_security_year(request)
    return {
        "ts_code": request.subjects[0],
        "start_date": f"{year}0101",
        "end_date": f"{year}1231",
    }


def _financial_indicator_params(request: ProviderRequest) -> dict[str, str]:
    """Request one security's filings for one **report-period** year: `{ts_code, start_date,
    end_date}`, with the year named in `subjects` rather than taken from `as_of`.

    `_financial_statement_params`' shape with one deliberate difference, and the difference
    closes a hole rather than adding an option.

    This endpoint's window filters `end_date`, the fiscal period, while `as_of` bounds
    *availability* -- and a period's report is announced after the period ends. Deriving the
    window from `as_of`'s year makes the two collide: the only request that returns
    `000001.SZ`'s 2018 annual indicators (`end_date=20181231`) is the one naming period-year
    2018, and `_decode_panel_rows` drops that row for every `as_of` inside 2018 because it was
    announced 2019-03-07. A full backfill would silently hold three quarters of every year and
    **no annual report at all** -- the one filing a value factor cannot do without. Widening the
    window to `[Y-1, Y]` shortens the hole rather than closing it: `001278.SZ`'s 2018 annual was
    announced 2022-01-06, three years and one week later.

    So the period year is a **second subject**, refused rather than defaulted, exactly as
    `_index_member_all_params` refuses to default `is_new` and `_index_classify_params` refuses
    to default `src`. `as_of` then does only its own job. The stored subject is still the
    security -- `subject_field` reads `ts_code` off the row -- so the extra request subject
    never reaches a partition, which is the arrangement `index_member_all` already has.

    One period year stays far inside the 100-row cap: measured over the 53-security sample
    paged to exhaustion, the largest single (security, period-year) window is **8** rows, four
    fiscal periods at the largest multiplicity the probe found. `_check_response_completeness`
    refuses at the cap rather than storing a suffix if that ever climbs.
    """
    if len(request.subjects) != 2:
        raise ProviderFailure(
            provider_id=_PROVIDER_ID,
            category="configuration",
            message=(
                f"{request.dataset} needs exactly two subjects, a ts_code and a four-digit "
                "report-period year: its start_date/end_date filter end_date rather than "
                "ann_date, so a window taken from as_of would make every annual report "
                f"unreachable; got {list(request.subjects)}"
            ),
            retryable=False,
        )
    security, period_year = request.subjects
    if len(period_year) != 4 or not period_year.isdigit():
        raise ProviderFailure(
            provider_id=_PROVIDER_ID,
            category="configuration",
            message=(
                f"{request.dataset}'s second subject must be a four-digit report-period year; "
                f"got {period_year!r}"
            ),
            retryable=False,
        )
    return {
        "ts_code": security,
        "start_date": f"{period_year}0101",
        "end_date": f"{period_year}1231",
    }


def _require_one_security_year(request: ProviderRequest) -> int:
    """The window year, after refusing a request that does not name exactly one security."""
    if len(request.subjects) != 1:
        raise ProviderFailure(
            provider_id=_PROVIDER_ID,
            category="configuration",
            message=(
                f"{request.dataset} requires exactly one ts_code per request: the endpoint "
                "rejects a request without one and answers a comma-joined list with zero rows "
                f"rather than an error; got {list(request.subjects)}"
            ),
            retryable=False,
        )
    return request.as_of.astimezone(_CHINA_TZ).year


TUSHARE_FINANCIAL_ROW_CAP: Final[int] = 100
"""Rows per response for `balancesheet` and `fina_indicator`, measured on 2026-08-09.

**The lowest cap in this table by a factor of thirty**, against `index_member_all`'s 3,000.
`balancesheet(ts_code=000001.SZ)` returns exactly 100 rows with `has_more=True`, and
`limit=101`, `limit=500` and `limit=5000` all return the same 100; `fina_indicator` behaves
identically. `limit` is not ignored, it only narrows. Restricting `fields` does not raise it
either -- `balancesheet` with six requested columns and `limit=5000` still returns 100 rows --
so it is a row cap and not a cell or payload budget.

The cap drops the **oldest** rows: the capped `balancesheet` response starts at
`end_date=20091231` while `offset=100` returns 62 more reaching back to 19891231.

`income` and `cashflow` are **not** given this constant, and the reason is that they were
measured *not* to share it: `income(ts_code=000001.SZ, limit=5000)` returns **127** rows with
`has_more=False` and `cashflow` returns 90, and 000002.SZ and 600000.SH behave the same way. So
their cap is above 127 and is unreachable from outside -- a security cannot file more reports
than it has quarters -- which is `index_classify`'s situation exactly. They declare no cap and
demand the truncation flag instead.
"""


CURRENT_INDUSTRY_MEMBERSHIP: Final[str] = "Y"
SUPERSEDED_INDUSTRY_MEMBERSHIP: Final[str] = "N"
"""The two values `index_member_all`'s `is_new` filter accepts, and the reason it is not
optional.

Measured on 2026-08-09. The endpoint's **default is a filter, not the absence of one**: a bare
request returns 3,000 rows with `has_more=True` and `offset=3000` returns the remaining 2,889,
and all 5,889 of them are `is_new='Y'` with an empty `out_date` -- one row per security, and
exactly the 5,889 the 31 `l1_code` slices add up to. The 2,004 superseded assignments are
reachable only by asking for `is_new='N'` explicitly, and there is no request that returns both:
`is_new=''` behaves like the bare request, `is_new='Y,N'` and `is_new='A'` return zero rows, and
`src` is ignored (`src='SW2021'` and `src='SW2014'` both return zero).

So a fetch that leaves the parameter out stores a *current snapshot* that is indistinguishable
from a complete history -- no truncation flag, no short count, one tidy row per security. That is
why `_index_member_all_params` refuses to default it.
"""

_INDUSTRY_MEMBERSHIP_STATES: Final[tuple[str, ...]] = (
    CURRENT_INDUSTRY_MEMBERSHIP,
    SUPERSEDED_INDUSTRY_MEMBERSHIP,
)


def _index_classify_params(request: ProviderRequest) -> dict[str, str]:
    """Request one taxonomy vintage's whole tree: `{src}`.

    **The vintage is refused rather than defaulted**, which is the one thing this builder is
    really for. Measured: the bare request, `src=''` and any `level` without a `src` all answer
    **SW2014** (359 rows: 28 L1 / 104 L2 / 227 L3), while every `index_member_all` row is
    labelled with SW2021 nodes. A default would therefore build one vintage's tree beside the
    other's memberships, and the only symptom would be membership rows whose `l1_code` the tree
    does not carry -- which this dataset already produces for 25 rows for an unrelated reason,
    so the symptom is not even distinctive.

    `level` is deliberately **not** sent: `src` alone returns all three levels in one response
    (511 rows for SW2021, `has_more=False`), and the parent chain is only checkable when the
    whole tree is in hand.

    A vintage with no measured effective date is refused too. `src='SW'`, `'CI'` and `'SW2014L1'`
    return zero rows rather than an error, so an unrecognised value would otherwise become an
    empty partition reading as "this taxonomy has no industries"; and a vintage whose birthday is
    unmeasured has no honest `available_time` in the first place -- see
    `domain/industry_classification.py::INDUSTRY_TAXONOMY_EFFECTIVE_FROM`.
    """
    if len(request.subjects) != 1:
        raise ProviderFailure(
            provider_id=_PROVIDER_ID,
            category="configuration",
            message=(
                f"{INDUSTRY_TREE_DATASET} serves one taxonomy vintage per request and every "
                f"request names its taxonomy vintage; got {list(request.subjects)}. The "
                "endpoint's own default is SW2014 while every index_member_all row is SW2021, "
                "so an unstated vintage silently builds the wrong tree"
            ),
            retryable=False,
        )
    taxonomy = request.subjects[0]
    if taxonomy not in INDUSTRY_TAXONOMY_EFFECTIVE_FROM:
        raise ProviderFailure(
            provider_id=_PROVIDER_ID,
            category="configuration",
            message=(
                f"{INDUSTRY_TREE_DATASET} vintage {taxonomy!r} has no measured effective date "
                f"(known: {sorted(INDUSTRY_TAXONOMY_EFFECTIVE_FROM)}); an unrecognised src "
                "returns zero rows rather than an error, and a vintage with no birthday has no "
                "honest availability instant"
            ),
            retryable=False,
        )
    return {"src": taxonomy}


def _index_member_all_params(request: ProviderRequest) -> dict[str, str]:
    """Request one L1 slice of one membership state: `{l1_code, is_new}`.

    Two subjects, in that order, and both are mandatory.

    **The `l1_code` slice** is what keeps a request under the 3,000-row cap -- the lowest in this
    table. The whole corpus is 7,893 rows and the largest slice is `801890.SI` 机械设备 at 625
    current plus 229 superseded, so 31 codes x 2 states is 62 requests with an order of magnitude
    of headroom each. `offset` paging is available and sound here (the two default pages are
    3,000 + 2,889 = 5,889 rows with no overlap, exactly what the 31 slices add up to) and is
    still not used: `ProviderRequest` carries no offset, and a paging scheme is a request shape
    whose completeness depends on the caller looping correctly rather than on one response's own
    flag.

    **The `is_new` state** is the refusal this dataset exists for; see
    `CURRENT_INDUSTRY_MEMBERSHIP`. Leaving it out does not widen the answer, it silently narrows
    it to the current snapshot.
    """
    if len(request.subjects) != 2:
        raise ProviderFailure(
            provider_id=_PROVIDER_ID,
            category="configuration",
            message=(
                f"{INDUSTRY_MEMBERSHIP_DATASET} serves one l1_code slice of one membership state "
                f"per request and needs both, as (l1_code, is_new); got "
                f"{list(request.subjects)}. Omitting the state does not widen the answer -- the "
                "endpoint's default hides the 2,004 superseded assignments and returns only the "
                "5,889 current ones, with no flag and no short count to notice it by"
            ),
            retryable=False,
        )
    level_one, state = request.subjects
    if state not in _INDUSTRY_MEMBERSHIP_STATES:
        raise ProviderFailure(
            provider_id=_PROVIDER_ID,
            category="configuration",
            message=(
                f"{INDUSTRY_MEMBERSHIP_DATASET}'s membership state must be "
                f"{CURRENT_INDUSTRY_MEMBERSHIP!r} or {SUPERSEDED_INDUSTRY_MEMBERSHIP!r}; got "
                f"{state!r}, and the endpoint answers an unrecognised value with zero rows "
                "rather than an error"
            ),
            retryable=False,
        )
    return {"l1_code": level_one, "is_new": state}


def _industry_tree_panel_rows(row: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Give a tree row the one date it has: the day its own taxonomy came into force.

    `index_classify` carries **no date column of any kind** -- the response is
    `index_code / industry_name / level / industry_code / is_pub / parent_code / src` and nothing
    else -- so a descriptor needs a synthesised one, the way `stock_basic` needs
    `lifecycle_date`. The vintage's effective date is the honest choice and not a placeholder: a
    tree node's whole content is a statement of the classification it belongs to, and that
    statement came into being when the classification did.

    The value is written in Tushare's own `YYYYMMDD` shape so that everything downstream --
    `_parse_tushare_date`, the partition year, `_calendar_date_text` -- reads it through the same
    parse as a real response column.
    """
    taxonomy = row["src"]
    effective_from = INDUSTRY_TAXONOMY_EFFECTIVE_FROM.get(taxonomy)
    if effective_from is None:
        raise ValueError(
            f"{INDUSTRY_TREE_DATASET} returned a {taxonomy!r} node and that vintage has no "
            f"measured effective date (known: {sorted(INDUSTRY_TAXONOMY_EFFECTIVE_FROM)})"
        )
    return ({**row, INDUSTRY_TAXONOMY_DATE_COLUMN: f"{effective_from:%Y%m%d}"},)


def _published_industry_flag(value: object) -> object:
    """Parse `is_pub` into a real `bool`, keeping a genuine null as `None`.

    `_open_flag`'s rule and `_open_flag`'s reason -- `bool("0")` is `True`, so any coercion turns
    a schema change into a wrong answer about which industries carry a published index. The null
    branch is not defence: **all 227 SW2014 L3 rows carry `None`**, while SW2021's carry `'1'` or
    `'0'` (10 of its 134 L2 nodes and 87 of its 346 L3 nodes are `'0'`). Folding that null into
    `False` would assert that SW2014 publishes no third-level index at all.
    """
    if value is None or value == "":
        return None
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return value == 1
    if type(value) is str and value in _OPEN_FLAGS:
        return _OPEN_FLAGS[value]
    raise ValueError(f"must be 0, 1 or absent, got {type(value).__name__} {value!r}")


def _optional_industry_date_text(value: object) -> object:
    """Parse `out_date` into ISO text, keeping the open interval's null as `None`.

    `_optional_calendar_date_text`'s shape minus its blank-string branch, and the difference is
    the point. The null is load-bearing rather than sparse: all 5,889 `is_new='Y'` rows have an
    empty `out_date` and all 2,004 `is_new='N'` rows have one, with no exceptions either way, so
    a null here *is* "this assignment is still in force" and a blank string coerced to a date
    would close an open interval on some arbitrary day.

    Tushare's own `""` never reaches this: `_industry_membership_panel_rows` reads `out_date`
    before anything else does, to decide whether the row has a second half at all, and writes
    `None` on the half that has no end. So the blank string is normalised where it is *decided
    on* and this parser refuses one instead of quietly accepting it -- a `""` arriving here means
    the split did not run, which is a wiring bug and not an open interval.
    """
    if value is None:
        return None
    return _calendar_date_text(value)


_INDUSTRY_EVENT_DATE_FIELD: Final[str] = "industry_event_date"
"""The instant one split membership row is dated at; synthesised, never stored.

Provider-private rather than a `domain` constant because it is not projected: the panel columns
are `industry_from`/`industry_through` and this is only the descriptor's `date_field`, which
decides the row's `event_time` and therefore its partition year. `stock_basic`'s
`lifecycle_date` is the same idea and *is* stored, because a lifecycle row's date is its whole
content; here the two interval ends are already columns.
"""


def _industry_membership_panel_rows(row: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Split one membership row into the events it carries: an assignment, and maybe its end.

    `_stock_lifecycle_panel_rows`' argument, on the dataset that has the same shape. A response
    row states *both* ends of one closed interval, and a panel row carries one `available_time`
    for everything in it, so a single row is a dilemma:

    - Date it at `in_date` and a 2024 termination is readable by a 2019 reader -- a look-ahead.
    - Date it at `out_date` -- what this descriptor did until the review of `V2-P1-010` -- and
      the whole interval is unreadable until it closes. That is fail-closed, but the cost was
      measured wrongly and is far larger than a row-level filter suggests: the readiness gate in
      `panel/catalog.py` compares a **partition's** `max_available_time`, so one row that closed
      in 2024 pushed its entire `in_date` year past every earlier `as_of`. Fed the real 7,893-row
      corpus, 29 of the 38 requestable year partitions were blocked outright at `as_of`
      2023-06-30 and the 9 that read held 118 securities between them -- no usable cross section
      at any historical `as_of` before 2026-07-31, which is the one thing this dataset exists to
      supply.

    So the two facts get one row each. The **opening** row carries the assignment with an empty
    `industry_through` and is dated at `in_date`: that is what was knowable while the assignment
    ran, and it is true then. The **closing** row carries the same assignment with its end and is
    dated at `out_date`. `industry_histories_from_panel_rows` folds the pair back into one
    assignment whenever both are read, so nothing downstream sees two.

    **What this costs, named rather than implied.** The two rows land in different partitions
    whenever the interval crosses a new year, so a read that names the opening year and not the
    closing one sees an interval that never ends -- fail-open, where the un-split row was
    fail-closed. That is why `SecurityIndustryHistory` carries `years_read` and refuses a day
    after the last year a read covered; see
    `KNOWN_INDUSTRY_LIMITATIONS.a_partial_year_read_cannot_see_an_interval_close`.
    """
    opened = {**row, "out_date": None, _INDUSTRY_EVENT_DATE_FIELD: row["in_date"]}
    closed_on = row["out_date"]
    if closed_on is None or closed_on == "":
        return (opened,)
    return (opened, {**row, _INDUSTRY_EVENT_DATE_FIELD: closed_on})


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


def _finite_number(value: object) -> object:
    """Parse a numeric cell that may legitimately be zero or negative into a ``float``.

    `pct_chg` is negative on roughly half the market every session, and `vol`/`amount` are
    counts rather than prices, so this is the parse for everything `_positive_price` is too
    strict for. An ``int`` is widened for the reason `_adjustment_factor` widens one -- JSON has
    one number type and ``PanelColumn`` refuses an ``int`` in a ``float`` column -- and
    ``type(value) is int`` refuses ``bool`` along the way, because ``type(True)`` is ``bool``.
    """
    if (type(value) is float or type(value) is int) and isfinite(value):
        return float(value)
    raise ValueError(f"must be a finite number, got {type(value).__name__} {value!r}")


def _positive_price(value: object) -> object:
    """Parse a price cell into a finite **positive** ``float``.

    Five real sessions spanning 2023-01-03..2026-08-07 (24,188 bars) carried no null and no
    non-positive value in ``open``/``high``/``low``/``close``/``pre_close``, so a zero or a
    ``None`` there is a fault rather than sparse data -- and ``pre_close`` divides into every
    published return, so a zero would surface as a ``ZeroDivisionError`` several layers from
    the row that caused it.
    """
    if (type(value) is float or type(value) is int) and isfinite(value) and value > 0:
        return float(value)
    raise ValueError(f"must be a finite positive number, got {type(value).__name__} {value!r}")


def _lower_limit_price(value: object) -> object:
    """Parse a published ``down_limit`` into a finite **non-negative** ``float``.

    `_positive_price` cannot be reused here, and the difference is one published value: **the
    Beijing board encodes "no lower bound" as ``down_limit`` of exactly ``0.0``**, against an
    ``up_limit`` of ``99999.99``. It is not sparse data and it is not a fault -- it is one of
    six limit-free encodings (see `domain/price_limits.py`), and it is systematic: every
    ``.BJ`` security's first trading session carries it, from the board's first registration-
    system listing on 2022-02-28 (``920857.BJ``) onward, as does the first session of a
    delisting arrangement (``920680.BJ``, 2025-12-11, between a 12.37/6.67 band and a
    3.57/1.93 one). Verified on the whole-market responses for 2022-02-28, 2023-01-19,
    2024-02-02, 2025-05-13, 2026-03-09 and 2026-07-27: each carries exactly one
    ``down_limit == 0.0`` row and it is a ``.BJ`` listing.

    So refusing a zero here refuses **235 whole sessions** -- the distinct ``.BJ``
    ``list_date`` values at or after 2022-02-28 (64 in 2022, 75 in 2023, 23 in 2024, 26 in
    2025, 47 in 2026) -- and `panel_ingest.write_price_limits` needs every open session of a
    year at once, so it refuses the whole partition: no `stk_limit` year since 2021 could be
    stored at all.

    A **negative** lower limit is still refused, because no price floor is below zero and the
    band check downstream (`down > up`) would not catch a small negative against a real
    upper bound.
    """
    if (type(value) is float or type(value) is int) and isfinite(value) and value >= 0:
        return float(value)
    raise ValueError(f"must be a finite non-negative number, got {type(value).__name__} {value!r}")


def _constituent_weight(value: object) -> object:
    """Parse a published index weight into a real, usable `float`.

    The published unit is a **percentage** -- 5.19 means 5.19% and a publication's column sums
    to about 100 -- so the accepted range is `(0, 100]`. Four refusals, each of which would
    otherwise be silent:

    - **Zero.** 336,298 constituent rows across 633 publications carry a weight between 0.007
      and 7.745 and not one zero, so a zero is a fault -- and the dangerous kind: it leaves the
      security in the membership while removing it from every weighted sum, which is a
      difference no downstream check can see.
    - **Negative and non-finite.** Neither is a share of anything.
    - **Over 100.** No constituent is worth more than the whole index.
    - **`bool`.** `isinstance(True, int)` is `True`, so any check written against the numeric
      tower admits it and it lands as a 1% weight. `type(value) is int` refuses it, which is the
      same rule `_finite_number` applies for the same reason.

    An `int` is accepted and widened, for `_adjustment_factor`'s reason: JSON has one number
    type, so a weight of exactly 1 can arrive as `1`, and `PanelColumn` refuses an `int` in a
    `float` column.
    """
    if type(value) is float or type(value) is int:
        number = float(value)
        if isfinite(number) and 0.0 < number <= TOTAL_PUBLISHED_WEIGHT:
            return number
    raise ValueError(
        f"{INDEX_WEIGHT_COLUMN} must be a finite number in (0, {TOTAL_PUBLISHED_WEIGHT}], got "
        f"{type(value).__name__} {value!r}"
    )


def _named_parser(column: str, parse: Callable[[object], object]) -> Callable[[object], object]:
    """Wrap a parse so its failure names the column it was applied to.

    The price and valuation projections run the same two or three rules over 26 columns
    between them, and a bare "must be a finite number" leaves a reader of a 5,338-row cross
    section with no way to tell which one. Naming the column at the point of failure is what
    `_adjustment_factor` gets for free by having exactly one.
    """

    def _parse(value: object) -> object:
        try:
            return parse(value)
        except ValueError as error:
            raise ValueError(f"{column} {error}") from error

    return _parse


def _optional_number(value: object) -> object:
    """Same as `_finite_number`, but a missing value stays missing.

    For the eight `daily_basic` ratios that are genuinely absent for part of the market: 1,102
    of 2024-06-28's 5,338 names had no `pe`, 1,840 no `dv_ttm`. See
    `domain/daily_prices.py::DAILY_BASIC_NULLABLE_COLUMNS`.
    """
    if value is None:
        return None
    return _finite_number(value)


def _suspension_type(value: object) -> object:
    """Pass an `S`/`R` through, refusing anything else.

    `_lifecycle_event_name`'s shape and its reason. The value domain was measured rather than
    assumed, and exhaustively rather than by sampling: 537,671 rows spanning 2007-01..2026-12,
    pulled in windows narrow enough that none was truncated, carry `S` or `R` and nothing else.
    See `domain/price_limits.py::HALT_TYPE` for how that pull was assembled. A third value is a
    schema change, and the two things this column decides -- whether the security traded, and
    whether an absent bar is explained -- both invert on it, so guessing is worse than failing.
    """
    if value not in (HALT_TYPE, RESUMPTION_TYPE):
        raise ValueError(
            f"{SUSPENSION_TYPE_COLUMN} must be {HALT_TYPE!r} or {RESUMPTION_TYPE!r}, got {value!r}"
        )
    return value


def _optional_required_text(value: object) -> object:
    """A nullable text cell: `None` stays `None`, anything present must be real text.

    `""` is refused rather than folded into `None`, unlike `_optional_calendar_date_text`'s
    treatment of an empty `pretrade_date`. The two columns differ in what an empty string would
    *mean*: a missing `pretrade_date` is simply unknown, whereas a null `suspend_timing` is the
    load-bearing signal that a halt covered the whole session (see
    `domain/price_limits.py::TradingState`), so a blank one would silently become a whole-day
    halt on a row where the upstream had in fact populated the column.
    """
    if value is None:
        return None
    return _required_text(value)


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

TUSHARE_STK_LIMIT_ROW_CAP: Final[int] = 7800
"""Rows per response for `stk_limit`, measured on 2026-08-09 -- and the tightest margin here.

A single session fits with `has_more=False` (7,733 rows on 2026-08-07). A two-session window,
`start_date=20260701 / end_date=20260807`, returns exactly **7,800** rows with `has_more=True`,
and `limit=8000`, `limit=10000` and `limit=12000` all return the same 7,800. So the ceiling is
this endpoint's own and not the request's.

**The headroom is 67 rows and it is shrinking fast.** The whole-market cross section was 6,867
on 2024-06-28, 7,216 on 2025-08-01 and 7,733 on 2026-08-07 -- +349 then +517 per year, against
`daily`'s +41/+88/+77 on a 465-row margin. This dataset covers funds and B shares as well as
stocks (see `domain/price_limits.py`), which is why it is both larger and faster-growing than
the price panel. `_check_response_completeness` refuses at the cap rather than storing a short
session, and `ProviderRequest.subjects` splits the session when it goes; this constant is a
bound to watch, not a schedule.
"""

TUSHARE_SUSPEND_ROW_CAP: Final[int] = 5000
"""Rows per response for `suspend_d`, measured on 2026-08-09.

`start_date=20150601 / end_date=20150831` returns exactly 5,000 rows with `has_more=True`, and
`limit=6000` / `8000` / `10000` / `12000` do not raise it. Unlike `stk_limit` this one has room:
one session of the whole market is 28 rows on an ordinary day and 1,466 on the worst measured
one (2015-07-09, the week a large part of the market halted at once), or 29% of the cap.
"""


TUSHARE_INDEX_WEIGHT_ROW_CAP: Final[int] = 7000
"""Rows per response for `index_weight`, measured on 2026-08-09. No `limit` raises it.

`index_weight(000852.SH, start_date=20230101, end_date=20231231)` should hold 12,000 rows
(12 monthly publications of 1,000 constituents) and returns exactly **7,000** with
`has_more=True`. Widening the window does not change it: `20200101..20231231` returns the same
7,000. Neither does raising `limit`: `8000`, `10000`, `12000` and `20000` all return 7,000.

`limit` is not ignored, it only narrows -- re-measured for the task-32 review because the first
pass got this wrong: on that same window `limit=4000` returns 4,000, `limit=5000` returns
5,000, `limit=6999` returns 6,999, and the string `'5000'` returns 5,000, every one of them
with `has_more=True`. So 7,000 is the server's own ceiling rather than a default this provider
could argue up. Nothing here sends `limit` (see `_index_weight_params`), so the correction
changes no behaviour -- it changes a stated measurement that was false.

The cap drops the **oldest** rows and can split a publication rather than only dropping whole
ones: `index_weight(000300.SH, 20100101..20231231)` returns 7,000 rows over 24 dates whose
oldest, 2022-01-28, carries 100 of its 300 names.

**For the three indices in `INDEX_WEIGHT_INDEX_CODES` the headroom is the widest in this table
and it is not on the market's clock.** One publication is 300, 500 or 1,000 rows, so a month
window uses at most a seventh of the cap, and what would have to grow for that to bind is an
index's *constituent count* -- set by the index's own definition, and unchanged for 000905.SH
across all 235 of its publications and for 000852.SH across all 142. `stk_limit`'s 67 spare
rows and `daily`'s 465 are whole-market cross sections that grow with every listing; those
three are not.

That is a statement about those three indices and **not** about the descriptor, which takes
whatever `index_code` a caller passes. A whole-market index is a whole-market cross section and
is squarely on the market's clock: measured on the same day, one month window
(`20260701..20260731`) returns 5,126 rows for `000985.CSI` 中证全指 and 5,175 for `000902.CSI`
中证流通 -- 73% of the cap for a single publication, growing with every listing -- against 2,000
for `932000.CSI` and 800 for `000906.SH`. The flag is the guard that survives either way.
"""


TUSHARE_INDUSTRY_MEMBER_ROW_CAP: Final[int] = 3000
"""Rows per response for `index_member_all`, measured on 2026-08-09 -- **the lowest cap here**.

A bare request returns exactly 3,000 rows with `has_more=True`, and `limit=3001`, `limit=5000`
and `limit=10000` all return the same 3,000. `limit` is not ignored, it only narrows:
`limit=100` returns 100 rows, also with `has_more=True`. So 3,000 is the server's own ceiling
and not a default this provider could argue up -- the same shape `index_weight`'s 7,000 has, and
lower than every other endpoint in this table (`suspend_d` 5,000, `daily`/`adj_factor`/
`daily_basic` 6,000, `index_weight` 7,000, `stk_limit` 7,800, `namechange` 10,000).

`offset` **does** work on this endpoint, unlike the pagination `namechange` was measured to
break on: `offset=3000` returns the remaining 2,889 rows with `has_more=False` and `offset=5000`
returns 889, and 3,000 + 2,889 = 5,889 is exactly what the 31 `l1_code` slices add up to, with
5,889 distinct `ts_code` and no duplicate. It is still not used -- see
`_index_member_all_params` -- because the descriptor's guarantee is about one response and a
paging loop's is about the caller.

**The headroom is wide and is not on the market's clock in the same way `stk_limit`'s is.** The
largest current slice is 801890.SI 机械设备 at 625 rows and the largest superseded one is the
same L1 at 229. What grows is the *superseded* half, and it grows in steps rather than
continuously: it stood at 439 rows at the end of 2015, 801 at the end of 2020 and 2,004 today,
with 587 of those added by the 2021 taxonomy revision alone and 265 by the 2022 annual review.
Split across 31 L1 codes that is a bound to watch rather than a schedule.
"""


def _taxonomy_backfill_timeline(
    row: dict[str, Any], date_field: str, ingested_at: datetime
) -> Timeline:
    """`index_member_all`'s clock: the split row's own event, floored at the taxonomy's birthday.

    `event_time` is `industry_event_date`'s midnight in Asia/Shanghai -- `in_date` on the opening
    half of a split row and `out_date` on the closing half, which is exactly why the row is split
    (`_industry_membership_panel_rows`). `available_time` is the **later** of two instants:

    - **The row's own event.** The floor. Within a vintage's own era this is the same bound
      `stock_basic`'s lifecycle rows get and carries the same residue: the annual review is
      published before it takes effect and nothing in this endpoint says how far before.
    - **The taxonomy's effective date**, 2021-12-13 for SW2021. This is the bound `V2-P1-010`
      exists for. Every one of the 7,893 rows is labelled with an SW2021 node and the earliest
      `in_date` is 1984-05-09, so dating them at `in_date` alone would present a 2021
      classification as 1984 contemporaneous fact. See
      `KNOWN_INDUSTRY_LIMITATIONS.every_pre_2021_answer_is_a_backfill` for how far the two
      endpoints disagree over the window this bound covers, and for why neither of them can be
      called the classification that was actually in force.

    **The bound this rule no longer takes, and why.** Until the review of `V2-P1-010` a closed
    interval was also held back to its `out_date`, on the argument that a row carrying both ends
    cannot have been knowable before the later one. The argument was right and the mechanism was
    wrong: the readiness gate in `panel/catalog.py` compares a **partition's**
    `max_available_time`, so one 2024 `out_date` blocked the whole `in_date` year and, on the
    real corpus, 29 of the 38 requestable years at `as_of` 2023-06-30 -- not a 4% row-level
    residue but a refusal of every historical cross section. Splitting the row keeps the
    look-ahead closed (a termination is still dated at the termination) without a closed interval
    ever dating its own beginning.

    `revision_time` equals `available_time`: the endpoint serves one snapshot with no revision
    instant, and every alternative invents one. Read a partition's `revised_row_count` of 0 as
    "unmeasured", not "none".

    `ingested_time` is raised to `available_time` when the latter runs ahead of the fetch, for
    `_calendar_static_timeline`'s reason and with the same bound on the consequence: the raise
    exists only so the row can be represented long enough for `_decode_panel_rows` to drop it at
    `min(as_of, ingested_at)`. The corpus's newest `out_date` is 2026-06-30 and its newest
    `in_date` 2026-07-31, both behind the probe, so this is defence rather than an observed case
    -- but a vintage published after a fetch would produce exactly it.
    """
    effective_day = _parse_tushare_date(row[date_field])
    event_time = datetime.combine(effective_day, time(0, 0), tzinfo=_CHINA_TZ)
    available_time = max(event_time, _INDUSTRY_TAXONOMY_AVAILABLE_FROM)
    return Timeline(
        event_time=event_time,
        available_time=available_time,
        ingested_time=max(ingested_at, available_time),
        revision_time=available_time,
    )


_INDUSTRY_TAXONOMY_AVAILABLE_FROM: Final[datetime] = datetime.combine(
    INDUSTRY_TAXONOMY_EFFECTIVE_FROM[INDUSTRY_MEMBERSHIP_TAXONOMY], time(0, 0), tzinfo=_CHINA_TZ
)
"""The instant SW2021 came into force, which is the floor under every membership row's
availability. One vintage rather than a per-row lookup because `index_member_all` takes no `src`
-- passing one returns zero rows -- so the whole corpus is SW2021 by construction."""


def _price_panel_column(name: str) -> TusharePanelColumn:
    """One `daily` / `daily_basic` column, with the parse its nullability and sign demand.

    Built rather than written out 26 times: the three rules (`trade_date` is a date, a price is
    positive, a ratio may be absent) are decided by the column's name and stating them per
    column would be 26 opportunities to state one of them wrongly. The column name is also the
    response field name for both datasets -- unlike `adj_factor`, which renames `trade_date` to
    `factor_date` because two datasets storing a column of that name would mean two different
    things by it. Here they mean the same thing.
    """
    if name == PRICE_DATE_COLUMN:
        return TusharePanelColumn(
            name=name, kind="string", source_field=name, parse=_calendar_date_text
        )
    if name in DAILY_PRICE_COLUMNS:
        parse = _positive_price
    elif name in DAILY_BASIC_NULLABLE_COLUMNS:
        parse = _optional_number
    else:
        parse = _finite_number
    return TusharePanelColumn(
        name=name, kind="float", source_field=name, parse=_named_parser(name, parse)
    )


def _price_limit_panel_column(name: str) -> TusharePanelColumn:
    """One `stk_limit` column: the session date, or a published limit price.

    `_price_panel_column`'s shape, kept separate rather than extended because the two datasets
    disagree about what a "price" column may hold. A `daily` price is a traded price; a
    `stk_limit` price is a *bound*, and the limit-free sentinels -- six encodings, listed in
    `domain/price_limits.py` -- are real published values meaning "this security has no limit
    today". They go through untouched: normalising them to `None` here would destroy the only
    statement that the security was unbounded, and they are classified on the read side,
    against the previous close, by `PriceLimit.is_bounded`.

    **The two sides take different parses**, which is the one place this dataset's asymmetry
    shows up in code. An `up_limit` is strictly positive; a `down_limit` may be exactly `0.0`,
    because that is how the Beijing board publishes "no lower bound". See `_lower_limit_price`
    for the 235 sessions that ride on it.
    """
    if name == PRICE_DATE_COLUMN:
        return TusharePanelColumn(
            name=name, kind="string", source_field=name, parse=_calendar_date_text
        )
    parse = _lower_limit_price if name == DOWN_LIMIT_COLUMN else _positive_price
    return TusharePanelColumn(
        name=name, kind="float", source_field=name, parse=_named_parser(name, parse)
    )


def _statement_panel_columns(dataset: str) -> tuple[TusharePanelColumn, ...]:
    """One financial-statement dataset's projection: the keys, then the projected values.

    Built rather than written out four times because the rule is the same in all four cases and
    only the column list differs -- `domain/financial_statements.py` owns that list, so the
    provider cannot drift from what the reader expects to find.

    Every value column is nullable, and that is data rather than laxity: `oper_cost` is `None`
    on all 127 of `000001.SZ`'s `income` rows because a bank has no cost of sales, and
    `total_cur_assets` / `total_cur_liab` / `money_cap` are `None` on every one of its
    `balancesheet` rows for the same reason. Tushare's field set is the union over company
    types (`comp_type` 1..4) and a null here means "this company type does not publish this
    line", which is exactly what `_optional_number` preserves and what a refusal or a zero
    would destroy.
    """
    columns: list[TusharePanelColumn] = [
        TusharePanelColumn(
            name=REPORT_PERIOD_COLUMN,
            kind="string",
            source_field="end_date",
            parse=_named_parser(REPORT_PERIOD_COLUMN, _calendar_date_text),
        ),
        TusharePanelColumn(
            name=ANNOUNCEMENT_DATE_COLUMN,
            kind="string",
            source_field=ANNOUNCEMENT_DATE_COLUMN,
            parse=_named_parser(ANNOUNCEMENT_DATE_COLUMN, _calendar_date_text),
        ),
    ]
    if dataset in DATASETS_WITH_REVISION_LABEL:
        columns.extend(
            (
                TusharePanelColumn(
                    name=FIRST_ANNOUNCEMENT_COLUMN,
                    kind="string",
                    source_field=FIRST_ANNOUNCEMENT_COLUMN,
                    parse=_named_parser(FIRST_ANNOUNCEMENT_COLUMN, _optional_calendar_date_text),
                ),
                TusharePanelColumn(
                    name=REVISION_LABEL_COLUMN,
                    kind="string",
                    source_field=REVISION_LABEL_COLUMN,
                    parse=_named_parser(REVISION_LABEL_COLUMN, _required_text),
                ),
            )
        )
    columns.extend(
        TusharePanelColumn(
            name=name,
            kind="float",
            source_field=name,
            parse=_named_parser(name, _optional_number),
        )
        for name in STATEMENT_DATA_COLUMNS[dataset]
    )
    return tuple(columns)


def _statement_descriptor(dataset: str) -> TushareDatasetDescriptor:
    """One of the four `V2-P1-011` endpoints, wired identically except where they differ.

    They differ in exactly three things and each is a measurement rather than a preference:
    which key columns exist (`fina_indicator` has neither `f_ann_date` nor `update_flag`),
    which value columns are projected, and whether a per-response row cap was reachable.

    `serves_evidence_plane` stays `True` for all four -- the first datasets since `daily` for
    which that is uncontroversial. Unlike `stock_basic`, `namechange`, `index_member_all` and
    `index_classify`, a statement row states facts that all became knowable at one instant, its
    own announcement, so `fetch()` can hand back the response row verbatim under a single
    clock and the record stays re-provable. The duplicate versions do not change that: two rows
    with equal timelines and different payloads are two honest citations of two things the
    publisher served, and `ProviderRecord.record_id` is content-derived so they do not collide.
    """
    values = STATEMENT_DATA_COLUMNS[dataset]
    has_label = dataset in DATASETS_WITH_REVISION_LABEL
    required = (
        "ts_code",
        "end_date",
        ANNOUNCEMENT_DATE_COLUMN,
        *((FIRST_ANNOUNCEMENT_COLUMN, REVISION_LABEL_COLUMN) if has_label else ()),
        *values,
    )
    return TushareDatasetDescriptor(
        dataset=dataset,
        kind=dataset,
        subject_field="ts_code",
        # The **announcement**, not the period. This column dates the row for
        # `_panel_source_uri`, orders `_decode_panel_rows`' output and picks the partition year,
        # and all three want the instant the row became knowable rather than the fiscal quarter
        # it describes -- `001278.SZ` announced its 2018 annual on 2022-01-06, three years and
        # one week after the period it reports on.
        date_field=ANNOUNCEMENT_DATE_COLUMN,
        clock=ClockStrategy.announcement,
        params_builder=(
            _financial_indicator_params
            if dataset == FINANCIAL_INDICATOR_DATASET
            else _financial_statement_params
        ),
        # Named rather than left at `""`. The defaults are 85 / 152 / 97 / 108 columns and the
        # projection reads 14 / 11 / 9 / 13 of them; asking for the rest costs payload on every
        # request and hands `_check_panel_projection` a wider surface to drift on. It also makes
        # the contract legible: this tuple is exactly what this dataset promises to store.
        response_fields=",".join(required),
        required_response_fields=required,
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        panel_columns=_statement_panel_columns(dataset),
        max_rows_per_response=(
            TUSHARE_FINANCIAL_ROW_CAP
            if dataset in (BALANCE_SHEET_DATASET, FINANCIAL_INDICATOR_DATASET)
            else None
        ),
        # Demanded on all four, which makes them the sixth through ninth descriptors to do so.
        # For `income` and `cashflow` the flag is the **only** witness -- their cap is above the
        # 127 rows a security can produce and so is unmeasurable from outside, `index_classify`'s
        # situation. For `balancesheet` and `fina_indicator` it is demanded *as well as* the cap,
        # for `adj_factor`'s reason: the cap drops the oldest rows, and a statement history
        # missing its early years is not short but silently wrong -- every year-on-year growth
        # rate computed across the boundary answers from a period that is not there.
        requires_truncation_flag=True,
    )


DAILY_BASIC_FIELD_NAMES: Final[tuple[str, ...]] = ("ts_code", *DAILY_BASIC_DATA_COLUMNS)
"""Every column `daily_basic`'s descriptor reads, which is every column it serves.

Pinned as `required_response_fields` because -- unlike `daily`, whose evidence-plane contract
predates this issue and is driven in tests by a four-column response -- nothing has ever
fetched this dataset with a narrower shape, so the schema check can be the full one.
"""

TUSHARE_DATASETS: tuple[TushareDatasetDescriptor, ...] = (
    TushareDatasetDescriptor(
        dataset=DAILY_DATASET,
        kind=DAILY_DATASET,
        subject_field="ts_code",
        date_field=PRICE_DATE_COLUMN,
        clock=ClockStrategy.daily_close,
        # One trading day of the whole market, the same builder `adj_factor` uses, and for the
        # same arithmetic: 5,338 rows on 2024-06-28 and 5,535 on 2026-08-07 against a
        # 6,000-row cap, while one security's own history is 6,000-and-truncated. `subjects`
        # is the escape route when the market outgrows the cap -- it splits the session
        # instead of losing rows.
        params_builder=_trade_date_params,
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        # Measured, and carried since `V2-P1-006`: the cap is a property of the endpoint, not
        # of the task that first stores its rows.
        max_rows_per_response=TUSHARE_PRICE_ROW_CAP,
        # Deliberately *not* demanded, unlike `adj_factor`'s. This flag governs only the case
        # where `has_more` is **absent** -- a flag that is present is checked for every
        # dataset -- and the two datasets differ in what a dropped row costs. A truncated
        # factor series is silently *wrong*: the step function answers the missing days from an
        # older level. A truncated price series is *short*: the bar is simply absent and
        # nothing interpolates it. The residue is stated rather than claimed away, and pinned
        # in `tests/contract/providers/test_tushare_daily.py`: a response that both omits the
        # flag and comes in under 6,000 rows is accepted.
        requires_truncation_flag=False,
        # `required_response_fields` stays at its default `(trade_date,)` and cannot be
        # widened here: `fetch()` is a payload-passthrough contract whose oldest test drives
        # this dataset with a four-column response, so the evidence path has to stay tolerant
        # of a narrow one. The panel path is checked instead, against the columns it actually
        # projects -- see `_check_panel_projection`.
        panel_columns=tuple(_price_panel_column(name) for name in DAILY_DATA_COLUMNS),
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
        # No measured cap, and the cap is genuinely **unmeasurable from outside**: the whole
        # `L,D` registry -- 5,878 rows on 2026-08-08 -- comes back with `has_more=False`, and
        # so does `limit=100000`, while `limit=5878` reports `has_more=True`. The flag turns
        # `True` at whatever effective limit is in force, so no probe can push past the real
        # ceiling to find it. All that is established is that it is **above 5,878**; how far
        # above is unknown, and "6,000 minus 5,878 = 122 rows of margin" would be exactly the
        # guess this comment declines to make -- the true headroom could be 122 or, if this
        # endpoint shares `namechange`'s 10,000, 4,122. Declaring a number is load-bearing in
        # both directions: too low refuses a complete registry, too high passes a truncated
        # one. So the flag is this descriptor's *only* witness, and
        # `requires_truncation_flag` below is what makes it an actual check rather than an
        # optional one.
        max_rows_per_response=None,
        requires_truncation_flag=True,
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
    TushareDatasetDescriptor(
        dataset=DAILY_BASIC_DATASET,
        kind=DAILY_BASIC_DATASET,
        subject_field="ts_code",
        date_field=PRICE_DATE_COLUMN,
        clock=ClockStrategy.daily_close,
        # The same cross-section builder `daily` and `adj_factor` use: measured at 5,338 rows
        # on 2024-06-28 and 5,535 on 2026-08-07, and the two price datasets are fetched for the
        # same session so that their `close` columns cross-check each other for free.
        params_builder=_trade_date_params,
        # `""` asks for the endpoint's defaults, which are exactly the 18 columns below --
        # measured, not assumed. `required_response_fields` then pins every one of them, which
        # `daily` cannot do; see that descriptor.
        response_fields="",
        required_response_fields=DAILY_BASIC_FIELD_NAMES,
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        # Measured on 2026-08-08: a bare `daily_basic(ts_code=000001.SZ)` returns exactly
        # 6,000 rows with `has_more=True`, the same ceiling `daily` and `adj_factor` hit.
        max_rows_per_response=TUSHARE_PRICE_ROW_CAP,
        # Not demanded, for the reason `daily`'s comment gives.
        requires_truncation_flag=False,
        panel_columns=tuple(_price_panel_column(name) for name in DAILY_BASIC_DATA_COLUMNS),
    ),
    TushareDatasetDescriptor(
        dataset=SUSPENSION_DATASET,
        kind=SUSPENSION_DATASET,
        subject_field="ts_code",
        date_field=PRICE_DATE_COLUMN,
        # A halt is knowable once the session it covers has closed, the same instant a bar is.
        # Dating it at that day's midnight would make tomorrow's halt readable this morning,
        # which is precisely the day a backtest would act on it.
        clock=ClockStrategy.daily_close,
        # One trading day of the whole market, the builder every cross-section dataset here
        # uses. Measured: 28 rows on 2024-06-28, 5 on 2026-08-07, and 1,466 on 2015-07-09 --
        # the worst session in the week a large part of the market halted at once.
        params_builder=_trade_date_params,
        # `""` asks for the endpoint's defaults, which are exactly the four columns below --
        # measured, not assumed. `required_response_fields` then pins every one of them.
        response_fields="",
        required_response_fields=("ts_code", *SUSPENSION_DATA_COLUMNS),
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        max_rows_per_response=TUSHARE_SUSPEND_ROW_CAP,
        # Deliberately **not** demanded, and the reason is the direction a lost row fails in.
        # Every consumer of this dataset uses a halt to *excuse* an absent bar --
        # `explain_unpriced` and `panel_ingest._refuse_unexplained_thin_sessions` -- so a
        # truncated response excuses fewer absences and raises more alarms, never fewer. That
        # is the fail-closed direction, which is what `daily` argued and `adj_factor` could
        # not. The cap witness is kept because it is cheap and because it is what would catch
        # a multi-session window; the residue is a response that both omits `has_more` and
        # comes in under 5,000 rows, and it is stated rather than argued away.
        requires_truncation_flag=False,
        panel_columns=(
            TusharePanelColumn(
                name=PRICE_DATE_COLUMN,
                kind="string",
                source_field=PRICE_DATE_COLUMN,
                parse=_calendar_date_text,
            ),
            TusharePanelColumn(
                name=SUSPENSION_TYPE_COLUMN,
                kind="string",
                source_field=SUSPENSION_TYPE_COLUMN,
                parse=_suspension_type,
            ),
            # Nullable on purpose: a null here is `TradingState.halted` (the whole session) and
            # a populated window is `TradingState.interrupted` (the security traded). Storing
            # it is what keeps those two apart -- 31 of 2015-07-08's 1,343 `S` rows carry a
            # window and all 31 have a bar.
            TusharePanelColumn(
                name=SUSPENSION_TIMING_COLUMN,
                kind="string",
                source_field=SUSPENSION_TIMING_COLUMN,
                parse=_optional_required_text,
            ),
        ),
    ),
    TushareDatasetDescriptor(
        dataset=PRICE_LIMIT_DATASET,
        kind=PRICE_LIMIT_DATASET,
        subject_field="ts_code",
        date_field=PRICE_DATE_COLUMN,
        # The band for session D is computed from D-1's close and published before D opens, so
        # dating it at D's close is one session *late* rather than early -- the conservative
        # direction, and the same clock every other trading-day dataset here uses. A separate
        # clock strategy would be the honest refinement and is not free: `available_time` would
        # have to name the pre-open instant, which the response does not carry.
        clock=ClockStrategy.daily_close,
        # One trading day, the same builder. Measured: 6,867 rows on 2024-06-28 and 7,733 on
        # 2026-08-07, against a 7,800-row cap -- see `TUSHARE_STK_LIMIT_ROW_CAP` for why that
        # 67-row margin is the tightest in this table. `subjects` is the escape route.
        params_builder=_trade_date_params,
        response_fields="",
        required_response_fields=("ts_code", *PRICE_LIMIT_DATA_COLUMNS),
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        max_rows_per_response=TUSHARE_STK_LIMIT_ROW_CAP,
        # Demanded, which makes this the third descriptor to do so and the first price one.
        # `daily` declined it because a dropped bar is *short* and nothing downstream
        # interpolates it. That argument does not transfer here, because this dataset does have
        # a substitute and the substitute is known to be wrong: with no published band,
        # `AShareExecutionPolicy` falls back to its board-plus-`is_st` rule, which disagrees
        # with the exchange on 159 of 5,338 names on one ordinary session (see
        # `domain/price_limits.py`). So a silently missing row here is not an absence, it is a
        # quiet substitution of a measurably wrong number -- `adj_factor`'s situation rather
        # than `daily`'s. The cross section also sits 67 rows under the cap, so the row-count
        # witness is about to stop having any margin at all.
        requires_truncation_flag=True,
        panel_columns=tuple(_price_limit_panel_column(name) for name in PRICE_LIMIT_DATA_COLUMNS),
    ),
    TushareDatasetDescriptor(
        dataset=INDEX_WEIGHT_DATASET,
        kind=INDEX_WEIGHT_DATASET,
        # The **index**, not the constituent. `PanelStore`'s key is `(dataset, year)` with no
        # index dimension, so three indices sharing a year are told apart by this column alone
        # and `panel_ingest._refuse_to_drop_stored_subjects` compares exactly it -- see
        # `domain/index_membership.py::INDEX_WEIGHT_PANEL_COLUMNS`.
        subject_field="index_code",
        date_field="trade_date",
        # The snapshot is computed from that session's close, so it cannot exist before 16:30.
        # It is the *earliest* honest instant rather than a measured one: the response carries
        # no announcement column, so if the publisher releases the file the next morning this is
        # up to one session early. Stated rather than argued away in
        # `domain/index_membership.py::KNOWN_INDEX_MEMBERSHIP_LIMITATIONS`.
        clock=ClockStrategy.daily_close,
        params_builder=_index_weight_params,
        # `""` asks for the endpoint's defaults, which are exactly the four columns below --
        # measured, not assumed. `required_response_fields` then pins every one of them.
        response_fields="",
        required_response_fields=(
            "index_code",
            INDEX_CONSTITUENT_COLUMN,
            "trade_date",
            INDEX_WEIGHT_COLUMN,
        ),
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        max_rows_per_response=TUSHARE_INDEX_WEIGHT_ROW_CAP,
        # Demanded, which makes this the fourth descriptor to do so. `daily`'s argument -- a
        # dropped row is an absence nothing interpolates -- does not transfer, because the cap
        # here can truncate a publication *in the middle*: the oldest date of a capped response
        # was measured carrying 100 of its 300 constituents. A publication missing two thirds of
        # its members is not short, it is a different index, and every membership and weighted
        # sum computed from it is silently wrong. That is `adj_factor`'s situation.
        #
        # Unlike `adj_factor`, this dataset has a second, independent witness one layer up --
        # `domain/index_membership.py::build_index_publication` refuses a publication whose
        # weights do not sum to 100 within the tolerance its own published precision implies,
        # and a third of a publication misses by ~67. The flag is still demanded rather than
        # leaned on that: the checksum catches a *split* publication and cannot see a truncation
        # that happens to land on a publication boundary, which drops whole months and leaves
        # every remaining publication summing perfectly.
        requires_truncation_flag=True,
        panel_columns=(
            TusharePanelColumn(
                name=INDEX_PUBLICATION_DATE_COLUMN,
                kind="string",
                source_field="trade_date",
                parse=_calendar_date_text,
            ),
            TusharePanelColumn(
                name=INDEX_CONSTITUENT_COLUMN,
                kind="string",
                source_field=INDEX_CONSTITUENT_COLUMN,
                parse=_required_text,
            ),
            TusharePanelColumn(
                name=INDEX_WEIGHT_COLUMN,
                kind="float",
                source_field=INDEX_WEIGHT_COLUMN,
                parse=_constituent_weight,
            ),
        ),
    ),
    TushareDatasetDescriptor(
        dataset=INDUSTRY_TREE_DATASET,
        kind=INDUSTRY_TREE_DATASET,
        # The industry node. A membership row's `l1_code`/`l2_code`/`l3_code` join against this
        # column, so it is what a reader filters on and what `_refuse_to_drop_stored_subjects`
        # compares when a vintage's tree is re-fetched.
        subject_field="index_code",
        # Synthesised by `_industry_tree_panel_rows`, not a response column: this endpoint
        # carries no date at all, and a node's one honest instant is its vintage's own.
        date_field=INDUSTRY_TAXONOMY_DATE_COLUMN,
        # Every node of a vintage came into force on one day and was knowable that day, which is
        # exactly what this clock says. Unlike `stock_basic`'s use of it, no split is needed
        # first: a tree row states one fact.
        clock=ClockStrategy.calendar_static,
        params_builder=_index_classify_params,
        # `""` asks for the endpoint's defaults, which are exactly the seven columns below --
        # measured, not assumed. `required_response_fields` then pins every one of them,
        # including `src`, which the expansion reads to date the row.
        response_fields="",
        required_response_fields=(
            "index_code",
            "industry_name",
            INDUSTRY_LEVEL_COLUMN,
            INDUSTRY_CODE_COLUMN,
            "is_pub",
            INDUSTRY_PARENT_COLUMN,
            "src",
        ),
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        panel_rows=_industry_tree_panel_rows,
        # Forced rather than chosen: `date_field` names a column the expansion synthesises, so
        # there is no verbatim response row for `fetch()` to hand back under a single clock.
        serves_evidence_plane=False,
        # No measured cap, and the cap is **unmeasurable from outside** -- `stock_basic`'s
        # situation, reproduced exactly. The 511-row SW2021 tree comes back with
        # `has_more=False`, `limit=100000` also returns 511 with `has_more=False`, and
        # `limit=511` returns 511 with `has_more=True`: the flag turns `True` at whatever
        # effective limit is in force, so no probe can push past the real ceiling to find it.
        # All that is established is that it is above 511. Declaring a number would be
        # load-bearing in both directions, so the flag below is this descriptor's only witness
        # and `requires_truncation_flag` is what makes it a check rather than an option.
        max_rows_per_response=None,
        requires_truncation_flag=True,
        panel_columns=(
            TusharePanelColumn(
                name=INDUSTRY_CODE_COLUMN,
                kind="string",
                source_field=INDUSTRY_CODE_COLUMN,
                parse=_required_text,
            ),
            TusharePanelColumn(
                name=INDUSTRY_NAME_COLUMN,
                kind="string",
                source_field=INDUSTRY_NAME_COLUMN,
                parse=_required_text,
            ),
            TusharePanelColumn(
                name=INDUSTRY_LEVEL_COLUMN,
                kind="string",
                source_field=INDUSTRY_LEVEL_COLUMN,
                parse=_required_text,
            ),
            TusharePanelColumn(
                name=INDUSTRY_PARENT_COLUMN,
                kind="string",
                source_field=INDUSTRY_PARENT_COLUMN,
                parse=_required_text,
            ),
            # Nullable on purpose, and the null is data: all 227 SW2014 L3 rows carry it.
            TusharePanelColumn(
                name=INDUSTRY_PUBLISHED_COLUMN,
                kind="boolean",
                source_field="is_pub",
                parse=_named_parser(INDUSTRY_PUBLISHED_COLUMN, _published_industry_flag),
            ),
            TusharePanelColumn(
                name=INDUSTRY_TAXONOMY_COLUMN,
                kind="string",
                source_field="src",
                parse=_required_text,
            ),
            TusharePanelColumn(
                name=INDUSTRY_TAXONOMY_DATE_COLUMN,
                kind="string",
                source_field=INDUSTRY_TAXONOMY_DATE_COLUMN,
                parse=_calendar_date_text,
            ),
        ),
    ),
    TushareDatasetDescriptor(
        dataset=INDUSTRY_MEMBERSHIP_DATASET,
        kind=INDUSTRY_MEMBERSHIP_DATASET,
        # The **security**, not the industry: a reader asks "what was this one's industry on day
        # D", so the security is what `load_industry_classification` groups by and what
        # `_refuse_to_drop_stored_subjects` must see go missing when an `l1_code` slice is
        # dropped from a re-fetch. `namechange` stores its subject the same way for the same
        # reason.
        subject_field="ts_code",
        # Synthesised by `_industry_membership_panel_rows`, not a response column: a response row
        # states both ends of one interval and they became knowable at different instants, so the
        # row is split and each half is dated at its own end.
        date_field=_INDUSTRY_EVENT_DATE_FIELD,
        clock=ClockStrategy.taxonomy_backfill,
        params_builder=_index_member_all_params,
        panel_rows=_industry_membership_panel_rows,
        # `""` asks for the endpoint's defaults, which are exactly the eleven columns below --
        # measured, not assumed. Only six are pinned and only five are projected; see below for
        # the four the projection deliberately drops.
        response_fields="",
        required_response_fields=(
            "ts_code",
            INDUSTRY_L1_COLUMN,
            INDUSTRY_L2_COLUMN,
            INDUSTRY_L3_COLUMN,
            "in_date",
            "out_date",
        ),
        source_uri_template="tushare://{dataset}/{subject}/{date}",
        # `l1_name`, `l2_name`, `l3_name` and `name` are fetched and never projected. The three
        # industry names belong to the *tree* and would be a second, drifting copy of it -- 25
        # rows carry `特钢Ⅲ` for a node the SW2021 tree does not carry at all -- and the security
        # name is the current registry's, which `stock_basic` refuses to project because
        # stamping today's `ST星源(退)` onto a 1990 row is a 34-year look-ahead. `is_new` is not
        # projected either: it is exactly `out_date is None` on all 7,893 rows, so it is fetched
        # as an independent witness and cross-checked in the tests, the treatment `namechange`
        # gives `end_date`.
        #
        # Forced rather than chosen, on top of all that: `date_field` names a column the
        # expansion synthesises, so there is no verbatim response row for `fetch()` to hand back
        # under a single clock -- which is the same admission the expansion itself is.
        serves_evidence_plane=False,
        max_rows_per_response=TUSHARE_INDUSTRY_MEMBER_ROW_CAP,
        # Demanded, which makes this the fifth descriptor to do so. `daily`'s argument -- a
        # dropped row is an absence nothing interpolates -- does not transfer. The cap drops the
        # *oldest* rows, and the oldest rows of an `l1_code` slice are the assignments that
        # opened first, so a truncated slice does not shorten one security's history: it removes
        # whole early assignments across many securities at once. What is left then looks
        # complete -- `build_security_industry_history` is happy with any non-overlapping set --
        # and every question about the missing years is answered either by a horizon error or,
        # where a later assignment survives, by an industry the security was not in yet. That is
        # `adj_factor`'s situation rather than `daily`'s, and this cap is the lowest here.
        requires_truncation_flag=True,
        panel_columns=(
            TusharePanelColumn(
                name=INDUSTRY_FROM_COLUMN,
                kind="string",
                source_field="in_date",
                parse=_calendar_date_text,
            ),
            # Nullable on purpose: a null here is "this assignment is still in force", which is
            # true of exactly the 5,889 `is_new='Y'` rows and of none of the 2,004 others.
            TusharePanelColumn(
                name=INDUSTRY_THROUGH_COLUMN,
                kind="string",
                source_field="out_date",
                parse=_optional_industry_date_text,
            ),
            TusharePanelColumn(
                name=INDUSTRY_L1_COLUMN,
                kind="string",
                source_field=INDUSTRY_L1_COLUMN,
                parse=_required_text,
            ),
            TusharePanelColumn(
                name=INDUSTRY_L2_COLUMN,
                kind="string",
                source_field=INDUSTRY_L2_COLUMN,
                parse=_required_text,
            ),
            TusharePanelColumn(
                name=INDUSTRY_L3_COLUMN,
                kind="string",
                source_field=INDUSTRY_L3_COLUMN,
                parse=_required_text,
            ),
        ),
    ),
    *(_statement_descriptor(dataset) for dataset in FINANCIAL_STATEMENT_DATASETS),
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
    """15:00 close / 16:30 availability, both Asia/Shanghai, for trading-day datasets.

    The two instants come from `domain/daily_prices.py` rather than being literals here,
    because `panel_ingest.daily_requirement` derives *which sessions a partition must hold*
    from the same 16:30. Two independent literals would let those drift, and the drift is
    silent in both directions: a later provider time makes every intraday read demand a session
    the write could not have held, an earlier one stops requiring the newest session.
    """
    trading_day = _parse_tushare_date(row[date_field])
    event_time = datetime.combine(trading_day, SESSION_CLOSE_TIME, tzinfo=_CHINA_TZ)
    available_time = datetime.combine(trading_day, DAILY_AVAILABILITY_TIME, tzinfo=_CHINA_TZ)
    return Timeline(
        event_time=event_time,
        available_time=available_time,
        ingested_time=ingested_at,
        revision_time=available_time,
    )


def _announcement_timeline(row: dict[str, Any], date_field: str, ingested_at: datetime) -> Timeline:
    """``ann_date`` sets event/available time; a **later** ``f_ann_date`` is a revision.

    ``date_field`` is unused here: announcement data always keys its PIT clocks off the
    fixed ``ann_date``/``f_ann_date`` columns that every Tushare financial-statement
    endpoint shares, regardless of which column the descriptor uses for display purposes.

    ## ``f_ann_date`` is not always the later date (``V2-P1-011``)

    Roadmap section 7 recorded ``f_ann_date >= ann_date`` with "zero violations" from a 65-row
    window (3 securities, 2022-2025), and this function read ``revision_time`` straight off
    ``f_ann_date`` on the strength of it. **Full histories violate it.** ``000001.SZ``'s
    ``income`` alone carries ``end_date=20060331`` with ``ann_date=20070426`` and
    ``f_ann_date=20060426``, and ``end_date=20051231`` with ``ann_date=20070322`` and
    ``f_ann_date=20060401``; the same 20051231 row is in its ``balancesheet`` and its
    ``cashflow``. ``Timeline.__post_init__`` refuses a ``revision_time`` before its
    ``available_time``, so every one of those rows **raised** -- a real dataset could not be
    fetched at all.

    ``max`` is the fix and it is not a widening: wherever the old assumption held, ``max`` *is*
    ``f_ann_date``, so no row that worked before changes. Where it did not hold, the two dates
    disagree about which announcement this row belongs to and there is no basis for calling the
    earlier one a revision of the later. See
    ``KNOWN_FINANCIAL_STATEMENT_LIMITATIONS.the_two_announcement_dates_are_not_ordered``.

    ## The correction still carries no instant, and that is now a decision (``V2-P1-011``)

    ``update_flag`` marks a corrected filing and this function still does not read it, because
    **both rows of a corrected pair carry the same ``ann_date``** -- and the same ``f_ann_date``
    on all but 5 of ``income``'s 633 and 5 of ``balancesheet``'s 1,244 duplicate keys, measured
    over a 53-security sample on 2026-08-09. So the instant at which the correction became
    knowable is not in the response, and the two rows produce byte-equal ``Timeline`` objects.

    That was recorded as a gap to close later. ``V2-P1-011`` looked and closed it the other way:
    the dimension is genuinely absent, so dating one row later would be inventing the instant --
    Task 32's error in a new dataset. The disambiguation lives in
    ``domain/financial_statements.py`` instead, which keeps both versions and refuses a read of
    the fields they disagree on. ``fina_indicator`` settles the question on its own: it has no
    ``update_flag`` column at all and 81.7% of its keys carry more than one row.
    ``test_announcement_clock_cannot_yet_distinguish_restatement_via_update_flag`` therefore
    still passes, and now pins a decision rather than a deficiency.

    ``ingested_time`` is raised to the announcement when the latter runs ahead of the fetch, for
    ``_calendar_static_timeline``'s reason: ``Timeline`` refuses an ``ingested_time`` before its
    ``available_time``, and the row has to be representable long enough for
    ``_decode_panel_rows`` to drop it at ``min(as_of, ingested_at)``. Defence rather than an
    observed case here -- a statement endpoint serves nothing it has not already announced --
    but ``fina_indicator``'s request window is a *report period* year rather than an
    announcement year (see ``_financial_statement_params``), so a request for the current year
    routinely names periods whose announcement has not happened.
    """
    ann_moment = datetime.combine(
        _parse_tushare_date(row["ann_date"]), time(0, 0), tzinfo=_CHINA_TZ
    )
    f_ann_date_raw = row.get("f_ann_date")
    revision_moment = (
        max(
            ann_moment,
            datetime.combine(_parse_tushare_date(f_ann_date_raw), time(0, 0), tzinfo=_CHINA_TZ),
        )
        if f_ann_date_raw
        else ann_moment
    )
    return Timeline(
        event_time=ann_moment,
        available_time=ann_moment,
        ingested_time=max(ingested_at, ann_moment),
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
    ClockStrategy.taxonomy_backfill: _taxonomy_backfill_timeline,
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


def _check_panel_projection(
    descriptor: TushareDatasetDescriptor, rows: list[dict[str, Any]], provider_id: str
) -> None:
    """Refuse a response whose rows are missing a column this descriptor projects.

    `_response_rows` checks `checked_response_fields`, which is the *evidence* plane's schema
    contract and is deliberately narrow for `daily` -- `fetch()` is a payload-passthrough whose
    oldest caller drives it with four columns. The panel projection reads nine, and without
    this a drifted response surfaced as `Tushare response could not be decoded: 'pre_close'`, a
    bare `KeyError` reason for what is a named schema change.

    Run against the **expanded** rows rather than the response's `fields`, because a descriptor
    with a `panel_rows` expansion projects columns the response does not carry:
    `stock_basic`'s `lifecycle_event` and `lifecycle_date` are synthesised by
    `_stock_lifecycle_panel_rows`. One row is enough -- every row of one response is built from
    the same `fields` list, and an expansion produces the same keys for every branch.
    """
    if not rows:
        return
    present = rows[0].keys()
    for spec in descriptor.panel_columns:
        if spec.source_field not in present:
            raise ProviderFailure(
                provider_id=provider_id,
                category="upstream",
                message=(
                    f"the panel projection for {descriptor.dataset} needs a "
                    f"{spec.source_field!r} column and the response carries "
                    f"{sorted(present)}"
                ),
                retryable=False,
            )


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


MAX_PANEL_SOURCE_URI_LENGTH: Final[int] = MAX_SOURCE_URI_LENGTH
"""`ColumnarPanelBatch`'s own limit on `source_uri`, so this module stays under it rather
than trips over it.

Bound to `domain.panel_batch.MAX_SOURCE_URI_LENGTH` rather than restated as a literal, and
the equality is additionally asserted by `tests/contract/providers/test_tushare_adj_factor.py
::test_the_producer_side_uri_bound_is_the_contracts_own_constant`. A comment saying "the two
must agree" is not a check: while this was a separate `2048`, moving it to 3,000 left the
whole suite green and quietly restored the original bug for every fetch of ~202 to ~300
subjects, whose joined URI lands between the two values and is refused by the contract
*outside* `fetch_panel`'s decode `try`."""


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
        _check_panel_projection(descriptor, items, self.metadata.provider_id)
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
