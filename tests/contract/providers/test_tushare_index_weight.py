"""`index_weight` on the descriptor table (`V2-P1-009`): the request, the cap, the projection.

Every response body here is real, captured live on 2026-08-09; the transport is doubled so the
suite never touches the network.

The three things this file is really about:

- **One index per request, one calendar month per request.** The endpoint ignores a
  comma-joined `index_code` (it returns zero rows) and publishes exactly once a month, so a
  month window is the request shape that maps one-to-one onto a publication.
- **The cap is 7,000 and it is this endpoint's own.** `limit` moves it in neither direction.
- **The truncation flag is demanded**, which makes `index_weight` the fourth descriptor to do
  so and the first whose second witness is a domain-level checksum rather than a row count.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from openalpha_cn.domain.index_membership import (
    CSI300_INDEX_CODE,
    CSI500_INDEX_CODE,
    INDEX_CONSTITUENT_COLUMN,
    INDEX_PUBLICATION_DATE_COLUMN,
    INDEX_WEIGHT_COLUMN,
    INDEX_WEIGHT_DATASET,
)
from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.tushare import (
    TUSHARE_DATASETS,
    TUSHARE_INDEX_WEIGHT_ROW_CAP,
    TUSHARE_RESPONSE_TRUNCATION_FLAG,
    TushareProvider,
)

RESPONSE_FIELDS = ["index_code", "con_code", "trade_date", "weight"]

CSI300_20240628 = (
    # The four heaviest names and the lightest, from the real 300-row response.
    ("600519.SH", 5.19),
    ("300750.SZ", 2.676),
    ("601318.SH", 2.507),
    ("600036.SH", 2.383),
    ("001289.SZ", 0.015),
)

CSI500_20181228 = (
    # Three real rows of the 500-row publication, including the constituent whose registry
    # delist_date is that very day -- see `domain/index_membership.py`'s named limitation.
    ("600201.SH", 0.646),
    ("000656.SZ", 0.549),
    ("600270.SH", 0.253),
)


def _response(
    rows: tuple[tuple[str, float], ...],
    *,
    index_code: str,
    trade_date: str,
    has_more: bool | None = False,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "fields": list(RESPONSE_FIELDS),
        "items": [[index_code, con_code, trade_date, weight] for con_code, weight in rows],
    }
    if has_more is not None:
        data[TUSHARE_RESPONSE_TRUNCATION_FLAG] = has_more
    return {"code": 0, "msg": "", "data": data}


def _provider(
    fake_tushare_transport: Any, response: dict[str, Any], *, clock: datetime
) -> tuple[TushareProvider, Any]:
    transport = fake_tushare_transport(response)
    return (
        TushareProvider(token="secret-token", transport=transport, clock=lambda: clock),
        transport,
    )


def _descriptor() -> Any:
    (descriptor,) = (entry for entry in TUSHARE_DATASETS if entry.dataset == INDEX_WEIGHT_DATASET)
    return descriptor


# --------------------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------------------


def test_the_request_asks_for_one_index_and_one_calendar_month(fake_tushare_transport) -> None:
    """A month window is the request shape that maps one-to-one onto a publication.

    The endpoint publishes on the last open session of each month, and a caller who had to name
    that date would have to derive it from a calendar first. Measured over 633 publications: no
    month inside an index's life carries none and no month carries two, so
    `start_date=YYYYMM01 / end_date=<month end>` returns exactly one.
    """
    as_of = datetime(2024, 6, 30, 12, 0, tzinfo=UTC)
    provider, transport = _provider(
        fake_tushare_transport,
        _response(CSI300_20240628, index_code=CSI300_INDEX_CODE, trade_date="20240628"),
        clock=as_of,
    )

    provider.fetch_panel(
        ProviderRequest(dataset=INDEX_WEIGHT_DATASET, as_of=as_of, subjects=(CSI300_INDEX_CODE,))
    )

    assert transport.payload is not None
    assert transport.payload["params"] == {
        "index_code": CSI300_INDEX_CODE,
        "start_date": "20240601",
        "end_date": "20240630",
    }
    assert transport.payload["api_name"] == INDEX_WEIGHT_DATASET
    assert transport.payload["token"] == "secret-token"


def test_the_month_window_ends_on_the_real_last_day_of_february(fake_tushare_transport) -> None:
    """A leap February is 29 days and a naive `day=30` would ask for a date that does not
    exist; a fixed `day=31` would ask the endpoint for 2024-02-31."""
    as_of = datetime(2024, 2, 29, 12, 0, tzinfo=UTC)
    provider, transport = _provider(
        fake_tushare_transport,
        _response(CSI300_20240628, index_code=CSI300_INDEX_CODE, trade_date="20240229"),
        clock=as_of,
    )

    provider.fetch_panel(
        ProviderRequest(dataset=INDEX_WEIGHT_DATASET, as_of=as_of, subjects=(CSI300_INDEX_CODE,))
    )

    assert transport.payload["params"]["start_date"] == "20240201"
    assert transport.payload["params"]["end_date"] == "20240229"


def test_the_month_is_resolved_in_shanghai_rather_than_in_utc(fake_tushare_transport) -> None:
    """2024-12-31 17:00Z is already 2025-01-01 in Shanghai.

    `_trade_cal_params` and `_namechange_params` take the same care for the same reason: asking
    UTC would fetch the wrong period for every late-evening request on a month boundary, and
    here it would file a January publication under December's window.
    """
    as_of = datetime(2024, 12, 31, 17, 0, tzinfo=UTC)
    # An empty publication set: January's has not happened yet at this instant, which is what
    # the endpoint really answers here. The assertion is about the window, not the rows.
    provider, transport = _provider(
        fake_tushare_transport,
        _response((), index_code=CSI300_INDEX_CODE, trade_date="20250127"),
        clock=as_of,
    )

    provider.fetch_panel(
        ProviderRequest(dataset=INDEX_WEIGHT_DATASET, as_of=as_of, subjects=(CSI300_INDEX_CODE,))
    )

    assert transport.payload["params"]["start_date"] == "20250101"
    assert transport.payload["params"]["end_date"] == "20250131"


def test_a_request_with_no_index_is_refused_rather_than_defaulted(fake_tushare_transport) -> None:
    """`trade_cal` defaults its missing subject to SSE because the endpoint has a default and
    it was measured; this endpoint has none -- a bare request is not "the market"."""
    as_of = datetime(2024, 6, 15, 12, 0, tzinfo=UTC)
    provider, _ = _provider(fake_tushare_transport, {}, clock=as_of)

    with pytest.raises(ProviderFailure, match="serves one index per request"):
        provider.fetch_panel(ProviderRequest(dataset=INDEX_WEIGHT_DATASET, as_of=as_of))


def test_two_indices_in_one_request_are_refused_because_the_endpoint_drops_them(
    fake_tushare_transport,
) -> None:
    """Measured: `index_code="000300.SH,000905.SH"` returns **zero** rows, not the union.

    So comma-joining would produce an empty `no_data` batch that reads as "the publisher has
    not published this month" -- a silent, plausible, and completely wrong answer.
    """
    as_of = datetime(2024, 6, 15, 12, 0, tzinfo=UTC)
    provider, _ = _provider(fake_tushare_transport, {}, clock=as_of)

    with pytest.raises(ProviderFailure, match="serves one index per request"):
        provider.fetch_panel(
            ProviderRequest(
                dataset=INDEX_WEIGHT_DATASET,
                as_of=as_of,
                subjects=(CSI300_INDEX_CODE, CSI500_INDEX_CODE),
            )
        )


# --------------------------------------------------------------------------------------
# The projection
# --------------------------------------------------------------------------------------


def test_a_real_publication_decodes_into_the_panel_projection(fake_tushare_transport) -> None:
    as_of = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        fake_tushare_transport,
        _response(CSI300_20240628, index_code=CSI300_INDEX_CODE, trade_date="20240628"),
        clock=as_of,
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INDEX_WEIGHT_DATASET, as_of=as_of, subjects=(CSI300_INDEX_CODE,))
    )

    assert batch.status == "success"
    assert set(batch.subjects) == {CSI300_INDEX_CODE}
    columns = {column.name: column.values for column in batch.columns}
    assert set(columns) == {
        INDEX_PUBLICATION_DATE_COLUMN,
        INDEX_CONSTITUENT_COLUMN,
        INDEX_WEIGHT_COLUMN,
    }
    assert set(columns[INDEX_PUBLICATION_DATE_COLUMN]) == {"2024-06-28"}
    assert columns[INDEX_CONSTITUENT_COLUMN][0] == "600519.SH"
    assert columns[INDEX_WEIGHT_COLUMN][0] == pytest.approx(5.19)
    assert columns[INDEX_WEIGHT_COLUMN][-1] == pytest.approx(0.015)


def test_the_index_is_the_subject_and_the_constituent_is_a_column(
    fake_tushare_transport,
) -> None:
    """The choice `panel_ingest`'s overwrite guard rests on -- see
    `domain/index_membership.py::INDEX_WEIGHT_PANEL_COLUMNS`."""
    descriptor = _descriptor()
    assert descriptor.subject_field == "index_code"
    assert INDEX_CONSTITUENT_COLUMN in {spec.name for spec in descriptor.panel_columns}


def test_the_publication_date_is_renamed_on_the_way_into_the_panel() -> None:
    """`trade_date` in the response, `publication_date` in the panel.

    `adj_factor` renames the same column to `factor_date` for the same reason: four datasets
    already store a `trade_date` meaning "the session this row describes", and this one means
    "the session whose close the snapshot was computed from".
    """
    (spec,) = (
        spec for spec in _descriptor().panel_columns if spec.name == INDEX_PUBLICATION_DATE_COLUMN
    )
    assert spec.source_field == "trade_date"


def test_a_response_that_lost_the_weight_column_fails_by_name(fake_tushare_transport) -> None:
    as_of = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
    response = {
        "code": 0,
        "msg": "",
        "data": {
            "fields": ["index_code", "con_code", "trade_date"],
            "items": [[CSI300_INDEX_CODE, "600519.SH", "20240628"]],
            TUSHARE_RESPONSE_TRUNCATION_FLAG: False,
        },
    }
    provider, _ = _provider(fake_tushare_transport, response, clock=as_of)

    with pytest.raises(ProviderFailure, match="no weight column"):
        provider.fetch_panel(
            ProviderRequest(
                dataset=INDEX_WEIGHT_DATASET, as_of=as_of, subjects=(CSI300_INDEX_CODE,)
            )
        )


@pytest.mark.parametrize("bad", [0.0, -0.5, "5.19", True, None])
def test_a_weight_that_cannot_be_a_share_of_an_index_is_refused(
    fake_tushare_transport, bad: object
) -> None:
    """Zero is refused with the negatives, and it is the interesting one.

    336,298 real constituent rows carry a weight between 0.007 and 7.745 and not one zero, so a
    zero is a fault -- and the dangerous kind, because it leaves the security in the membership
    while removing it from every weighted sum. `True` is refused because `isinstance(True, int)`
    is `True` and a bool would otherwise arrive as a 1% weight.
    """
    as_of = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
    response = {
        "code": 0,
        "msg": "",
        "data": {
            "fields": list(RESPONSE_FIELDS),
            "items": [[CSI300_INDEX_CODE, "600519.SH", "20240628", bad]],
            TUSHARE_RESPONSE_TRUNCATION_FLAG: False,
        },
    }
    provider, _ = _provider(fake_tushare_transport, response, clock=as_of)

    with pytest.raises(ProviderFailure, match="weight"):
        provider.fetch_panel(
            ProviderRequest(
                dataset=INDEX_WEIGHT_DATASET, as_of=as_of, subjects=(CSI300_INDEX_CODE,)
            )
        )


def test_a_publication_that_is_not_yet_knowable_is_not_absent(fake_tushare_transport) -> None:
    """The two empty results this provider keeps apart, on this dataset.

    A publication dated at the close of a session after the request's `as_of` is filtered out,
    and the batch says so -- "served but not yet knowable" rather than "the publisher has not
    published it".
    """
    as_of = datetime(2024, 6, 20, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        fake_tushare_transport,
        _response(CSI300_20240628, index_code=CSI300_INDEX_CODE, trade_date="20240628"),
        clock=datetime(2024, 7, 1, 12, 0, tzinfo=UTC),
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INDEX_WEIGHT_DATASET, as_of=as_of, subjects=(CSI300_INDEX_CODE,))
    )

    assert batch.status == "no_data"
    assert batch.no_data_reason is not None
    assert "not yet" in batch.no_data_reason


def test_a_real_constituent_the_registry_calls_delisted_still_decodes(
    fake_tushare_transport,
) -> None:
    """The provider stores what the publisher published; the registry disagreement is a fact
    for `domain/index_membership.py::constituent_listing_report` to report, not a parse error.

    `600270.SH` 外运发展 is in `000905.SH`'s real 2018-12-28 publication at weight 0.253 while
    `stock_basic` gives it `delist_date=20181228`.
    """
    as_of = datetime(2019, 1, 2, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        fake_tushare_transport,
        _response(CSI500_20181228, index_code=CSI500_INDEX_CODE, trade_date="20181228"),
        clock=as_of,
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INDEX_WEIGHT_DATASET, as_of=as_of, subjects=(CSI500_INDEX_CODE,))
    )

    columns = {column.name: column.values for column in batch.columns}
    position = columns[INDEX_CONSTITUENT_COLUMN].index("600270.SH")
    assert columns[INDEX_WEIGHT_COLUMN][position] == pytest.approx(0.253)


# --------------------------------------------------------------------------------------
# Completeness
# --------------------------------------------------------------------------------------


def test_the_cap_is_seven_thousand_and_a_response_at_it_is_refused(
    fake_tushare_transport,
) -> None:
    """Measured on 2026-08-09, and it is the endpoint's own rather than the request's.

    `index_weight(000852.SH, 20230101..20231231)` should hold 12,000 rows and returns exactly
    **7,000** with `has_more=True`. `limit=5000`, `8000`, `10000`, `12000` and `20000` all
    return the same 7,000 -- this endpoint ignores `limit` in both directions, which no other
    row in the table does.
    """
    assert TUSHARE_INDEX_WEIGHT_ROW_CAP == 7000
    as_of = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
    rows = tuple((f"{600000 + index}.SH", 0.014) for index in range(7000))
    provider, _ = _provider(
        fake_tushare_transport,
        _response(rows, index_code=CSI300_INDEX_CODE, trade_date="20240628"),
        clock=as_of,
    )

    with pytest.raises(ProviderFailure, match="measured per-response cap of 7000"):
        provider.fetch_panel(
            ProviderRequest(
                dataset=INDEX_WEIGHT_DATASET, as_of=as_of, subjects=(CSI300_INDEX_CODE,)
            )
        )


def test_a_response_with_no_truncation_flag_is_refused(fake_tushare_transport) -> None:
    """Demanded, unlike `daily`'s and `suspend_d`'s, because of what a dropped row costs here.

    The cap drops the **oldest** rows and can split a publication rather than only dropping
    whole ones: `index_weight(000300.SH, 20100101..20231231)` returns 7,000 rows over 24 dates
    whose oldest, 2022-01-28, carries 100 of its 300 names. A partial publication is silently
    *wrong* rather than short -- the missing constituents simply are not in the index as far as
    every downstream question is concerned -- which is `adj_factor`'s situation, not `daily`'s.
    """
    as_of = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        fake_tushare_transport,
        _response(
            CSI300_20240628, index_code=CSI300_INDEX_CODE, trade_date="20240628", has_more=None
        ),
        clock=as_of,
    )

    with pytest.raises(ProviderFailure, match="carries no has_more flag"):
        provider.fetch_panel(
            ProviderRequest(
                dataset=INDEX_WEIGHT_DATASET, as_of=as_of, subjects=(CSI300_INDEX_CODE,)
            )
        )


def test_the_row_cap_is_stated_per_endpoint_and_this_one_has_room() -> None:
    """One publication is 300, 500 or 1,000 rows against a 7,000-row cap.

    A whole month window therefore uses at most a seventh of it, which is the widest headroom in
    the table -- `stk_limit` sits 67 rows under its 7,800 and `daily` 465 under its 6,000. The
    number that would have to change for this to bind is the constituent count, which is set by
    the index's own definition rather than by market growth, so this cap is not on the same
    clock as the cross-section ones.
    """
    descriptor = _descriptor()
    assert descriptor.max_rows_per_response == TUSHARE_INDEX_WEIGHT_ROW_CAP
    assert descriptor.requires_truncation_flag is True
    largest_publication = 1000  # 000852.SH, on all 142 of its publications
    assert TUSHARE_INDEX_WEIGHT_ROW_CAP - largest_publication == 6000


def test_availability_is_dated_at_the_publication_session_close(fake_tushare_transport) -> None:
    """`ClockStrategy.daily_close`, and the module's limitations say why that may be one
    session early rather than late -- the response carries no announcement column."""
    as_of = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
    provider, _ = _provider(
        fake_tushare_transport,
        _response(CSI300_20240628, index_code=CSI300_INDEX_CODE, trade_date="20240628"),
        clock=as_of,
    )

    batch = provider.fetch_panel(
        ProviderRequest(dataset=INDEX_WEIGHT_DATASET, as_of=as_of, subjects=(CSI300_INDEX_CODE,))
    )

    available = set(batch.timeline.available_time)
    assert len(available) == 1
    # Normalised to UTC by the batch contract; 08:30Z is 16:30 Asia/Shanghai.
    assert available.pop().isoformat() == "2024-06-28T08:30:00+00:00"
    assert {instant.isoformat() for instant in batch.timeline.event_time} == {
        "2024-06-28T07:00:00+00:00"
    }
