"""The whole-market route to the four statement datasets (`V2-P6-002`).

`income`, `balancesheet`, `cashflow` and `fina_indicator` used to be reachable only one security
at a time, because their per-security endpoints require `ts_code`. Tushare's `*_vip` endpoints
answer the same query for the whole market, and a live probe on 2026-09-26 compared 78
security-windows of their rows against the per-security rows and found them identical over the
descriptor's `response_fields` (`.superpowers/sdd/p6-vip-probe.md`). So the sweep is a second
*route* to the same dataset: same projection, same clock, same stored names.

It is **never paged**. `offset` paging on these endpoints was measured unsound -- two pages of a
closed month served 239 rows twice and 239 never -- so every response is judged by the one-shot
completeness witnesses, and an announcement window that does not fit is halved instead.

Nothing here touches the network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from openalpha_cn.domain.financial_statements import (
    BALANCE_SHEET_DATASET,
    CASH_FLOW_DATASET,
    FINANCIAL_INDICATOR_DATASET,
    FINANCIAL_STATEMENT_DATASETS,
    INCOME_DATASET,
    STATEMENT_DATA_COLUMNS,
)
from openalpha_cn.providers import tushare
from openalpha_cn.providers.base import ProviderFailure, ProviderRequest
from openalpha_cn.providers.tushare import (
    TUSHARE_DATASETS,
    TUSHARE_STATEMENT_SWEEPS,
    TushareProvider,
    TushareResponseTruncated,
    statement_sweep_windows,
)

TOKEN = "sweep-token-never-printed-5521"
CLOCK = datetime(2026, 9, 26, 4, 0, tzinfo=UTC)


def _fields(dataset: str) -> list[str]:
    keys = ["ts_code", "end_date", "ann_date"]
    if dataset != FINANCIAL_INDICATOR_DATASET:
        keys.extend(["f_ann_date", "update_flag"])
    return [*keys, *STATEMENT_DATA_COLUMNS[dataset]]


def _row(dataset: str, code: str, period: str, announced: str, value: float) -> list[Any]:
    keys: list[Any] = [code, period, announced]
    if dataset != FINANCIAL_INDICATOR_DATASET:
        keys.extend([announced, "1"])
    return [*keys, *([value] * len(STATEMENT_DATA_COLUMNS[dataset]))]


class CappedMarket:
    """Serves a fixed market and caps every response the way the live endpoints do.

    A window's rows past `cap` are withheld and `has_more` says so -- the measured behaviour of
    every endpoint in this table. `limit`/`offset` are recorded if sent and otherwise ignored,
    because nothing may send them. `static` answers every request with one body regardless.
    """

    def __init__(
        self,
        dataset: str,
        rows: list[list[Any]],
        *,
        cap: int,
        static: dict[str, Any] | None = None,
        drops_from_lists: bool = False,
    ) -> None:
        self.dataset = dataset
        self.fields = _fields(dataset)
        self.rows = rows
        self.cap = cap
        self.static = static
        self.drops_from_lists = drops_from_lists
        """Answer every comma-joined `ts_code` list one row short: the silent short answer the
        per-security endpoints give a list (zero rows, `code=0`), in its mildest form."""
        self.payloads: list[dict[str, Any]] = []

    def post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        if self.static is not None:
            return self.static
        params = payload["params"]
        if "period" in params:
            window = [row for row in self.rows if row[1] == params["period"]]
        else:
            window = [
                row for row in self.rows if params["start_date"] <= row[2] <= params["end_date"]
            ]
        if "ts_code" in params:
            # Measured on the VIP endpoints (2026-09-26/27): a comma-joined list of up to 1,000
            # codes filters the window to exactly those securities' rows.
            listed = set(str(params["ts_code"]).split(","))
            window = [row for row in window if row[0] in listed]
            if self.drops_from_lists and window:
                window = window[1:]
        return {
            "code": 0,
            "msg": "",
            "data": {
                "fields": self.fields,
                "items": window[: self.cap],
                "has_more": len(window) > self.cap,
            },
        }


def _cap(monkeypatch: pytest.MonkeyPatch, dataset: str, cap: int) -> None:
    """Shrink one sweep's measured cap so a handful of rows reaches it.

    A test seam rather than a production knob: the real caps are measured per endpoint and
    thousands of rows wide, and a fixture that size would make the narrowing assertions about
    volume instead of about the windows.
    """
    narrowed = tushare._TUSHARE_SWEEPS_BY_NAME[dataset].model_copy(
        update={"max_rows_per_response": cap}
    )
    monkeypatch.setitem(tushare._TUSHARE_SWEEPS_BY_NAME, dataset, narrowed)


def _provider(transport: CappedMarket) -> TushareProvider:
    return TushareProvider(token=TOKEN, transport=transport, clock=lambda: CLOCK)


def _request(dataset: str, window: str) -> ProviderRequest:
    return ProviderRequest(dataset=dataset, as_of=CLOCK, subjects=(window,))


def _windows(transport: CappedMarket) -> list[tuple[str, str]]:
    return [
        (str(entry["params"]["start_date"]), str(entry["params"]["end_date"]))
        for entry in transport.payloads
    ]


# --- the table ---------------------------------------------------------------------------------


@pytest.mark.parametrize("dataset", FINANCIAL_STATEMENT_DATASETS)
def test_every_statement_dataset_has_a_sweep_that_stores_the_same_dataset(dataset: str) -> None:
    """A different endpoint, and nothing else different that a stored row could carry."""
    (sweep,) = (entry for entry in TUSHARE_STATEMENT_SWEEPS if entry.dataset == dataset)
    (subject,) = (entry for entry in TUSHARE_DATASETS if entry.dataset == dataset)

    assert sweep.endpoint == f"{dataset}_vip"
    assert subject.endpoint == dataset
    for field in (
        "dataset",
        "kind",
        "subject_field",
        "date_field",
        "clock",
        "response_fields",
        "required_response_fields",
        "source_uri_template",
        "panel_columns",
    ):
        assert getattr(sweep, field) == getattr(subject, field), field
    assert sweep.requires_truncation_flag
    assert sweep.max_rows_per_response is not None
    # Measured unsound: see `_statement_sweep_descriptor`.
    assert sweep.page_size is None


def test_the_sweeps_are_not_datasets_of_their_own() -> None:
    """`supported_datasets`, `doctor --probe` and every table-wide census read
    `TUSHARE_DATASETS`; a `*_vip` name there would be a dataset nothing stores."""
    names = {entry.dataset for entry in TUSHARE_DATASETS}
    assert not any(name.endswith("_vip") for name in names)
    assert {entry.dataset for entry in TUSHARE_STATEMENT_SWEEPS} == set(
        FINANCIAL_STATEMENT_DATASETS
    )


def test_a_year_is_twelve_announcement_months_or_four_report_periods() -> None:
    assert statement_sweep_windows(INCOME_DATASET, 2024) == tuple(
        f"2024{month:02d}" for month in range(1, 13)
    )
    assert statement_sweep_windows(FINANCIAL_INDICATOR_DATASET, 2023) == (
        "20230331",
        "20230630",
        "20230930",
        "20231231",
    )


# --- the request ---------------------------------------------------------------------------------


@pytest.mark.parametrize("dataset", [INCOME_DATASET, BALANCE_SHEET_DATASET, CASH_FLOW_DATASET])
def test_a_month_that_fits_is_one_request_for_the_whole_market(
    monkeypatch: pytest.MonkeyPatch, dataset: str
) -> None:
    _cap(monkeypatch, dataset, 10)
    rows = [
        _row(dataset, f"00000{index}.SZ", "20240331", f"202404{10 + index:02d}", float(index))
        for index in range(1, 6)
    ]
    transport = CappedMarket(dataset, rows, cap=10)

    batch = _provider(transport).fetch_panel_sweep(_request(dataset, "202404"))

    assert batch.status == "success"
    assert batch.dataset == dataset
    assert batch.row_count == 5
    assert [entry["api_name"] for entry in transport.payloads] == [f"{dataset}_vip"]
    assert [entry["params"] for entry in transport.payloads] == [
        {"start_date": "20240401", "end_date": "20240430"}
    ]
    assert batch.source_uri is not None and batch.source_uri.startswith(f"tushare://{dataset}/")


def test_a_month_over_the_cap_is_halved_until_every_window_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five filings at a cap of three: April is refused whole, its first half fits, its second
    half is refused and fits once halved again. Every row arrives exactly once."""
    _cap(monkeypatch, INCOME_DATASET, 3)
    days = ("20240405", "20240412", "20240418", "20240425", "20240429")
    rows = [
        _row(INCOME_DATASET, f"00000{index}.SZ", "20240331", day, float(index))
        for index, day in enumerate(days, start=1)
    ]
    transport = CappedMarket(INCOME_DATASET, rows, cap=3)

    batch = _provider(transport).fetch_panel_sweep(_request(INCOME_DATASET, "202404"))

    assert batch.row_count == 5
    assert sorted(batch.subjects) == [f"00000{index}.SZ" for index in range(1, 6)]
    assert _windows(transport) == [
        ("20240401", "20240430"),
        ("20240401", "20240415"),
        ("20240416", "20240430"),
        ("20240416", "20240423"),
        ("20240424", "20240430"),
    ]
    assert all("offset" not in entry["params"] for entry in transport.payloads)


def test_a_report_period_is_one_request_by_period(monkeypatch: pytest.MonkeyPatch) -> None:
    _cap(monkeypatch, FINANCIAL_INDICATOR_DATASET, 10)
    rows = [
        _row(FINANCIAL_INDICATOR_DATASET, "000001.SZ", "20231231", "20240315", 1.0),
        _row(FINANCIAL_INDICATOR_DATASET, "600000.SH", "20231231", "20240420", 2.0),
        _row(FINANCIAL_INDICATOR_DATASET, "600001.SH", "20230930", "20231025", 3.0),
    ]
    transport = CappedMarket(FINANCIAL_INDICATOR_DATASET, rows, cap=10)

    batch = _provider(transport).fetch_panel_sweep(
        _request(FINANCIAL_INDICATOR_DATASET, "20231231")
    )

    assert batch.row_count == 2
    assert transport.payloads[0]["api_name"] == "fina_indicator_vip"
    assert [entry["params"] for entry in transport.payloads] == [{"period": "20231231"}]


@pytest.mark.parametrize(
    ("dataset", "subjects"),
    [
        (INCOME_DATASET, ("2024",)),
        (INCOME_DATASET, ("202413",)),
        (INCOME_DATASET, ("000001.SZ",)),
        (INCOME_DATASET, ("202404", "202405")),
        (INCOME_DATASET, ()),
        (FINANCIAL_INDICATOR_DATASET, ("20230515",)),
        (FINANCIAL_INDICATOR_DATASET, ("2023",)),
        (FINANCIAL_INDICATOR_DATASET, ("000001.SZ", "2023")),
    ],
)
def test_a_window_that_is_not_one_month_or_one_quarter_end_is_refused(
    dataset: str, subjects: tuple[str, ...]
) -> None:
    transport = CappedMarket(dataset, [], cap=10)

    with pytest.raises(ProviderFailure) as raised:
        _provider(transport).fetch_panel_sweep(
            ProviderRequest(dataset=dataset, as_of=CLOCK, subjects=subjects)
        )

    assert raised.value.category == "configuration"
    assert transport.payloads == []


# --- the completeness rules ----------------------------------------------------------------------


def test_a_response_at_the_cap_is_refused_even_when_it_says_it_is_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repository's oldest completeness rule: a response at the measured cap cannot be told
    from one the cap truncated, whatever its `has_more` says. Every window gets the same answer
    here, so the halving reaches a single day and that day is refused."""
    _cap(monkeypatch, INCOME_DATASET, 3)
    at_cap = [
        _row(INCOME_DATASET, f"00000{index}.SZ", "20240331", "20240420", 1.0) for index in (1, 2, 3)
    ]
    transport = CappedMarket(
        INCOME_DATASET,
        [],
        cap=3,
        static={
            "code": 0,
            "msg": "",
            "data": {"fields": _fields(INCOME_DATASET), "items": at_cap, "has_more": False},
        },
    )

    with pytest.raises(TushareResponseTruncated) as raised:
        _provider(transport).fetch_panel_sweep(_request(INCOME_DATASET, "202404"))

    assert "single announcement day 20240401" in str(raised.value)


def test_a_single_day_over_the_cap_is_refused_rather_than_paged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cap(monkeypatch, INCOME_DATASET, 3)
    rows = [
        _row(INCOME_DATASET, f"00000{index}.SZ", "20240331", "20240428", float(index))
        for index in range(1, 6)
    ]
    transport = CappedMarket(INCOME_DATASET, rows, cap=3)

    with pytest.raises(TushareResponseTruncated) as raised:
        _provider(transport).fetch_panel_sweep(_request(INCOME_DATASET, "202404"))

    assert "single announcement day 20240428" in str(raised.value)
    assert ("20240428", "20240428") in _windows(transport)
    assert all("limit" not in entry["params"] for entry in transport.payloads)


def test_a_report_period_at_the_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A period has no finer date axis to halve along, so the cap is a refusal outright."""
    _cap(monkeypatch, FINANCIAL_INDICATOR_DATASET, 2)
    rows = [
        _row(FINANCIAL_INDICATOR_DATASET, f"00000{index}.SZ", "20231231", "20240315", 1.0)
        for index in (1, 2, 3)
    ]
    transport = CappedMarket(FINANCIAL_INDICATOR_DATASET, rows, cap=2)

    with pytest.raises(TushareResponseTruncated):
        _provider(transport).fetch_panel_sweep(_request(FINANCIAL_INDICATOR_DATASET, "20231231"))

    assert len(transport.payloads) == 1


def test_an_empty_month_is_no_data_rather_than_a_failure() -> None:
    transport = CappedMarket(INCOME_DATASET, [], cap=10)

    batch = _provider(transport).fetch_panel_sweep(_request(INCOME_DATASET, "202402"))

    assert batch.status == "no_data"
    assert len(transport.payloads) == 1


def test_the_token_is_resolved_by_the_provider_and_required_for_a_sweep() -> None:
    transport = CappedMarket(INCOME_DATASET, [], cap=10)

    with pytest.raises(ProviderFailure) as raised:
        TushareProvider(token="", transport=transport, clock=lambda: CLOCK).fetch_panel_sweep(
            _request(INCOME_DATASET, "202404")
        )

    assert raised.value.category == "configuration"
    assert transport.payloads == []


def test_a_dataset_without_a_sweep_is_refused_by_name() -> None:
    transport = CappedMarket(INCOME_DATASET, [], cap=10)

    with pytest.raises(ProviderFailure) as raised:
        _provider(transport).fetch_panel_sweep(
            ProviderRequest(dataset="daily", as_of=CLOCK, subjects=("202404",))
        )

    assert raised.value.category == "configuration"
    assert "daily" in str(raised.value)


# --- a window at the cap that cannot be narrowed by date: registry chunks (fix round 3) ----------


def _codes(transport: CappedMarket) -> list[str | None]:
    return [entry["params"].get("ts_code") for entry in transport.payloads]


def _list_limit(monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    """Shrink the measured 1,000-code list limit, for `_cap`'s reason."""
    monkeypatch.setattr(tushare, "TUSHARE_TS_CODE_LIST_LIMIT", limit)


def test_a_capped_report_period_is_refetched_in_chunks_of_the_given_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`fina_indicator_vip period=20211231` answered exactly its 12,000-row cap live; a period has
    no date axis to halve along, so the codes are the axis: disjoint chunks, one row set."""
    _cap(monkeypatch, FINANCIAL_INDICATOR_DATASET, 3)
    _list_limit(monkeypatch, 2)
    codes = ("000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ")
    rows = [
        _row(FINANCIAL_INDICATOR_DATASET, code, "20211231", "20220420", float(index))
        for index, code in enumerate(codes)
    ]
    transport = CappedMarket(FINANCIAL_INDICATOR_DATASET, rows, cap=3)

    batch = _provider(transport).fetch_panel_sweep(
        _request(FINANCIAL_INDICATOR_DATASET, "20211231"), codes=tuple(reversed(codes))
    )

    assert batch.row_count == 5
    assert sorted(batch.subjects) == list(codes)
    # One capped answer, then the codes in sorted order, two at a time.
    assert _codes(transport) == [
        None,
        "000001.SZ,000002.SZ",
        "000003.SZ,000004.SZ",
        "000005.SZ",
    ]
    assert all(entry["params"]["period"] == "20211231" for entry in transport.payloads)


def test_a_capped_chunk_is_halved_until_each_half_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    _cap(monkeypatch, FINANCIAL_INDICATOR_DATASET, 3)
    codes = ("000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ")
    rows = [_row(FINANCIAL_INDICATOR_DATASET, code, "20211231", "20220420", 1.0) for code in codes]
    transport = CappedMarket(FINANCIAL_INDICATOR_DATASET, rows, cap=3)

    batch = _provider(transport).fetch_panel_sweep(
        _request(FINANCIAL_INDICATOR_DATASET, "20211231"), codes=codes
    )

    assert batch.row_count == 4
    assert _codes(transport) == [
        None,
        "000001.SZ,000002.SZ,000003.SZ,000004.SZ",
        "000001.SZ,000002.SZ",
        "000003.SZ,000004.SZ",
    ]


def test_a_single_code_at_the_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _cap(monkeypatch, FINANCIAL_INDICATOR_DATASET, 3)
    rows = [
        _row(FINANCIAL_INDICATOR_DATASET, "000001.SZ", "20211231", f"2022042{day}", 1.0)
        for day in (1, 2, 3)
    ]
    transport = CappedMarket(FINANCIAL_INDICATOR_DATASET, rows, cap=3)

    with pytest.raises(TushareResponseTruncated) as raised:
        _provider(transport).fetch_panel_sweep(
            _request(FINANCIAL_INDICATOR_DATASET, "20211231"), codes=("000001.SZ",)
        )

    assert "000001.SZ" in str(raised.value)


def test_a_capped_single_day_is_refetched_in_chunks_of_the_given_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The statement endpoints' narrowest date window is one day; measured live, their VIP twins
    honour a comma-joined `ts_code` inside it (unlike the per-security endpoints, which answer a
    list with zero rows), so a capped day takes the same fallback instead of refusing."""
    _cap(monkeypatch, INCOME_DATASET, 3)
    codes = ("000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ")
    rows = [_row(INCOME_DATASET, code, "20240331", "20240428", 1.0) for code in codes]
    transport = CappedMarket(INCOME_DATASET, rows, cap=3)

    batch = _provider(transport).fetch_panel_sweep(_request(INCOME_DATASET, "202404"), codes=codes)

    assert batch.row_count == 5
    chunked = [entry["params"] for entry in transport.payloads if "ts_code" in entry["params"]]
    assert {(entry["start_date"], entry["end_date"]) for entry in chunked} == {
        ("20240428", "20240428")
    }
    assert {code for entry in chunked for code in entry["ts_code"].split(",")} == set(codes)


# --- the capped answer is a witness the chunks must cover (fix round 4) ---------------------------


@pytest.mark.parametrize(
    ("dataset", "window", "period", "announced"),
    [
        (FINANCIAL_INDICATOR_DATASET, "20211231", "20211231", "20220420"),
        (INCOME_DATASET, "202404", "20240331", "20240428"),
    ],
)
@pytest.mark.parametrize("drops", [True, False])
def test_chunks_that_miss_a_row_the_capped_answer_showed_are_refused(
    monkeypatch: pytest.MonkeyPatch,
    dataset: str,
    window: str,
    period: str,
    announced: str,
    drops: bool,
) -> None:
    """The capped first answer is a witness: every row of it whose `ts_code` is among `codes` must
    be in the chunks' union. A comma-list answer that comes back short -- which is what the
    per-security endpoints do with a list, silently -- is refused instead of storing a capped day
    or period short; one that answers correctly is accepted."""
    _cap(monkeypatch, dataset, 3)
    codes = ("000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ")
    rows = [_row(dataset, code, period, announced, 1.0) for code in codes]
    transport = CappedMarket(dataset, rows, cap=3, drops_from_lists=drops)

    if drops:
        with pytest.raises(ProviderFailure) as raised:
            _provider(transport).fetch_panel_sweep(_request(dataset, window), codes=codes)
        message = str(raised.value)
        assert f"{dataset}_vip" in message
        assert (period if dataset == FINANCIAL_INDICATOR_DATASET else announced) in message
        assert "missing 2 of the 3" in message
    else:
        batch = _provider(transport).fetch_panel_sweep(_request(dataset, window), codes=codes)
        assert batch.row_count == 5


def test_the_codes_a_capped_answer_showed_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A chunked window fetches only `codes`, so the securities outside them that the capped
    answer showed are the only ones the caller can count; the provider keeps them."""
    _cap(monkeypatch, FINANCIAL_INDICATOR_DATASET, 3)
    rows = [
        _row(FINANCIAL_INDICATOR_DATASET, code, "20211231", "20220420", 1.0)
        for code in ("000001.SZ", "000002.SZ", "900001.SH", "900002.SH")
    ]
    transport = CappedMarket(FINANCIAL_INDICATOR_DATASET, rows, cap=3)
    provider = _provider(transport)

    batch = provider.fetch_panel_sweep(
        _request(FINANCIAL_INDICATOR_DATASET, "20211231"), codes=("000001.SZ", "000002.SZ")
    )

    assert sorted(batch.subjects) == ["000001.SZ", "000002.SZ"]
    assert provider.witnessed_codes == frozenset({"000001.SZ", "000002.SZ", "900001.SH"})
