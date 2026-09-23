"""`daily` and `daily_basic` on the descriptor table (`V2-P1-007`).

No network. Every shape below is one a live Tushare endpoint actually produced on
2026-08-08/09, and the row counts are the measured ones.

## The measurement this file is built on

    q(daily,       trade_date=20240628)      -> 5338 rows, 11 fields, has_more=False
    q(daily_basic, trade_date=20240628)      -> 5338 rows, 18 fields, has_more=False
    q(adj_factor,  trade_date=20240628)      -> 5387 rows,  3 fields, has_more=False
    q(daily,       trade_date=20260807)      -> 5535 rows
    q(daily,       ts_code=000001.SZ)        -> 6000 rows, has_more=True
    q(daily_basic, ts_code=000001.SZ)        -> 6000 rows, has_more=True
    q(daily,       trade_date=20240101)      ->    0 rows, has_more=False   (a holiday)

So the fetch granularity is **one session of the whole market**, exactly as `adj_factor`'s is:
a cross section fits under the 6,000-row cap with ~8% of headroom (5,535 against 6,000 on
2026-08-07) and one security's history does not.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from openalpha_cn.domain.daily_prices import (
    DAILY_AVAILABILITY_TIME,
    DAILY_BASIC_DATASET,
    DAILY_DATASET,
    SESSION_CLOSE_TIME,
)
from openalpha_cn.evidence.builder import EvidenceBuilder
from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.tushare import (
    TUSHARE_DATASETS,
    TUSHARE_PRICE_ROW_CAP,
    TUSHARE_RESPONSE_TRUNCATION_FLAG,
    TushareProvider,
)

PING_AN = "000001.SZ"

DAILY_FIELDS = [
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
]
DAILY_BASIC_FIELDS = [
    "ts_code",
    "trade_date",
    "close",
    "turnover_rate",
    "turnover_rate_f",
    "volume_ratio",
    "pe",
    "pe_ttm",
    "pb",
    "ps",
    "ps_ttm",
    "dv_ratio",
    "dv_ttm",
    "total_share",
    "float_share",
    "free_share",
    "total_mv",
    "circ_mv",
]

# Real rows for 000001.SZ across its 2026-06-12 ex-dividend date. Kept unformatted so each
# response row reads as one market observation rather than as eleven lines.
# fmt: off
BAR_06_11: list[Any] = [
    PING_AN, "20260611", 11.32, 11.39, 11.25, 11.3, 11.32, -0.02, -0.1767, 1156222.22,
    1308133.97213,
]
BAR_06_12: list[Any] = [
    PING_AN, "20260612", 11.0, 11.25, 10.88, 11.24, 10.94, 0.3, 2.7422, 2032355.46,
    2263042.93057,
]
VALUATION_06_12: list[Any] = [
    PING_AN, "20260612", 11.24, 1.0473, 2.4905, 1.71, 5.1163, 5.0655, 0.47, 1.6595, 1.6399,
    5.3204, 5.3026, 1940591.8198, 1940560.0653, 816048.1215, 21812252.0568, 21811895.1868,
]
# fmt: on

FETCHED_AT = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
AFTER_CLOSE_06_12 = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)  # 20:00 Asia/Shanghai


def _response(
    fields: list[str], items: list[list[Any]], *, has_more: Any = False, omit_flag: bool = False
) -> dict[str, Any]:
    data: dict[str, Any] = {"fields": list(fields), "items": items, "count": 0}
    if not omit_flag:
        data[TUSHARE_RESPONSE_TRUNCATION_FLAG] = has_more
    return {"code": 0, "msg": "", "data": data}


def _provider(fake_tushare_transport, response: dict[str, Any], *, clock: datetime = FETCHED_AT):
    transport = fake_tushare_transport(response)
    return TushareProvider(
        token="secret-token", transport=transport, clock=lambda: clock
    ), transport


def _descriptor(name: str):
    (descriptor,) = (entry for entry in TUSHARE_DATASETS if entry.dataset == name)
    return descriptor


# --- the descriptor table ---------------------------------------------------------------


def test_both_price_datasets_are_on_the_table_with_the_measured_cap() -> None:
    """The cap is per endpoint and is the *measured* one: a bare per-security request for
    either endpoint returns exactly 6,000 rows with `has_more=True`."""
    for name in (DAILY_DATASET, DAILY_BASIC_DATASET):
        descriptor = _descriptor(name)
        assert descriptor.max_rows_per_response == TUSHARE_PRICE_ROW_CAP
        assert descriptor.subject_field == "ts_code"
        assert descriptor.date_field == "trade_date"
        assert descriptor.response_fields == ""


def test_neither_price_dataset_demands_the_truncation_flag_and_the_reason_is_the_cap() -> None:
    """A deliberate difference from `adj_factor`, not an oversight.

    `requires_truncation_flag` governs only what happens when `has_more` is **absent**; a flag
    that is present is checked for every dataset. `adj_factor` demands it because a factor is a
    step function and a truncated series answers later days from an older step -- a *wrong*
    number rather than a missing one. A truncated `daily` response has no such amplification: a
    bar that was dropped is simply an absent row, and `daily_bars_from_panel_rows` has nothing
    to interpolate it from.

    What remains is stated rather than claimed away: a response that both omits `has_more`
    **and** comes in under 6,000 rows is accepted, so a schema change that removed the flag and
    lowered the cap at the same time would pass. The two tests below pin both halves.
    """
    for name in (DAILY_DATASET, DAILY_BASIC_DATASET):
        assert _descriptor(name).requires_truncation_flag is False


def test_a_price_response_at_the_measured_cap_is_refused_even_with_a_false_flag(
    fake_tushare_transport,
) -> None:
    items = [
        [f"{600000 + index}.SH", "20260612", 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 1.0, 1.0]
        for index in range(TUSHARE_PRICE_ROW_CAP)
    ]
    provider, _ = _provider(fake_tushare_transport, _response(DAILY_FIELDS, items))
    with pytest.raises(ProviderFailure, match=r"served 6000 daily row\(s\), which is its"):
        provider.fetch_panel(ProviderRequest(dataset=DAILY_DATASET, as_of=AFTER_CLOSE_06_12))


def test_a_price_response_that_omits_the_flag_under_the_cap_is_accepted_and_the_gap_is_named(
    fake_tushare_transport,
) -> None:
    """The residue of the choice above, exercised rather than only described."""
    provider, _ = _provider(
        fake_tushare_transport,
        _response(DAILY_FIELDS, [BAR_06_12], omit_flag=True),
        clock=AFTER_CLOSE_06_12,
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=DAILY_DATASET, as_of=AFTER_CLOSE_06_12))
    assert batch.status == "success"


def test_a_price_response_that_says_it_has_more_is_still_refused(fake_tushare_transport) -> None:
    """The flag is checked whenever it is present, on both endpoints -- which is what makes
    the per-security 6,000-row shape unstorable rather than merely discouraged."""
    for name, fields, row in (
        (DAILY_DATASET, DAILY_FIELDS, BAR_06_12),
        (DAILY_BASIC_DATASET, DAILY_BASIC_FIELDS, VALUATION_06_12),
    ):
        provider, _ = _provider(
            fake_tushare_transport, _response(fields, [row], has_more=True), clock=AFTER_CLOSE_06_12
        )
        with pytest.raises(ProviderFailure, match="reports that it has more rows to give"):
            provider.fetch_panel(ProviderRequest(dataset=name, as_of=AFTER_CLOSE_06_12))


def test_the_request_is_one_session_of_the_whole_market(fake_tushare_transport) -> None:
    """5,338 rows on 2024-06-28 and 5,535 on 2026-08-07 against a 6,000-row cap; one
    security's own history is 6,000-and-truncated. So the cross section is the granularity,
    and `subjects` is the escape route when the market outgrows the cap."""
    for name, fields, row in (
        (DAILY_DATASET, DAILY_FIELDS, BAR_06_12),
        (DAILY_BASIC_DATASET, DAILY_BASIC_FIELDS, VALUATION_06_12),
    ):
        provider, transport = _provider(
            fake_tushare_transport, _response(fields, [row]), clock=AFTER_CLOSE_06_12
        )
        provider.fetch_panel(ProviderRequest(dataset=name, as_of=AFTER_CLOSE_06_12))
        assert transport.payload is not None
        assert transport.payload["api_name"] == name
        assert transport.payload["params"] == {"trade_date": "20260612"}


def test_the_session_is_resolved_in_shanghai_not_utc(fake_tushare_transport) -> None:
    """2026-06-11 17:00Z is already 2026-06-12 in Shanghai; asking UTC would fetch the previous
    session for every late-evening request."""
    provider, transport = _provider(
        fake_tushare_transport, _response(DAILY_FIELDS, [BAR_06_12]), clock=AFTER_CLOSE_06_12
    )
    provider.fetch_panel(
        ProviderRequest(dataset=DAILY_DATASET, as_of=datetime(2026, 6, 11, 17, 0, tzinfo=UTC))
    )
    assert transport.payload is not None
    assert transport.payload["params"]["trade_date"] == "20260612"


# --- the projection ---------------------------------------------------------------------


def test_the_daily_projection_keeps_pre_close_and_drops_the_derivable_change(
    fake_tushare_transport,
) -> None:
    """`pre_close` is the whole reason this dataset can answer a return from one row, so it is
    projected. `change` is `close - pre_close` and carries nothing those two do not, so it is
    fetched (it is in the endpoint's defaults) and not stored."""
    provider, _ = _provider(
        fake_tushare_transport, _response(DAILY_FIELDS, [BAR_06_12]), clock=AFTER_CLOSE_06_12
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=DAILY_DATASET, as_of=AFTER_CLOSE_06_12))

    assert [column.name for column in batch.columns] == [
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "pct_chg",
        "vol",
        "amount",
    ]
    values = {column.name: column.values[0] for column in batch.columns}
    assert values["trade_date"] == "2026-06-12"
    assert values["close"] == 11.24
    assert values["pre_close"] == 10.94
    assert values["pct_chg"] == 2.7422
    assert all(column.kind == "float" for column in batch.columns if column.name != "trade_date")


def test_the_daily_basic_projection_carries_every_field_the_endpoint_serves(
    fake_tushare_transport,
) -> None:
    """`total_mv`/`circ_mv`/`turnover_rate` are P3's neutralisation and P4's leaderboard
    inputs; the rest cost one column each in a projected store while a re-fetch of a decade
    costs hours. `close` is kept despite duplicating `daily`, because that duplication is the
    free cross-check."""
    provider, _ = _provider(
        fake_tushare_transport,
        _response(DAILY_BASIC_FIELDS, [VALUATION_06_12]),
        clock=AFTER_CLOSE_06_12,
    )
    batch = provider.fetch_panel(
        ProviderRequest(dataset=DAILY_BASIC_DATASET, as_of=AFTER_CLOSE_06_12)
    )

    assert [column.name for column in batch.columns] == DAILY_BASIC_FIELDS[1:]
    values = {column.name: column.values[0] for column in batch.columns}
    assert values["trade_date"] == "2026-06-12"
    assert values["close"] == 11.24
    assert values["total_mv"] == 21812252.0568
    assert values["circ_mv"] == 21811895.1868
    assert values["turnover_rate"] == 1.0473


def test_a_null_valuation_ratio_is_stored_as_a_null_rather_than_refused(
    fake_tushare_transport,
) -> None:
    """1,102 of 2024-06-28's 5,338 names had a null `pe` and 1,840 a null `dv_ttm`; a
    loss-making company has no P/E and a non-payer has no yield."""
    row = list(VALUATION_06_12)
    for field in ("pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm", "volume_ratio"):
        row[DAILY_BASIC_FIELDS.index(field)] = None
    provider, _ = _provider(
        fake_tushare_transport, _response(DAILY_BASIC_FIELDS, [row]), clock=AFTER_CLOSE_06_12
    )
    batch = provider.fetch_panel(
        ProviderRequest(dataset=DAILY_BASIC_DATASET, as_of=AFTER_CLOSE_06_12)
    )
    values = {column.name: column.values[0] for column in batch.columns}
    assert values["pe"] is None
    assert values["dv_ttm"] is None
    assert values["total_mv"] == 21812252.0568


@pytest.mark.parametrize("field", ["close", "total_mv", "circ_mv", "turnover_rate"])
def test_a_null_in_a_valuation_column_that_is_never_null_upstream_is_refused(
    fake_tushare_transport, field: str
) -> None:
    """The complement, and the claim that carries risk. Those four were populated on every row
    of five sessions across 2023..2026; a null `total_mv` silently drops a name from a
    neutralisation regression, so it fails at the boundary instead.

    `close` is refused by the *stronger* rule of the two, because it is a price and shares
    `daily`'s positivity requirement; the other three only have to be numbers.
    """
    row = list(VALUATION_06_12)
    row[DAILY_BASIC_FIELDS.index(field)] = None
    provider, _ = _provider(
        fake_tushare_transport, _response(DAILY_BASIC_FIELDS, [row]), clock=AFTER_CLOSE_06_12
    )
    expected = "finite positive number" if field == "close" else "finite number"
    with pytest.raises(ProviderFailure, match=f"{field} must be a {expected}, got NoneType"):
        provider.fetch_panel(ProviderRequest(dataset=DAILY_BASIC_DATASET, as_of=AFTER_CLOSE_06_12))


def test_the_two_share_columns_that_are_sparse_before_2019_are_stored_as_nulls(
    fake_tushare_transport,
) -> None:
    """A real row: `300290.SZ` on 2018-01-02, whose `free_share` is null -- as it is on 74
    consecutive sessions from 2017-09-20 through 2018-01-16.

    This is the row that made 2017 and 2018 unstorable. `free_share` and `turnover_rate_f` were
    in the refused half because the nullability sample ran only from 2023-01-03, and a refusal
    here fails the whole 3,252-row cross section rather than one cell: the session cannot be
    built, the year is then missing a session the calendar reports open, and `write_daily_panel`
    refuses the year on both datasets. Null counts on the eighteen sessions probed from
    2001-01-02 are in `domain/daily_prices.py::DAILY_BASIC_NULLABLE_COLUMNS`.
    """
    row: list[Any] = [
        "300290.SZ", "20180102", 8.71, 4.9386, 4.9358, 2.25, 86.4158, 108.6244, 3.9847, 5.5703,
        6.3894, 0.2411, 0.2411, 32142.9652, 12994.6866, None, 279965.2269, 113183.7203,
    ]  # fmt: skip
    provider, _ = _provider(
        fake_tushare_transport,
        _response(DAILY_BASIC_FIELDS, [row]),
        clock=datetime(2018, 1, 2, 12, 0, tzinfo=UTC),
    )
    batch = provider.fetch_panel(
        ProviderRequest(dataset=DAILY_BASIC_DATASET, as_of=datetime(2018, 1, 2, 12, 0, tzinfo=UTC))
    )
    values = {column.name: column.values[0] for column in batch.columns}
    assert values["free_share"] is None
    assert values["turnover_rate_f"] == 4.9358
    assert values["total_mv"] == 279965.2269
    assert values["float_share"] == 12994.6866

    # 2005-01-04 has four rows with a null `turnover_rate_f` as well.
    row[DAILY_BASIC_FIELDS.index("turnover_rate_f")] = None
    provider, _ = _provider(
        fake_tushare_transport,
        _response(DAILY_BASIC_FIELDS, [row]),
        clock=datetime(2018, 1, 2, 12, 0, tzinfo=UTC),
    )
    batch = provider.fetch_panel(
        ProviderRequest(dataset=DAILY_BASIC_DATASET, as_of=datetime(2018, 1, 2, 12, 0, tzinfo=UTC))
    )
    assert {column.name: column.values[0] for column in batch.columns}["turnover_rate_f"] is None


@pytest.mark.parametrize("field", ["pct_chg", "vol", "amount"])
def test_a_boolean_in_a_daily_column_is_refused_rather_than_widened_to_one(
    fake_tushare_transport, field: str
) -> None:
    """`_finite_number` is `type(v) is float or type(v) is int`, and `type(True)` is `bool`.

    The same property is pinned for `close` through the positive-price parse, but `close` is
    one of 26 columns that reach `_finite_number` and `_optional_number` between the two
    datasets -- so a numeric-tower `isinstance(v, (int, float))` there would put a 1.0 into
    `total_mv`, `vol` or `amount` with every existing test still green.
    """
    row = list(BAR_06_12)
    row[DAILY_FIELDS.index(field)] = True
    provider, _ = _provider(
        fake_tushare_transport, _response(DAILY_FIELDS, [row]), clock=AFTER_CLOSE_06_12
    )
    with pytest.raises(ProviderFailure, match=f"{field} must be a finite number, got bool"):
        provider.fetch_panel(ProviderRequest(dataset=DAILY_DATASET, as_of=AFTER_CLOSE_06_12))


@pytest.mark.parametrize(
    "field", ["total_mv", "circ_mv", "turnover_rate", "total_share", "float_share", "pe"]
)
def test_a_boolean_in_a_valuation_column_is_refused_rather_than_widened_to_one(
    fake_tushare_transport, field: str
) -> None:
    """The same rule on the other dataset, including one of the nullable columns -- a null is
    data there and a `bool` still is not."""
    row = list(VALUATION_06_12)
    row[DAILY_BASIC_FIELDS.index(field)] = True
    provider, _ = _provider(
        fake_tushare_transport, _response(DAILY_BASIC_FIELDS, [row]), clock=AFTER_CLOSE_06_12
    )
    with pytest.raises(ProviderFailure, match=f"{field} must be a finite number, got bool"):
        provider.fetch_panel(ProviderRequest(dataset=DAILY_BASIC_DATASET, as_of=AFTER_CLOSE_06_12))


@pytest.mark.parametrize("value", [None, 0.0, -1.0, True, "11.24", float("inf")])
def test_a_price_that_cannot_be_a_price_is_refused_at_the_boundary(
    fake_tushare_transport, value: Any
) -> None:
    """`True` is the one that matters: `isinstance(True, int)` is `True`, so a numeric-tower
    check would let it through as 1.0 and put a one-yuan close in the panel."""
    row = list(BAR_06_12)
    row[DAILY_FIELDS.index("close")] = value
    provider, _ = _provider(
        fake_tushare_transport, _response(DAILY_FIELDS, [row]), clock=AFTER_CLOSE_06_12
    )
    with pytest.raises(ProviderFailure, match="close must be a finite positive number"):
        provider.fetch_panel(ProviderRequest(dataset=DAILY_DATASET, as_of=AFTER_CLOSE_06_12))


def test_an_integer_price_is_accepted_and_widened(fake_tushare_transport) -> None:
    """JSON has one number type, and `PanelColumn` refuses an `int` in a `float` column."""
    row = list(BAR_06_12)
    row[DAILY_FIELDS.index("close")] = 11
    provider, _ = _provider(
        fake_tushare_transport, _response(DAILY_FIELDS, [row]), clock=AFTER_CLOSE_06_12
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=DAILY_DATASET, as_of=AFTER_CLOSE_06_12))
    close = next(column for column in batch.columns if column.name == "close")
    assert close.values == (11.0,)
    assert type(close.values[0]) is float


def test_a_negative_pct_chg_is_kept_because_a_return_has_a_sign(fake_tushare_transport) -> None:
    """The price columns demand positivity and `pct_chg` must not: -0.1767 is a real value."""
    provider, _ = _provider(
        fake_tushare_transport,
        _response(DAILY_FIELDS, [BAR_06_11]),
        clock=datetime(2026, 6, 11, 12, 0, tzinfo=UTC),
    )
    batch = provider.fetch_panel(
        ProviderRequest(dataset=DAILY_DATASET, as_of=datetime(2026, 6, 11, 12, 0, tzinfo=UTC))
    )
    pct = next(column for column in batch.columns if column.name == "pct_chg")
    assert pct.values == (-0.1767,)


def test_a_response_missing_a_projected_column_is_refused_by_name(fake_tushare_transport) -> None:
    """Without this the missing column surfaced as `Tushare response could not be decoded:
    'pre_close'` -- a bare `KeyError` reason for what is a schema drift in a named column.

    `daily`'s own `required_response_fields` cannot carry this: `fetch()` is a
    payload-passthrough contract whose oldest test drives it with a four-column response, so
    the *evidence* path must stay tolerant of a narrow one. The panel path reads nine columns
    and says so.
    """
    narrow = [field for field in DAILY_FIELDS if field != "pre_close"]
    row = [
        value for field, value in zip(DAILY_FIELDS, BAR_06_12, strict=True) if field != "pre_close"
    ]
    provider, _ = _provider(
        fake_tushare_transport, _response(narrow, [row]), clock=AFTER_CLOSE_06_12
    )
    with pytest.raises(ProviderFailure, match="panel projection for daily needs a 'pre_close'"):
        provider.fetch_panel(ProviderRequest(dataset=DAILY_DATASET, as_of=AFTER_CLOSE_06_12))


def test_a_holiday_cross_section_is_no_data_and_not_a_truncation(fake_tushare_transport) -> None:
    """Measured: `daily(20240101)` and `daily_basic(20240101)` both return zero rows with
    `has_more=False`."""
    as_of = datetime(2024, 1, 1, 9, 0, tzinfo=UTC)
    provider, _ = _provider(fake_tushare_transport, _response(DAILY_FIELDS, []), clock=as_of)
    batch = provider.fetch_panel(ProviderRequest(dataset=DAILY_DATASET, as_of=as_of))
    assert batch.status == "no_data"
    assert batch.no_data_reason is not None
    assert "which is a horizon and not a closed period" in batch.no_data_reason


# --- the clock ---------------------------------------------------------------------------


def test_the_clock_is_built_from_the_domain_constants_the_read_side_also_reads(
    fake_tushare_transport,
) -> None:
    """The provider dates availability at 16:30 Asia/Shanghai and `panel_ingest` derives which
    sessions a partition is *required* to hold from the same instant. Two literals would let
    those drift, and the drift is silent in both directions -- a later provider time makes the
    read demand a session the write could not have held, an earlier one stops requiring the
    newest session. So the constants live in `domain/daily_prices.py` and both sides import
    them; this asserts the provider actually does.
    """
    from zoneinfo import ZoneInfo

    provider, _ = _provider(
        fake_tushare_transport, _response(DAILY_FIELDS, [BAR_06_12]), clock=AFTER_CLOSE_06_12
    )
    batch = provider.fetch_panel(ProviderRequest(dataset=DAILY_DATASET, as_of=AFTER_CLOSE_06_12))
    shanghai = ZoneInfo("Asia/Shanghai")
    assert batch.timeline.event_time[0] == datetime(2026, 6, 12, tzinfo=shanghai).replace(
        hour=SESSION_CLOSE_TIME.hour, minute=SESSION_CLOSE_TIME.minute
    )
    assert batch.timeline.available_time[0] == datetime(2026, 6, 12, tzinfo=shanghai).replace(
        hour=DAILY_AVAILABILITY_TIME.hour, minute=DAILY_AVAILABILITY_TIME.minute
    )
    assert (SESSION_CLOSE_TIME.hour, SESSION_CLOSE_TIME.minute) == (15, 0)
    assert (DAILY_AVAILABILITY_TIME.hour, DAILY_AVAILABILITY_TIME.minute) == (16, 30)


def test_a_session_that_has_not_reached_its_availability_time_is_not_yet_knowable(
    fake_tushare_transport,
) -> None:
    morning = datetime(2026, 6, 12, 1, 0, tzinfo=UTC)  # 09:00 Asia/Shanghai
    provider, _ = _provider(fake_tushare_transport, _response(DAILY_FIELDS, [BAR_06_12]))
    batch = provider.fetch_panel(ProviderRequest(dataset=DAILY_DATASET, as_of=morning))
    assert batch.status == "no_data"
    assert batch.no_data_reason is not None
    assert "not yet knowable is not the same as absent" in batch.no_data_reason


# --- the row-wise plane, which this issue reroutes rather than removes ---------------------


def test_the_row_wise_plane_still_serves_daily_verbatim(fake_tushare_transport) -> None:
    """`daily` keeps `serves_evidence_plane=True`, unlike `stock_basic` and `namechange`.

    The reason those two refuse is that one response row carries facts that became knowable at
    different instants, so no single `available_time` is honest. A daily bar is the opposite:
    everything in the row became knowable at that session's 16:30, exactly as `adj_factor`'s
    does. What moves this dataset onto the panel plane is *scale* -- ADR-0002 sizes the price
    panel at ~1.35e7 rows per field, and an evidence record costs a pydantic rebuild and three
    SHA-256 digests each -- and scale is a reason to add a second output shape, not to make the
    first one lie about the data.
    """
    provider, _ = _provider(
        fake_tushare_transport, _response(DAILY_FIELDS, [BAR_06_12]), clock=AFTER_CLOSE_06_12
    )
    batch = provider.fetch(ProviderRequest(dataset=DAILY_DATASET, as_of=AFTER_CLOSE_06_12))
    (record,) = batch.records
    assert record.kind == "daily"
    assert record.payload == dict(zip(DAILY_FIELDS, BAR_06_12, strict=True))
    assert record.source_uri == "tushare://daily/000001.SZ/20260612"


def test_the_evidence_builder_still_refuses_a_daily_record_and_that_is_the_right_answer(
    fake_tushare_transport,
) -> None:
    """The roadmap's own note for this issue: "existing `kind="daily"` records are refused by
    `evidence/builder.py:88-90`, this issue reroutes onto the panel".

    That refusal is not a gap this task fills. `EvidenceSnapshot` is a normalised, narrated
    citation -- a limit-up, a disclosure, a theme -- and a price bar is none of those; there is
    no `_Facts` model it would validate against and no `EvidenceFamily` it belongs to. So the
    row-wise fetch stays available and the builder keeps refusing, and the dataset reaches
    storage through `fetch_panel` instead. Pinned so that a later task adding a `"daily"`
    normaliser has to argue for it rather than acquire it by accident.
    """
    provider, _ = _provider(
        fake_tushare_transport, _response(DAILY_FIELDS, [BAR_06_12]), clock=AFTER_CLOSE_06_12
    )
    batch = provider.fetch(ProviderRequest(dataset=DAILY_DATASET, as_of=AFTER_CLOSE_06_12))
    with pytest.raises(ValueError, match="unsupported evidence kind: daily"):
        EvidenceBuilder().build(batch=batch, metadata=provider.metadata)
