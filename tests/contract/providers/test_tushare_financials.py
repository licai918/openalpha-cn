"""`income` / `balancesheet` / `cashflow` / `fina_indicator` on the descriptor table
(`V2-P1-011`).

Every response body here is real, captured live on 2026-08-09 with exactly the projection the
descriptors request; the transport is doubled so the suite never touches the network.

The five things this file is really about:

- **`ts_code` is mandatory and a comma-joined list is silently empty.** There is no
  cross-section fetch on these endpoints, and `income` answers a two-code request with zero
  rows and `code=0`.
- **The window is one calendar year, and it means a different column on `fina_indicator`.**
  `start_date`/`end_date` filter `ann_date` on three endpoints and `end_date` on the fourth.
- **The cap is 100 where it could be measured and unmeasurable where it could not**, and every
  one of the four demands the truncation flag.
- **The correction has no instant, so the two rows keep byte-equal clocks.** Nothing here reads
  `update_flag`.
- **`f_ann_date` is not always the later date**, and the row that proves it used to raise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIAL_INDICATOR_DATASET,
    FINANCIAL_STATEMENT_DATASETS,
    INCOME_DATASET,
    statement_panel_columns,
)
from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.tushare import (
    TUSHARE_DATASETS,
    TUSHARE_FINANCIAL_ROW_CAP,
    TUSHARE_RESPONSE_TRUNCATION_FLAG,
    TushareProvider,
)

AS_OF = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
SHANGHAI = ZoneInfo("Asia/Shanghai")

INCOME_FIELDS = [
    "ts_code",
    "end_date",
    "ann_date",
    "f_ann_date",
    "update_flag",
    "total_revenue",
    "revenue",
    "oper_cost",
    "operate_profit",
    "total_profit",
    "income_tax",
    "n_income",
    "n_income_attr_p",
    "basic_eps",
    "ebit",
]
INDICATOR_FIELDS = [
    "ts_code",
    "end_date",
    "ann_date",
    "eps",
    "bps",
    "roe",
    "roa",
    "netprofit_margin",
    "grossprofit_margin",
    "debt_to_assets",
    "or_yoy",
    "netprofit_yoy",
    "ocfps",
    "fcff",
]

# income(000001.SZ, period=20180630): the two rows, verbatim. `oper_cost` is null on both
# because a bank publishes no cost of sales; `ebit` is present on one row and absent on the
# other, which is `income`'s commonest disagreement (256 of its 259 differing pairs).
PINGAN_2018H1_FLAG0 = [
    "000001.SZ",
    "20180630",
    "20180816",
    "20180816",
    "0",
    57241000000.0,
    57241000000.0,
    None,
    17402000000.0,
    17367000000.0,
    3995000000.0,
    13372000000.0,
    13372000000.0,
    0.73,
    39700000000.0,
]
PINGAN_2018H1_FLAG1 = [*PINGAN_2018H1_FLAG0[:4], "1", *PINGAN_2018H1_FLAG0[5:14], None]

# income(000001.SZ, period=20060331): f_ann_date PRECEDES ann_date by a year. Under the rule
# that read revision_time straight off f_ann_date this row raised out of Timeline.
PINGAN_2006Q1 = [
    "000001.SZ",
    "20060331",
    "20070426",
    "20060426",
    "0",
    1596766603.0,
    1596766603.0,
    None,
    330829778.0,
    359738923.0,
    126555377.0,
    233183546.0,
    233183546.0,
    0.12,
    None,
]

# income(000001.SZ, period=20050630): f_ann_date FOLLOWS ann_date by ten months.
PINGAN_2005H1 = [
    "000001.SZ",
    "20050630",
    "20050819",
    "20060705",
    "0",
    4199083639.0,
    4199083639.0,
    None,
    1982690802.0,
    370120574.0,
    162633782.0,
    157834800.0,
    157834800.0,
    None,
    2814098601.0,
]

# fina_indicator(600519.SH, period=20180331): two rows, one field, sign flipped.
MOUTAI_2018Q1_A = [
    "600519.SH",
    "20180331",
    "20180428",
    6.77,
    79.5718,
    8.8887,
    9.0471,
    52.2756,
    91.3055,
    21.7665,
    31.2393,
    38.9309,
    3.9289,
    -966053502.6718,
]
MOUTAI_2018Q1_B = [*MOUTAI_2018Q1_A[:13], 843920834.0382]

BALANCE_FIELDS = [
    "ts_code",
    "end_date",
    "ann_date",
    "f_ann_date",
    "update_flag",
    "total_assets",
    "total_liab",
    "total_hldr_eqy_exc_min_int",
    "total_cur_assets",
    "total_cur_liab",
    "money_cap",
    "total_share",
]
# balancesheet(000002.SZ, period=20230630): only the share count differs between the versions.
VANKE_2023H1_FLAG0 = [
    "000002.SZ",
    "20230630",
    "20230831",
    "20230831",
    "0",
    1684196409372.7,
    1281551927215.46,
    249326669106.12,
    1325043809366.61,
    981909082942.54,
    122180878822.26,
    11630709471.0,
]
VANKE_2023H1_FLAG1 = [*VANKE_2023H1_FLAG0[:4], "1", *VANKE_2023H1_FLAG0[5:11], 11930709471.0]

CASHFLOW_FIELDS = [
    "ts_code",
    "end_date",
    "ann_date",
    "f_ann_date",
    "update_flag",
    "n_cashflow_act",
    "n_cashflow_inv_act",
    "n_cash_flows_fnc_act",
    "c_fr_sale_sg",
    "free_cashflow",
]
# cashflow(300002.SZ, period=20230630): free cash flow flips sign between the versions.
SHENZHOU_2023H1_FLAG0 = [
    "300002.SZ",
    "20230630",
    "20230829",
    "20230829",
    "0",
    395838995.7,
    -271171253.84,
    -68063597.74,
    2230094255.46,
    -294173456.01,
]

RESPONSES: dict[str, tuple[list[str], list[Any]]] = {
    INCOME_DATASET: (INCOME_FIELDS, PINGAN_2018H1_FLAG0),
    BALANCE_SHEET_DATASET: (BALANCE_FIELDS, VANKE_2023H1_FLAG0),
    CASH_FLOW_DATASET: (CASHFLOW_FIELDS, SHENZHOU_2023H1_FLAG0),
    FINANCIAL_INDICATOR_DATASET: (INDICATOR_FIELDS, MOUTAI_2018Q1_A),
}
"""One real row per dataset, so every parametrised case exercises its own projection rather
than borrowing `income`'s columns."""


def _response(
    fields: list[str], items: tuple[list[Any], ...], *, has_more: bool | None = False
) -> dict[str, Any]:
    data: dict[str, Any] = {"fields": list(fields), "items": [list(row) for row in items]}
    if has_more is not None:
        data[TUSHARE_RESPONSE_TRUNCATION_FLAG] = has_more
    return {"code": 0, "msg": "", "data": data}


def _provider(transport_factory: Any, response: dict[str, Any], *, clock: datetime) -> Any:
    transport = transport_factory(response)
    return (
        TushareProvider(token="secret-token", transport=transport, clock=lambda: clock),
        transport,
    )


def _descriptor(name: str) -> Any:
    (descriptor,) = (entry for entry in TUSHARE_DATASETS if entry.dataset == name)
    return descriptor


def _subjects(dataset: str, security: str, period_year: str = "2018") -> tuple[str, ...]:
    """`fina_indicator` names its report-period year as a second subject; the other three take
    the window from `as_of`. See `_financial_indicator_params`."""
    if dataset == FINANCIAL_INDICATOR_DATASET:
        return (security, period_year)
    return (security,)


# --------------------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("dataset", FINANCIAL_STATEMENT_DATASETS)
def test_a_request_without_a_security_is_refused_rather_than_sent(
    fake_tushare_transport, dataset: str
) -> None:
    """The endpoint answers a subject-less request with `code=50101, 必填参数, ts_code`. Sending
    it anyway would turn a caller's mistake into an upstream failure a retry loop might chase."""
    provider, transport = _provider(
        fake_tushare_transport, _response(INCOME_FIELDS, ()), clock=AS_OF
    )

    expected = (
        r"needs exactly two subjects"
        if dataset == FINANCIAL_INDICATOR_DATASET
        else r"requires exactly one ts_code per request"
    )
    with pytest.raises(ProviderFailure, match=expected):
        provider.fetch_panel(ProviderRequest(dataset=dataset, as_of=AS_OF))

    assert transport.payload is None


@pytest.mark.parametrize("dataset", FINANCIAL_STATEMENT_DATASETS)
def test_two_securities_are_refused_because_the_endpoint_answers_them_with_silence(
    fake_tushare_transport, dataset: str
) -> None:
    """`income(ts_code="000001.SZ,600000.SH")` returns **zero rows with `code=0` and
    `has_more=False`** -- indistinguishable from "neither security has ever filed". A refusal
    here is the only place that difference can still be seen."""
    provider, _ = _provider(fake_tushare_transport, _response(INCOME_FIELDS, ()), clock=AS_OF)

    expected = (
        r"second subject must be a four-digit report-period year"
        if dataset == FINANCIAL_INDICATOR_DATASET
        else r"answers a comma-joined list with zero rows"
    )
    with pytest.raises(ProviderFailure, match=expected):
        provider.fetch_panel(
            ProviderRequest(dataset=dataset, as_of=AS_OF, subjects=("000001.SZ", "600000.SH"))
        )


def test_the_request_window_is_one_calendar_year_of_the_named_security(
    fake_tushare_transport,
) -> None:
    provider, transport = _provider(
        fake_tushare_transport,
        _response(INCOME_FIELDS, (PINGAN_2018H1_FLAG0,)),
        clock=AS_OF,
    )

    provider.fetch_panel(
        ProviderRequest(
            dataset=INCOME_DATASET,
            as_of=datetime(2018, 12, 31, 12, 0, tzinfo=UTC),
            subjects=("000001.SZ",),
        )
    )

    assert transport.payload is not None
    assert transport.payload["params"] == {
        "ts_code": "000001.SZ",
        "start_date": "20180101",
        "end_date": "20181231",
    }


def test_the_window_year_is_asia_shanghais_rather_than_utcs(fake_tushare_transport) -> None:
    """2024-12-31 17:00Z is already 2025 in Shanghai; asking UTC would fetch the wrong year for
    every late-evening request on a year boundary."""
    provider, transport = _provider(
        fake_tushare_transport, _response(INCOME_FIELDS, ()), clock=AS_OF
    )

    provider.fetch_panel(
        ProviderRequest(
            dataset=INCOME_DATASET,
            as_of=datetime(2024, 12, 31, 17, 0, tzinfo=UTC),
            subjects=("000001.SZ",),
        )
    )

    assert transport.payload is not None
    assert transport.payload["params"]["start_date"] == "20250101"


def test_every_projected_column_is_requested_by_name_rather_than_defaulted() -> None:
    """The endpoint defaults are 85 / 152 / 97 / 108 columns. Naming the projection keeps the
    payload proportional to what is stored and makes the contract legible."""
    for dataset in FINANCIAL_STATEMENT_DATASETS:
        descriptor = _descriptor(dataset)
        requested = descriptor.response_fields.split(",")
        assert requested[0] == "ts_code"
        assert set(descriptor.required_response_fields) == set(requested)
        stored = {column.name for column in descriptor.panel_columns}
        assert stored == set(statement_panel_columns(dataset))


def test_the_token_never_reaches_the_params(fake_tushare_transport) -> None:
    provider, transport = _provider(
        fake_tushare_transport,
        _response(INCOME_FIELDS, (PINGAN_2018H1_FLAG0,)),
        clock=AS_OF,
    )

    provider.fetch_panel(
        ProviderRequest(dataset=INCOME_DATASET, as_of=AS_OF, subjects=("000001.SZ",))
    )

    assert transport.payload is not None
    assert "secret-token" not in str(transport.payload["params"])
    assert transport.payload["token"] == "secret-token"


# --------------------------------------------------------------------------------------
# The cap and the flag
# --------------------------------------------------------------------------------------


def test_the_measured_cap_is_one_hundred_and_only_two_endpoints_carry_it() -> None:
    """`balancesheet(ts_code=000001.SZ)` returns exactly 100 rows with `has_more=True` and
    `limit=101` / `500` / `5000` return the same 100; asking for 6 columns instead of 152 also
    returns 100, so it is a row cap. `income` returned **127** rows with `has_more=False` on the
    same security and `cashflow` 90, so their cap is above what a security can produce."""
    assert TUSHARE_FINANCIAL_ROW_CAP == 100
    capped = {
        dataset
        for dataset in FINANCIAL_STATEMENT_DATASETS
        if _descriptor(dataset).max_rows_per_response is not None
    }
    assert capped == {BALANCE_SHEET_DATASET, FINANCIAL_INDICATOR_DATASET}
    for dataset in capped:
        assert _descriptor(dataset).max_rows_per_response == TUSHARE_FINANCIAL_ROW_CAP


def test_a_response_at_the_cap_is_refused_even_when_the_flag_says_complete(
    fake_tushare_transport,
) -> None:
    """A response *at* the cap cannot be told apart from one the cap truncated, and the rows it
    drops are the oldest -- which is a security's early filings, not its recent ones."""
    provider, _ = _provider(
        fake_tushare_transport,
        _response(
            INDICATOR_FIELDS,
            tuple([MOUTAI_2018Q1_A] * TUSHARE_FINANCIAL_ROW_CAP),
            has_more=False,
        ),
        clock=AS_OF,
    )

    with pytest.raises(ProviderFailure, match=r"measured per-response cap of 100"):
        provider.fetch_panel(
            ProviderRequest(
                dataset=FINANCIAL_INDICATOR_DATASET,
                as_of=AS_OF,
                subjects=("600519.SH", "2018"),
            )
        )


def test_a_response_one_row_under_the_cap_is_accepted(fake_tushare_transport) -> None:
    """The boundary, from the other side: 99 rows is a complete year, not a truncated one."""
    provider, _ = _provider(
        fake_tushare_transport,
        _response(
            INDICATOR_FIELDS,
            tuple([MOUTAI_2018Q1_A] * (TUSHARE_FINANCIAL_ROW_CAP - 1)),
            has_more=False,
        ),
        clock=AS_OF,
    )

    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=FINANCIAL_INDICATOR_DATASET,
            as_of=AS_OF,
            subjects=("600519.SH", "2018"),
        )
    )

    assert batch.row_count == TUSHARE_FINANCIAL_ROW_CAP - 1


@pytest.mark.parametrize("dataset", FINANCIAL_STATEMENT_DATASETS)
def test_a_response_without_the_truncation_flag_is_refused_on_all_four(
    fake_tushare_transport, dataset: str
) -> None:
    """For `income` and `cashflow` the flag is the only witness there is; for the other two it
    guards a cap that drops the oldest filings, which is `adj_factor`'s situation rather than
    `daily`'s."""
    fields, row = RESPONSES[dataset]
    provider, _ = _provider(
        fake_tushare_transport, _response(fields, (row,), has_more=None), clock=AS_OF
    )

    with pytest.raises(ProviderFailure, match=r"carries no has_more flag"):
        provider.fetch_panel(
            ProviderRequest(dataset=dataset, as_of=AS_OF, subjects=_subjects(dataset, row[0]))
        )


# --------------------------------------------------------------------------------------
# The clock
# --------------------------------------------------------------------------------------


def test_a_filing_is_dated_at_its_announcement_and_not_at_its_period(
    fake_tushare_transport,
) -> None:
    provider, _ = _provider(
        fake_tushare_transport,
        _response(INCOME_FIELDS, (PINGAN_2018H1_FLAG0,)),
        clock=AS_OF,
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INCOME_DATASET, as_of=AS_OF, subjects=("000001.SZ",))
    )

    (available,) = batch.timeline.available_time
    (event,) = batch.timeline.event_time
    assert available.astimezone(SHANGHAI) == datetime(2018, 8, 16, 0, 0, tzinfo=SHANGHAI)
    assert event == available
    period = next(column for column in batch.columns if column.name == "report_period")
    assert period.values == ("2018-06-30",)


def test_the_two_rows_of_a_corrected_pair_get_byte_equal_clocks(
    fake_tushare_transport,
) -> None:
    """The decision `V2-P1-011` made rather than the gap it inherited: both rows carry the same
    `ann_date` and the same `f_ann_date`, so there is no instant to date the correction at, and
    inventing one would be worse than carrying both versions into the read."""
    provider, _ = _provider(
        fake_tushare_transport,
        _response(INCOME_FIELDS, (PINGAN_2018H1_FLAG0, PINGAN_2018H1_FLAG1)),
        clock=AS_OF,
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INCOME_DATASET, as_of=AS_OF, subjects=("000001.SZ",))
    )

    assert batch.row_count == 2
    assert batch.timeline.row_timeline(0) == batch.timeline.row_timeline(1)
    ebit = next(column for column in batch.columns if column.name == "ebit")
    assert set(ebit.values) == {39700000000.0, None}


def test_a_first_announcement_before_the_announcement_no_longer_raises(
    fake_tushare_transport,
) -> None:
    """`000001.SZ`'s 2006Q1 `income` row carries `ann_date=20070426` and
    `f_ann_date=20060426`. Reading `revision_time` straight off `f_ann_date` put it a year
    before `available_time`, which `Timeline` refuses -- so this real row could not be fetched
    at all. `max` leaves it with no revision rather than an impossible one."""
    provider, _ = _provider(
        fake_tushare_transport, _response(INCOME_FIELDS, (PINGAN_2006Q1,)), clock=AS_OF
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INCOME_DATASET, as_of=AS_OF, subjects=("000001.SZ",))
    )

    (revision,) = batch.timeline.revision_time
    (available,) = batch.timeline.available_time
    assert revision == available
    assert available.astimezone(SHANGHAI).date().isoformat() == "2007-04-26"


def test_a_first_announcement_after_the_announcement_is_still_a_revision(
    fake_tushare_transport,
) -> None:
    """The direction the old rule was written for, unchanged: `20050819` announced,
    `20060705` re-announced, so `revision_time` runs ten months past `available_time` and
    `PartitionCoverage.revised_row_count` counts the row."""
    provider, _ = _provider(
        fake_tushare_transport, _response(INCOME_FIELDS, (PINGAN_2005H1,)), clock=AS_OF
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INCOME_DATASET, as_of=AS_OF, subjects=("000001.SZ",))
    )

    (revision,) = batch.timeline.revision_time
    (available,) = batch.timeline.available_time
    assert revision > available
    assert revision.astimezone(SHANGHAI).date().isoformat() == "2006-07-05"


def test_a_filing_announced_after_the_as_of_is_dropped_rather_than_stored(
    fake_tushare_transport,
) -> None:
    """`fina_indicator`'s window filters the *period*, so a current-year request routinely
    names periods whose annual report lands next spring."""
    provider, _ = _provider(
        fake_tushare_transport,
        _response(INDICATOR_FIELDS, (MOUTAI_2018Q1_A,)),
        clock=datetime(2018, 4, 27, 12, 0, tzinfo=UTC),
    )

    batch = provider.fetch_panel(
        ProviderRequest(
            dataset=FINANCIAL_INDICATOR_DATASET,
            as_of=datetime(2018, 4, 27, 12, 0, tzinfo=UTC),
            subjects=("600519.SH", "2018"),
        )
    )

    assert batch.status == "no_data"
    assert batch.no_data_reason is not None
    assert "not yet knowable" in batch.no_data_reason


# --------------------------------------------------------------------------------------
# The stored shape
# --------------------------------------------------------------------------------------


def test_a_null_value_column_is_stored_as_null_rather_than_refused(
    fake_tushare_transport,
) -> None:
    """`oper_cost` is `None` on all 127 of `000001.SZ`'s `income` rows because a bank publishes
    no cost of sales. Tushare's field set is the union over `comp_type` 1..4, so a null here is
    "this company type does not publish this line" and a zero would be a fabricated number."""
    provider, _ = _provider(
        fake_tushare_transport,
        _response(INCOME_FIELDS, (PINGAN_2018H1_FLAG0,)),
        clock=AS_OF,
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INCOME_DATASET, as_of=AS_OF, subjects=("000001.SZ",))
    )

    oper_cost = next(column for column in batch.columns if column.name == "oper_cost")
    assert oper_cost.values == (None,)


def test_the_report_period_is_stored_under_its_own_name_and_as_an_iso_date(
    fake_tushare_transport,
) -> None:
    provider, _ = _provider(
        fake_tushare_transport,
        _response(INCOME_FIELDS, (PINGAN_2018H1_FLAG0,)),
        clock=AS_OF,
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INCOME_DATASET, as_of=AS_OF, subjects=("000001.SZ",))
    )

    period = next(column for column in batch.columns if column.name == "report_period")
    assert period.values == ("2018-06-30",)
    assert "end_date" not in {column.name for column in batch.columns}


def test_fina_indicator_stores_no_revision_label_because_it_has_none() -> None:
    stored = {column.name for column in _descriptor(FINANCIAL_INDICATOR_DATASET).panel_columns}
    assert "update_flag" not in stored
    assert "f_ann_date" not in stored
    for dataset in (INCOME_DATASET, BALANCE_SHEET_DATASET, CASH_FLOW_DATASET):
        stored = {column.name for column in _descriptor(dataset).panel_columns}
        assert {"update_flag", "f_ann_date"} <= stored


def test_a_response_missing_a_projected_column_is_refused_by_name(
    fake_tushare_transport,
) -> None:
    narrowed = [name for name in INCOME_FIELDS if name != "ebit"]
    row = [
        value
        for name, value in zip(INCOME_FIELDS, PINGAN_2018H1_FLAG0, strict=True)
        if name != "ebit"
    ]
    provider, _ = _provider(fake_tushare_transport, _response(narrowed, (row,)), clock=AS_OF)

    with pytest.raises(ProviderFailure, match=r"ebit"):
        provider.fetch_panel(
            ProviderRequest(dataset=INCOME_DATASET, as_of=AS_OF, subjects=("000001.SZ",))
        )


def test_all_four_serve_the_evidence_plane_unlike_the_registry_datasets(
    fake_tushare_transport,
) -> None:
    """A statement row states facts that all became knowable at one instant -- its own
    announcement -- so `fetch()` can hand back the response row verbatim under a single clock.
    That is what `stock_basic`, `namechange`, `index_member_all` and `index_classify` cannot do,
    and it makes these the first real production consumers of `ClockStrategy.announcement`."""
    for dataset in FINANCIAL_STATEMENT_DATASETS:
        assert _descriptor(dataset).serves_evidence_plane

    provider, _ = _provider(
        fake_tushare_transport,
        _response(INCOME_FIELDS, (PINGAN_2018H1_FLAG0, PINGAN_2018H1_FLAG1)),
        clock=AS_OF,
    )

    batch = provider.fetch(
        ProviderRequest(dataset=INCOME_DATASET, as_of=AS_OF, subjects=("000001.SZ",))
    )

    first, second = batch.records
    assert first.timeline == second.timeline
    assert first.record_id != second.record_id
    assert first.payload["ebit"] == 39700000000.0
    assert second.payload["ebit"] is None


def test_the_indicator_period_year_is_a_subject_rather_than_a_function_of_as_of(
    fake_tushare_transport,
) -> None:
    """The hole this closes: `start_date`/`end_date` filter `end_date` here while `as_of` bounds
    availability, so a window taken from `as_of`'s year can never contain a report announced
    after its own period. `001278.SZ`'s 2018 annual was announced 2022-01-06."""
    provider, transport = _provider(
        fake_tushare_transport, _response(INDICATOR_FIELDS, ()), clock=AS_OF
    )

    provider.fetch_panel(
        ProviderRequest(
            dataset=FINANCIAL_INDICATOR_DATASET, as_of=AS_OF, subjects=("001278.SZ", "2018")
        )
    )

    assert transport.payload is not None
    assert transport.payload["params"] == {
        "ts_code": "001278.SZ",
        "start_date": "20180101",
        "end_date": "20181231",
    }


@pytest.mark.parametrize("period_year", ["18", "twenty", "2018-01", ""])
def test_an_indicator_period_year_that_is_not_four_digits_is_refused(
    fake_tushare_transport, period_year: str
) -> None:
    provider, _ = _provider(fake_tushare_transport, _response(INDICATOR_FIELDS, ()), clock=AS_OF)

    with pytest.raises(
        ProviderFailure, match=r"second subject must be a four-digit report-period year"
    ):
        provider.fetch_panel(
            ProviderRequest(
                dataset=FINANCIAL_INDICATOR_DATASET,
                as_of=AS_OF,
                subjects=("001278.SZ", period_year),
            )
        )


def test_the_provenance_uri_spans_the_announcements_rather_than_the_periods(
    fake_tushare_transport,
) -> None:
    """`date_field` is the announcement, so the batch's `source_uri` names the window a reader
    would have to re-request to reprove it. Dating the rows at `end_date` instead would put a
    2017 period range on a batch of 2018 disclosures, and it would sort the partition by fiscal
    quarter rather than by the order the market learned things."""
    provider, _ = _provider(
        fake_tushare_transport,
        _response(
            INCOME_FIELDS,
            (
                # end_date 20171231, announced 20180315; and end_date 20180331, announced
                # 20180420 -- so the two ranges are genuinely different.
                [
                    *PINGAN_2018H1_FLAG0[:1],
                    "20171231",
                    "20180315",
                    "20180315",
                    *PINGAN_2018H1_FLAG0[4:],
                ],
                [
                    *PINGAN_2018H1_FLAG0[:1],
                    "20180331",
                    "20180420",
                    "20180420",
                    *PINGAN_2018H1_FLAG0[4:],
                ],
            ),
        ),
        clock=AS_OF,
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INCOME_DATASET, as_of=AS_OF, subjects=("000001.SZ",))
    )

    assert batch.source_uri == "tushare://income/000001.SZ/20180315-20180420"
