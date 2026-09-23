"""`stock_basic` and `namechange` on the descriptor table (`V2-P1-005`).

No network: every response below is the shape, and where it matters the exact rows, the live
Tushare endpoints returned while this task was implemented.

Three things this file exists to pin, none of which is visible from the descriptor table
alone:

1. **The request has to ask for what it needs.** `stock_basic`'s default field set is
   `ts_code, symbol, name, area, industry, cnspell, market, list_date, act_name,
   act_ent_type` -- **no `delist_date`** -- and its default `list_status` is `L`, so the
   obvious request returns 5,539 survivors with no way to see that 339 securities are
   missing. The descriptor sends an explicit `fields` list and `list_status="L,D"`.
2. **One registry row becomes one or two panel rows.** A listing and a delisting cannot share
   an `available_time`: dating the row at the listing makes a 2024 termination visible to a
   2019 reader, and dating it at the termination hides the security from every earlier
   `as_of`, which is survivorship bias. So the row is split, and the point-in-time filter
   then does the right thing at both ends by itself.
3. **Neither dataset is served on the evidence plane.** `fetch()` is a verbatim-passthrough
   contract, and a verbatim row of either of these carries facts that became knowable at
   different instants -- `delist_date` on a listing, `end_date` on a name in effect. There is
   no single honest `available_time` for such a row, so it is refused by name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from openalpha_cn.domain.name_history import NAMECHANGE_DATASET
from openalpha_cn.domain.stock_universe import STOCK_BASIC_DATASET
from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.tushare import (
    TUSHARE_DATASETS,
    TUSHARE_RESPONSE_TRUNCATION_FLAG,
    TushareProvider,
    _calendar_static_timeline,
)

# --- real `stock_basic` rows, list_status="L,D", explicit fields ----------------------

REGISTRY_FIELDS = [
    "ts_code",
    "name",
    "exchange",
    "market",
    "list_status",
    "list_date",
    "delist_date",
]

REGISTRY_ITEMS: list[list[Any]] = [
    ["000001.SZ", "平安银行", "SZSE", "主板", "L", "19910403", None],
    ["000005.SZ", "ST星源(退)", "SZSE", "主板", "D", "19901210", "20240426"],
    ["000018.SZ", "神城A退(退)", "SZSE", "主板", "D", "19920616", "20200107"],
    ["688001.SH", "华兴源创", "SSE", "科创板", "L", "20190722", None],
]

# --- real `namechange` rows -----------------------------------------------------------

NAMECHANGE_FIELDS = ["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"]

PING_AN_ITEMS: list[list[Any]] = [
    ["000001.SZ", "平安银行", "20120802", None, "20120120", "其他"],
    ["000001.SZ", "深发展A", "20070620", "20120801", "20070614", "其他"],
    ["000001.SZ", "S深发展A", "20061009", "20070619", "20060928", "其他"],
    ["000001.SZ", "深发展A", "19910403", "20061008", "19910403", "其他"],
]

# A live 2026 row whose announcement date is in the *future* of the fetch: Tushare
# pre-loads a scheduled Beijing listing. Captured 2026-08-08.
FUTURE_ANNOUNCEMENT: list[Any] = ["920165.BJ", "珈凯生物", "20260811", None, "20260811", "其他"]


def _response(fields: list[str], items: list[list[Any]]) -> dict[str, Any]:
    """A complete response. `has_more=False` is what a live one actually carries, and
    `stock_basic` now requires it to be present -- it has no measured row cap, so the flag is
    that endpoint's only witness that nothing was withheld."""
    return {
        "code": 0,
        "msg": None,
        "data": {
            "fields": list(fields),
            "items": items,
            TUSHARE_RESPONSE_TRUNCATION_FLAG: False,
        },
    }


FETCHED_AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
"""When the fixtures were captured. Kept distinct from a request's `as_of`, because the
point-in-time question this task is about is "what would an observer in 2019 have seen",
asked of data fetched today -- not "pretend the fetch happened in 2019", which would hand a
2019 clock a snapshot that did not exist then."""


def _provider(clock: datetime, fake_tushare_transport, response: dict[str, Any]):
    transport = fake_tushare_transport(response)
    return TushareProvider(
        token="secret-token", transport=transport, clock=lambda: clock
    ), transport


def _descriptor(name: str):
    (descriptor,) = (entry for entry in TUSHARE_DATASETS if entry.dataset == name)
    return descriptor


# --- the request ----------------------------------------------------------------------


def test_stock_basic_asks_for_the_delisted_set_and_for_the_column_the_defaults_omit(
    fake_tushare_transport,
) -> None:
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    provider, transport = _provider(
        as_of, fake_tushare_transport, _response(REGISTRY_FIELDS, REGISTRY_ITEMS)
    )
    provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of))

    assert transport.payload is not None
    assert transport.payload["params"] == {"list_status": "L,D"}
    requested = transport.payload["fields"].split(",")
    assert "delist_date" in requested
    assert "list_status" in requested
    assert "list_date" in requested


def test_stock_basic_refuses_a_filtered_request_because_a_filtered_universe_is_not_one(
    fake_tushare_transport,
) -> None:
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        as_of, fake_tushare_transport, _response(REGISTRY_FIELDS, REGISTRY_ITEMS)
    )
    with pytest.raises(ProviderFailure, match="serves the whole registry"):
        provider.fetch_panel(
            ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of, subjects=("000001.SZ",))
        )


def test_namechange_asks_for_one_announcement_year_taken_from_as_of_in_shanghai(
    fake_tushare_transport,
) -> None:
    # 2024-12-31 17:00Z is already 2025-01-01 in Shanghai; asking UTC would fetch 2024.
    as_of = datetime(2024, 12, 31, 17, 0, tzinfo=UTC)
    provider, transport = _provider(as_of, fake_tushare_transport, _response(NAMECHANGE_FIELDS, []))
    provider.fetch_panel(ProviderRequest(dataset=NAMECHANGE_DATASET, as_of=as_of))

    assert transport.payload is not None
    assert transport.payload["params"] == {"start_date": "20250101", "end_date": "20251231"}


def test_namechange_refuses_a_single_name_request(fake_tushare_transport) -> None:
    as_of = datetime(2012, 12, 31, 12, 0, tzinfo=UTC)
    provider, _ = _provider(as_of, fake_tushare_transport, _response(NAMECHANGE_FIELDS, []))
    with pytest.raises(ProviderFailure, match="serves one announcement year"):
        provider.fetch_panel(
            ProviderRequest(dataset=NAMECHANGE_DATASET, as_of=as_of, subjects=("000001.SZ",))
        )


# --- the lifecycle split ----------------------------------------------------------------


def test_one_registry_row_becomes_a_listing_row_and_a_delisting_row(
    fake_tushare_transport,
) -> None:
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        as_of, fake_tushare_transport, _response(REGISTRY_FIELDS, REGISTRY_ITEMS)
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of))

    # Two listed securities contribute one row each; two delisted contribute two each.
    assert batch.row_count == 6
    by_name = {column.name: column.values for column in batch.columns}
    pairs = sorted(zip(batch.subjects, by_name["lifecycle_event"], strict=True))
    assert pairs == [
        ("000001.SZ", "listing"),
        ("000005.SZ", "delisting"),
        ("000005.SZ", "listing"),
        ("000018.SZ", "delisting"),
        ("000018.SZ", "listing"),
        ("688001.SH", "listing"),
    ]


def test_a_delisting_is_invisible_before_it_happens_while_the_listing_is_not(
    fake_tushare_transport,
) -> None:
    """The property the split exists for. At `as_of=2019-06-28` the 2020 and 2024
    terminations are not yet knowable, and the securities are still in the universe."""
    as_of = datetime(2019, 6, 28, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        FETCHED_AT, fake_tushare_transport, _response(REGISTRY_FIELDS, REGISTRY_ITEMS)
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of))

    by_name = {column.name: column.values for column in batch.columns}
    rows = sorted(zip(batch.subjects, by_name["lifecycle_event"], strict=True))
    assert rows == [
        ("000001.SZ", "listing"),
        ("000005.SZ", "listing"),
        ("000018.SZ", "listing"),
    ]
    # 688001.SH lists 2019-07-22, after this as_of, so its listing is not knowable either.
    assert "688001.SH" not in batch.subjects


def test_the_registrys_descriptive_columns_never_reach_the_panel(
    fake_tushare_transport,
) -> None:
    """`name`, `market` and the rest are attributes of the *snapshot*. Stamping today's
    `ST星源(退)` onto a 1990 listing row would be a 34-year look-ahead with no clock on it."""
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        as_of, fake_tushare_transport, _response(REGISTRY_FIELDS, REGISTRY_ITEMS)
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of))

    assert tuple(column.name for column in batch.columns) == (
        "lifecycle_event",
        "lifecycle_date",
        "exchange",
    )


def test_the_lifecycle_clock_dates_availability_at_the_event_itself(
    fake_tushare_transport,
) -> None:
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        as_of, fake_tushare_transport, _response(REGISTRY_FIELDS, REGISTRY_ITEMS)
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of))

    by_name = {column.name: column.values for column in batch.columns}
    index = next(
        position
        for position, pair in enumerate(
            zip(batch.subjects, by_name["lifecycle_event"], strict=True)
        )
        if pair == ("000018.SZ", "delisting")
    )
    timeline = batch.row_timeline(index)
    # 2020-01-07 00:00 Asia/Shanghai == 2020-01-06 16:00Z.
    assert timeline.event_time == datetime(2020, 1, 6, 16, 0, tzinfo=UTC)
    assert timeline.available_time == timeline.event_time
    assert timeline.revision_time == timeline.available_time


# --- namechange's two clocks --------------------------------------------------------------


def test_a_namechange_row_is_dated_at_its_announcement_and_carries_the_effect_as_data(
    fake_tushare_transport,
) -> None:
    """The row records a *published announcement*. Its clocks are the announcement, and the
    effective date rides along as a column because it is published in that same announcement
    -- the asymmetry with `stock_basic`, whose delisting is not announced with its listing and
    therefore has to become its own row."""
    as_of = datetime(2012, 12, 31, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        as_of, fake_tushare_transport, _response(NAMECHANGE_FIELDS, PING_AN_ITEMS)
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=NAMECHANGE_DATASET, as_of=as_of))

    by_name = {column.name: column.values for column in batch.columns}
    index = by_name["name"].index("平安银行")
    timeline = batch.row_timeline(index)
    # Announced 2012-01-20 00:00 Asia/Shanghai == 2012-01-19 16:00Z.
    assert timeline.event_time == datetime(2012, 1, 19, 16, 0, tzinfo=UTC)
    assert timeline.available_time == timeline.event_time
    assert timeline.revision_time == timeline.available_time
    # The effect is seven months later and is data, not a clock.
    assert by_name["effective_date"][index] == "2012-08-02"


def test_both_clocks_are_stored_as_columns_and_end_date_is_not(
    fake_tushare_transport,
) -> None:
    as_of = datetime(2012, 12, 31, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        as_of, fake_tushare_transport, _response(NAMECHANGE_FIELDS, PING_AN_ITEMS)
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=NAMECHANGE_DATASET, as_of=as_of))

    names = tuple(column.name for column in batch.columns)
    assert names == ("name", "effective_date", "announcement_date", "change_reason")
    assert "end_date" not in names
    by_name = {column.name: column.values for column in batch.columns}
    index = by_name["name"].index("平安银行")
    assert by_name["effective_date"][index] == "2012-08-02"
    assert by_name["announcement_date"][index] == "2012-01-20"


def test_a_rename_announced_after_the_as_of_never_reaches_the_batch(
    fake_tushare_transport,
) -> None:
    """The live 2026 case: Tushare pre-loads `920165.BJ`, announced 2026-08-11, into the
    2026 window. A fetch standing at 2026-08-08 must not see it."""
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        as_of, fake_tushare_transport, _response(NAMECHANGE_FIELDS, [FUTURE_ANNOUNCEMENT])
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=NAMECHANGE_DATASET, as_of=as_of))

    assert batch.status == "no_data"
    assert batch.no_data_reason is not None
    assert "none of which was yet knowable" in batch.no_data_reason


def test_the_ingest_clock_is_raised_rather_than_the_availability_clock_lowered() -> None:
    """`Timeline` forbids `available_time > ingested_time`, and a pre-loaded future
    announcement violates it. Lowering `available_time` to the fetch instant would make an
    unannounced rename readable -- the dangerous direction and an invented fact. Raising
    `ingested_time` overstates only the clock no point-in-time filter consults, and the row is
    dropped immediately afterwards by that filter for every request whose `as_of` does not run
    ahead of its own clock."""
    ingested_at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    row = dict(zip(NAMECHANGE_FIELDS, FUTURE_ANNOUNCEMENT, strict=True))
    timeline = _calendar_static_timeline(row, "ann_date", ingested_at)

    assert timeline.available_time == datetime(2026, 8, 10, 16, 0, tzinfo=UTC)
    assert timeline.ingested_time == timeline.available_time
    assert timeline.ingested_time > ingested_at

    # An ordinary row leaves the fetch instant alone.
    ordinary = dict(zip(NAMECHANGE_FIELDS, PING_AN_ITEMS[0], strict=True))
    assert _calendar_static_timeline(ordinary, "ann_date", ingested_at).ingested_time == (
        ingested_at
    )


@pytest.mark.parametrize(
    "as_of",
    [
        datetime(2026, 12, 31, 12, 0, tzinfo=UTC),
        datetime(2099, 1, 1, 12, 0, tzinfo=UTC),
    ],
)
def test_a_raised_ingest_clock_never_survives_the_filter_however_late_the_as_of_is(
    as_of: datetime, fake_tushare_transport
) -> None:
    """The raise above exists only so the row can be represented long enough to be discarded.

    `ProviderRequest` accepts any `as_of`, including one past the fetch instant, and dating a
    batch at the end of the calendar year is a shape this repository's own fixtures use
    (`tests/integration/panel/test_registry_ingest.py::_seed_namechange`). Filtering on
    `as_of` alone let the 2026-08-11 announcement through on both of these, carrying an
    `ingested_time` two days after the clock that fetched it. The bound is
    `min(as_of, ingested_at)`, so it is dropped instead -- and dropping is the honest answer,
    because by its own clock the row was not knowable when the fetch ran.
    """
    fetch_clock = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        fetch_clock, fake_tushare_transport, _response(NAMECHANGE_FIELDS, [FUTURE_ANNOUNCEMENT])
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=NAMECHANGE_DATASET, as_of=as_of))

    assert batch.status == "no_data"
    assert batch.no_data_reason is not None
    assert "none of which was yet knowable" in batch.no_data_reason
    assert "or at the instant of the fetch" in batch.no_data_reason


def test_a_pinned_stamp_cannot_raise_the_ceiling_the_filter_is_bounded_at(
    fake_tushare_transport,
) -> None:
    """The bound reads `clock()`, never the instant a caller pinned the stamps at.

    `V2-P1-018` gave `cli.panel_build` an `--as-of` and passed it in as the provider's `clock`,
    for a real reason -- `ingested_time` is a stored column, so an unpinned rebuild rewrites the
    partition every time. But it pinned the *judge* along with the *stamp*: the bound
    `min(as_of, ingested_at)` became `min(as_of, as_of)`, and this very row -- announced
    2026-08-11, already in the corpus on 2026-08-08 -- went back through it.

    `stamped_at` is now the pin and `clock` is the ceiling, and the assertion below is that they
    are not the same knob: the stamp is pinned three days past the announcement and the row is
    still dropped, because the process is still running on 2026-08-08.
    """
    fetch_clock = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    transport = fake_tushare_transport(_response(NAMECHANGE_FIELDS, [FUTURE_ANNOUNCEMENT]))
    provider = TushareProvider(
        token="secret-token",
        transport=transport,
        clock=lambda: fetch_clock,
        stamped_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=NAMECHANGE_DATASET, as_of=datetime(2026, 12, 31, 12, 0, tzinfo=UTC))
    )

    assert batch.status == "no_data"
    assert batch.no_data_reason is not None
    assert "or at the instant of the fetch" in batch.no_data_reason


def test_a_pinned_stamp_is_what_every_kept_row_and_the_batch_itself_carry(
    fake_tushare_transport,
) -> None:
    """The other half of `_stamp`: pinning still does the job it was added for.

    `fetched_at` and every row's `ingested_time` come from the pin rather than from the clock,
    which is what makes a partition re-fetched at the same `--as-of` hash to the same value --
    `ingested_time` is one of the four clock columns `ColumnarPanelBatch.storage_columns()`
    persists. Asserted against a clock deliberately *later* than the pin, so a fixture that
    quietly stopped pinning would show up here rather than only under `--as-of`.
    """
    stamped_at = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    transport = fake_tushare_transport(_response(NAMECHANGE_FIELDS, PING_AN_ITEMS))
    provider = TushareProvider(
        token="secret-token",
        transport=transport,
        clock=lambda: datetime(2026, 8, 9, 3, 0, tzinfo=UTC),
        stamped_at=stamped_at,
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=NAMECHANGE_DATASET, as_of=datetime(2026, 8, 8, 23, 0, tzinfo=UTC))
    )

    assert batch.status == "success"
    assert batch.fetched_at == stamped_at
    assert set(batch.timeline.ingested_time) == {stamped_at}


def test_a_late_as_of_still_keeps_every_row_that_was_knowable_when_the_fetch_ran(
    fake_tushare_transport,
) -> None:
    """The other side of the same bound: the drop is `available_time > ingested_at`, not
    "the `as_of` is in the future", so an ordinary batch fetched with a year-end `as_of`
    keeps every row and stores the real fetch instant on all of them."""
    fetch_clock = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    as_of = datetime(2026, 12, 31, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        fetch_clock, fake_tushare_transport, _response(NAMECHANGE_FIELDS, PING_AN_ITEMS)
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=NAMECHANGE_DATASET, as_of=as_of))

    assert batch.status == "success"
    assert len(batch.subjects) == len(PING_AN_ITEMS)
    assert set(batch.timeline.ingested_time) == {fetch_clock}
    assert max(batch.timeline.available_time) <= fetch_clock


# --- neither dataset is served on the evidence plane --------------------------------------


@pytest.mark.parametrize("dataset", [STOCK_BASIC_DATASET, NAMECHANGE_DATASET])
def test_the_row_wise_evidence_path_refuses_both_registry_datasets(
    dataset: str, fake_tushare_transport
) -> None:
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    provider, _ = _provider(as_of, fake_tushare_transport, _response(REGISTRY_FIELDS, []))
    with pytest.raises(ProviderFailure, match="knowable at different instants"):
        provider.fetch(ProviderRequest(dataset=dataset, as_of=as_of))


def test_the_calendar_still_serves_both_planes(fake_tushare_transport) -> None:
    """The refusal above is per-descriptor, not a new blanket rule."""
    assert _descriptor("trade_cal").serves_evidence_plane is True
    assert _descriptor("daily").serves_evidence_plane is True
    assert _descriptor(STOCK_BASIC_DATASET).serves_evidence_plane is False
    assert _descriptor(NAMECHANGE_DATASET).serves_evidence_plane is False


# --- shape guards -------------------------------------------------------------------------


def test_a_response_missing_a_required_column_is_refused_by_name(
    fake_tushare_transport,
) -> None:
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    trimmed = [field for field in REGISTRY_FIELDS if field != "delist_date"]
    items = [row[:-1] for row in REGISTRY_ITEMS]
    provider, _ = _provider(as_of, fake_tushare_transport, _response(trimmed, items))
    with pytest.raises(ProviderFailure, match="has no delist_date column"):
        provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of))


def test_rows_are_sorted_ascending_by_their_own_lifecycle_date(
    fake_tushare_transport,
) -> None:
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        as_of, fake_tushare_transport, _response(REGISTRY_FIELDS, REGISTRY_ITEMS)
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of))

    by_name = {column.name: column.values for column in batch.columns}
    dates = list(by_name["lifecycle_date"])
    assert dates == sorted(dates)
    assert dates[0] == "1990-12-10"
    assert dates[-1] == "2024-04-26"


def test_a_registry_row_with_no_delist_date_produces_no_delisting_row(
    fake_tushare_transport,
) -> None:
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    listed_only = [row for row in REGISTRY_ITEMS if row[-1] is None]
    provider, _ = _provider(as_of, fake_tushare_transport, _response(REGISTRY_FIELDS, listed_only))
    batch = provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of))

    by_name = {column.name: column.values for column in batch.columns}
    assert set(by_name["lifecycle_event"]) == {"listing"}
    assert batch.row_count == len(listed_only)


def test_an_empty_delist_date_string_is_treated_as_no_delisting(
    fake_tushare_transport,
) -> None:
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    blank = [["000001.SZ", "平安银行", "SZSE", "主板", "L", "19910403", ""]]
    provider, _ = _provider(as_of, fake_tushare_transport, _response(REGISTRY_FIELDS, blank))
    batch = provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of))

    by_name = {column.name: column.values for column in batch.columns}
    assert by_name["lifecycle_event"] == ("listing",)


def test_a_null_key_column_is_refused_rather_than_grouped_under_none(
    fake_tushare_transport,
) -> None:
    """`PanelColumn` allows `None` in any data column, because panel data is genuinely sparse.
    These four columns are keys rather than observations, so a missing one would silently
    produce a universe entry with no exchange or a name history with no name."""
    as_of = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    no_exchange = [["000001.SZ", "平安银行", None, "主板", "L", "19910403", None]]
    provider, _ = _provider(as_of, fake_tushare_transport, _response(REGISTRY_FIELDS, no_exchange))
    with pytest.raises(ProviderFailure, match="expected a non-empty string, got NoneType"):
        provider.fetch_panel(ProviderRequest(dataset=STOCK_BASIC_DATASET, as_of=as_of))

    no_reason = [["000001.SZ", "平安银行", "20120802", None, "20120120", None]]
    provider, _ = _provider(as_of, fake_tushare_transport, _response(NAMECHANGE_FIELDS, no_reason))
    with pytest.raises(ProviderFailure, match="expected a non-empty string, got NoneType"):
        provider.fetch_panel(ProviderRequest(dataset=NAMECHANGE_DATASET, as_of=as_of))


def test_the_lifecycle_event_column_admits_only_the_two_events_the_domain_knows() -> None:
    """A same-module consistency check rather than an upstream boundary: the values come from
    `_stock_lifecycle_panel_rows` a few lines above, not from Tushare. It is here so that an
    edit which starts emitting a third event name fails in the projection instead of reaching
    `stock_universe_from_panel_rows` as an unreadable partition."""
    from openalpha_cn.providers.tushare import _lifecycle_event_name

    assert _lifecycle_event_name("listing") == "listing"
    assert _lifecycle_event_name("delisting") == "delisting"
    with pytest.raises(ValueError, match="lifecycle_event must be 'listing' or 'delisting'"):
        _lifecycle_event_name("suspension")
